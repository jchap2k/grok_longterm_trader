"""Prepare broad non-Fool universe candidates for enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from longterm.discovery import DiscoveryEngine
from longterm.research_universe import build_research_universe_batches


@dataclass(frozen=True)
class ExtendedUniverseResult:
    watchlist_ideas: list[dict[str, Any]] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def prepare_extended_universe(
    candidates: list[Mapping[str, Any]],
    *,
    source: str,
    supplemental_candidates: list[list[Mapping[str, Any]]] | None = None,
    include_symbols: list[str] | None = None,
    watchlist_limit: int = 100,
    batch_size: int = 10,
    engine: DiscoveryEngine | None = None,
) -> ExtendedUniverseResult:
    """Convert broad listing candidates into capped enrichment-ready ideas.

    This is intentionally pre-research. Raw listing presence should not become a
    committee call or trade signal until enrichment adds fundamentals/news
    context.
    """
    normalized_include = _normalize_include_symbols(include_symbols or [])
    supplemental_groups = supplemental_candidates or []
    combined_candidates = [dict(candidate) for candidate in candidates]
    for group in supplemental_groups:
        combined_candidates.extend(dict(candidate) for candidate in group)
    selected_candidates = _filter_candidates(combined_candidates, normalized_include)
    discovery = (engine or DiscoveryEngine()).build_queue(
        [dict(candidate) for candidate in selected_candidates],
        research_limit=max(0, int(watchlist_limit or 0)),
    )
    all_discovered = [*discovery.research_queue, *discovery.watchlist, *discovery.rejected]
    always_enrich = [item for item in all_discovered if _requires_pre_llm_enrichment(item.source_metadata.get("sources"))]
    capped_watchlist = _merge_unique_candidates(
        [
            *always_enrich,
            *discovery.watchlist[: max(0, int(watchlist_limit or 0))],
        ]
    )
    if normalized_include:
        capped_watchlist = sorted(
            capped_watchlist,
            key=lambda item: normalized_include.index(item.symbol)
            if item.symbol in normalized_include
            else len(normalized_include),
        )
    watchlist_ideas = DiscoveryEngine.to_research_ideas(capped_watchlist)
    batches = build_research_universe_batches(watchlist_ideas, batch_size=batch_size)
    summary = {
        "schema_version": 1,
        "mode": "extended_universe_prepare",
        "source": source,
        "candidate_count": len(selected_candidates),
        "source_candidate_count": len(candidates),
        "supplemental_source_count": len(supplemental_groups),
        "supplemental_candidate_count": sum(len(group) for group in supplemental_groups),
        "always_enrich_count": len(always_enrich),
        "include_symbols": normalized_include,
        "research_ready_count": len(discovery.research_queue),
        "watchlist_count": len(discovery.watchlist),
        "rejected_count": len(discovery.rejected),
        "watchlist_ideas_count": len(watchlist_ideas),
        "batch_count": len(batches),
        "symbols": [idea["symbol"] for idea in watchlist_ideas if idea.get("symbol")],
        "next_enrichment_command": _next_enrichment_command(),
    }
    return ExtendedUniverseResult(
        watchlist_ideas=watchlist_ideas,
        batches=batches,
        summary=summary,
    )


def _normalize_include_symbols(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in symbols:
        for part in str(value or "").split(","):
            symbol = part.strip().upper()
            if symbol and symbol not in normalized:
                normalized.append(symbol)
    return normalized


def _filter_candidates(candidates: list[Mapping[str, Any]], include_symbols: list[str]) -> list[Mapping[str, Any]]:
    if not include_symbols:
        return [dict(candidate) for candidate in candidates]
    by_symbol = {str(candidate.get("symbol") or "").upper(): dict(candidate) for candidate in candidates}
    return [by_symbol[symbol] for symbol in include_symbols if symbol in by_symbol]


def _merge_unique_candidates(candidates: list) -> list:
    merged = []
    seen = set()
    for candidate in candidates:
        symbol = str(getattr(candidate, "symbol", "") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        merged.append(candidate)
    return merged


def _requires_pre_llm_enrichment(sources: object) -> bool:
    values = sources if isinstance(sources, list) else [sources]
    for source in values:
        normalized = str(source or "").lower().replace("_", "").replace("&", "and")
        if "motleyfool" in normalized:
            return True
        if normalized in {"sp500", "sandp500", "s&p500", "spyholdings"}:
            return True
    return False


def _next_enrichment_command() -> str:
    return (
        "python scripts/longterm_evidence_enrichment_pipeline.py "
        "--idea-batch path\\to\\extended_watchlist_ideas.json "
        "--fundamentals-provider yfinance "
        "--polygon-news --news-cache-path path\\to\\polygon_news_cache.json "
        "--rate-limit-batch-size 5 --rate-limit-pause-seconds 66 "
        "--output path\\to\\extended_watchlist.evidence_ready.json "
        "--summary-output path\\to\\extended_watchlist.evidence_summary.json"
    )


__all__ = ["ExtendedUniverseResult", "prepare_extended_universe"]
