"""Fallback data sources for the read-only long-term dashboard."""

from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from longterm.market_regime_snapshot import fetch_yfinance_history


def load_decision_journal_evidence_items(path: Path | None, *, limit: int = 500) -> list[dict[str, Any]]:
    """Hydrate dashboard evidence from the durable decision journal when no evidence file was supplied."""
    if not path or not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT timestamp, symbol, company_name, recommendation, confidence,
                   suggested_size_pct, key_thesis, packet_json, decision_json
            FROM longterm_decision_journal
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"] or "").upper().strip()
        if not symbol or symbol in latest_by_symbol:
            continue
        item = _journal_row_to_evidence_item(dict(row))
        if item:
            latest_by_symbol[symbol] = item
    return list(latest_by_symbol.values())


def fetch_missing_price_history(
    *,
    existing: Mapping[str, Any],
    symbols: list[str],
    period: str,
    fetcher,
) -> dict[str, Any]:
    """Fetch price history only for symbols not already present in the supplied history map."""
    price_history = dict(existing or {})
    for symbol in symbols:
        symbol = str(symbol or "").upper().strip()
        if not symbol or price_history.get(symbol):
            continue
        price_history[symbol] = fetcher(symbol, period)
    return price_history


def dashboard_price_history_symbols(
    *,
    dashboard: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    portfolio_state: Mapping[str, Any],
    evidence_items: list[dict[str, Any]],
) -> list[str]:
    """Collect symbols that may need price history on generated dashboard pages."""
    symbols: list[str] = []
    for key in ("paper_submit_candidates", "parking_symbols"):
        for value in dashboard.get(key) or []:
            _append_symbol(symbols, value)
    for intent in action_plan.get("intents") or []:
        if isinstance(intent, Mapping):
            _append_symbol(symbols, intent.get("symbol"))
    for holding in portfolio_state.get("holdings") or []:
        if isinstance(holding, Mapping):
            _append_symbol(symbols, holding.get("symbol"))
    for item in evidence_items:
        _append_symbol(symbols, item.get("symbol"))
    return symbols


def requested_ticker_symbols(parsed_path: str) -> list[str]:
    """Return the requested ticker symbol when a dashboard route targets one ticker page."""
    if not parsed_path.startswith("/tickers/") or not parsed_path.endswith(".html"):
        return []
    symbol = Path(parsed_path).stem.upper().strip()
    return [symbol] if symbol else []


@lru_cache(maxsize=512)
def cached_yfinance_history(symbol: str, period: str = "1y") -> list[dict[str, Any]]:
    """Cache per-symbol history fetches while the dashboard server process is alive.
    Returns empty list on any provider failure (rate limit, network, etc.) so
    page generation degrades gracefully instead of crashing the request.
    """
    try:
        return fetch_yfinance_history(symbol, period)
    except Exception:
        # Degrade: no chart data instead of failing the whole ticker page or test
        return []


def _journal_row_to_evidence_item(row: Mapping[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return {}
    packet = _safe_json_loads(row.get("packet_json"), default={})
    decision = _safe_json_loads(row.get("decision_json"), default={})
    source_notes = [str(item) for item in packet.get("source_notes") or [] if str(item).strip()]
    thesis = str(decision.get("key_thesis") or row.get("key_thesis") or packet.get("thesis_summary") or "")
    business_summary = str(packet.get("business_summary") or thesis or f"{symbol} journal-backed research context.")
    superscore = _first_number(
        packet.get("combined_attractiveness_score"),
        _extract_score(thesis, r"scorecard\s+([0-9]+(?:\.[0-9]+)?)"),
    )
    quality = _first_number(packet.get("quality_score"), _extract_score(thesis, r"quality\s+([0-9]+(?:\.[0-9]+)?)"))
    growth = _extract_score(thesis, r"growth\s+([0-9]+(?:\.[0-9]+)?)")
    valuation = _first_number(
        packet.get("valuation_score"),
        _extract_score(thesis, r"valuation(?:\s+score)?\s+([0-9]+(?:\.[0-9]+)?)"),
    )
    selection_score = _selection_score_from_source_notes(source_notes)
    return {
        "symbol": symbol,
        "company_name": row.get("company_name") or packet.get("company_name") or symbol,
        "business_summary": business_summary,
        "journal_key_thesis": thesis,
        "source_notes": source_notes,
        "quality_growth_scorecard": {
            "superscore": superscore,
            "quality_score": quality,
            "growth_score": growth,
            "valuation_score": valuation,
            "analysis": {
                "quality": quality,
                "growth": growth,
                "valuation": valuation,
            },
            "investing_type": _investing_type_from_text(thesis) or packet.get("company_category") or "journal-backed",
            "score_reasons": source_notes[:8] or [str(packet.get("evidence_brief") or "")],
        },
        "book_reviewer_signals": {
            "support": _reviewer_lines(packet.get("reviewer_support")),
            "objections": _reviewer_lines(packet.get("reviewer_objections")),
        },
        "python_first_pass_scan": {
            "score": packet.get("combined_attractiveness_score"),
            "rank_score": selection_score,
            "reason": _source_note_starting_with(source_notes, "Research selection:")
            or str(packet.get("thesis_summary") or ""),
        },
        "fundamental_metrics": _fundamental_metrics_from_evidence_brief(packet.get("evidence_brief")),
        "latest_earnings": _latest_earnings_from_source_notes(source_notes),
        "article_evidence_summaries": _article_summaries_from_source_notes(source_notes),
    }


def _fundamental_metrics_from_evidence_brief(value: Any) -> dict[str, Any]:
    text = str(value or "")
    if not text:
        return {}
    return _prune_empty_dict(
        {
            "revenue_growth_cagr": {
                "3yr_revenue_growth": _regex_value(text, r"3yr revenue growth\s+([^;]+)"),
                "3yr_ebitda_growth": _regex_value(text, r"3yr EBITDA growth\s+([^;]+)"),
            },
            "valuation_ttm": {
                "pe": _regex_value(text, r"P/E\s+([^;]+)"),
                "ev_ebitda": _regex_value(text, r"EV/EBITDA\s+([^;]+)"),
            },
            "profitability_ttm": {
                "gross_margin": _regex_value(text, r"gross margin\s+([^;]+)"),
                "operating_margin": _regex_value(text, r"operating margin\s+([^;]+)"),
            },
            "financials_ttm": {
                "revenue": _regex_value(text, r"TTM revenue\s+([^;]+)"),
                "free_cash_flow": _regex_value(text, r"(?:TTM )?FCF\s+([^;]+)"),
            },
        }
    )


def _latest_earnings_from_source_notes(source_notes: list[str]) -> dict[str, Any]:
    note = _source_note_starting_with(source_notes, "Latest earnings enrichment:")
    if not note:
        return {}
    return {
        "quarter": "latest_available",
        "summary": note,
        "key_takeaways": [note],
    }


def _article_summaries_from_source_notes(source_notes: list[str]) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    news_note = _source_note_starting_with(source_notes, "Latest relevant news:")
    if news_note:
        titles = [item.strip(" .") for item in news_note.split(":", 1)[-1].split(";") if item.strip(" .")]
        articles.extend({"title": title, "summary": "Captured from latest relevant news enrichment."} for title in titles)
    convergence = _source_note_starting_with(source_notes, "Source convergence:")
    if convergence:
        articles.append({"title": "Source convergence", "summary": convergence.split(":", 1)[-1].strip()})
    for note in source_notes:
        if note.startswith(("Python quality-growth scorecard:", "Python fundamental metrics:", "Extended-universe Python first pass:")):
            articles.append({"title": note.split(":", 1)[0], "summary": note.split(":", 1)[-1].strip()})
    return articles


def _reviewer_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _append_symbol(symbols: list[str], value: Any) -> None:
    symbol = str(value or "").upper().strip()
    if symbol and symbol not in symbols:
        symbols.append(symbol)


def _safe_json_loads(value: Any, *, default: Any) -> Any:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return default
    return payload if isinstance(payload, type(default)) else default


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            if value is not None and value != "":
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_score(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return _first_number(match.group(1)) if match else None


def _selection_score_from_source_notes(source_notes: list[str]) -> float | None:
    note = _source_note_starting_with(source_notes, "Research selection:")
    return _extract_score(note, r"selection_score=([0-9]+(?:\.[0-9]+)?)") if note else None


def _source_note_starting_with(source_notes: list[str], prefix: str) -> str:
    prefix_lower = prefix.lower()
    for note in source_notes:
        if note.lower().startswith(prefix_lower):
            return note
    return ""


def _investing_type_from_text(text: str) -> str:
    for label in ("Premium Compounder", "Cautious Compounder", "Moderate Compounder", "Quality Compounder"):
        if label.lower() in (text or "").lower():
            return label
    return ""


def _regex_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _prune_empty_dict(payload: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        section: {key: value for key, value in values.items() if value not in (None, "")}
        for section, values in payload.items()
        if any(value not in (None, "") for value in values.values())
    }


__all__ = [
    "cached_yfinance_history",
    "dashboard_price_history_symbols",
    "fetch_missing_price_history",
    "load_decision_journal_evidence_items",
    "requested_ticker_symbols",
]
