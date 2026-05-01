"""Local enrichment helpers for long-term research packet readiness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CORE_PACKET_FIELDS = ("company_name", "idea_source", "research_context")
OPTIONAL_QUALITY_FIELDS = (
    "business_summary",
    "thesis_summary",
    "revenue_growth_1y_pct",
    "earnings_growth_1y_pct",
    "gross_margin_pct",
    "debt_to_equity",
    "valuation_label",
    "invalidation_conditions",
)


def enrich_research_idea(
    idea: Mapping[str, Any],
    enrichment: Mapping[str, Any] | None = None,
    *,
    enrichment_source: str = "local_enrichment",
) -> dict[str, Any]:
    """Merge local enrichment into one idea and score packet readiness."""
    payload = dict(idea)
    payload["symbol"] = str(payload.get("symbol") or "").upper()
    enrichment_payload = dict(enrichment or {})
    for key, value in enrichment_payload.items():
        if value not in (None, ""):
            payload[key] = value

    notes = [str(item) for item in payload.get("source_notes") or []]
    if enrichment_payload:
        notes.append(_enrichment_note(enrichment_source, enrichment_payload))
    payload["source_notes"] = notes

    missing = _missing_fields(payload)
    score = _completeness_score(payload, missing)
    payload["missing_fields"] = missing
    payload["completeness_score"] = score
    payload["completeness_bucket"] = _completeness_bucket(score, missing)
    return payload


def enrich_research_ideas(
    ideas: list[Mapping[str, Any]],
    enrichment_by_symbol: Mapping[str, Mapping[str, Any]],
    *,
    enrichment_source: str = "local_enrichment",
) -> list[dict[str, Any]]:
    """Apply symbol-keyed local enrichment rows to research ideas."""
    normalized_enrichment = {
        str(symbol).upper(): dict(value)
        for symbol, value in (enrichment_by_symbol or {}).items()
    }
    return [
        enrich_research_idea(
            idea,
            normalized_enrichment.get(str(idea.get("symbol") or "").upper(), {}),
            enrichment_source=enrichment_source,
        )
        for idea in ideas
    ]


def _missing_fields(payload: Mapping[str, Any]) -> list[str]:
    missing = []
    if not payload.get("company_name"):
        missing.append("company_name")
    if not payload.get("idea_source"):
        missing.append("idea_source")
    if not _has_research_context(payload):
        missing.append("research_context")
    return missing


def _has_research_context(payload: Mapping[str, Any]) -> bool:
    return bool(
        payload.get("business_summary")
        or payload.get("thesis_summary")
        or payload.get("source_notes")
    )


def _completeness_score(payload: Mapping[str, Any], missing: list[str]) -> int:
    score = 100 - (len(missing) * 25)
    for field in OPTIONAL_QUALITY_FIELDS:
        if payload.get(field):
            score += 3
    if payload.get("invalidation_conditions"):
        score += 5
    return max(0, min(100, score))


def _completeness_bucket(score: int, missing: list[str]) -> str:
    if not missing and score >= 80:
        return "ready"
    if len(missing) <= 1 and score >= 60:
        return "needs_review_context"
    return "needs_enrichment"


def _enrichment_note(source: str, enrichment: Mapping[str, Any]) -> str:
    fields = ", ".join(sorted(str(key) for key in enrichment.keys()))
    return f"Enriched from {source}: {fields}."


__all__ = ["enrich_research_idea", "enrich_research_ideas"]
