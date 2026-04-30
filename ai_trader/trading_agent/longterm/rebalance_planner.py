"""Dry-run rebalance proposal helpers for the long-term active sleeve."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from longterm.benchmark_guard import BenchmarkGuardResult
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


@dataclass(frozen=True)
class RebalanceProposal:
    should_rebalance: bool
    fund_from_symbol: str
    target_symbol: str
    proposed_sell_value: float
    reason: str
    source_current_value: float = 0.0
    source_target_value: float = 0.0
    source_rank: int = 0
    target_rank: int = 0
    rank_gap: int = 0
    target_suggested_size_pct: float = 0.0
    source_decision_id: str = ""
    target_decision_id: str = ""
    benchmark_guard_reason: str = ""
    source_review_due: bool | None = None
    target_review_due: bool | None = None
    source_thesis_state: str = ""
    target_thesis_state: str = ""
    source_review_adjustment: int = 0
    source_rebalance_score: int = 0
    rebalance_score_gap: int = 0


class RebalancePlanner:
    """Propose funding better-ranked ideas from weaker non-protected holdings."""

    def propose(
        self,
        recommendations: list[Mapping[str, Any]],
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        benchmark_guard_result: BenchmarkGuardResult | None = None,
        review_status_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
        min_rank_gap: int = 3,
    ) -> RebalanceProposal:
        if not recommendations:
            return RebalanceProposal(False, "", "", 0.0, "No recommendations available.")

        by_symbol = {row["symbol"]: row for row in recommendations}
        best = recommendations[0]
        best_symbol = str(best.get("symbol", "")).upper()
        best_rank = int(best.get("rank") or 999)
        target_suggested_size_pct = float(best.get("suggested_size_pct") or 0.0)
        target_decision_id = str(best.get("decision_id") or "")
        benchmark_guard_reason = (
            benchmark_guard_result.reason if benchmark_guard_result else "Benchmark guard was not evaluated."
        )
        review_status_by_symbol = review_status_by_symbol or {}
        target_review_status = _review_status_for(review_status_by_symbol, best_symbol)
        if portfolio_state.holding_value(best_symbol) > 0:
            return RebalanceProposal(
                False,
                "",
                best_symbol,
                0.0,
                "Top idea is already held.",
                target_rank=best_rank,
                target_suggested_size_pct=target_suggested_size_pct,
                target_decision_id=target_decision_id,
                benchmark_guard_reason=benchmark_guard_reason,
                target_review_due=_review_due(target_review_status),
                target_thesis_state=_thesis_state(target_review_status),
            )
        if benchmark_guard_result and benchmark_guard_result.should_pause_new_buys:
            return RebalanceProposal(
                False,
                "",
                best_symbol,
                0.0,
                benchmark_guard_result.reason,
                target_rank=best_rank,
                target_suggested_size_pct=target_suggested_size_pct,
                target_decision_id=target_decision_id,
                benchmark_guard_reason=benchmark_guard_reason,
                target_review_due=_review_due(target_review_status),
                target_thesis_state=_thesis_state(target_review_status),
            )

        protected_symbols = set(profile.protected_symbols) | set(portfolio_state.protected_symbols)
        weakest_symbol = ""
        weakest_rank = -1
        weakest_value = 0.0
        weakest_target_value = 0.0
        weakest_decision_id = ""
        weakest_review_due: bool | None = None
        weakest_thesis_state = ""
        weakest_review_adjustment = 0
        weakest_rebalance_score = -1
        for holding in portfolio_state.holdings:
            if holding.symbol in protected_symbols:
                continue
            row = by_symbol.get(holding.symbol)
            rank = int(row.get("rank") or 999) if row else 999
            review_status = _review_status_for(review_status_by_symbol, holding.symbol)
            review_adjustment = _review_risk_adjustment(review_status)
            rebalance_score = rank + review_adjustment
            if rebalance_score > weakest_rebalance_score:
                weakest_symbol = holding.symbol
                weakest_rank = rank
                weakest_value = holding.market_value
                weakest_decision_id = str(row.get("decision_id") or "") if row else ""
                weakest_review_due = _review_due(review_status)
                weakest_thesis_state = _thesis_state(review_status)
                weakest_review_adjustment = review_adjustment
                weakest_rebalance_score = rebalance_score
                weakest_target_value = (
                    profile.tradable_capital * (float(row.get("suggested_size_pct") or 0.0) / 100.0)
                    if row
                    else 0.0
                )

        rank_gap = max(0, weakest_rank - best_rank)
        rebalance_score_gap = max(0, weakest_rebalance_score - best_rank)
        if not weakest_symbol or rebalance_score_gap < min_rank_gap:
            return RebalanceProposal(
                False,
                weakest_symbol,
                best_symbol,
                0.0,
                "No material rank upgrade found.",
                source_current_value=weakest_value,
                source_target_value=weakest_target_value,
                source_rank=weakest_rank if weakest_symbol else 0,
                target_rank=best_rank,
                rank_gap=rank_gap,
                target_suggested_size_pct=target_suggested_size_pct,
                source_decision_id=weakest_decision_id,
                target_decision_id=target_decision_id,
                benchmark_guard_reason=benchmark_guard_reason,
                source_review_due=weakest_review_due,
                target_review_due=_review_due(target_review_status),
                source_thesis_state=weakest_thesis_state,
                target_thesis_state=_thesis_state(target_review_status),
                source_review_adjustment=weakest_review_adjustment,
                source_rebalance_score=max(0, weakest_rebalance_score),
                rebalance_score_gap=rebalance_score_gap,
            )

        proposed_sell = round(max(0.0, weakest_value - weakest_target_value), 2)
        reason = f"{best_symbol} is materially higher ranked than {weakest_symbol}."
        if weakest_review_adjustment > 0:
            reason += " Source holding has additional review risk."
        return RebalanceProposal(
            should_rebalance=proposed_sell > 0,
            fund_from_symbol=weakest_symbol,
            target_symbol=best_symbol,
            proposed_sell_value=proposed_sell,
            reason=reason,
            source_current_value=weakest_value,
            source_target_value=weakest_target_value,
            source_rank=weakest_rank,
            target_rank=best_rank,
            rank_gap=rank_gap,
            target_suggested_size_pct=target_suggested_size_pct,
            source_decision_id=weakest_decision_id,
            target_decision_id=target_decision_id,
            benchmark_guard_reason=benchmark_guard_reason,
            source_review_due=weakest_review_due,
            target_review_due=_review_due(target_review_status),
            source_thesis_state=weakest_thesis_state,
            target_thesis_state=_thesis_state(target_review_status),
            source_review_adjustment=weakest_review_adjustment,
            source_rebalance_score=max(0, weakest_rebalance_score),
            rebalance_score_gap=rebalance_score_gap,
        )


def _review_status_for(
    statuses: Mapping[str, Mapping[str, Any]],
    symbol: str,
) -> Mapping[str, Any]:
    return statuses.get(symbol.upper()) or statuses.get(symbol.lower()) or {}


def _review_due(status: Mapping[str, Any]) -> bool | None:
    if "review_due" not in status:
        return None
    return bool(status.get("review_due"))


def _thesis_state(status: Mapping[str, Any]) -> str:
    return str(status.get("thesis_state") or "")


def _review_risk_adjustment(status: Mapping[str, Any]) -> int:
    adjustment = 1 if _review_due(status) else 0
    thesis_state = _thesis_state(status).lower()
    if thesis_state in {"broken", "invalidated"}:
        adjustment += 4
    elif thesis_state in {"stale", "deteriorating", "weakening", "at_risk"}:
        adjustment += 2
    return adjustment
