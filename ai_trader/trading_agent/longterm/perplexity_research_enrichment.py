"""Perplexity-backed research enrichment for long-term ideas.

This client intentionally emits the same structured payload as the Grok
research enrichment path so the broader evidence pipeline can swap providers
without changing downstream decision/review code.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping


DEFAULT_PERPLEXITY_MODEL = "sonar"
DEFAULT_PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_TIER_1_CREDIT_TARGET_USD = 50.0
DEFAULT_PERPLEXITY_MAX_TOKENS = 3500

_TOKEN_PRICING_PER_M = {
    "sonar": {"input": 1.0, "output": 1.0},
    "sonar-pro": {"input": 3.0, "output": 15.0},
    "sonar-reasoning-pro": {"input": 2.0, "output": 8.0},
    "sonar-deep-research": {"input": 2.0, "output": 8.0},
}

_REQUEST_PRICING = {
    "sonar": {"low": 0.005, "medium": 0.008, "high": 0.012},
    "sonar-pro": {"low": 0.006, "medium": 0.010, "high": 0.014},
    "sonar-reasoning-pro": {"low": 0.006, "medium": 0.010, "high": 0.014},
}


@dataclass
class PerplexityResearchClient:
    """Perplexity Sonar client compatible with GrokResearchClient."""

    api_key: str | None = None
    api_key_env: str = "PERPLEXITY_API_KEY"
    model: str = DEFAULT_PERPLEXITY_MODEL
    api_url: str = DEFAULT_PERPLEXITY_API_URL
    timeout_seconds: float = 120.0
    max_tokens: int = DEFAULT_PERPLEXITY_MAX_TOKENS
    search_context_size: str = "low"
    credits_purchased_to_date_usd: float | None = None
    tier_1_credit_target_usd: float = DEFAULT_TIER_1_CREDIT_TARGET_USD

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv(self.api_key_env)
        if not self.api_key:
            raise ValueError(f"Missing Perplexity API key. Set {self.api_key_env} or pass api_key.")
        self.api_key = self.api_key.strip()
        if self.credits_purchased_to_date_usd is None:
            self.credits_purchased_to_date_usd = _float_env("PERPLEXITY_CREDITS_PURCHASED_USD")
        self.usage_calls: list[dict[str, Any]] = []

    def enrich(
        self,
        idea: Mapping[str, Any],
        *,
        free_facts: Mapping[str, Any] | None = None,
        as_of_date: str | None = None,
    ) -> Mapping[str, Any]:
        """Return structured long-term enrichment for one idea."""
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on optional environment
            raise RuntimeError("Install requests to use Perplexity research enrichment.") from exc

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": build_perplexity_research_messages(
                    idea,
                    free_facts=free_facts,
                    as_of_date=as_of_date,
                ),
                "max_tokens": self.max_tokens,
                "temperature": 0.1,
                "web_search_options": {"search_context_size": self.search_context_size},
            },
            timeout=float(self.timeout_seconds),
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            status_code = getattr(response, "status_code", None)
            if status_code in {401, 403}:
                raise RuntimeError(
                    "Perplexity authentication failed. Confirm PERPLEXITY_API_KEY is a valid "
                    "API auth token for an API group, typically shaped like 'pplx-...', and "
                    "that the key has not been revoked. The key value was not logged."
                ) from exc
            raise
        payload = response.json()
        self._record_usage(payload)
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not content:
            raise RuntimeError("Perplexity returned an empty research enrichment response.")
        citations = _string_list(payload.get("citations"))
        search_summaries = _search_result_summaries(payload)
        try:
            parsed = _extract_json_object(content)
        except (json.JSONDecodeError, ValueError) as exc:
            parsed = _fallback_enrichment_from_response(
                idea,
                content=content,
                citations=citations,
                search_summaries=search_summaries,
                as_of_date=as_of_date,
                error=exc,
            )
        _sanitize_source_urls(parsed)
        if citations and not parsed.get("source_urls"):
            parsed["source_urls"] = citations
        if search_summaries and not parsed.get("article_evidence_summaries"):
            parsed["article_evidence_summaries"] = search_summaries
        parsed.setdefault("source_type", "perplexity_research_enrichment")
        return parsed

    def _record_usage(self, payload: Mapping[str, Any]) -> None:
        usage = dict(payload.get("usage") or {})
        prompt_tokens = _int_value(usage.get("prompt_tokens"))
        completion_tokens = _int_value(usage.get("completion_tokens"))
        if completion_tokens == 0:
            completion_tokens = _int_value(usage.get("output_tokens"))
        total_tokens = _int_value(usage.get("total_tokens"))
        if total_tokens and not prompt_tokens and not completion_tokens:
            prompt_tokens = total_tokens

        pricing = _TOKEN_PRICING_PER_M.get(self.model, _TOKEN_PRICING_PER_M["sonar"])
        request_fee = _REQUEST_PRICING.get(self.model, {}).get(self.search_context_size, 0.0)
        input_cost = prompt_tokens / 1_000_000 * pricing["input"]
        output_cost = completion_tokens / 1_000_000 * pricing["output"]
        total_cost = request_fee + input_cost + output_cost
        self.usage_calls.append(
            {
                "provider": "perplexity",
                "model": self.model,
                "search_context_size": self.search_context_size,
                "request_fee_usd": round(request_fee, 6),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens or prompt_tokens + completion_tokens,
                "input_cost_usd": round(input_cost, 6),
                "output_cost_usd": round(output_cost, 6),
                "estimated_total_cost_usd": round(total_cost, 6),
            }
        )

    def usage_summary(self) -> dict[str, Any]:
        """Return estimated Perplexity spend for this client instance."""
        request_count = len(self.usage_calls)
        request_fees = sum(float(call["request_fee_usd"]) for call in self.usage_calls)
        input_cost = sum(float(call["input_cost_usd"]) for call in self.usage_calls)
        output_cost = sum(float(call["output_cost_usd"]) for call in self.usage_calls)
        total_cost = sum(float(call["estimated_total_cost_usd"]) for call in self.usage_calls)
        credits_to_date = float(self.credits_purchased_to_date_usd or 0.0)
        estimated_progress = credits_to_date + total_cost
        remaining = max(0.0, float(self.tier_1_credit_target_usd) - estimated_progress)
        return {
            "provider": "perplexity",
            "model": self.model,
            "search_context_size": self.search_context_size,
            "request_count": request_count,
            "prompt_tokens": sum(int(call["prompt_tokens"]) for call in self.usage_calls),
            "completion_tokens": sum(int(call["completion_tokens"]) for call in self.usage_calls),
            "total_tokens": sum(int(call["total_tokens"]) for call in self.usage_calls),
            "request_fees_usd": round(request_fees, 6),
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "estimated_total_cost_usd": round(total_cost, 6),
            "credits_purchased_to_date_usd": round(credits_to_date, 2),
            "tier_1_credit_target_usd": round(float(self.tier_1_credit_target_usd), 2),
            "estimated_progress_to_tier_1_usd": round(estimated_progress, 2),
            "estimated_remaining_to_tier_1_usd": round(remaining, 2),
            "console_check_required": True,
            "tier_note": (
                "Perplexity tiers are confirmed in the API console and may be based "
                "on cumulative credits purchased, not only this run's consumed spend."
            ),
        }


def build_perplexity_research_messages(
    idea: Mapping[str, Any],
    *,
    free_facts: Mapping[str, Any] | None = None,
    as_of_date: str | None = None,
) -> list[dict[str, str]]:
    """Build a cost-conscious, source-backed Perplexity enrichment prompt."""
    target_date = as_of_date or date.today().isoformat()
    system = (
        "You are a source-backed long-term equity research analyst. Use web search "
        "and the provided facts to produce concise, cited research enrichment. "
        "Do not invent numbers. Do not claim to be Motley Fool. Return valid JSON only. "
        "Keep the JSON compact: At most 3 catalysts, At most 2 article summaries, "
        "At most 3 bull cases, At most 3 bear cases, and Keep every string under 220 characters."
    )
    user = {
        "task": "Create long-term catalyst, earnings, and thesis enrichment for a stock.",
        "as_of_date": target_date,
        "idea": dict(idea),
        "free_facts": dict(free_facts or {}),
        "relevant_news": list(idea.get("relevant_news") or []),
        "latest_earnings_enrichment": dict(idea.get("latest_earnings_enrichment") or {}),
        "python_quality_growth_scorecard": dict(idea.get("quality_growth_scorecard") or {}),
        "instructions": [
            "Prefer primary/company, SEC, earnings, and reputable financial-news sources.",
            "Use supplied relevant_news snippets when present instead of broad noisy headlines.",
            "If evidence is thin, lower confidence and add warnings.",
            "Label generated ratings as basis=model_estimate.",
            "Use source URLs for catalysts and article summaries whenever available.",
            "At most 3 catalysts.",
            "At most 2 article summaries.",
            "Keep every string under 220 characters.",
        ],
        "required_output": {
            "source_type": "perplexity_research_enrichment",
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
                    "summary": "article/snippet-grounded summary",
                    "thesis_relevance": "why this matters to the long-term thesis",
                    "key_facts": ["fact from source"],
                    "risk_flags": ["risk from source"],
                    "confidence": 0.0,
                    "basis": "source_grounded",
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


def _extract_json_object(content: str) -> dict[str, Any]:
    cleaned = content.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Perplexity research enrichment must return a JSON object.")
    return parsed


def _search_result_summaries(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    summaries = []
    for item in payload.get("search_results") or []:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url and not title:
            continue
        summaries.append(
            {
                "title": title or url,
                "url": url,
                "source": str(item.get("source") or "web").strip(),
                "date": str(item.get("date") or item.get("last_updated") or "").strip(),
                "summary": str(item.get("snippet") or "").strip(),
                "thesis_relevance": "Search result used by Perplexity for this enrichment.",
                "key_facts": [],
                "risk_flags": [],
                "confidence": 0.5,
                "basis": "perplexity_search_result",
            }
        )
    return summaries


def _fallback_enrichment_from_response(
    idea: Mapping[str, Any],
    *,
    content: str,
    citations: list[str],
    search_summaries: list[dict[str, Any]],
    as_of_date: str | None,
    error: Exception,
) -> dict[str, Any]:
    symbol = str(idea.get("symbol") or "").strip().upper()
    source_urls = _dedupe(_valid_urls([*citations, *[item.get("url", "") for item in search_summaries]]))
    article_summaries = search_summaries[:2]
    content_excerpt = _compact_text(content, limit=500)
    warnings = ["perplexity_malformed_json_fallback"]
    return {
        "source_type": "perplexity_research_enrichment",
        "symbol": symbol,
        "company_name": str(idea.get("company_name") or idea.get("company") or symbol),
        "as_of_date": str(as_of_date or date.today().isoformat()),
        "business_summary": _compact_text(str(idea.get("business_summary") or content_excerpt), limit=320),
        "earnings_summary": {
            "quarter": "latest available",
            "summary": content_excerpt,
            "key_takeaways": [
                _compact_text(item.get("summary") or item.get("title") or "", limit=180)
                for item in article_summaries[:2]
                if item.get("summary") or item.get("title")
            ],
        },
        "thesis_relevant_catalysts": [],
        "article_evidence_summaries": article_summaries,
        "bull_cases": [],
        "bear_cases": [],
        "thesis_watch_items": ["Retry Perplexity enrichment or escalate to Grok for complete structured synthesis."],
        "risk_flags": ["research_model_parse_failure"],
        "financial_snapshot": {},
        "model_estimated_scores": {
            "basis": "model_estimate",
            "quality": 0,
            "growth": 0,
            "valuation": 0,
            "safety": 0,
            "market_attention": 0,
        },
        "source_urls": source_urls,
        "confidence": 0.35 if source_urls else 0.2,
        "warnings": warnings,
        "malformed_json_error": str(error),
    }


def _sanitize_source_urls(payload: dict[str, Any]) -> None:
    payload["source_urls"] = _valid_urls(payload.get("source_urls"))
    for key in ("thesis_relevant_catalysts", "article_evidence_summaries"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if isinstance(item, dict):
                item["source_urls"] = _valid_urls(item.get("source_urls"))
                if key == "article_evidence_summaries":
                    url = str(item.get("url") or "").strip()
                    if not _is_valid_url(url):
                        item["url"] = ""


def _valid_urls(value: Any) -> list[str]:
    return [url for url in _string_list(value) if _is_valid_url(url)]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _compact_text(value: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _is_valid_url(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith(("http://", "https://")) and "..." not in text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_env(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_PERPLEXITY_API_URL",
    "DEFAULT_PERPLEXITY_MODEL",
    "PerplexityResearchClient",
    "build_perplexity_research_messages",
]
