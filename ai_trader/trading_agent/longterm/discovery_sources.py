"""Load local universe source files into discovery candidate dictionaries."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def load_candidate_source_file(path: str | Path, *, source: str) -> list[dict[str, Any]]:
    """Load a CSV or NasdaqTrader pipe file as discovery candidates."""
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8-sig")
    delimiter = "|" if "|" in text.splitlines()[0] else ","
    rows = csv.DictReader(text.splitlines(), delimiter=delimiter)
    candidates = []
    for row in rows:
        candidate = _candidate_from_row(row, source=source)
        if candidate:
            candidates.append(candidate)
    return candidates


def _candidate_from_row(row: dict[str, str], *, source: str) -> dict[str, Any]:
    normalized = {_normalize_header(key): (value or "").strip() for key, value in row.items()}
    if _is_excluded_listing_row(normalized):
        return {}

    symbol = _first_present(
        normalized,
        ["symbol", "ticker", "holdingticker", "securitysymbol"],
    )
    if not symbol or symbol.lower() in {"file creation time"}:
        return {}

    company_name = _first_present(
        normalized,
        ["security", "securityname", "name", "company", "companyname", "holdingname"],
    ) or symbol
    notes = _notes_from_row(normalized)
    candidate: dict[str, Any] = {
        "symbol": symbol.upper(),
        "company_name": company_name,
        "source": source,
    }
    if notes:
        candidate["notes"] = notes

    weight = _parse_float(_first_present(normalized, ["weight", "weighting", "weightpct", "weightpercent"]))
    if weight is not None:
        candidate["source_score"] = weight
    return candidate


def _is_excluded_listing_row(row: dict[str, str]) -> bool:
    if (row.get("etf") or "").upper() == "Y":
        return True
    if (row.get("testissue") or "").upper() == "Y":
        return True
    return False


def _notes_from_row(row: dict[str, str]) -> list[str]:
    notes = []
    sector = row.get("gicssector") or row.get("sector")
    if sector:
        notes.append(f"GICS Sector: {sector}.")
    market_category = row.get("marketcategory")
    if market_category:
        notes.append(f"Market Category: {market_category}.")
    weight = _parse_float(_first_present(row, ["weight", "weighting", "weightpct", "weightpercent"]))
    if weight is not None:
        notes.append(f"ETF/index weight: {weight:g}%.")
    return notes


def _first_present(row: dict[str, str], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return ""


def _parse_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace("%", "").replace(",", ""))
    except ValueError:
        return None


def _normalize_header(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())
