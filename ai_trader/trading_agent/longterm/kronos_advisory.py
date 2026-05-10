"""Kronos advisory payload helpers.

Kronos is treated as an optional short-term technical/timing input. These
helpers intentionally do not import torch, yfinance, or Kronos.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


ADVISORY_POLICY_BOUNDARY = (
    "Kronos output is experimental advisory context only; it does not authorize "
    "orders or override long-term quality, valuation, safety, FRED regime, "
    "FXAIX benchmark, or protected-holding gates."
)


def classify_forecast_direction(forecast_return_pct: float | None, *, deadband_pct: float = 0.75) -> str:
    """Classify short-horizon forecast drift with a small no-edge deadband."""
    if forecast_return_pct is None:
        return "unavailable"
    if forecast_return_pct >= deadband_pct:
        return "up"
    if forecast_return_pct <= -deadband_pct:
        return "down"
    return "flat"


def build_kronos_advisory_payload(
    *,
    symbol: str,
    last_close: float,
    forecast: list[Mapping[str, Any]],
    model: str,
    tokenizer: str,
    device: str,
    lookback_rows: int,
    timing_seconds: Mapping[str, Any] | None = None,
    provider_status: str = "ok",
    provider_mode: str = "kronos_subagent",
    provider_warning: str = "",
) -> dict[str, Any]:
    """Normalize raw Kronos forecast rows into the long-term advisory contract."""
    normalized_symbol = str(symbol or "").upper()
    normalized_forecast = _normalize_forecast_rows(forecast, last_close=last_close)
    final_return = (
        normalized_forecast[-1].get("close_return_from_last_pct")
        if normalized_forecast
        else None
    )
    return {
        "schema_version": 1,
        "source_type": "kronos_advisory",
        "symbol": normalized_symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_status": provider_status,
        "provider_mode": provider_mode,
        "provider_warning": provider_warning,
        "model": model,
        "tokenizer": tokenizer,
        "device": device,
        "lookback_rows": int(lookback_rows or 0),
        "last_close": round(float(last_close), 4),
        "forecast_horizon_rows": len(normalized_forecast),
        "forecast_return_pct": final_return,
        "forecast_direction": classify_forecast_direction(final_return),
        "forecast": normalized_forecast,
        "timing_seconds": dict(timing_seconds or {}),
        "policy_boundary": ADVISORY_POLICY_BOUNDARY,
    }


def build_unavailable_kronos_advisory(
    *,
    symbol: str,
    provider_mode: str,
    provider_warning: str,
) -> dict[str, Any]:
    """Build a non-failing artifact when the optional Kronos subagent is unavailable."""
    normalized_symbol = str(symbol or "").upper()
    return {
        "schema_version": 1,
        "source_type": "kronos_advisory",
        "symbol": normalized_symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_status": "unavailable",
        "provider_mode": provider_mode,
        "provider_warning": provider_warning[:500],
        "model": "",
        "tokenizer": "",
        "device": "",
        "lookback_rows": 0,
        "last_close": None,
        "forecast_horizon_rows": 0,
        "forecast_return_pct": None,
        "forecast_direction": "unavailable",
        "forecast": [],
        "timing_seconds": {},
        "policy_boundary": ADVISORY_POLICY_BOUNDARY,
    }


def _normalize_forecast_rows(forecast: list[Mapping[str, Any]], *, last_close: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = float(last_close)
    for row in forecast:
        close = _float(row.get("close"))
        if close is None:
            continue
        normalized = {
            "date": str(row.get("date") or ""),
            "open": _round_optional(row.get("open")),
            "high": _round_optional(row.get("high")),
            "low": _round_optional(row.get("low")),
            "close": round(close, 4),
            "close_return_from_last_pct": round((close / base - 1.0) * 100.0, 3),
            "volume": _round_optional(row.get("volume"), digits=2),
        }
        rows.append(normalized)
    return rows


def _round_optional(value: Any, *, digits: int = 4) -> float | None:
    parsed = _float(value)
    return None if parsed is None else round(parsed, digits)


def _float(value: Any) -> float | None:
    if value in ("", None, "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ADVISORY_POLICY_BOUNDARY",
    "build_kronos_advisory_payload",
    "build_unavailable_kronos_advisory",
    "classify_forecast_direction",
]
