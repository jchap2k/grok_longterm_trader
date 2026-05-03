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
    watchlist_limit: int = 100,
    batch_size: int = 10,
    engine: DiscoveryEngine | None = None,
) -> ExtendedUniverseResult:
    """Convert broad listing candidates into capped enrichment-ready ideas.

    This is intentionally pre-research. Raw listing presence should not become a
    committee call or trade signal until enrichment adds fundamentals/news
    context.
    """
    discovery = (engine or DiscoveryEngine()).build_queue(
        [dict(candidate) for candidate in candidates],
        research_limit=max(0, int(watchlist_limit or 0)),
    )
    capped_watchlist = discovery.watchlist[: max(0, int(watchlist_limit or 0))]
    watchlist_ideas = DiscoveryEngine.to_research_ideas(capped_watchlist)
    batches = build_research_universe_batches(watchlist_ideas, batch_size=batch_size)
    summary = {
        "schema_version": 1,
        "mode": "extended_universe_prepare",
        "source": source,
        "candidate_count": len(candidates),
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
