"""Motley Fool dashboard intake helpers for investigation candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class MotleyFoolSource:
    key: str
    label: str
    url: str


@dataclass(frozen=True)
class MotleyFoolDashboardRow:
    source_table: str
    symbol: str
    rank: int | None = None
    company: str = ""
    action: str = ""
    rec_date: str = ""
    risk_type: str = ""
    service: str = ""
    price: str = ""
    discussion_count: int | None = None
    company_url: str = ""
    exchange: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _clean_symbol(self.symbol))


@dataclass
class MotleyFoolCandidate:
    symbol: str
    company: str = ""
    action: str = ""
    rec_date: str = ""
    risk_type: str = ""
    service: str = ""
    rank: int | None = None
    price: str = ""
    discussion_count: int | None = None
    company_url: str = ""
    exchange: str = ""
    source_tables: list[str] = field(default_factory=list)


def normalize_motley_fool_dashboard(
    *,
    new_recommendations: list[MotleyFoolDashboardRow],
    rankings: list[MotleyFoolDashboardRow],
) -> list[MotleyFoolCandidate]:
    """Merge dashboard rows by symbol while preserving source-table provenance."""
    by_symbol: dict[str, MotleyFoolCandidate] = {}
    for row in [*new_recommendations, *rankings]:
        candidate = by_symbol.setdefault(row.symbol, MotleyFoolCandidate(symbol=row.symbol))
        if row.source_table not in candidate.source_tables:
            candidate.source_tables.append(row.source_table)
        candidate.company = candidate.company or row.company
        candidate.action = candidate.action or row.action
        candidate.rec_date = candidate.rec_date or row.rec_date
        candidate.risk_type = candidate.risk_type or row.risk_type
        candidate.service = candidate.service or row.service
        candidate.rank = candidate.rank if candidate.rank is not None else row.rank
        candidate.price = candidate.price or row.price
        candidate.company_url = candidate.company_url or row.company_url
        candidate.exchange = candidate.exchange or row.exchange
        candidate.discussion_count = (
            candidate.discussion_count
            if candidate.discussion_count is not None
            else row.discussion_count
        )

    return sorted(
        by_symbol.values(),
        key=lambda item: (
            item.rank if item.rank is not None else 999,
            item.symbol,
        ),
    )


def motley_rows_to_ideas(candidates: list[MotleyFoolCandidate]) -> list[dict]:
    """Convert Motley Fool dashboard rows to research ideas, not trade actions."""
    ideas = []
    for candidate in candidates:
        notes = [
            "Motley Fool candidate; requires independent long-term research before any action."
        ]
        if candidate.action:
            notes.append(f"New recommendation action: {candidate.action}.")
        if candidate.rec_date:
            notes.append(f"Recommendation date: {candidate.rec_date}.")
        if candidate.rank is not None:
            notes.append(f"Stock Advisor rank: {candidate.rank}.")
        if candidate.risk_type:
            notes.append(f"Motley Fool type/risk label: {candidate.risk_type}.")
        if candidate.price:
            notes.append(f"Reported price: {candidate.price}.")
        if candidate.discussion_count is not None:
            notes.append(f"Discussion count: {candidate.discussion_count}.")
        if candidate.company_url:
            notes.append(f"Motley Fool company URL: {candidate.company_url}.")

        idea = {
            "symbol": candidate.symbol,
            "company_name": candidate.company or candidate.symbol,
            "idea_source": "motley_fool_dashboard",
            "source_notes": notes,
        }
        if candidate.company_url:
            idea["motley_fool_company_url"] = candidate.company_url
            idea["source_url"] = candidate.company_url
        if candidate.exchange:
            idea["motley_fool_exchange"] = candidate.exchange
        ideas.append(idea)
    return ideas


def motley_table_payloads_to_ideas(source_key: str, table_payloads: list[dict[str, Any]]) -> list[dict]:
    """Convert captured Motley Fool table payloads into investigation ideas."""
    sources = default_motley_fool_sources()
    source = sources[source_key]
    ranking_source_table = (
        "stock_advisor_rankings" if source_key == "dashboard" else source_key
    )
    new_recs, rankings = rows_from_table_payloads(
        table_payloads,
        ranking_source_table=ranking_source_table,
    )
    candidates = normalize_motley_fool_dashboard(
        new_recommendations=new_recs,
        rankings=rankings,
    )
    ideas = motley_rows_to_ideas(candidates)
    idea_source = f"motley_fool_{source_key}"
    for idea in ideas:
        idea["idea_source"] = idea_source
        idea["source_notes"].insert(1, f"Motley Fool source: {source.label}.")
    return ideas


def default_motley_fool_sources() -> dict[str, MotleyFoolSource]:
    """Return known Motley Fool premium table sources."""
    return {
        "dashboard": MotleyFoolSource(
            key="dashboard",
            label="Dashboard summary",
            url="https://www.fool.com/premium?watchSymbols=NASDAQ%3ACRWD",
        ),
        "new_recommendations": MotleyFoolSource(
            key="new_recommendations",
            label="New recommendations",
            url="https://www.fool.com/premium/new-recs",
        ),
        "analyst_rankings": MotleyFoolSource(
            key="analyst_rankings",
            label="Analyst rankings",
            url="https://www.fool.com/premium/rankings?type=ANALYST",
        ),
        "quant_rankings": MotleyFoolSource(
            key="quant_rankings",
            label="AI rankings",
            url="https://www.fool.com/premium/rankings?type=QUANT",
        ),
    }


def rows_from_table_payloads(
    table_payloads: list[dict[str, Any]],
    *,
    ranking_source_table: str,
) -> tuple[list[MotleyFoolDashboardRow], list[MotleyFoolDashboardRow]]:
    """Convert browser-extracted table payloads into typed Motley Fool rows."""
    new_recommendations: list[MotleyFoolDashboardRow] = []
    rankings: list[MotleyFoolDashboardRow] = []

    for table in table_payloads:
        headers = [_normalize_header(value) for value in table.get("headers", [])]
        rows = table.get("rows", [])
        row_links = table.get("row_links", [])
        if {"symbol", "action", "rec date"}.issubset(set(headers)):
            for row_index, row in enumerate(rows):
                by_header = _row_by_header(headers, row)
                symbol = by_header.get("symbol", "")
                if not symbol:
                    continue
                company_url = _company_url_from_row(
                    headers,
                    row,
                    row_links[row_index] if row_index < len(row_links) else [],
                    symbol=symbol,
                )
                new_recommendations.append(
                    MotleyFoolDashboardRow(
                        source_table="new_recommendations",
                        symbol=symbol,
                        company=by_header.get("company", ""),
                        action=by_header.get("action", ""),
                        rec_date=by_header.get("rec date", ""),
                        risk_type=by_header.get("type", ""),
                        service=by_header.get("service", ""),
                        discussion_count=_parse_int(row[-1] if row else None),
                        company_url=company_url,
                        exchange=_exchange_from_company_url(company_url),
                    )
                )
            continue

        if {"#", "symbol", "price"}.issubset(set(headers)):
            for row_index, row in enumerate(rows):
                by_header = _row_by_header(headers, row)
                symbol = by_header.get("symbol", "")
                if not symbol:
                    continue
                company_url = _company_url_from_row(
                    headers,
                    row,
                    row_links[row_index] if row_index < len(row_links) else [],
                    symbol=symbol,
                )
                rankings.append(
                    MotleyFoolDashboardRow(
                        source_table=ranking_source_table,
                        rank=_parse_int(by_header.get("#")),
                        symbol=symbol,
                        company=by_header.get("company", ""),
                        price=by_header.get("price", ""),
                        risk_type=by_header.get("type", ""),
                        discussion_count=_parse_int(
                            by_header.get("times rec'd") or (row[-1] if row else None)
                        ),
                        company_url=company_url,
                        exchange=_exchange_from_company_url(company_url),
                    )
                )

    return new_recommendations, rankings


def _row_by_header(headers: list[str], row: list[Any]) -> dict[str, str]:
    return {
        header: _clean_text(row[index])
        for index, header in enumerate(headers)
        if header and index < len(row)
    }


def _normalize_header(value: Any) -> str:
    return _clean_text(value).lower()


def _parse_int(value: Any) -> int | None:
    text = _clean_text(value).replace("+", "").replace(".", "")
    digits = "".join(char for char in text if char.isdigit())
    return int(digits) if digits else None


def _company_url_from_row(
    headers: list[str],
    row: list[Any],
    links: list[Any],
    *,
    symbol: str,
) -> str:
    """Return the Motley Fool company URL embedded in a ticker/company row."""
    symbol = _clean_symbol(symbol)
    preferred_indexes = [
        index
        for index, header in enumerate(headers)
        if header in {"symbol", "company"} and index < len(links)
    ]
    for index in [*preferred_indexes, *range(len(links))]:
        href = _clean_text(links[index] if index < len(links) else "")
        if _is_company_url_for_symbol(href, symbol):
            return href
    return ""


def _is_company_url_for_symbol(href: str, symbol: str) -> bool:
    if not href or "/premium/company/" not in href:
        return False
    if re.search(r"/premium/company/\d+/?$", href, flags=re.I):
        return True
    if not symbol:
        return True
    return f"/{symbol.upper()}/" in href.upper()


def _exchange_from_company_url(company_url: str) -> str:
    match = re.search(r"/premium/company/([^/]+)/[^/]+/financials/", company_url or "", flags=re.I)
    return match.group(1).upper() if match else ""


def _clean_symbol(value: Any) -> str:
    lines = [
        line.strip().upper()
        for line in _clean_text(value).replace("\r", "\n").split("\n")
        if line.strip()
    ]
    for line in reversed(lines):
        if line != "EXPAND CURRENT ROW":
            return line
    return ""


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u200c", "").strip()
