"""Prioritized next-action planning for the long-term trader."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from longterm.action_planner import ActionPlanner
from longterm.benchmark_guard import BenchmarkGuard, BenchmarkGuardResult
from longterm.buy_promotion import BuyPromotionReview, BuyPromotionReviewer
from longterm.decision_journal import LongTermDecisionJournal
from longterm.graham_risk import mr_market_review_trigger
from longterm.portfolio_state import PortfolioState
from longterm.report_builder import RecommendationEnricher, RecommendationTableBuilder
from longterm.review_status import ReviewStatusBuilder
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


@dataclass(frozen=True)
class NextAction:
    priority: int
    category: str
    symbol: str
    action: str
    reason: str


class NextActionsPlanner:
    """Create a concise list of research, review, and dry-run trade priorities."""

    def __init__(
        self,
        *,
        enricher: RecommendationEnricher | None = None,
        review_status_by_symbol: dict[str, dict] | None = None,
        paper_preview_status_by_decision: dict[str, dict] | None = None,
        paper_preview_status_by_symbol: dict[str, dict] | None = None,
        paper_execution_status_by_decision: dict[str, dict] | None = None,
        paper_execution_status_by_symbol: dict[str, dict] | None = None,
        buy_promotion_reviewer: BuyPromotionReviewer | None = None,
    ):
        self.enricher = enricher
        self.review_status_by_symbol = review_status_by_symbol or {}
        self.paper_preview_status_by_decision = paper_preview_status_by_decision or {}
        self.paper_preview_status_by_symbol = paper_preview_status_by_symbol or {}
        self.paper_execution_status_by_decision = paper_execution_status_by_decision or {}
        self.paper_execution_status_by_symbol = paper_execution_status_by_symbol or {}
        self.buy_promotion_reviewer = buy_promotion_reviewer or BuyPromotionReviewer()

    def plan(
        self,
        journal: LongTermDecisionJournal,
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        benchmark_guard_result: BenchmarkGuardResult | None = None,
        limit: int = 10,
    ) -> list[NextAction]:
        recommendations = RecommendationTableBuilder(
            journal,
            enricher=self.enricher,
            review_status_by_symbol=self.review_status_by_symbol,
            paper_preview_status_by_decision=self.paper_preview_status_by_decision,
            paper_preview_status_by_symbol=self.paper_preview_status_by_symbol,
            paper_execution_status_by_decision=self.paper_execution_status_by_decision,
            paper_execution_status_by_symbol=self.paper_execution_status_by_symbol,
        ).build(limit=limit)
        actions: list[NextAction] = []
        promotion_reviews = {
            str(row.get("decision_id") or ""): self.buy_promotion_reviewer.evaluate_decision_row(
                row,
                packet=_load_packet(row),
                profile=profile,
                portfolio_state=portfolio_state,
            )
            for row in recommendations
        }

        for row in recommendations:
            symbol = row["symbol"]
            promotion_review = promotion_reviews.get(str(row.get("decision_id") or ""))
            packet = create_research_packet_from_idea({"symbol": symbol}, profile=profile)
            # Pass staged entry size to ActionPlanner when available (Priority 2)
            staged_size = None
            if promotion_review and promotion_review.staged_entry_label == "starter_position":
                staged_size = promotion_review.staged_entry_size_pct

            category_already_applied = False
            if promotion_review and getattr(promotion_review, "category_adjustment_applied", False):
                category_already_applied = True

            planned = ActionPlanner().plan(
                packet,
                profile=profile,
                portfolio_state=portfolio_state,
                decision={
                    "recommendation": row.get("recommendation"),
                    "confidence": row.get("confidence"),
                    "suggested_size_pct": row.get("suggested_size_pct"),
                },
                recommended_size_pct=staged_size,
                category_adjustment_already_applied=category_already_applied,
            )
            if portfolio_state.holding_value(symbol) <= 0 and planned.order_intent == "BUY":
                if promotion_review and promotion_review.promotion_decision != "ACTIONABLE_BUY":
                    actions.append(
                        NextAction(
                            priority=len(actions) + 1,
                            category=_promotion_category(promotion_review),
                            symbol=symbol,
                            action=_promotion_action(promotion_review),
                            reason=_promotion_reason(promotion_review),
                        )
                    )
                    continue
                reason = planned.reason
                if row.get("review_due"):
                    reason += " Review due before committing new capital."
                if benchmark_guard_result and benchmark_guard_result.should_pause_new_buys:
                    actions.append(
                        NextAction(
                            priority=len(actions) + 1,
                            category="paused_buy_candidate",
                            symbol=symbol,
                            action="PAUSED",
                            reason=benchmark_guard_result.reason,
                        )
                    )
                    continue
                if planned.capital_needed_alert:
                    actions.append(
                        NextAction(
                            priority=len(actions) + 1,
                            category="capital_needed",
                            symbol=symbol,
                            action="ALERT",
                            reason=(
                                f"Planned buy needs ${planned.cash_shortfall:,.2f} additional "
                                "active-sleeve cash."
                            ),
                        )
                    )
                    continue
                preview_action = _paper_preview_action(row)
                if preview_action:
                    actions.append(
                        NextAction(
                            priority=len(actions) + 1,
                            category=preview_action["category"],
                            symbol=symbol,
                            action=preview_action["action"],
                            reason=preview_action["reason"],
                        )
                    )
                    continue
                actions.append(
                    NextAction(
                        priority=len(actions) + 1,
                        category="buy_candidate",
                        symbol=symbol,
                        action=planned.action,
                        reason=reason,
                    )
                )
            elif portfolio_state.holding_value(symbol) > 0:
                reason = row.get("reason") or "Held symbol remains on recommendation table."
                thesis_state = str(row.get("thesis_state") or "").lower()
                category = "review_holding"
                quote_review = _mr_market_review_for_symbol(portfolio_state, symbol)
                if thesis_state in {"broken", "weakening"}:
                    category = "urgent_review_holding"
                    reason += f" Thesis state is {thesis_state}."
                elif quote_review and quote_review.review_due:
                    category = quote_review.category
                    reason = quote_review.reason
                elif row.get("review_due"):
                    reason += " Review due."
                actions.append(
                    NextAction(
                        priority=len(actions) + 1,
                        category=_paper_execution_category(row) or category,
                        symbol=symbol,
                        action="REVIEW",
                        reason=_paper_execution_reason(row) or reason,
                    )
                )

        return _prioritize_actions(actions)


def build_next_actions_markdown(
    journal: LongTermDecisionJournal,
    *,
    profile: PortfolioProfile,
    portfolio_state: PortfolioState,
    benchmark_guard: BenchmarkGuard | None = None,
    review_status_today: date | None = None,
    last_review_dates_by_symbol: Mapping[str, date] | None = None,
    review_status_by_symbol: dict[str, dict] | None = None,
    evidence_by_symbol: Mapping[str, list[str]] | None = None,
    evidence_file: str | Path | None = None,
    deferred_research_queue: list[Mapping[str, Any]] | None = None,
    paper_preview_status_by_decision: dict[str, dict] | None = None,
    paper_preview_status_by_symbol: dict[str, dict] | None = None,
    paper_execution_status_by_decision: dict[str, dict] | None = None,
    paper_execution_status_by_symbol: dict[str, dict] | None = None,
    paper_execution_eligibility: Mapping[str, Any] | None = None,
    account_action_plan: Mapping[str, Any] | None = None,
    limit: int = 10,
) -> str:
    guard = benchmark_guard or BenchmarkGuard()
    guard_result = guard.evaluate(journal.summarize_benchmark_performance())
    if evidence_file:
        evidence_by_symbol = load_evidence_by_symbol(
            evidence_file,
            protected_symbols=portfolio_state.protected_symbols or profile.protected_symbols,
        )
    if review_status_by_symbol is None:
        review_status_by_symbol = ReviewStatusBuilder(
            journal,
            today=review_status_today,
            last_review_dates_by_symbol=last_review_dates_by_symbol,
            evidence_by_symbol=evidence_by_symbol,
        ).build(limit=limit)
    actions = NextActionsPlanner(
        review_status_by_symbol=review_status_by_symbol,
        paper_preview_status_by_decision=paper_preview_status_by_decision,
        paper_preview_status_by_symbol=paper_preview_status_by_symbol,
        paper_execution_status_by_decision=paper_execution_status_by_decision,
        paper_execution_status_by_symbol=paper_execution_status_by_symbol,
    ).plan(
        journal,
        profile=profile,
        portfolio_state=portfolio_state,
        benchmark_guard_result=guard_result,
        limit=limit,
    )
    actions.extend(_paper_execution_eligibility_actions(paper_execution_eligibility))
    actions = _prioritize_actions(actions)

    # Priority 2 Polish: Separate staged entry recommendations for prominent display
    staged_entry_actions = [a for a in actions if a.category == "open_starter_position"]
    regular_actions = [a for a in actions if a.category != "open_starter_position"]

    lines = [
        "# Long-Term Next Actions",
        "",
        f"Benchmark gate: {guard_result.reason}",
        "",
        "## Category Summary",
        "",
        "| Category | Count |",
        "|---|---:|",
        *_category_summary_lines(actions),
        "",
    ]

    # Dedicated Staged Entry section (high visibility for operator)
    if staged_entry_actions:
        lines.append("## Staged Entry Recommendations")
        lines.append("")
        lines.append("**These positions should be opened at reduced starter size due to developing margin of safety.**")
        lines.append("")
        lines.append("| Symbol | Action | Details |")
        lines.append("|---|---|---|")
        for action in staged_entry_actions:
            lines.append(f"| **{action.symbol}** | **{action.action}** | {action.reason} |")
        lines.append("")

    # Main Actions table (excluding staged entries to avoid duplication)
    lines.extend([
        "## Actions",
        "",
        "| Priority | Category | Symbol | Action | Reason |",
        "|---:|---|---|---|---|",
    ])
    for action in regular_actions:
        cat_display = action.category
        # Polish: Show Lynch company category in the Category column when available in the reason
        if action.category in ("buy_promotion_review", "open_starter_position") and "[" in action.reason and "]" in action.reason:
            # Try to extract category from the end of the reason
            import re
            match = re.search(r'\[([a-z_]+)\]', action.reason)
            if match:
                cat_display = f"{action.category} [{match.group(1)}]"

        lines.append(
            f"| {action.priority} | {cat_display} | {action.symbol} | {action.action} | {action.reason} |"
        )

    if account_action_plan:
        lines.extend(_account_action_plan_lines(account_action_plan))
    if deferred_research_queue:
        lines.extend(_deferred_research_queue_lines(deferred_research_queue))
    return "\n".join(lines) + "\n"


def load_evidence_by_symbol(
    path: str | Path,
    *,
    protected_symbols: list[str] | None = None,
) -> dict[str, list[str]]:
    """Load symbol evidence from JSON or CSV for review-status calculations."""
    path = Path(path)
    protected = {symbol.upper() for symbol in (protected_symbols or [])}
    if path.suffix.lower() == ".csv":
        return _load_evidence_csv(path, protected_symbols=protected)
    return _load_evidence_json(path, protected_symbols=protected)


def _deferred_research_queue_lines(deferred_research_queue: list[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Deferred Research Queue",
        "",
        "| Symbol | Missing Fields | Provenance | Next Step |",
        "|---|---|---|---|",
    ]
    command_lines: list[str] = []
    for item in deferred_research_queue:
        symbol = _markdown_cell(str(item.get("symbol") or "UNKNOWN"))
        missing_fields = _markdown_cell(_format_missing_fields(item.get("missing_fields")))
        provenance = _markdown_cell(str(item.get("provenance_bucket") or "unknown"))
        next_step = _markdown_cell(str(item.get("suggested_next_step") or "enrich_candidate_before_research"))
        lines.append(f"| {symbol} | {missing_fields} | {provenance} | {next_step} |")
        suggested_command = str(item.get("suggested_enrichment_command") or "").strip()
        if suggested_command:
            command_lines.append(f"- {symbol}: `{suggested_command}`")
    if command_lines:
        lines.extend(["", "Suggested enrichment commands:", *command_lines])
    return lines


def _account_action_plan_lines(account_action_plan: Mapping[str, Any]) -> list[str]:
    intents = account_action_plan.get("intents") or []
    suppressed_reasons = _account_action_suppressed_reasons(account_action_plan)
    if (not isinstance(intents, list) or not intents) and not suppressed_reasons:
        return []
    lines: list[str] = []
    if isinstance(intents, list) and intents:
        lines.extend(
            [
                "",
                "## Account Action Plan Intents",
                "",
                "| Symbol | Intent | Order | Trade Value | Allowed | Reason |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for intent in intents:
            if not isinstance(intent, Mapping):
                continue
            trade_value = _format_currency(intent.get("trade_value"))
            allowed = "yes" if bool(intent.get("allowed")) else "no"

            intent_type = str(intent.get("intent_type") or "")
            promotion = intent.get("promotion_review") or {}
            staged_label = promotion.get("staged_entry_label")
            company_cat = promotion.get("company_category") or ""

            # Priority 2 Polish: Clearly mark staged starter entries in the action plan
            if staged_label == "starter_position":
                display_intent = f"{intent_type} (Starter)"
            else:
                display_intent = intent_type

            if company_cat:
                display_intent += f" [{company_cat}]"

            lines.append(
                "| "
                f"{_markdown_cell(str(intent.get('symbol') or ''))} | "
                f"{_markdown_cell(display_intent)} | "
                f"{_markdown_cell(str(intent.get('order_intent') or ''))} | "
                f"{trade_value} | "
                f"{allowed} | "
                f"{_markdown_cell(str(intent.get('reason') or ''))} |"
            )
    if suppressed_reasons:
        lines.extend(
            [
                "",
                "## Account Action Plan Suppressions",
                "",
                "| Suppression | Code |",
                "|---|---|",
            ]
        )
        for reason in suppressed_reasons:
            lines.append(f"| {_markdown_cell(_human_label(reason))} | {_markdown_cell(reason)} |")
    return lines


def _account_action_suppressed_reasons(account_action_plan: Mapping[str, Any]) -> list[str]:
    return [
        str(reason).strip()
        for reason in (account_action_plan.get("suppressed_reasons") or [])
        if str(reason).strip()
    ]


def _human_label(value: str) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").title()


def _format_currency(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _format_missing_fields(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(field) for field in value)
    return str(value or "")


def _load_packet(row: Mapping[str, Any]) -> Mapping[str, Any]:
    packet_json = row.get("packet_json")
    if isinstance(packet_json, str) and packet_json.strip():
        try:
            payload = json.loads(packet_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, Mapping):
            return payload
    return {
        "symbol": row.get("symbol") or "",
        "company_name": row.get("company_name") or "",
    }


def _promotion_category(review: BuyPromotionReview) -> str:
    if review.promotion_decision == "WATCHLIST_PENDING_EVIDENCE":
        return "buy_promotion_pending_evidence"
    if review.promotion_decision == "WATCHLIST_PENDING_CONFIRMATION":
        return "buy_promotion_pending_confirmation"
    if review.promotion_decision == "REVIEW_EXISTING_POSITION":
        return "review_holding"
    if review.promotion_decision == "BLOCKED":
        return "buy_promotion_blocked"

    # Priority 2 improvement: Make starter positions clearly trackable
    if review.staged_entry_label == "starter_position":
        return "open_starter_position"
    if review.staged_entry_label == "confirm_before_entry":
        return "confirm_before_entry"

    return "buy_promotion_review"


def _promotion_action(review: BuyPromotionReview) -> str:
    if review.promotion_decision == "WATCHLIST_PENDING_EVIDENCE":
        return "ENRICH"
    if review.promotion_decision == "WATCHLIST_PENDING_CONFIRMATION":
        return "CONFIRM"
    if review.promotion_decision == "BLOCKED":
        return "BLOCKED"

    # Priority 2: Clearer actions for staged entry situations
    if review.staged_entry_label == "starter_position":
        return "OPEN STARTER"
    if review.staged_entry_label == "confirm_before_entry":
        return "CONFIRM"

    return "REVIEW"


def _promotion_reason(review: BuyPromotionReview) -> str:
    details = review.blockers or review.followups or review.reasons
    suffix = "; ".join(details)

    base = f"Buy promotion review: {review.promotion_decision}."
    if suffix:
        base += f" {suffix}"

    # Priority 2: Clear, trackable recommendation for staged entry
    if review.staged_entry_label == "starter_position" and review.staged_entry_size_pct > 0:
        msg = (
            f"Open **starter position** at {review.staged_entry_size_pct}% of portfolio "
            f"(full size would be {review.suggested_size_pct}%). "
            f"Margin of safety still developing."
        )
        if review.company_category:
            msg += f"  | Category: {review.company_category}"
        return msg

    if review.staged_entry_label == "confirm_before_entry":
        base += " High permanent loss risk — confirm before any entry."

    if review.company_category:
        base += f" Category: {review.company_category}."

    return base


def _mr_market_review_for_symbol(portfolio_state: PortfolioState, symbol: str):
    normalized = symbol.upper()
    for holding in portfolio_state.holdings:
        if holding.symbol != normalized:
            continue
        if holding.original_purchase_total_cost <= 0 and holding.avg_entry_price <= 0:
            return None
        return mr_market_review_trigger(holding)
    return None


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _prioritize_actions(actions: list[NextAction]) -> list[NextAction]:
    category_order = {
        "urgent_review_holding": 0,
        "paper_preview_blocked": 1,
        "paper_execution_preview_stale": 1,
        "paper_execution_preview_blocked": 1,
        "paper_execution_blocked": 1,
        "paper_preview_ready": 2,
        "paper_execution_eligible": 2,
        "paper_execution_filled": 2,
        "paper_execution_rejected": 1,
        "mr_market_drawdown_review": 1,
        "mr_market_rally_review": 2,
        "paper_execution_submitted": 2,
        "paused_buy_candidate": 1,
        "buy_promotion_blocked": 1,
        "buy_promotion_pending_evidence": 2,
        "buy_promotion_pending_confirmation": 2,
        "capital_needed": 2,
        "buy_candidate": 3,
        "review_holding": 4,
    }
    ordered = sorted(
        actions,
        key=lambda action: (category_order.get(action.category, 99), action.priority),
    )
    return [
        NextAction(
            priority=index,
            category=action.category,
            symbol=action.symbol,
            action=action.action,
            reason=action.reason,
        )
        for index, action in enumerate(ordered, start=1)
    ]


def _category_summary_lines(actions: list[NextAction]) -> list[str]:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.category] = counts.get(action.category, 0) + 1
    return [f"| {category} | {count} |" for category, count in counts.items()]


def _paper_execution_eligibility_actions(
    eligibility: Mapping[str, Any] | None,
) -> list[NextAction]:
    if not eligibility:
        return []
    actions = []
    for item in eligibility.get("items") or []:
        symbol = str(item.get("symbol") or "").upper()
        status = str(item.get("status") or "")
        action = str(item.get("action") or "")
        reasons = "; ".join(str(reason) for reason in (item.get("blocked_reasons") or []))
        category = "paper_execution_eligible" if item.get("eligible") else "paper_execution_blocked"
        if status == "preview_stale":
            category = "paper_execution_preview_stale"
        elif status == "preview_blocked":
            category = "paper_execution_preview_blocked"
        actions.append(
            NextAction(
                priority=len(actions) + 1,
                category=category,
                symbol=symbol,
                action=action,
                reason=reasons or status,
            )
        )
    return actions


def _paper_preview_action(row: Mapping[str, Any]) -> dict[str, str]:
    status = str(row.get("paper_preview_status") or "")
    if not status:
        return {}
    preview_id = str(row.get("paper_preview_id") or "")
    log_id = str(row.get("paper_preview_log_id") or "")
    if status == "blocked":
        reasons = ", ".join(str(item) for item in (row.get("paper_preview_blocked_reasons") or []))
        return {
            "category": "paper_preview_blocked",
            "action": "NEEDS_ATTENTION",
            "reason": f"Paper preview {preview_id} is blocked. {reasons}".strip(),
        }
    if status == "ready":
        return {
            "category": "paper_preview_ready",
            "action": "BUY_PREVIEW_READY",
            "reason": f"Paper preview {preview_id} is ready for operator review. Preview log {log_id}.",
        }
    return {}


def _paper_execution_category(row: Mapping[str, Any]) -> str:
    status = str(row.get("paper_execution_status") or row.get("paper_execution_latest_status") or "")
    if not status:
        return ""
    if status in {"filled", "partially_filled"}:
        return "paper_execution_filled"
    if status in {"rejected", "status_refresh_error"}:
        return "paper_execution_rejected"
    return "paper_execution_submitted"


def _paper_execution_reason(row: Mapping[str, Any]) -> str:
    status = str(row.get("paper_execution_status") or row.get("paper_execution_latest_status") or "")
    if not status:
        return ""
    broker_order = str(row.get("paper_execution_broker_order_id") or "")
    return f"Paper execution status is {status}. Broker order {broker_order}."


def _load_evidence_json(path: Path, *, protected_symbols: set[str]) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Evidence JSON must contain a symbol mapping.")
    evidence: dict[str, list[str]] = {}
    for symbol, value in payload.items():
        normalized = str(symbol).upper()
        action_hint = ""
        if isinstance(value, dict):
            action_hint = str(value.get("action_hint") or "")
            raw_evidence = value.get("evidence") or []
        else:
            raw_evidence = value
        _validate_protected_evidence(normalized, action_hint, protected_symbols)
        evidence[normalized] = _normalize_evidence_list(raw_evidence)
    return evidence


def _load_evidence_csv(path: Path, *, protected_symbols: set[str]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            action_hint = str(row.get("action_hint") or "")
            _validate_protected_evidence(symbol, action_hint, protected_symbols)
            text = str(row.get("evidence") or row.get("note") or "").strip()
            if text:
                evidence.setdefault(symbol, []).append(text)
    return evidence


def _normalize_evidence_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def _validate_protected_evidence(
    symbol: str,
    action_hint: str,
    protected_symbols: set[str],
) -> None:
    if symbol not in protected_symbols:
        return
    if action_hint.lower() in {"sell", "trim", "reduce", "rebalance"}:
        raise ValueError(f"Evidence file cannot suggest {action_hint} for protected symbol {symbol}.")
