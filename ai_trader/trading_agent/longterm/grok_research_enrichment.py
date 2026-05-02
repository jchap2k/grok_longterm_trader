"""Grok-powered catalyst enrichment for long-term research ideas.

This module treats Grok as a source-backed synthesis layer. Free/provider facts
such as Finnhub snapshots can be supplied as inputs, while generated scores stay
explicitly labeled as model estimates rather than proprietary third-party data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol


DEFAULT_GROK_MODEL = "grok-4-1-fast-reasoning"
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"


class GrokResearchClient(Protocol):
    """Minimal client interface for Grok research synthesis."""

    def enrich(
        self,
        idea: Mapping[str, Any],
        *,
        free_facts: Mapping[str, Any] | None = None,
        as_of_date: str | None = None,
    ) -> Mapping[str, Any]:
        """Return raw structured enrichment for one research idea."""


@dataclass
class FakeGrokResearchClient:
    """Offline client backed by symbol-keyed structured responses."""

    responses_by_symbol: Mapping[str, Mapping[str, Any]]

    def enrich(
        self,
        idea: Mapping[str, Any],
        *,
        free_facts: Mapping[str, Any] | None = None,
        as_of_date: str | None = None,
    ) -> Mapping[str, Any]:
        symbol = _normalize_symbol(str(idea.get("symbol") or ""))
        response = self.responses_by_symbol.get(symbol)
        if response is None:
            raise KeyError(f"No Grok research snapshot found for {symbol}")
        return response


class XaiGrokResearchClient:
    """OpenAI-compatible xAI client for structured Grok research enrichment."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_env: str = "XAI_API_KEY",
        model: str = DEFAULT_GROK_MODEL,
        base_url: str = DEFAULT_XAI_BASE_URL,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.api_key = api_key or os.getenv(api_key_env)
        if not self.api_key:
            raise ValueError(f"Missing xAI API key. Set {api_key_env} or pass api_key.")
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def enrich(
        self,
        idea: Mapping[str, Any],
        *,
        free_facts: Mapping[str, Any] | None = None,
        as_of_date: str | None = None,
    ) -> Mapping[str, Any]:
        try:
            import httpx
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional environment
            raise RuntimeError("Install openai and httpx to use live xAI Grok enrichment.") from exc

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=httpx.Timeout(float(self.timeout_seconds)),
        )
        completion = client.chat.completions.create(
            model=self.model,
            messages=build_grok_research_messages(
                idea,
                free_facts=free_facts,
                as_of_date=as_of_date,
            ),
            response_format=_response_format_schema(),
        )
        content = completion.choices[0].message.content
        if not content:
            raise RuntimeError("xAI Grok returned an empty enrichment response.")
        return json.loads(content)


def enrich_idea_with_grok_research(
    idea: Mapping[str, Any],
    *,
    client: GrokResearchClient,
    free_facts: Mapping[str, Any] | None = None,
    as_of_date: str | None = None,
    allow_unsourced: bool = False,
) -> dict[str, Any]:
    """Enrich one idea with Grok catalyst research and packet-ready text."""
    payload = dict(idea)
    payload["symbol"] = _normalize_symbol(str(payload.get("symbol") or ""))
    raw = client.enrich(payload, free_facts=free_facts, as_of_date=as_of_date)
    normalized = normalize_grok_research_result(
        raw,
        idea=payload,
        free_facts=free_facts,
        as_of_date=as_of_date,
        allow_unsourced=allow_unsourced,
    )

    payload["grok_research_enrichment"] = normalized
    if normalized.get("business_summary"):
        payload["business_summary"] = normalized["business_summary"]
    thesis = _build_thesis_summary(normalized)
    if thesis:
        payload["thesis_summary"] = thesis
    payload["source_notes"] = _merge_notes(payload.get("source_notes"), _source_notes(normalized, free_facts))
    return payload


def enrich_ideas_with_grok_research(
    ideas: list[Mapping[str, Any]],
    *,
    client: GrokResearchClient,
    free_facts_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    as_of_date: str | None = None,
    allow_unsourced: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Enrich a batch of research ideas with symbol-keyed free facts."""
    facts = {_normalize_symbol(symbol): dict(value) for symbol, value in (free_facts_by_symbol or {}).items()}
    selected = ideas[:limit] if limit is not None else ideas
    return [
        enrich_idea_with_grok_research(
            idea,
            client=client,
            free_facts=facts.get(_normalize_symbol(str(idea.get("symbol") or "")), {}),
            as_of_date=as_of_date,
            allow_unsourced=allow_unsourced,
        )
        for idea in selected
    ]


def normalize_grok_research_result(
    raw: Mapping[str, Any],
    *,
    idea: Mapping[str, Any],
    free_facts: Mapping[str, Any] | None = None,
    as_of_date: str | None = None,
    allow_unsourced: bool = False,
) -> dict[str, Any]:
    """Normalize one Grok structured result into an auditable payload."""
    symbol = _normalize_symbol(str(raw.get("symbol") or idea.get("symbol") or ""))
    warnings = _string_list(raw.get("warnings"))
    source_urls = _dedupe(_string_list(raw.get("source_urls")))
    catalysts = [_normalize_catalyst(item) for item in _mapping_list(raw.get("thesis_relevant_catalysts"))]
    article_summaries = [
        _normalize_article_evidence_summary(item)
        for item in _mapping_list(raw.get("article_evidence_summaries"))
    ]
    catalyst_urls = [
        url
        for catalyst in catalysts
        for url in _string_list(catalyst.get("source_urls"))
    ]
    article_urls = [item["url"] for item in article_summaries if item.get("url")]
    source_urls = _dedupe([*source_urls, *catalyst_urls, *article_urls])
    if not source_urls and not allow_unsourced and "missing_source_urls" not in warnings:
        warnings.append("missing_source_urls")

    confidence = _bounded_float(raw.get("confidence"), default=0.0)
    if "missing_source_urls" in warnings:
        confidence = min(confidence, 0.5)

    scores = dict(raw.get("model_estimated_scores") or {})
    scores["basis"] = "model_estimate"

    return {
        "symbol": symbol,
        "company_name": str(raw.get("company_name") or idea.get("company_name") or ""),
        "as_of_date": str(raw.get("as_of_date") or as_of_date or date.today().isoformat()),
        "source_type": "grok_research_enrichment",
        "business_summary": str(raw.get("business_summary") or ""),
        "earnings_summary": dict(raw.get("earnings_summary") or {}),
        "article_evidence_summaries": article_summaries,
        "thesis_relevant_catalysts": catalysts,
        "bull_cases": _string_list(raw.get("bull_cases")),
        "bear_cases": _string_list(raw.get("bear_cases")),
        "thesis_watch_items": _string_list(raw.get("thesis_watch_items")),
        "risk_flags": _string_list(raw.get("risk_flags")),
        "financial_snapshot": dict(raw.get("financial_snapshot") or {}),
        "model_estimated_scores": scores,
        "source_urls": source_urls,
        "confidence": confidence,
        "warnings": _dedupe(warnings),
        "free_facts": dict(free_facts or {}),
    }


def build_grok_research_messages(
    idea: Mapping[str, Any],
    *,
    free_facts: Mapping[str, Any] | None = None,
    as_of_date: str | None = None,
) -> list[dict[str, str]]:
    """Build the source-backed Grok research prompt."""
    target_date = as_of_date or date.today().isoformat()
    system = (
        "You are a source-backed long-term equity research analyst for an autonomous "
        "research-first trading system. Do not claim to be Motley Fool, do not copy "
        "proprietary score names, and do not invent citations. Use public/current "
        "knowledge plus any Finnhub/free factual inputs supplied by the user. Return "
        "only valid JSON. Label all generated ratings with basis=model_estimate. "
        "If evidence is thin, lower confidence and add warnings."
    )
    user = {
        "task": "Create deeper catalyst and thesis enrichment for long-term stock research.",
        "as_of_date": target_date,
        "idea": dict(idea),
        "finnhub_or_free_facts": dict(free_facts or {}),
        "relevant_news": list(idea.get("relevant_news") or []),
        "latest_earnings_enrichment": dict(idea.get("latest_earnings_enrichment") or {}),
        "python_quality_growth_scorecard": dict(idea.get("quality_growth_scorecard") or {}),
        "news_instruction": (
            "If relevant_news is supplied, use only those article titles, URLs, "
            "dates, summaries, relevance scores, and impact categories when "
            "writing latest developments or news-backed catalysts. Also create "
            "article_evidence_summaries for the top primary-company articles. "
            "These summaries are snippet-grounded: summarize only the supplied "
            "title/summary/metadata unless full article text is explicitly provided."
        ),
        "scorecard_instruction": (
            "If python_quality_growth_scorecard is supplied, treat it as a "
            "deterministic Python model output, not as Motley Fool data. You may "
            "explain its implications but must not rename it as proprietary scores."
        ),
        "earnings_instruction": (
            "If latest_earnings_enrichment is supplied, use it as the primary "
            "earnings context and preserve its warnings/confidence boundaries."
        ),
        "required_output": {
            "symbol": "upper-case ticker",
            "company_name": "company name",
            "as_of_date": target_date,
            "business_summary": "plain-English durable business summary",
            "earnings_summary": {
                "quarter": "recent quarter if known",
                "summary": "concise earnings narrative",
                "key_takeaways": ["source-backed takeaway"],
            },
            "thesis_relevant_catalysts": [
                {
                    "name": "specific catalyst",
                    "direction": "positive/negative/mixed",
                    "time_horizon": "near_term/multi_year",
                    "evidence": "source-backed evidence",
                    "source_urls": ["https://..."],
                    "confidence": 0.0,
                }
            ],
            "article_evidence_summaries": [
                {
                    "title": "article title",
                    "url": "https://...",
                    "source": "publisher/source",
                    "date": "YYYY-MM-DD",
                    "summary": "snippet-grounded article summary",
                    "thesis_relevance": "why this matters to the long-term thesis",
                    "key_facts": ["fact from supplied snippet"],
                    "risk_flags": ["risk from supplied snippet"],
                    "confidence": 0.0,
                    "basis": "snippet_grounded",
                }
            ],
            "bull_cases": ["source-backed bull case"],
            "bear_cases": ["source-backed bear case"],
            "thesis_watch_items": ["what the agent should monitor"],
            "risk_flags": ["risk flag"],
            "financial_snapshot": {"metric": "value"},
            "model_estimated_scores": {
                "basis": "model_estimate",
                "quality": 0,
                "growth": 0,
                "valuation": 0,
                "safety": 0,
                "market_attention": 0,
            },
            "source_urls": ["https://..."],
            "confidence": 0.0,
            "warnings": [],
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, sort_keys=True)},
    ]


def _response_format_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "grok_research_enrichment",
            "strict": False,
            "schema": {
                "type": "object",
                "additionalProperties": True,
            },
        },
    }


def _build_thesis_summary(enrichment: Mapping[str, Any]) -> str:
    catalysts = _mapping_list(enrichment.get("thesis_relevant_catalysts"))
    catalyst_names = [str(item.get("name") or "").strip() for item in catalysts if item.get("name")]
    bull_cases = _string_list(enrichment.get("bull_cases"))
    bear_cases = _string_list(enrichment.get("bear_cases"))
    parts = []
    if catalyst_names:
        parts.append("Catalysts: " + "; ".join(catalyst_names[:4]) + ".")
    if bull_cases:
        parts.append("Bull case: " + "; ".join(bull_cases[:3]) + ".")
    if bear_cases:
        parts.append("Bear case: " + "; ".join(bear_cases[:3]) + ".")
    return " ".join(parts)


def _source_notes(enrichment: Mapping[str, Any], free_facts: Mapping[str, Any] | None) -> list[str]:
    notes = [
        (
            "Grok research enrichment: "
            f"confidence={enrichment.get('confidence')}, "
            f"sources={len(enrichment.get('source_urls') or [])}, "
            "scores_basis=model_estimate."
        )
    ]
    if free_facts:
        notes.append("Finnhub/free facts supplied to Grok research enrichment.")
    warnings = _string_list(enrichment.get("warnings"))
    if warnings:
        notes.append("Grok research enrichment warnings: " + ", ".join(warnings) + ".")
    return notes


def _normalize_catalyst(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(value.get("name") or ""),
        "direction": str(value.get("direction") or ""),
        "time_horizon": str(value.get("time_horizon") or ""),
        "evidence": str(value.get("evidence") or ""),
        "source_urls": _dedupe(_string_list(value.get("source_urls"))),
        "confidence": _bounded_float(value.get("confidence"), default=0.0),
    }


def _normalize_article_evidence_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": str(value.get("title") or ""),
        "url": str(value.get("url") or ""),
        "source": str(value.get("source") or ""),
        "date": str(value.get("date") or ""),
        "summary": str(value.get("summary") or ""),
        "thesis_relevance": str(value.get("thesis_relevance") or ""),
        "key_facts": _string_list(value.get("key_facts")),
        "risk_flags": _string_list(value.get("risk_flags")),
        "confidence": _bounded_float(value.get("confidence"), default=0.0),
        "basis": str(value.get("basis") or "snippet_grounded"),
    }


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _merge_notes(existing: Any, additions: list[str]) -> list[str]:
    return _dedupe([*_string_list(existing), *additions])


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _normalize_symbol(value: str) -> str:
    return value.strip().upper()


__all__ = [
    "DEFAULT_GROK_MODEL",
    "DEFAULT_XAI_BASE_URL",
    "FakeGrokResearchClient",
    "GrokResearchClient",
    "XaiGrokResearchClient",
    "build_grok_research_messages",
    "enrich_idea_with_grok_research",
    "enrich_ideas_with_grok_research",
    "normalize_grok_research_result",
]
