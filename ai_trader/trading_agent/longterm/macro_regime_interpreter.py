"""Advisory interpretation layer for raw macro/FRED regime fields."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def interpret_macro_regime(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Turn raw macro fields into a compact advisory decision context."""
    payload = dict(context or {})
    if not payload:
        return {
            "macro_regime_label": "not_supplied",
            "severity": "low",
            "review_trigger": False,
            "sizing_caution": "normal",
            "new_buy_posture": "normal",
            "provider_healthy": False,
            "provider_status": "not_supplied",
            "provider_mode": "",
            "reasons": ["macro_context_not_supplied"],
            "policy_boundary": (
                "Advisory macro context only: may affect review cadence, sizing caution, "
                "parking posture, and committee skepticism; never directly authorizes orders."
            ),
        }
    thresholds = _thresholds(payload)
    reasons: list[str] = []

    provider_status = str(payload.get("provider_status") or "unknown").lower()
    provider_mode = str(payload.get("provider_mode") or "").lower()
    provider_healthy = provider_status == "ok" and provider_mode == "fredapi"
    if not provider_healthy:
        reasons.append(f"provider_status_{provider_status or 'unknown'}")

    risk_regime = str(payload.get("risk_regime") or "unknown").lower()
    vix_level = _float_or_none(payload.get("vix_level"))
    yield_curve_spread = _float_or_none(payload.get("yield_curve_spread"))
    credit_spread = _float_or_none(payload.get("credit_spread"))
    inflation_pressure = bool(payload.get("inflation_pressure"))
    spy_above_200d = payload.get("spy_above_200d")

    if vix_level is not None and vix_level >= thresholds["vix_stress"]:
        reasons.append("vix_stress")
    elif vix_level is not None and vix_level >= thresholds["vix_elevated"]:
        reasons.append("vix_elevated")
    if spy_above_200d is False:
        reasons.append("equity_index_below_200d")
    if inflation_pressure:
        reasons.append("inflation_pressure")
    if yield_curve_spread is not None and yield_curve_spread < thresholds["yield_curve_inverted_threshold"]:
        reasons.append("yield_curve_inverted")
    if credit_spread is not None and credit_spread >= thresholds["credit_spread_elevated_pct"]:
        reasons.append("credit_spread_elevated")

    label = _label(
        provider_healthy=provider_healthy,
        risk_regime=risk_regime,
        reasons=reasons,
    )
    severity = _severity(label, reasons)
    review_trigger = severity in {"high", "severe"} or label in {"credit_stress", "contraction_risk"}
    sizing_caution = "tighten_new_buy_sizing" if severity in {"medium", "high", "severe"} else "normal"
    new_buy_posture = (
        "pause_or_reduce_new_buys_unless_exceptional"
        if severity in {"high", "severe"}
        else "prefer_smaller_staged_entries"
        if severity == "medium"
        else "normal"
    )

    return {
        "macro_regime_label": label,
        "severity": severity,
        "review_trigger": review_trigger,
        "sizing_caution": sizing_caution,
        "new_buy_posture": new_buy_posture,
        "provider_healthy": provider_healthy,
        "provider_status": provider_status,
        "provider_mode": provider_mode,
        "reasons": reasons,
        "policy_boundary": (
            "Advisory macro context only: may affect review cadence, sizing caution, "
            "parking posture, and committee skepticism; never directly authorizes orders."
        ),
    }


def _thresholds(payload: Mapping[str, Any]) -> dict[str, float]:
    signals = payload.get("macro_signals") if isinstance(payload.get("macro_signals"), Mapping) else {}
    raw = signals.get("thresholds") if isinstance(signals.get("thresholds"), Mapping) else {}
    return {
        "vix_elevated": _float_or_none(raw.get("vix_elevated")) or 22.0,
        "vix_stress": _float_or_none(raw.get("vix_stress")) or 30.0,
        "yield_curve_inverted_threshold": (
            _float_or_none(raw.get("yield_curve_inverted_threshold"))
            if raw.get("yield_curve_inverted_threshold") is not None
            else 0.0
        ),
        "credit_spread_elevated_pct": _float_or_none(raw.get("credit_spread_elevated_pct")) or 5.0,
    }


def _label(*, provider_healthy: bool, risk_regime: str, reasons: list[str]) -> str:
    if not provider_healthy:
        return "provider_attention"
    if risk_regime in {"market_data_unavailable", "inflation_rate_shock", "equity_panic_falling_rates"}:
        return risk_regime
    if "credit_spread_elevated" in reasons:
        return "credit_stress"
    if "yield_curve_inverted" in reasons:
        return "late_cycle_caution"
    if {"vix_elevated", "equity_index_below_200d"} & set(reasons):
        return "elevated_uncertainty"
    return risk_regime or "normal"


def _severity(label: str, reasons: list[str]) -> str:
    if label in {"market_data_unavailable", "inflation_rate_shock", "equity_panic_falling_rates"}:
        return "severe"
    if label in {"credit_stress", "contraction_risk"}:
        return "high"
    if label in {"late_cycle_caution", "elevated_uncertainty"}:
        return "medium"
    if "provider_status_" in " ".join(reasons):
        return "low"
    return "low"


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["interpret_macro_regime"]
