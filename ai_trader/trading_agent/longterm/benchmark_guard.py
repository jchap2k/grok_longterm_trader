"""Benchmark guardrails for deciding whether active buys should continue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


@dataclass(frozen=True)
class BenchmarkGuardResult:
    should_pause_new_buys: bool
    reason: str


class BenchmarkGuard:
    """Pause new active buys when the strategy is not beating FXAIX enough."""

    def __init__(self, *, min_excess_return_pct: float = 0.0, min_decisions: int = 5):
        self.min_excess_return_pct = float(min_excess_return_pct)
        self.min_decisions = int(min_decisions)

    def evaluate(self, summary: Mapping[str, Any]) -> BenchmarkGuardResult:
        evaluated = int(summary.get("evaluated_decisions") or 0)
        excess = float(summary.get("average_excess_return_pct") or 0.0)
        if evaluated < self.min_decisions:
            return BenchmarkGuardResult(
                should_pause_new_buys=False,
                reason="Not enough evaluated decisions to judge active sleeve versus FXAIX.",
            )
        if excess < self.min_excess_return_pct:
            return BenchmarkGuardResult(
                should_pause_new_buys=True,
                reason=(
                    f"Pause new buys: active sleeve average excess return is {excess}% "
                    "versus FXAIX, below required threshold."
                ),
            )
        return BenchmarkGuardResult(
            should_pause_new_buys=False,
            reason="Active sleeve is clearing the FXAIX benchmark guard.",
        )
