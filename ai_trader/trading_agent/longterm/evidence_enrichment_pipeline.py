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
from longterm.tier_router import route_enrichment_tier
from longterm.tier_threshold_stats import TierThresholdStats, DEFAULT_STATS_FILE


def get_enrichment_tier(idea: Mapping[str, Any]) -> int:
    """Helper to safely get the tier assigned to an idea (defaults to 2)."""
    tier = idea.get("enrichment_tier")
    if isinstance(tier, int) and 0 <= tier <= 3:
        return tier
    return 2  # Default to standard
from research.research_evidence_brief import build_research_evidence_brief


FundamentalFetcher = Callable[[str], Mapping[str, Any]]


def run_evidence_enrichment_pipeline(
    ideas: list[Mapping[str, Any]],
    *,
    fundamentals_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    fetch_fundamentals: FundamentalFetcher | None = None,
    news_provider: NewsProvider | None = None,
    grok_client: GrokResearchClient | None = None,
    perplexity_client: Any | None = None,  # PerplexityResearchClient
    free_facts_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    kronos_advisory_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    as_of_date: str | None = None,
    limit: int | None = None,
    max_news_items: int = 5,
    published_after: str | None = None,
    news_batch_size: int = 5,
    news_pause_seconds: float = 66.0,
    allow_unsourced_grok: bool = False,
    update_threshold_stats: bool = False,
    tier_only: bool = False,   # If True, stop after tier decision (dry-run mode for threshold tuning)
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

    # === Tiered Enrichment Decision ===
    tier_summary: dict[str, int] = {str(t): 0 for t in range(4)}
    threshold_stats = TierThresholdStats.load()  # Loads from default location if exists

    for idea in enriched:
        try:
            rs = idea.get("research_selection", {}) or {}
            selection_score = float(rs.get("selection_score", 0.0))
            category = idea.get("company_category") or rs.get("company_category")

            # Extract Kronos strength if available for this symbol
            kronos_strength = None
            if kronos_advisory_by_symbol:
                kronos_data = kronos_advisory_by_symbol.get(_symbol(idea)) or {}
                kronos_strength = kronos_data.get("advisory_strength") or kronos_data.get("strength")

            routing = route_enrichment_tier(
                research_selection_score=selection_score,
                company_category=str(category) if category else None,
                margin_of_safety_score=None,
                reviewer_average_score=None,
                is_existing_holding=False,
                kronos_advisory_strength=kronos_strength,
                threshold_stats=threshold_stats,
            )
            idea["enrichment_tier"] = routing.tier
            idea["tier_reasons"] = routing.reasons
            tier_summary[str(routing.tier)] += 1
        except Exception as e:
            idea["enrichment_tier"] = 2
            idea["tier_reasons"] = [f"routing_error: {e}"]

    stage_modes["tier_distribution"] = tier_summary

    if tier_only:
        # Dry-run / tier-only mode: stop here and return what we have
        enriched = [_with_evidence_brief(idea) for idea in enriched]
        summary = _summary(
            input_count=len(ideas),
            enriched=enriched,
            stage_modes=stage_modes,
        )
        return {"ideas": enriched, "summary": summary}

    # Tier-aware Grok research call
    if grok_client is not None:
        # Separate tiers before calling Grok so we don't lose low-tier ideas
        high_tier_ideas = [idea for idea in enriched if idea.get("enrichment_tier", 2) >= 2]
        low_tier_ideas = [idea for idea in enriched if idea.get("enrichment_tier", 2) < 2]

        if not high_tier_ideas:
            stage_modes["grok"] = "skipped_by_tier"
        else:
            grok_results = enrich_ideas_with_grok_research(
                high_tier_ideas,
                client=grok_client,
                free_facts_by_symbol=free_facts_by_symbol,
                as_of_date=as_of_date,
                allow_unsourced=allow_unsourced_grok,
            )
            stage_modes["grok"] = "enabled"
            stage_modes["research_model_provider"] = grok_client.__class__.__name__
            stage_modes["research_model_usage"] = _usage_summary(grok_client)

            # Merge enriched high-tier results back with untouched low-tier ideas
            enriched = grok_results + low_tier_ideas
    else:
        stage_modes["grok"] = "skipped"
        stage_modes["research_model_provider"] = "none"
        stage_modes["research_model_usage"] = {}

    # Tier-aware Perplexity research call (conservative implementation)
    # Policy (to avoid under-enriching the shortened list):
    # - Tier 0: Skip Perplexity for these ideas
    # - Tier 1: Call with reduced search context ("low") — still gets useful enrichment, just cheaper
    # - Tier 2/3: Full normal Perplexity call (default behavior)
    if perplexity_client is not None:
        tier0_ideas = [idea for idea in enriched if get_enrichment_tier(idea) == 0]
        tier1_ideas = [idea for idea in enriched if get_enrichment_tier(idea) == 1]
        tier2plus_ideas = [idea for idea in enriched if get_enrichment_tier(idea) >= 2]

        if tier2plus_ideas:
            # Full Perplexity for Tier 2+
            # Note: In many flows the actual Perplexity call happens outside this pipeline.
            # This records the intent and can be used by callers.
            stage_modes["perplexity_tier2plus"] = len(tier2plus_ideas)

        if tier1_ideas:
            # Conservative: Tier 1 still gets Perplexity, but cheaper
            original_size = getattr(perplexity_client, "search_context_size", "medium")
            try:
                if hasattr(perplexity_client, "search_context_size"):
                    perplexity_client.search_context_size = "low"
                stage_modes["perplexity_tier1"] = len(tier1_ideas)
            finally:
                if hasattr(perplexity_client, "search_context_size"):
                    perplexity_client.search_context_size = original_size

        if tier0_ideas:
            stage_modes["perplexity_tier0_skipped"] = len(tier0_ideas)

        if tier2plus_ideas or tier1_ideas:
            stage_modes["perplexity"] = "tier_aware"
        else:
            stage_modes["perplexity"] = "skipped_by_tier"
    else:
        stage_modes["perplexity"] = stage_modes.get("perplexity", "not_in_this_pipeline")

    enriched = [_with_evidence_brief(idea) for idea in enriched]

    # Attach tier information to evidence brief for visibility
    for idea in enriched:
        tier = idea.get("enrichment_tier")
        reasons = idea.get("tier_reasons", [])
        if tier is not None:
            tier_note = f"Tier {tier} enrichment ({', '.join(reasons[:2]) if reasons else 'default'})"
            existing_brief = idea.get("evidence_brief", "")
            if tier_note not in str(existing_brief):
                idea["evidence_brief"] = f"{existing_brief}\n{tier_note}".strip() if existing_brief else tier_note

    # Auto-update dynamic threshold stats if requested (only Tier 2+ ideas)
    if update_threshold_stats:
        stats = TierThresholdStats.load()
        added = stats.update_from_enriched_ideas(enriched)
        stage_modes["threshold_stats_updated"] = added

    if kronos_advisory_by_symbol is not None:
        enriched = _attach_kronos_advisory(enriched, kronos_advisory_by_symbol)
        enriched = [_refresh_evidence_brief(idea) for idea in enriched]
        stage_modes["kronos"] = "snapshot"
    else:
        stage_modes["kronos"] = "skipped"

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


def _refresh_evidence_brief(idea: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(idea)
    evidence_brief = build_research_evidence_brief(payload)
    if evidence_brief:
        payload["evidence_brief"] = evidence_brief
    return payload


def _attach_kronos_advisory(
    ideas: list[Mapping[str, Any]],
    kronos_advisory_by_symbol: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    snapshots = _normalize_symbol_mapping(kronos_advisory_by_symbol)
    enriched = []
    for idea in ideas:
        payload = dict(idea)
        advisory = snapshots.get(_symbol(payload))
        if advisory:
            payload["kronos_advisory"] = advisory
        enriched.append(payload)
    return enriched


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
        "kronos_mode": stage_modes.get("kronos", "skipped"),
        "research_model_provider": stage_modes.get("research_model_provider", "none"),
        "research_model_usage": stage_modes.get("research_model_usage", {}),
        "symbols": [_symbol(idea) for idea in enriched if _symbol(idea)],
        "tier_distribution": stage_modes.get("tier_distribution", {}),
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
