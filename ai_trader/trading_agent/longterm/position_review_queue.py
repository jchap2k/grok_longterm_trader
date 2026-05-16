"""No-submit sell/rebalance/news review queue for current positions."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.graham_risk import (
    evaluate_permanent_loss_risk,
    evaluate_staged_entry,
    mr_market_review_trigger,
)
from longterm.portfolio_state import Holding, PortfolioState
from longterm.review_status import ReviewStatusBuilder
from longterm.reviewers import MarginOfSafetyReviewer
from research.intake import create_research_packet_from_idea


NowFunc = Callable[[], datetime]


@dataclass(frozen=True)
class PositionReviewQueueInputs:
    """Local artifact inputs for deterministic position review triage."""

    portfolio_state: PortfolioState | Mapping[str, Any] | None = None
    action_plan: Mapping[str, Any] | None = None
    portfolio_news_monitor: Mapping[str, Any] | None = None
    journal_db: str | Path | None = None
    include_protected_symbols: bool = False


def build_position_review_queue_report(
    inputs: PositionReviewQueueInputs,
    *,
    now_func: NowFunc | None = None,
) -> dict[str, Any]:
    """Build an advisory review queue without broker, journal, or LLM mutation."""
    generated_at = _format_timestamp((now_func or _utc_now)())
    portfolio = _coerce_portfolio(inputs.portfolio_state)
    protected = set(portfolio.protected_symbols if portfolio else [])
    holdings = {holding.symbol: holding for holding in (portfolio.holdings if portfolio else [])}
    latest_by_symbol = _latest_journal_rows(inputs.journal_db)
    review_status = _review_status_by_symbol(inputs.journal_db)
    rows: list[dict[str, Any]] = []
    excluded_protected: set[str] = set()

    for intent in (inputs.action_plan or {}).get("intents") or []:
        if not isinstance(intent, Mapping):
            continue
        symbol = str(intent.get("symbol") or intent.get("source_symbol") or "").upper()
        if not symbol:
            continue
        if symbol in protected and not inputs.include_protected_symbols:
            excluded_protected.add(symbol)
            continue
        review_type = _review_type_for_intent(intent)
        if not review_type:
            continue
        rows.append(
            _base_row(
                symbol=symbol,
                review_type=review_type,
                trigger_source="account_action_plan",
                severity="high" if review_type == "sell_review" else "medium",
                generated_at=generated_at,
                holding=holdings.get(symbol),
                latest=latest_by_symbol.get(symbol, {}),
                status=review_status.get(symbol, {}),
                decision_id=str(intent.get("decision_id") or ""),
                reason=str(intent.get("reason") or intent.get("context") or ""),
                extra={
                    "intent_type": str(intent.get("intent_type") or "").upper(),
                    "order_intent": str(intent.get("order_intent") or "").upper(),
                    "target_symbol": str(intent.get("target_symbol") or "").upper(),
                    "source_symbol": str(intent.get("source_symbol") or "").upper(),
                },
            )
        )

    for item in (inputs.portfolio_news_monitor or {}).get("enrichment_needed_queue") or []:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        if symbol in protected and not inputs.include_protected_symbols:
            excluded_protected.add(symbol)
            continue
        if _news_should_escalate(item):
            rows.append(
                _base_row(
                    symbol=symbol,
                    review_type="thesis_news_review",
                    trigger_source="portfolio_news_monitor",
                    severity=_news_severity(item),
                    generated_at=generated_at,
                    holding=holdings.get(symbol),
                    latest=latest_by_symbol.get(symbol, {}),
                    status=review_status.get(symbol, {}),
                    decision_id=str(item.get("linked_decision_id") or ""),
                    reason=str(item.get("summary") or item.get("thesis_impact_hint") or ""),
                    extra={
                        "impact_category": str(item.get("impact_category") or ""),
                        "relevance_score": float(item.get("relevance_score") or 0.0),
                        "article_title": str(item.get("title") or ""),
                        "article_url": str(item.get("url") or ""),
                        "thesis_impact_hint": str(item.get("thesis_impact_hint") or ""),
                    },
                )
            )

    for holding in holdings.values():
        if holding.symbol in protected and not inputs.include_protected_symbols:
            excluded_protected.add(holding.symbol)
            continue
        graduation = _staged_graduation_review(
            holding,
            latest=latest_by_symbol.get(holding.symbol, {}),
            status=review_status.get(holding.symbol, {}),
            active_base_value=_active_base_value(portfolio),
        )
        if graduation:
            rows.append(
                _base_row(
                    symbol=holding.symbol,
                    review_type="staged_entry_graduation_review",
                    trigger_source="portfolio_state",
                    severity="medium",
                    generated_at=generated_at,
                    holding=holding,
                    latest=latest_by_symbol.get(holding.symbol, {}),
                    status=review_status.get(holding.symbol, {}),
                    decision_id=str(latest_by_symbol.get(holding.symbol, {}).get("decision_id") or ""),
                    reason=graduation["reason"],
                    extra=graduation["extra"],
                )
            )
        quote_review = mr_market_review_trigger(holding)
        if not quote_review.review_due:
            continue
        rows.append(
            _base_row(
                symbol=holding.symbol,
                review_type=quote_review.category,
                trigger_source="portfolio_state",
                severity="high" if quote_review.category == "mr_market_drawdown_review" else "medium",
                generated_at=generated_at,
                holding=holding,
                latest=latest_by_symbol.get(holding.symbol, {}),
                status=review_status.get(holding.symbol, {}),
                decision_id=str(latest_by_symbol.get(holding.symbol, {}).get("decision_id") or ""),
                reason=quote_review.reason,
                extra={
                    "mr_market_gain_percent": quote_review.gain_percent,
                    "suggested_review_focus": _mr_market_review_focus(quote_review.category),
                },
            )
        )

    rows = _dedupe_rows(rows)
    rows.sort(key=lambda row: (_severity_sort(row), row["symbol"], row["review_type"]))
    counts = _counts_by_type(rows)
    return {
        "schema_version": 1,
        "mode": "position_review_queue",
        "status": "completed",
        "generated_at": generated_at,
        "order_submission_enabled": False,
        "llm_calls_enabled": False,
        "broker_calls_enabled": False,
        "review_count": len(rows),
        "counts_by_review_type": counts,
        "excluded_protected_symbols": sorted(excluded_protected),
        "review_queue": rows,
        "next_safe_action": "review_position_queue_before_any_sell_rebalance_or_submit_profile",
        "notes": [
            "Advisory queue only. It does not submit orders, call a broker, call an LLM, or mutate the journal.",
            "Rows are inputs for operator/LLM review, not authorization to trade.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a no-submit current-position review queue.")
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--portfolio-news-monitor", default="")
    parser.add_argument("--journal-db", default="")
    parser.add_argument("--include-protected-symbols", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_position_review_queue_report(
        PositionReviewQueueInputs(
            portfolio_state=PortfolioState.from_file(args.portfolio_state) if args.portfolio_state else None,
            action_plan=_load_optional_mapping(args.action_plan),
            portfolio_news_monitor=_load_optional_mapping(args.portfolio_news_monitor),
            journal_db=args.journal_db or None,
            include_protected_symbols=bool(args.include_protected_symbols),
        )
    )
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Position review queue completed with {report['review_count']} row(s).")
    return 0


def _base_row(
    *,
    symbol: str,
    review_type: str,
    trigger_source: str,
    severity: str,
    generated_at: str,
    holding: Holding | None,
    latest: Mapping[str, Any],
    status: Mapping[str, Any],
    decision_id: str,
    reason: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    market_value = float(holding.market_value if holding else 0.0)
    cost = float(holding.original_purchase_total_cost if holding else 0.0)
    return {
        "symbol": symbol,
        "review_type": review_type,
        "trigger_source": trigger_source,
        "severity": severity,
        "actionability": "review_required",
        "decision_id": decision_id or str(latest.get("decision_id") or ""),
        "latest_recommendation": str(latest.get("recommendation") or ""),
        "thesis_state": str(status.get("thesis_state") or ""),
        "review_due": bool(status.get("review_due")),
        "review_reason": str(status.get("review_reason") or ""),
        # Re-underwriting durability signals (new in 2026-05)
        "thesis_durability": str(status.get("thesis_durability") or status.get("thesis_state") or ""),
        "reunderwrite_due": bool(status.get("reunderwrite_due") or status.get("thesis_durability") in {"weakening", "broken"}),
        "portfolio_market_value": market_value,
        "portfolio_quantity": float(holding.quantity if holding else 0.0),
        "portfolio_cost_basis": cost,
        "portfolio_unrealized_pnl": float(holding.unrealized_pnl if holding else 0.0),
        "portfolio_unrealized_pnl_percent": float(holding.unrealized_pnl_percent if holding else 0.0),
        "reason": reason,
        "generated_at": generated_at,
        **dict(extra),
    }


def _review_type_for_intent(intent: Mapping[str, Any]) -> str:
    intent_type = str(intent.get("intent_type") or "").upper()
    order_intent = str(intent.get("order_intent") or "").upper()
    if intent_type == "REBALANCE" or str(intent.get("source_symbol") or ""):
        return "rebalance_review"
    if intent_type == "SELL" or order_intent == "SELL":
        return "sell_review"
    if intent_type == "REDUCE" or order_intent in {"REDUCE", "TRIM"}:
        return "reduce_review"
    return ""


def _news_should_escalate(item: Mapping[str, Any]) -> bool:
    hint = str(item.get("thesis_impact_hint") or "").lower()
    impact = str(item.get("impact_category") or "").lower()
    return hint in {"review_required", "potential_invalidation"} or "high" in impact


def _news_severity(item: Mapping[str, Any]) -> str:
    impact = str(item.get("impact_category") or "").lower()
    hint = str(item.get("thesis_impact_hint") or "").lower()
    if "high" in impact or hint == "potential_invalidation":
        return "high"
    return "medium"


def _mr_market_review_focus(category: str) -> str:
    if category == "mr_market_drawdown_review":
        return "sell_or_add_after_thesis_check"
    if category == "mr_market_rally_review":
        return "trim_or_trailing_profit_review"
    return "review_quote_vs_value"


def _staged_graduation_review(
    holding: Holding,
    *,
    latest: Mapping[str, Any],
    status: Mapping[str, Any],
    active_base_value: float,
) -> dict[str, Any] | None:
    recommendation = str(latest.get("recommendation") or "").upper()
    if recommendation not in {"BUY", "ADD"}:
        return None
    thesis_state = str(status.get("thesis_state") or "").lower()
    if thesis_state in {"broken", "weakening"}:
        return None
    suggested_size_pct = _float(latest.get("suggested_size_pct"))
    if suggested_size_pct <= 0 or active_base_value <= 0:
        return None
    packet = _packet_from_latest(latest)
    if not packet:
        return None
    margin_score = MarginOfSafetyReviewer().review(create_research_packet_from_idea(packet)).score
    staged = evaluate_staged_entry(
        suggested_size_pct=suggested_size_pct,
        margin_of_safety_score=margin_score,
        risk_report=evaluate_permanent_loss_risk(packet),
        company_category=packet.get("company_category"),
    )
    if staged.label != "starter_position" or staged.recommended_size_pct <= 0:
        return None
    current_size_pct = holding.market_value / active_base_value * 100.0
    if current_size_pct < staged.recommended_size_pct * 0.75:
        return None
    if current_size_pct >= suggested_size_pct * 0.8:
        return None
    original_target_value = round(active_base_value * suggested_size_pct / 100.0, 2)
    starter_target_value = round(active_base_value * staged.recommended_size_pct / 100.0, 2)
    remaining_to_target = round(max(0.0, original_target_value - holding.market_value), 2)
    return {
        "reason": (
            f"{holding.symbol} is near its Graham starter size "
            f"({current_size_pct:.2f}% vs {staged.recommended_size_pct:.2f}%) "
            f"but remains below the original {suggested_size_pct:.2f}% target; "
            "review whether margin of safety, evidence, and thesis quality now support adding toward target."
        ),
        "extra": {
            "suggested_review_focus": "add_toward_target_after_margin_review",
            "current_size_pct": round(current_size_pct, 2),
            "starter_size_pct": round(staged.recommended_size_pct, 2),
            "original_target_size_pct": round(suggested_size_pct, 2),
            "starter_target_value": starter_target_value,
            "original_target_value": original_target_value,
            "remaining_to_target_value": remaining_to_target,
            "margin_of_safety_score": round(float(margin_score), 2),
        },
    }


def _coerce_portfolio(value: PortfolioState | Mapping[str, Any] | None) -> PortfolioState | None:
    if value is None:
        return None
    if isinstance(value, PortfolioState):
        return value
    return PortfolioState(**dict(value))


def _active_base_value(portfolio: PortfolioState | None) -> float:
    if portfolio is None:
        return 0.0
    return float(portfolio.cash or 0.0) + float(portfolio.active_market_value or 0.0)


def _latest_journal_rows(journal_db: str | Path | None) -> dict[str, dict[str, Any]]:
    if not journal_db:
        return {}
    path = Path(journal_db)
    if not path.exists():
        return {}
    rows = LongTermDecisionJournal(path).list_recommendation_table(limit=1000)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in latest:
            latest[symbol] = dict(row)
    return latest


def _packet_from_latest(latest: Mapping[str, Any]) -> dict[str, Any]:
    packet_json = latest.get("packet_json")
    if isinstance(packet_json, str) and packet_json.strip():
        try:
            packet = json.loads(packet_json)
        except json.JSONDecodeError:
            packet = {}
        if isinstance(packet, Mapping):
            return dict(packet)
    symbol = str(latest.get("symbol") or "").upper()
    if not symbol:
        return {}
    return {
        "symbol": symbol,
        "company_name": latest.get("company_name") or symbol,
        "recommendation": latest.get("recommendation") or "",
    }


def _review_status_by_symbol(journal_db: str | Path | None) -> dict[str, dict[str, Any]]:
    if not journal_db:
        return {}
    path = Path(journal_db)
    if not path.exists():
        return {}
    return ReviewStatusBuilder(LongTermDecisionJournal(path)).build(limit=10000)


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _load_optional_mapping(path: str | Path) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Expected JSON object in {path}")
    return dict(payload)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("symbol") or ""),
            str(row.get("review_type") or ""),
            str(row.get("decision_id") or ""),
            str(row.get("article_url") or row.get("reason") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _counts_by_type(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("review_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _severity_sort(row: Mapping[str, Any]) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(row.get("severity") or "").lower(), 3)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "PositionReviewQueueInputs",
    "build_parser",
    "build_position_review_queue_report",
    "run_cli",
]
