"""Preflight reconciliation for committee research queues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ResearchQueueReconciliationResult:
    rows: list[dict[str, Any]]
    skipped_duplicates: list[dict[str, Any]]
    summary: dict[str, Any]


def reconcile_research_queue(
    queue: Iterable[Mapping[str, Any]],
    *,
    comparison_sources: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    recent_symbols: Iterable[str] | None = None,
    primary_source_label: str = "wide_universe",
) -> ResearchQueueReconciliationResult:
    """Annotate a selected queue with convergence and recent-research context."""

    comparison_by_symbol = _comparison_by_symbol(comparison_sources or {})
    recent = _symbols(recent_symbols)
    rows: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    seen: set[str] = set()
    primary_counts: dict[str, int] = {}
    queue_rows = [dict(item) for item in queue if isinstance(item, Mapping)]
    for item in queue_rows:
        symbol = _symbol(item)
        if not symbol:
            continue
        primary_counts[symbol] = primary_counts.get(symbol, 0) + 1

    for item in queue_rows:
        row = dict(item)
        symbol = _symbol(row)
        if not symbol:
            continue
        if symbol in seen:
            skipped_duplicates.append({"symbol": symbol, "skip_reason": "duplicate_primary_row"})
            continue
        seen.add(symbol)
        matches = comparison_by_symbol.get(symbol, [])
        sources = sorted({primary_source_label, *[match["source_label"] for match in matches]})
        recent_research = symbol in recent
        metadata = {
            "schema_version": 1,
            "primary_source": primary_source_label,
            "sources": sources,
            "source_count": len(sources),
            "comparison_matches": matches,
            "recent_research": recent_research,
            "suggested_research_mode": "update_existing_thesis" if recent_research else "fresh_research",
            "duplicate_primary_count": primary_counts.get(symbol, 1),
        }
        row["source_convergence"] = metadata
        row["source_notes"] = _source_notes(row, metadata)
        row["evidence_brief"] = _evidence_brief(row, metadata)
        rows.append(row)

    converged = [row for row in rows if int(row["source_convergence"]["source_count"]) > 1]
    recent_rows = [row for row in rows if row["source_convergence"]["recent_research"]]
    duplicate_symbols = sorted(symbol for symbol, count in primary_counts.items() if count > 1)
    summary = {
        "schema_version": 1,
        "mode": "research_queue_reconciliation",
        "input_count": len(queue_rows),
        "reconciled_count": len(rows),
        "skipped_duplicate_count": len(skipped_duplicates),
        "duplicate_primary_symbol_count": len(duplicate_symbols),
        "duplicate_primary_symbols": duplicate_symbols,
        "comparison_source_count": len(comparison_sources or {}),
        "converged_symbol_count": len(converged),
        "converged_symbols": [row["symbol"] for row in converged],
        "recent_research_symbol_count": len(recent_rows),
        "recent_research_symbols": [row["symbol"] for row in recent_rows],
    }
    return ResearchQueueReconciliationResult(rows=rows, skipped_duplicates=skipped_duplicates, summary=summary)


def _comparison_by_symbol(
    comparison_sources: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for source_label, rows in comparison_sources.items():
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = _symbol(row)
            if not symbol:
                continue
            result.setdefault(symbol, []).append(
                {
                    "source_label": str(source_label),
                    "idea_source": str(row.get("idea_source") or row.get("source") or source_label),
                    "company_name": str(row.get("company_name") or row.get("company") or symbol),
                    "info_url": str(row.get("source_url") or row.get("company_url") or row.get("url") or ""),
                }
            )
    return result


def _source_notes(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[str]:
    notes = [str(item) for item in row.get("source_notes") or [] if str(item)]
    sources = [str(item) for item in metadata.get("sources") or []]
    notes.append(f"Source convergence: {' + '.join(sources)}.")
    if metadata.get("recent_research"):
        notes.append("Recent research exists; route as thesis update instead of duplicate fresh idea.")
    if int(metadata.get("duplicate_primary_count") or 0) > 1:
        notes.append(f"Duplicate primary rows collapsed: {metadata.get('duplicate_primary_count')}.")
    return _dedupe(notes)


def _evidence_brief(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    brief = str(row.get("evidence_brief") or "")
    line = (
        "Source convergence: "
        f"{' + '.join(str(item) for item in metadata.get('sources') or [])}; "
        f"mode={metadata.get('suggested_research_mode')}."
    )
    return f"{brief}\n{line}".strip()


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").upper().strip()


def _symbols(symbols: Iterable[str] | None) -> set[str]:
    return {str(symbol).upper().strip() for symbol in (symbols or []) if str(symbol).strip()}


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = ["ResearchQueueReconciliationResult", "reconcile_research_queue"]
