"""Structured dry-run account action plans for future long-term autonomy."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from longterm.action_planner import ActionPlanner
from longterm.benchmark_guard import BenchmarkGuard
from longterm.buy_promotion import BuyPromotionReview, BuyPromotionReviewer
from longterm.capital_alert import _capital_request_suppression_reason
from longterm.decision_journal import LongTermDecisionJournal
from longterm.idle_cash_policy import IdleCashDeploymentPolicy, MarketRegimeSnapshot
from longterm.portfolio_state import PortfolioState
from longterm.rebalance_planner import RebalancePlanner
from longterm.report_builder import RecommendationTableBuilder
from longterm.review_status import ReviewStatusBuilder
from longterm.risk_review import RiskReviewBuilder
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
    risk_review: dict = field(default_factory=dict)
    promotion_review: dict = field(default_factory=dict)


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
        risk_review_builder: RiskReviewBuilder | None = None,
        idle_cash_policy: IdleCashDeploymentPolicy | None = None,
        market_regime: MarketRegimeSnapshot | None = None,
        buy_promotion_reviewer: BuyPromotionReviewer | None = None,
    ):
        self.benchmark_guard = benchmark_guard or BenchmarkGuard()
        self.rebalance_planner = rebalance_planner or RebalancePlanner()
        self.generated_at_func = generated_at_func or _now_iso
        self.plan_id_func = plan_id_func or (lambda: str(uuid.uuid4()))
        self.risk_review_builder = risk_review_builder or RiskReviewBuilder()
        self.idle_cash_policy = idle_cash_policy or IdleCashDeploymentPolicy()
        self.market_regime = market_regime
        self.buy_promotion_reviewer = buy_promotion_reviewer or BuyPromotionReviewer()

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
            symbol = str(row.get("symbol") or "").upper()
            promotion_review = promotion_reviews.get(str(row.get("decision_id") or ""))
            risk_review = self.risk_review_builder.build(
                row,
                profile=profile,
                portfolio_state=portfolio_state,
                benchmark_guard_result=guard_result,
                review_status=review_status_by_symbol.get(symbol, {}),
                intent_type=str(row.get("recommendation") or ""),
            )
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
                intents.append(
                    _blocked_intent(
                        row,
                        planned.reason,
                        risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                        promotion_review=promotion_review,
                    )
                )
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
                        risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                        promotion_review=_promotion_dict(promotion_review),
                    )
                )
                continue

            if planned.order_intent == "BUY":
                if promotion_review and promotion_review.promotion_decision != "ACTIONABLE_BUY":
                    intents.append(
                        AccountActionIntent(
                            symbol=symbol,
                            intent_type="REVIEW",
                            order_intent="NONE",
                            trade_value=0.0,
                            target_value=0.0,
                            allowed=True,
                            reason=_promotion_reason(promotion_review),
                            decision_id=str(row.get("decision_id") or ""),
                            risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                            promotion_review=promotion_review.to_dict(),
                        )
                    )
                    continue
                if not risk_review.allowed:
                    reason = "; ".join(risk_review.veto_reasons) or guard_result.reason
                    intents.append(
                        _blocked_intent(
                            row,
                            reason,
                            risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                            promotion_review=promotion_review,
                        )
                    )
                    blocked_reasons.append(reason)
                    continue
                if planned.capital_needed_alert:
                    if suppression_reason:
                        intents.append(
                            _blocked_intent(
                                row,
                                suppression_reason,
                                risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                                promotion_review=promotion_review,
                            )
                        )
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
                                risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                                promotion_review=_promotion_dict(promotion_review),
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
                        risk_review=_risk_with_promotion(risk_review.to_dict(), promotion_review),
                        promotion_review=_promotion_dict(promotion_review),
                    )
                )

        rebalance_recommendations = [
            row
            for row in recommendations
            if portfolio_state.holding_value(str(row.get("symbol") or "")) > 0
            or _is_actionable_promotion(promotion_reviews.get(str(row.get("decision_id") or "")))
        ]
        proposal = self.rebalance_planner.propose(
            rebalance_recommendations,
            profile=profile,
            portfolio_state=portfolio_state,
            benchmark_guard_result=guard_result,
            review_status_by_symbol=review_status_by_symbol,
        )
        if proposal.should_rebalance:
            rebalance_risk = self.risk_review_builder.build(
                {
                    "symbol": proposal.target_symbol,
                    "recommendation": "REBALANCE",
                    "suggested_size_pct": proposal.target_suggested_size_pct,
                },
                profile=profile,
                portfolio_state=portfolio_state,
                benchmark_guard_result=guard_result,
                review_status=review_status_by_symbol.get(proposal.target_symbol, {}),
                intent_type="REBALANCE",
            )
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
                    risk_review=rebalance_risk.to_dict(),
                )
            )

        if self.market_regime is not None:
            intents.extend(
                _idle_cash_intents(
                    profile=profile,
                    portfolio_state=portfolio_state,
                    intents=intents,
                    policy=self.idle_cash_policy,
                    market_regime=self.market_regime,
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


def _blocked_intent(
    row: dict,
    reason: str,
    *,
    risk_review: dict | None = None,
    promotion_review: BuyPromotionReview | None = None,
) -> AccountActionIntent:
    return AccountActionIntent(
        symbol=str(row.get("symbol") or "").upper(),
        intent_type="BLOCKED",
        order_intent="NONE",
        trade_value=0.0,
        target_value=0.0,
        allowed=False,
        reason=reason,
        decision_id=str(row.get("decision_id") or ""),
        risk_review=risk_review or {},
        promotion_review=_promotion_dict(promotion_review),
    )


def _load_packet(row: Mapping[str, Any]) -> Mapping[str, Any]:
    packet_json = row.get("packet_json")
    if isinstance(packet_json, str) and packet_json.strip():
        import json

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


def _promotion_dict(review: BuyPromotionReview | None) -> dict:
    return review.to_dict() if review else {}


def _risk_with_promotion(risk_review: dict, promotion_review: BuyPromotionReview | None) -> dict:
    payload = dict(risk_review or {})
    if promotion_review:
        payload["buy_promotion"] = promotion_review.to_dict()
    return payload


def _is_actionable_promotion(review: BuyPromotionReview | None) -> bool:
    return bool(review and review.promotion_decision == "ACTIONABLE_BUY")


def _promotion_reason(review: BuyPromotionReview) -> str:
    details = review.blockers or review.followups or review.reasons
    suffix = "; ".join(details)
    if suffix:
        return f"Buy promotion review: {review.promotion_decision}. {suffix}"
    return f"Buy promotion review: {review.promotion_decision}."


def _idle_cash_intents(
    *,
    profile: PortfolioProfile,
    portfolio_state: PortfolioState,
    intents: list[AccountActionIntent],
    policy: IdleCashDeploymentPolicy,
    market_regime: MarketRegimeSnapshot,
) -> list[AccountActionIntent]:
    committed_cash = sum(
        intent.trade_value
        for intent in intents
        if intent.allowed
        and intent.intent_type == "BUY"
        and intent.order_intent == "BUY"
    )
    available_cash = max(0.0, float(portfolio_state.cash or 0.0) - committed_cash)
    active_budget_remaining = available_cash
    if float(profile.tradable_capital or 0.0) > 0:
        active_budget_remaining = max(
            0.0,
            float(profile.tradable_capital)
            - float(portfolio_state.active_market_value or 0.0)
            - committed_cash,
        )
    idle_cash = round(min(available_cash, active_budget_remaining), 2)
    if idle_cash <= 0:
        return []

    results: list[AccountActionIntent] = []
    for allocation in policy.allocations(profile=profile, market_regime=market_regime):
        trade_value = round(idle_cash * float(allocation.weight), 2)
        if trade_value <= 0:
            continue
        symbol = allocation.symbol.upper()
        results.append(
            AccountActionIntent(
                symbol=symbol,
                intent_type=allocation.intent_type,
                order_intent="BUY",
                trade_value=trade_value,
                target_value=portfolio_state.holding_value(symbol) + trade_value,
                allowed=True,
                reason=(
                    f"{allocation.reason} Regime={market_regime.risk_regime}; "
                    f"idle active cash=${idle_cash:,.2f}."
                ),
                risk_review={
                    "symbol": symbol,
                    "intent_type": allocation.intent_type,
                    "allowed": True,
                    "risk_level": "low" if allocation.intent_type == "PARK_DEFENSIVE_CASH" else "medium",
                    "veto_reasons": [],
                    "warnings": [],
                    "market_regime": market_regime.risk_regime,
                },
            )
        )
    return results


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
