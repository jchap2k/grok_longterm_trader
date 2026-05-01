"""Deterministic dry-run risk review for long-term action intents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from longterm.benchmark_guard import BenchmarkGuardResult
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"


@dataclass(frozen=True)
class RiskReview:
    symbol: str
    intent_type: str
    allowed: bool
    risk_level: str
    veto_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class RiskReviewBuilder:
    """Review dry-run action intents against rules, benchmark, and portfolio state."""

    def __init__(
        self,
        *,
        max_new_position_pct: float = 10.0,
        rules_path: str | Path = DEFAULT_RULES_PATH,
    ):
        self.max_new_position_pct = float(max_new_position_pct)
        self.rules_text = _load_rules_text(rules_path)

    def build(
        self,
        row: Mapping[str, Any],
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        benchmark_guard_result: BenchmarkGuardResult,
        review_status: Mapping[str, Any] | None = None,
        intent_type: str = "",
    ) -> RiskReview:
        symbol = str(row.get("symbol") or "").upper()
        resolved_intent = intent_type or str(row.get("intent_type") or row.get("recommendation") or "REVIEW").upper()
        veto_reasons: list[str] = []
        warnings: list[str] = []

        if symbol in {item.upper() for item in profile.protected_symbols}:
            veto_reasons.append(f"{symbol} is protected and cannot be traded or rebalanced.")

        if benchmark_guard_result.should_pause_new_buys and resolved_intent in {"BUY", "ADD", "REBALANCE"}:
            veto_reasons.append(benchmark_guard_result.reason)

        suggested_size = float(row.get("suggested_size_pct") or 0.0)
        if resolved_intent in {"BUY", "ADD"} and suggested_size > self.max_new_position_pct:
            warnings.append(
                f"Suggested size {suggested_size:g}% exceeds default new-position risk cap {self.max_new_position_pct:g}%."
            )

        thesis_state = str((review_status or {}).get("thesis_state") or "").lower()
        if thesis_state in {"weakening", "stale"}:
            warnings.append(f"Thesis state is {thesis_state}; require review before increasing exposure.")
        elif thesis_state in {"broken", "invalidated"}:
            veto_reasons.append(f"Thesis state is {thesis_state}; block new exposure.")

        if resolved_intent in {"BUY", "ADD"} and portfolio_state.cash <= 0:
            warnings.append("No active-sleeve cash is currently available.")

        risk_level = _risk_level(veto_reasons, warnings)
        return RiskReview(
            symbol=symbol,
            intent_type=resolved_intent,
            allowed=not veto_reasons,
            risk_level=risk_level,
            veto_reasons=veto_reasons,
            warnings=warnings,
        )


def _risk_level(veto_reasons: list[str], warnings: list[str]) -> str:
    if veto_reasons:
        return "high"
    if len(warnings) >= 2:
        return "high"
    if warnings:
        return "medium"
    return "low"


def _load_rules_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return "Default dry-run safety: protect core holdings, benchmark gate buys, and start smaller."
