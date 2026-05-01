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
from longterm.decision_journal import LongTermDecisionJournal
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
    ):
        self.enricher = enricher
        self.review_status_by_symbol = review_status_by_symbol or {}
        self.paper_preview_status_by_decision = paper_preview_status_by_decision or {}
        self.paper_preview_status_by_symbol = paper_preview_status_by_symbol or {}

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
        ).build(limit=limit)
        actions: list[NextAction] = []

        for row in recommendations:
            symbol = row["symbol"]
            packet = create_research_packet_from_idea({"symbol": symbol}, profile=profile)
            planned = ActionPlanner().plan(
                packet,
                profile=profile,
                portfolio_state=portfolio_state,
                decision={
                    "recommendation": row.get("recommendation"),
                    "confidence": row.get("confidence"),
                    "suggested_size_pct": row.get("suggested_size_pct"),
                },
            )
            if portfolio_state.holding_value(symbol) <= 0 and planned.order_intent == "BUY":
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
                if thesis_state in {"broken", "weakening"}:
                    category = "urgent_review_holding"
                    reason += f" Thesis state is {thesis_state}."
                elif row.get("review_due"):
                    reason += " Review due."
                actions.append(
                    NextAction(
                        priority=len(actions) + 1,
                        category=category,
                        symbol=symbol,
                        action="REVIEW",
                        reason=reason,
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
    ).plan(
        journal,
        profile=profile,
        portfolio_state=portfolio_state,
        benchmark_guard_result=guard_result,
        limit=limit,
    )

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
        "## Actions",
        "",
        "| Priority | Category | Symbol | Action | Reason |",
        "|---:|---|---|---|---|",
    ]
    for action in actions:
        lines.append(
            f"| {action.priority} | {action.category} | {action.symbol} | {action.action} | {action.reason} |"
        )
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


def _format_missing_fields(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(field) for field in value)
    return str(value or "")


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _prioritize_actions(actions: list[NextAction]) -> list[NextAction]:
    category_order = {
        "urgent_review_holding": 0,
        "paper_preview_blocked": 1,
        "paper_preview_ready": 2,
        "paused_buy_candidate": 1,
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
