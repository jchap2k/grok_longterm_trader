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


class RebalancePlanner:
    """Propose funding better-ranked ideas from weaker non-protected holdings."""

    def propose(
        self,
        recommendations: list[Mapping[str, Any]],
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        benchmark_guard_result: BenchmarkGuardResult | None = None,
        min_rank_gap: int = 3,
    ) -> RebalanceProposal:
        if not recommendations:
            return RebalanceProposal(False, "", "", 0.0, "No recommendations available.")

        by_symbol = {row["symbol"]: row for row in recommendations}
        best = recommendations[0]
        best_symbol = str(best.get("symbol", "")).upper()
        if portfolio_state.holding_value(best_symbol) > 0:
            return RebalanceProposal(False, "", best_symbol, 0.0, "Top idea is already held.")
        if benchmark_guard_result and benchmark_guard_result.should_pause_new_buys:
            return RebalanceProposal(False, "", best_symbol, 0.0, benchmark_guard_result.reason)

        weakest_symbol = ""
        weakest_rank = -1
        weakest_value = 0.0
        weakest_target_value = 0.0
        for holding in portfolio_state.holdings:
            if holding.symbol in profile.protected_symbols:
                continue
            row = by_symbol.get(holding.symbol)
            rank = int(row.get("rank") or 999) if row else 999
            if rank > weakest_rank:
                weakest_symbol = holding.symbol
                weakest_rank = rank
                weakest_value = holding.market_value
                weakest_target_value = (
                    profile.tradable_capital * (float(row.get("suggested_size_pct") or 0.0) / 100.0)
                    if row
                    else 0.0
                )

        best_rank = int(best.get("rank") or 999)
        if not weakest_symbol or weakest_rank - best_rank < min_rank_gap:
            return RebalanceProposal(False, weakest_symbol, best_symbol, 0.0, "No material rank upgrade found.")

        proposed_sell = round(max(0.0, weakest_value - weakest_target_value), 2)
        return RebalanceProposal(
            should_rebalance=proposed_sell > 0,
            fund_from_symbol=weakest_symbol,
            target_symbol=best_symbol,
            proposed_sell_value=proposed_sell,
            reason=f"{best_symbol} is materially higher ranked than {weakest_symbol}.",
        )
