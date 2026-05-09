"""Regime-aware idle cash parking for the long-term active sleeve."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from portfolio.portfolio_profile import PortfolioProfile


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    """Small deterministic regime snapshot supplied by a future market-data layer."""

    risk_regime: str = "normal"
    vix_level: float | None = None
    spy_above_200d: bool | None = None
    ten_year_yield_trend: str = ""
    reason: str = ""
    inflation_pressure: bool = False
    yield_curve_spread: float | None = None
    credit_spread: float | None = None
    macro_signals: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "risk_regime", _normalize_regime(self.risk_regime))
        object.__setattr__(self, "ten_year_yield_trend", self.ten_year_yield_trend.lower())

    @classmethod
    def from_signals(
        cls,
        *,
        vix_level: float | None = None,
        spy_above_200d: bool | None = None,
        ten_year_yield_trend: str = "",
        inflation_pressure: bool = False,
    ) -> "MarketRegimeSnapshot":
        """Classify broad market stress without treating VIX alone as a TLT trigger."""
        vix = float(vix_level) if vix_level is not None else None
        yield_trend = (ten_year_yield_trend or "").lower()
        if vix is not None and vix >= 30:
            if yield_trend == "falling" and not inflation_pressure:
                return cls(
                    risk_regime="equity_panic_falling_rates",
                    vix_level=vix,
                    spy_above_200d=spy_above_200d,
                    ten_year_yield_trend=yield_trend,
                    reason="VIX stress with falling yields supports a capped duration hedge.",
                )
            return cls(
                risk_regime="inflation_rate_shock",
                vix_level=vix,
                spy_above_200d=spy_above_200d,
                ten_year_yield_trend=yield_trend,
                reason="VIX stress without falling-yield confirmation defaults to capital preservation.",
            )
        if vix is not None and vix >= 22:
            return cls(
                risk_regime="elevated_uncertainty",
                vix_level=vix,
                spy_above_200d=spy_above_200d,
                ten_year_yield_trend=yield_trend,
                reason="VIX is elevated, but not a confirmed equity-panic/falling-rates regime.",
            )
        if spy_above_200d is False:
            return cls(
                risk_regime="elevated_uncertainty",
                vix_level=vix,
                spy_above_200d=spy_above_200d,
                ten_year_yield_trend=yield_trend,
                reason="SPY is below its 200-day trend without extreme confirmed panic.",
            )
        return cls(
            risk_regime="normal",
            vix_level=vix,
            spy_above_200d=spy_above_200d,
            ten_year_yield_trend=yield_trend,
            reason="Constructive or unconfirmed regime defaults to normal parking.",
        )


def load_market_regime_snapshot(path: str | Path) -> MarketRegimeSnapshot:
    """Load an explicit regime snapshot from JSON."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Market regime file must contain a JSON object.")
    if payload.get("risk_regime"):
        return MarketRegimeSnapshot(
            risk_regime=str(payload.get("risk_regime") or "normal"),
            vix_level=payload.get("vix_level"),
            spy_above_200d=payload.get("spy_above_200d"),
            ten_year_yield_trend=str(payload.get("ten_year_yield_trend") or ""),
            reason=str(payload.get("reason") or ""),
            inflation_pressure=bool(payload.get("inflation_pressure") or False),
            yield_curve_spread=payload.get("yield_curve_spread"),
            credit_spread=payload.get("credit_spread"),
            macro_signals=dict(payload.get("macro_signals") or {}),
        )
    return MarketRegimeSnapshot.from_signals(
        vix_level=payload.get("vix_level"),
        spy_above_200d=payload.get("spy_above_200d"),
        ten_year_yield_trend=str(payload.get("ten_year_yield_trend") or ""),
        inflation_pressure=bool(payload.get("inflation_pressure") or False),
    )


@dataclass(frozen=True)
class ParkingAllocation:
    symbol: str
    weight: float
    intent_type: str
    reason: str


class IdleCashDeploymentPolicy:
    """Choose where leftover active-sleeve cash should wait for better picks."""

    def allocations(
        self,
        *,
        profile: PortfolioProfile,
        market_regime: MarketRegimeSnapshot,
    ) -> list[ParkingAllocation]:
        regime = market_regime.risk_regime
        equity = profile.defensive_parking_symbol or "SPY"
        low_risk = profile.low_risk_parking_symbol or "SGOV"
        duration = profile.duration_hedge_symbol or "TLT"

        if regime in {"normal", "recovery"}:
            return [
                ParkingAllocation(
                    symbol=equity,
                    weight=1.0,
                    intent_type="PARK_IDLE_CASH",
                    reason="Park idle active cash in the configured equity index parking vehicle.",
                )
            ]
        if regime == "elevated_uncertainty":
            return [
                ParkingAllocation(
                    symbol=equity,
                    weight=0.5,
                    intent_type="PARK_IDLE_CASH",
                    reason="Split idle active cash while uncertainty is elevated.",
                ),
                ParkingAllocation(
                    symbol=low_risk,
                    weight=0.5,
                    intent_type="PARK_IDLE_CASH",
                    reason="Split idle active cash into short-duration Treasury parking.",
                ),
            ]
        if regime == "equity_panic_falling_rates":
            return [
                ParkingAllocation(
                    symbol=low_risk,
                    weight=0.7,
                    intent_type="PARK_DEFENSIVE_CASH",
                    reason="Prioritize capital preservation during equity panic.",
                ),
                ParkingAllocation(
                    symbol=duration,
                    weight=0.3,
                    intent_type="PARK_DEFENSIVE_CASH",
                    reason="Use a capped duration hedge only because yields are falling.",
                ),
            ]
        if regime == "inflation_rate_shock":
            return [
                ParkingAllocation(
                    symbol=low_risk,
                    weight=1.0,
                    intent_type="PARK_DEFENSIVE_CASH",
                    reason="Avoid long-duration bonds when volatility is rate/inflation driven.",
                )
            ]
        return []


def _normalize_regime(value: str) -> str:
    normalized = (value or "normal").lower().replace("-", "_").replace(" ", "_")
    aliases: Mapping[str, str] = {
        "chaotic_equity_selloff_falling_rates": "equity_panic_falling_rates",
        "equity_panic": "equity_panic_falling_rates",
        "rate_shock": "inflation_rate_shock",
        "chaotic": "inflation_rate_shock",
    }
    return aliases.get(normalized, normalized)


__all__ = [
    "IdleCashDeploymentPolicy",
    "load_market_regime_snapshot",
    "MarketRegimeSnapshot",
    "ParkingAllocation",
]
