"""Structured dry-run account action plans for future long-term autonomy."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Callable

from longterm.action_planner import ActionPlanner
from longterm.benchmark_guard import BenchmarkGuard
from longterm.capital_alert import _capital_request_suppression_reason
from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from longterm.rebalance_planner import RebalancePlanner
from longterm.report_builder import RecommendationTableBuilder
from longterm.review_status import ReviewStatusBuilder
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


@dataclass(frozen=True)
class AccountActionIntent:
    symbol: str
    intent_type: str
    order_intent: str
    trade_value: float
    target_value: float
    allowed: bool
    reason: str
    source_symbol: str = ""
    decision_id: str = ""
    trade_id: str = ""
    lesson_id: str = ""


@dataclass(frozen=True)
class AccountActionPlan:
    schema_version: int
    plan_id: str
    mode: str
    generated_at: str
    status: str
    benchmark_gate_reason: str
    intents: list[AccountActionIntent] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class AccountActionPlanBuilder:
    """Build a machine-readable dry-run action contract from current research state."""

    def __init__(
        self,
        *,
        benchmark_guard: BenchmarkGuard | None = None,
        rebalance_planner: RebalancePlanner | None = None,
        generated_at_func: Callable[[], str] | None = None,
        plan_id_func: Callable[[], str] | None = None,
    ):
        self.benchmark_guard = benchmark_guard or BenchmarkGuard()
        self.rebalance_planner = rebalance_planner or RebalancePlanner()
        self.generated_at_func = generated_at_func or _now_iso
        self.plan_id_func = plan_id_func or (lambda: str(uuid.uuid4()))

    def build(
        self,
        journal: LongTermDecisionJournal,
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        limit: int = 10,
    ) -> AccountActionPlan:
        review_status_by_symbol = (
            ReviewStatusBuilder(journal).build(limit=limit)
            if hasattr(journal, "list_review_candidates")
            else {}
        )
        recommendations = RecommendationTableBuilder(
            journal,
            review_status_by_symbol=review_status_by_symbol,
        ).build(limit=limit)
        guard_result = self.benchmark_guard.evaluate(journal.summarize_benchmark_performance())
        suppression_reason = (
            _capital_request_suppression_reason(journal, portfolio_state)
            if hasattr(journal, "list_recent_decisions")
            else ""
        )
        intents: list[AccountActionIntent] = []
        blocked_reasons: list[str] = []

        for row in recommendations:
            symbol = str(row.get("symbol") or "").upper()
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

            if planned.action == "PROTECTED_HOLD":
                intents.append(_blocked_intent(row, planned.reason))
                blocked_reasons.append(planned.reason)
                continue

            if portfolio_state.holding_value(symbol) > 0:
                reason = row.get("reason") or "Held symbol remains on recommendation table."
                if row.get("review_due"):
                    reason += " Review due."
                intents.append(
                    AccountActionIntent(
                        symbol=symbol,
                        intent_type="REVIEW",
                        order_intent="NONE",
                        trade_value=0.0,
                        target_value=portfolio_state.holding_value(symbol),
                        allowed=True,
                        reason=reason,
                        decision_id=str(row.get("decision_id") or ""),
                    )
                )
                continue

            if planned.order_intent == "BUY":
                if guard_result.should_pause_new_buys:
                    intents.append(_blocked_intent(row, guard_result.reason))
                    blocked_reasons.append(guard_result.reason)
                    continue
                if planned.capital_needed_alert:
                    if suppression_reason:
                        intents.append(_blocked_intent(row, suppression_reason))
                        blocked_reasons.append(suppression_reason)
                    else:
                        intents.append(
                            AccountActionIntent(
                                symbol=symbol,
                                intent_type="CAPITAL_NEEDED",
                                order_intent="NONE",
                                trade_value=planned.trade_value,
                                target_value=planned.target_value,
                                allowed=False,
                                reason=(
                                    f"Planned buy needs ${planned.cash_shortfall:,.2f} "
                                    "additional active-sleeve cash."
                                ),
                                decision_id=str(row.get("decision_id") or ""),
                            )
                        )
                        blocked_reasons.append("Capital shortfall.")
                    continue
                intents.append(
                    AccountActionIntent(
                        symbol=symbol,
                        intent_type="BUY",
                        order_intent="BUY",
                        trade_value=planned.trade_value,
                        target_value=planned.target_value,
                        allowed=planned.allowed,
                        reason=planned.reason,
                        decision_id=str(row.get("decision_id") or ""),
                    )
                )

        proposal = self.rebalance_planner.propose(
            recommendations,
            profile=profile,
            portfolio_state=portfolio_state,
            benchmark_guard_result=guard_result,
            review_status_by_symbol=review_status_by_symbol,
        )
        if proposal.should_rebalance:
            intents.append(
                AccountActionIntent(
                    symbol=proposal.target_symbol,
                    intent_type="REBALANCE",
                    order_intent="SELL_TO_FUND_BUY",
                    trade_value=proposal.proposed_sell_value,
                    target_value=0.0,
                    allowed=True,
                    reason=proposal.reason,
                    source_symbol=proposal.fund_from_symbol,
                    decision_id=proposal.target_decision_id,
                )
            )

        status = "ready" if intents and not blocked_reasons else "blocked" if blocked_reasons else "no_action"
        return AccountActionPlan(
            schema_version=1,
            plan_id=self.plan_id_func(),
            mode="dry_run",
            generated_at=self.generated_at_func(),
            status=status,
            benchmark_gate_reason=guard_result.reason,
            intents=intents,
            blocked_reasons=blocked_reasons,
        )


def _blocked_intent(row: dict, reason: str) -> AccountActionIntent:
    return AccountActionIntent(
        symbol=str(row.get("symbol") or "").upper(),
        intent_type="BLOCKED",
        order_intent="NONE",
        trade_value=0.0,
        target_value=0.0,
        allowed=False,
        reason=reason,
        decision_id=str(row.get("decision_id") or ""),
    )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
