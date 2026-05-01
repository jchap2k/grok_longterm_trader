"""Apply local metric enrichment to long-term discovery candidates."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping


FIELD_ALIASES = {
    "market_cap": ["marketcap", "market_cap", "marketcapitalization"],
    "revenue_growth_1y_pct": ["revenuegrowth", "revenue_growth_1y_pct", "revenuegrowth1ypct"],
    "earnings_growth_1y_pct": ["earningsgrowth", "earnings_growth_1y_pct", "earningsgrowth1ypct"],
    "gross_margin_pct": ["grossmargin", "gross_margin_pct", "grossmarginpct"],
    "return_on_capital_pct": ["returnoncapital", "return_on_capital_pct", "returnoncapitalpct", "roic"],
    "debt_to_equity": ["debttoequity", "debt_to_equity"],
    "price_trend_6m_pct": ["pricetrend6m", "price_trend_6m_pct", "pricetrend6mpct"],
    "source_rank": ["sourcerank", "source_rank", "rank"],
    "source_score": ["sourcescore", "source_score", "score"],
    "valuation_label": ["valuation", "valuationlabel", "valuation_label"],
    "category_leader": ["categoryleader", "category_leader", "leader"],
    "existing_watchlist": ["existingwatchlist", "existing_watchlist", "watchlist"],
}

NUMERIC_FIELDS = {
    "market_cap",
    "revenue_growth_1y_pct",
    "earnings_growth_1y_pct",
    "gross_margin_pct",
    "return_on_capital_pct",
    "debt_to_equity",
    "price_trend_6m_pct",
    "source_score",
}

INTEGER_FIELDS = {"source_rank"}
BOOLEAN_FIELDS = {"category_leader", "existing_watchlist"}
STRING_FIELDS = {"valuation_label"}


def load_discovery_enrichment_file(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load local JSON or CSV enrichment rows keyed by normalized symbol."""
    file_path = Path(path)
    if file_path.suffix.lower() == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        rows = _json_rows(payload)
    else:
        rows = _csv_rows(file_path)

    enrichment: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _symbol_from_row(row)
        if not symbol:
            continue
        normalized = _normalize_enrichment_row(row)
        if normalized:
            enrichment[_normalize_symbol(symbol)] = normalized
    return enrichment


def apply_discovery_enrichment(
    candidates: list[Mapping[str, Any]],
    enrichment: Mapping[str, Mapping[str, Any]],
    *,
    source: str = "local_enrichment",
) -> list[dict[str, Any]]:
    """Merge enrichment metrics into candidates without changing discovery source."""
    normalized_enrichment = {
        _normalize_symbol(symbol): _normalize_enrichment_row(row)
        for symbol, row in enrichment.items()
    }
    enriched_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        updated = dict(candidate)
        metrics = normalized_enrichment.get(_normalize_symbol(str(updated.get("symbol") or "")))
        if metrics:
            updated.update(metrics)
            notes = _note_list(updated.get("notes"))
            note = f"Enriched from {source}."
            if note not in notes:
                notes.append(note)
            updated["notes"] = notes
        enriched_candidates.append(updated)
    return enriched_candidates


def _json_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        rows = []
        for symbol, values in payload.items():
            if isinstance(values, Mapping):
                row = dict(values)
                row.setdefault("symbol", symbol)
                rows.append(row)
        return rows
    raise ValueError("Discovery enrichment JSON must contain a list or symbol-keyed object.")


def _csv_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    rows = csv.DictReader(text.splitlines())
    return [dict(row) for row in rows]


def _normalize_enrichment_row(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {_normalize_header(key): value for key, value in row.items()}
    normalized: dict[str, Any] = {}
    for target, aliases in FIELD_ALIASES.items():
        value = _first_present(source, aliases)
        if value in ("", None):
            continue
        parsed = _parse_field(target, value)
        if parsed is not None:
            normalized[target] = parsed
    return normalized


def _parse_field(target: str, value: Any) -> Any:
    if target in NUMERIC_FIELDS:
        return _parse_float(value)
    if target in INTEGER_FIELDS:
        parsed = _parse_float(value)
        return int(parsed) if parsed is not None else None
    if target in BOOLEAN_FIELDS:
        return _parse_bool(value)
    if target in STRING_FIELDS:
        return str(value).strip()
    return value


def _symbol_from_row(row: Mapping[str, Any]) -> str:
    source = {_normalize_header(key): value for key, value in row.items()}
    value = _first_present(source, ["symbol", "ticker", "holdingticker"])
    return str(value or "").strip()


def _first_present(row: Mapping[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in ("", None):
            return value
    return ""


def _parse_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "y", "1", "leader"}:
        return True
    if normalized in {"false", "no", "n", "0"}:
        return False
    return None


def _note_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _normalize_symbol(value: str) -> str:
    return value.strip().upper()


def _normalize_header(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum() or ch == "_")
