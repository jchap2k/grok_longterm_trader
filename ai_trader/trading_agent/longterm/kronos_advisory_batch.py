"""Batch helpers for optional Kronos advisory artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.kronos_advisory import ADVISORY_POLICY_BOUNDARY, build_unavailable_kronos_advisory


def load_symbols_from_args(args: Any) -> list[str]:
    """Load unique symbols from CSV args and optional idea batch JSON."""
    symbols: list[str] = []
    symbols.extend(_split_symbols(getattr(args, "symbols", "") or ""))
    idea_batch = getattr(args, "idea_batch", "") or ""
    if idea_batch:
        payload = json.loads(Path(idea_batch).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Idea batch must contain a JSON list.")
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            symbol = item.get("symbol") or item.get("ticker")
            if symbol:
                symbols.append(str(symbol))
    return _unique_symbols(symbols)


def build_kronos_batch_payload(advisories: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build compact batch summary from per-symbol Kronos advisory payloads."""
    items = [_compact_item(dict(item)) for item in advisories]
    ok_count = sum(1 for item in items if item.get("provider_status") == "ok")
    unavailable_count = sum(1 for item in items if item.get("provider_status") != "ok")
    if not items:
        provider_status = "unavailable"
    elif unavailable_count and ok_count:
        provider_status = "degraded"
    elif unavailable_count:
        provider_status = "unavailable"
    else:
        provider_status = "ok"
    return {
        "schema_version": 1,
        "source_type": "kronos_advisory_batch",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_status": provider_status,
        "provider_mode": "kronos_batch",
        "symbol_count": len(items),
        "ok_count": ok_count,
        "unavailable_count": unavailable_count,
        "items": items,
        "policy_boundary": ADVISORY_POLICY_BOUNDARY,
    }


def build_symbol_error_advisory(symbol: str, exc: Exception) -> dict[str, Any]:
    """Return a per-symbol unavailable artifact for batch-level exceptions."""
    return build_unavailable_kronos_advisory(
        symbol=symbol,
        provider_mode="kronos_batch_symbol_error",
        provider_warning=_safe_error(exc),
    )


def _compact_item(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(payload.get("symbol") or "").upper(),
        "provider_status": str(payload.get("provider_status") or "unknown"),
        "provider_mode": str(payload.get("provider_mode") or ""),
        "provider_warning": str(payload.get("provider_warning") or ""),
        "forecast_direction": str(payload.get("forecast_direction") or "unavailable"),
        "forecast_return_pct": payload.get("forecast_return_pct"),
        "forecast_horizon_rows": payload.get("forecast_horizon_rows"),
    }


def _split_symbols(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def _unique_symbols(symbols: Iterable[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return sorted(result)


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message[:500]}"


__all__ = [
    "build_kronos_batch_payload",
    "build_symbol_error_advisory",
    "load_symbols_from_args",
]
