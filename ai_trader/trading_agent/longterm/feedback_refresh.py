"""Dry-run feedback refresh helpers for the long-term trader."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_execution_eligibility import PaperExecutionEligibilityBuilder
from longterm.paper_preview_status import PaperPreviewStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.review_status import ReviewStatusBuilder
from portfolio.portfolio_profile import PortfolioProfile


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"


def run_feedback_refresh(
    *,
    journal: LongTermDecisionJournal,
    paper_ledger: PaperTradeLedger | None = None,
    profile: PortfolioProfile | None = None,
    portfolio_state: PortfolioState | None = None,
    action_plan: Mapping[str, Any] | None = None,
    eligibility_payload: Mapping[str, Any] | None = None,
    record_eligibility_events: bool = False,
    reconciliation: Mapping[str, Any] | None = None,
    outcome_price_map: Mapping[str, Mapping[str, Any]] | None = None,
    lessons: list[Mapping[str, Any]] | None = None,
    stale_after_days: int = 30,
    today: datetime | None = None,
    active_rules_path: str | Path = DEFAULT_RULES_PATH,
) -> dict[str, Any]:
    """Run explicit dry-run feedback maintenance and return structured output."""
    warnings: list[str] = []
    current = today or datetime.now(UTC)
    profile_rebuild = journal.rebuild_symbol_feedback_profiles()
    preview_feedback = {"profiles_updated": 0, "symbols": []}
    if paper_ledger is not None:
        preview_status = PaperPreviewStatusBuilder(paper_ledger).build()
        preview_feedback = journal.apply_paper_preview_feedback(preview_status.by_symbol)
    reconciliation_feedback = {"profiles_updated": 0, "symbols": []}
    if reconciliation:
        reconciliation_feedback = journal.apply_paper_reconciliation_feedback(reconciliation)
    outcome_refresh = {"decisions_updated": 0, "updated_decision_ids": []}
    if outcome_price_map:
        outcome_refresh = journal.refresh_outcomes_from_price_map(outcome_price_map)

    eligibility = dict(eligibility_payload or {})
    if not eligibility and action_plan and paper_ledger and profile and portfolio_state:
        eligibility = PaperExecutionEligibilityBuilder(
            now_func=lambda: current,
            paper_execution_enabled=False,
        ).build(action_plan, ledger=paper_ledger, profile=profile, portfolio_state=portfolio_state)
    eligibility_events = {"events_recorded": 0, "events_skipped": 0, "event_ids": []}
    if record_eligibility_events:
        if paper_ledger is None:
            raise ValueError("--record-eligibility-events requires --paper-ledger-db.")
        if eligibility:
            eligibility_events = paper_ledger.record_eligibility_events(eligibility)

    freshness = outcome_freshness(journal, stale_after_days=stale_after_days, today=current)
    review_status = ReviewStatusBuilder(journal, today=current.date()).build(limit=1000)
    review_status_counts = _review_status_counts(review_status)
    benchmark_result = BenchmarkGuard().evaluate(journal.summarize_benchmark_performance())
    rules_reference = active_rules_reference(active_rules_path)
    tuning_inputs = build_feedback_tuning_inputs(
        journal=journal,
        outcome_freshness_payload=freshness,
        review_status_by_symbol=review_status,
        benchmark_guard_result=benchmark_result,
        active_rules_reference_payload=rules_reference,
        eligibility_payload=eligibility,
        lessons=lessons or [],
    )
    result = {
        "schema_version": 1,
        "mode": "dry_run_feedback_refresh",
        "order_submission_enabled": False,
        "warnings": warnings,
        "profile_rebuild": profile_rebuild,
        "paper_preview_feedback": preview_feedback,
        "reconciliation_feedback": reconciliation_feedback,
        "outcome_refresh": outcome_refresh,
        "outcome_freshness": freshness,
        "eligibility": eligibility or {"items": [], "eligible_count": 0, "blocked_count": 0},
        "eligibility_events": eligibility_events,
        "review_status_counts": review_status_counts,
        "benchmark_guard": {
            "should_pause_new_buys": benchmark_result.should_pause_new_buys,
            "reason": benchmark_result.reason,
        },
        "feedback_tuning_inputs": tuning_inputs,
    }
    result["markdown"] = build_feedback_markdown(result)
    return result


def outcome_freshness(
    journal: LongTermDecisionJournal,
    *,
    stale_after_days: int = 30,
    today: datetime | None = None,
) -> dict[str, Any]:
    """Compute ephemeral outcome freshness from journal timestamps."""
    current = today or datetime.now(UTC)
    rows = journal.list_recent_decisions(limit=10000)
    items = []
    counts: dict[str, int] = {}
    for row in rows:
        updated_at = row.get("outcome_updated_at")
        days = None
        if updated_at:
            parsed = _parse_datetime(updated_at)
            days = (current - parsed).days
            state = "stale" if days > stale_after_days else "fresh"
        else:
            state = "never_refreshed"
        counts[state] = counts.get(state, 0) + 1
        items.append(
            {
                "decision_id": row.get("decision_id"),
                "journal_short_id": str(row.get("decision_id") or "")[:8],
                "symbol": row.get("symbol"),
                "outcome_updated_at": updated_at,
                "days_since_outcome_update": days,
                "freshness_state": state,
            }
        )
    return {
        "stale_after_days": stale_after_days,
        "counts": counts,
        "items": items,
    }


def build_feedback_tuning_inputs(
    *,
    journal: LongTermDecisionJournal,
    outcome_freshness_payload: Mapping[str, Any],
    review_status_by_symbol: Mapping[str, Mapping[str, Any]],
    benchmark_guard_result: Any,
    active_rules_reference_payload: Mapping[str, Any],
    eligibility_payload: Mapping[str, Any] | None = None,
    lessons: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = journal.list_recommendation_table(limit=1000)
    freshness_by_symbol = {
        str(item.get("symbol") or "").upper(): item
        for item in outcome_freshness_payload.get("items") or []
    }
    symbols = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        profile = journal.get_symbol_feedback_profile(symbol) or {}
        symbols.append(
            {
                "symbol": symbol,
                "decision_id": row.get("decision_id"),
                "recommendation_count": profile.get("recommendation_count", row.get("times_recommended")),
                "new_information_count": profile.get("new_information_count", row.get("new_information_count")),
                "paper_preview_blocked_count": profile.get("paper_preview_blocked_count", 0),
                "paper_preview_blocked_reasons": profile.get("paper_preview_blocked_reasons", []),
                "paper_reconciliation_mismatch_count": profile.get("paper_reconciliation_mismatch_count", 0),
                "outcome_freshness": freshness_by_symbol.get(symbol, {}),
                "review_status": dict(review_status_by_symbol.get(symbol, {})),
                "excess_return_pct": row.get("excess_return_pct"),
            }
        )
    return {
        "analysis_only": True,
        "prohibited_uses": ["ranking_mutation", "sizing_mutation", "broker_execution"],
        "active_rules_reference": dict(active_rules_reference_payload),
        "benchmark_guard": {
            "should_pause_new_buys": benchmark_guard_result.should_pause_new_buys,
            "reason": benchmark_guard_result.reason,
        },
        "eligibility_summary": _eligibility_summary(eligibility_payload or {}),
        "lessons_count": len(lessons or []),
        "lessons": list(lessons or []),
        "symbols": symbols,
    }


def build_feedback_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Long-Term Feedback Refresh",
        "",
        f"- Mode: `{result.get('mode')}`",
        f"- Order submission enabled: `{str(result.get('order_submission_enabled')).lower()}`",
        f"- Benchmark gate: {result.get('benchmark_guard', {}).get('reason', '')}",
        "",
        "## Outcome Freshness",
        "",
        "| State | Count |",
        "|---|---:|",
    ]
    for state, count in sorted((result.get("outcome_freshness", {}).get("counts") or {}).items()):
        lines.append(f"| {state} | {count} |")
    lines.extend(["", "## Eligibility", "", "| Symbol | Status | Reasons |", "|---|---|---|"])
    for item in result.get("eligibility", {}).get("items") or []:
        reasons = "; ".join(str(reason) for reason in (item.get("blocked_reasons") or []))
        lines.append(f"| {item.get('symbol', '')} | {item.get('status', '')} | {reasons} |")
    lines.extend(["", "## Review Status", "", "| State | Count |", "|---|---:|"])
    for state, count in sorted((result.get("review_status_counts") or {}).items()):
        lines.append(f"| {state} | {count} |")
    return "\n".join(lines) + "\n"


def active_rules_reference(path: str | Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    return {
        "path": str(path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "excerpt": text[:600],
    }


def _eligibility_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for item in payload.get("items") or []:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        for reason in item.get("blocked_reasons") or []:
            text = str(reason)
            reasons[text] = reasons.get(text, 0) + 1
    return {"status_counts": counts, "blocked_reason_counts": reasons}


def _review_status_counts(review_status_by_symbol: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in review_status_by_symbol.values():
        state = str(row.get("thesis_state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _parse_datetime(value: Any) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "active_rules_reference",
    "build_feedback_markdown",
    "build_feedback_tuning_inputs",
    "outcome_freshness",
    "run_feedback_refresh",
]
