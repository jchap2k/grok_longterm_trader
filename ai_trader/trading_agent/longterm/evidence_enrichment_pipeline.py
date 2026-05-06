"""Composable evidence enrichment pipeline for long-term research batches."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from longterm.fundamental_metrics_enrichment import (
    enrich_ideas_with_fundamental_metrics,
    fetch_yfinance_fundamental_metrics,
)
from longterm.grok_research_enrichment import GrokResearchClient, enrich_ideas_with_grok_research
from longterm.latest_earnings_enrichment import enrich_ideas_with_latest_earnings
from longterm.news_relevance_enrichment import NewsProvider, enrich_ideas_with_relevant_news_paced
from longterm.quality_growth_scorecard import enrich_ideas_with_quality_growth_scorecard
from research.research_evidence_brief import build_research_evidence_brief


FundamentalFetcher = Callable[[str], Mapping[str, Any]]


def run_evidence_enrichment_pipeline(
    ideas: list[Mapping[str, Any]],
    *,
    fundamentals_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    fetch_fundamentals: FundamentalFetcher | None = None,
    news_provider: NewsProvider | None = None,
    grok_client: GrokResearchClient | None = None,
    free_facts_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    as_of_date: str | None = None,
    limit: int | None = None,
    max_news_items: int = 5,
    published_after: str | None = None,
    news_batch_size: int = 5,
    news_pause_seconds: float = 66.0,
    allow_unsourced_grok: bool = False,
) -> dict[str, Any]:
    """Run the modern enrichment chain and return enriched ideas plus a summary.

    The pipeline only prepares research evidence. It does not write to the
    decision journal, change rankings, build paper previews, or call a broker.
    """
    selected = [dict(idea) for idea in (ideas[:limit] if limit is not None else ideas)]
    stage_modes: dict[str, Any] = {}
    enriched: list[dict[str, Any]] = selected

    if fundamentals_by_symbol is not None:
        enriched = enrich_ideas_with_fundamental_metrics(
            enriched,
            _normalize_symbol_mapping(fundamentals_by_symbol),
            as_of_date=as_of_date,
        )
        stage_modes["fundamentals"] = "snapshot"
    elif fetch_fundamentals is not None:
        snapshots = {
            _symbol(idea): dict(fetch_fundamentals(_symbol(idea)))
            for idea in enriched
            if _symbol(idea)
        }
        enriched = enrich_ideas_with_fundamental_metrics(
            enriched,
            snapshots,
            as_of_date=as_of_date,
        )
        stage_modes["fundamentals"] = "provider"
    else:
        stage_modes["fundamentals"] = "skipped"

    if news_provider is not None:
        enriched = enrich_ideas_with_relevant_news_paced(
            enriched,
            provider=news_provider,
            as_of_date=as_of_date,
            max_items=max_news_items,
            published_after=published_after,
            batch_size=news_batch_size,
            pause_seconds=news_pause_seconds,
        )
        stage_modes["news"] = "enabled"
    else:
        stage_modes["news"] = "skipped"

    enriched = enrich_ideas_with_latest_earnings(enriched, as_of_date=as_of_date)
    stage_modes["latest_earnings"] = "enabled"

    enriched = enrich_ideas_with_quality_growth_scorecard(enriched, as_of_date=as_of_date)
    stage_modes["scorecard"] = "enabled"

    if grok_client is not None:
        enriched = enrich_ideas_with_grok_research(
            enriched,
            client=grok_client,
            free_facts_by_symbol=free_facts_by_symbol,
            as_of_date=as_of_date,
            allow_unsourced=allow_unsourced_grok,
        )
        stage_modes["grok"] = "enabled"
        stage_modes["research_model_provider"] = grok_client.__class__.__name__
        stage_modes["research_model_usage"] = _usage_summary(grok_client)
    else:
        stage_modes["grok"] = "skipped"
        stage_modes["research_model_provider"] = "none"
        stage_modes["research_model_usage"] = {}

    enriched = [_with_evidence_brief(idea) for idea in enriched]
    summary = _summary(
        input_count=len(ideas),
        enriched=enriched,
        stage_modes=stage_modes,
    )
    return {"ideas": enriched, "summary": summary}


def fetch_yfinance_fundamentals_for_pipeline(symbol: str) -> Mapping[str, Any]:
    """Named adapter for CLI readability and tests."""
    return fetch_yfinance_fundamental_metrics(symbol)


def _with_evidence_brief(idea: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(idea)
    if not payload.get("evidence_brief"):
        evidence_brief = build_research_evidence_brief(payload)
        if evidence_brief:
            payload["evidence_brief"] = evidence_brief
    return payload


def _summary(
    *,
    input_count: int,
    enriched: list[Mapping[str, Any]],
    stage_modes: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_count = sum(1 for idea in enriched if idea.get("evidence_brief"))
    article_evidence_count = sum(
        1
        for idea in enriched
        if "Article evidence:" in str(idea.get("evidence_brief") or "")
    )
    return {
        "input_count": input_count,
        "enriched_count": len(enriched),
        "evidence_brief_count": evidence_count,
        "article_evidence_brief_count": article_evidence_count,
        "fundamentals_mode": stage_modes.get("fundamentals", "skipped"),
        "news_mode": stage_modes.get("news", "skipped"),
        "latest_earnings_mode": stage_modes.get("latest_earnings", "skipped"),
        "scorecard_mode": stage_modes.get("scorecard", "skipped"),
        "grok_mode": stage_modes.get("grok", "skipped"),
        "research_model_provider": stage_modes.get("research_model_provider", "none"),
        "research_model_usage": stage_modes.get("research_model_usage", {}),
        "symbols": [_symbol(idea) for idea in enriched if _symbol(idea)],
    }


def _usage_summary(client: Any) -> dict[str, Any]:
    summary = getattr(client, "usage_summary", None)
    if not callable(summary):
        return {}
    return dict(summary())


def _normalize_symbol_mapping(
    value: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    return {_normalize_symbol(symbol): dict(row) for symbol, row in value.items()}


def _symbol(idea: Mapping[str, Any]) -> str:
    return _normalize_symbol(str(idea.get("symbol") or ""))


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


__all__ = [
    "fetch_yfinance_fundamentals_for_pipeline",
    "run_evidence_enrichment_pipeline",
]
