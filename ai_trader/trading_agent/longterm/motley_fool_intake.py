"""Motley Fool dashboard intake helpers for investigation candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
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

        idea = {
            "symbol": candidate.symbol,
            "company_name": candidate.company or candidate.symbol,
            "idea_source": "motley_fool_dashboard",
            "source_notes": notes,
        }
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
        if {"symbol", "action", "rec date"}.issubset(set(headers)):
            for row in rows:
                by_header = _row_by_header(headers, row)
                symbol = by_header.get("symbol", "")
                if not symbol:
                    continue
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
                    )
                )
            continue

        if {"#", "symbol", "price"}.issubset(set(headers)):
            for row in rows:
                by_header = _row_by_header(headers, row)
                symbol = by_header.get("symbol", "")
                if not symbol:
                    continue
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
