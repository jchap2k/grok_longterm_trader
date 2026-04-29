"""Prioritized next-action planning for the long-term trader."""

from __future__ import annotations

from dataclasses import dataclass

from longterm.action_planner import ActionPlanner
from longterm.benchmark_guard import BenchmarkGuard, BenchmarkGuardResult
from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from longterm.report_builder import RecommendationEnricher, RecommendationTableBuilder
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
    ):
        self.enricher = enricher
        self.review_status_by_symbol = review_status_by_symbol or {}

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
                if row.get("review_due"):
                    reason += " Review due."
                actions.append(
                    NextAction(
                        priority=len(actions) + 1,
                        category="review_holding",
                        symbol=symbol,
                        action="REVIEW",
                        reason=reason,
                    )
                )

        return actions


def build_next_actions_markdown(
    journal: LongTermDecisionJournal,
    *,
    profile: PortfolioProfile,
    portfolio_state: PortfolioState,
    benchmark_guard: BenchmarkGuard | None = None,
    limit: int = 10,
) -> str:
    guard = benchmark_guard or BenchmarkGuard()
    guard_result = guard.evaluate(journal.summarize_benchmark_performance())
    actions = NextActionsPlanner().plan(
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
        "| Priority | Category | Symbol | Action | Reason |",
        "|---:|---|---|---|---|",
    ]
    for action in actions:
        lines.append(
            f"| {action.priority} | {action.category} | {action.symbol} | {action.action} | {action.reason} |"
        )
    return "\n".join(lines) + "\n"
