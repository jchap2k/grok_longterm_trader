"""OpenRouter-backed source text summarization for long-term evidence.

This module is intentionally provider-adapter only. It summarizes supplied text
into auditable evidence rows and does not search the web, make trade decisions,
or wire itself into scheduler execution.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any, Mapping


DEFAULT_OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_TEXT_SUMMARY_MODEL = "xiaomi/mimo-v2-flash"
DEFAULT_OPENROUTER_TEXT_SUMMARY_MAX_TOKENS = 1200


@dataclass
class OpenRouterTextSummaryClient:
    """Summarize supplied article/page text through an OpenRouter chat model."""

    api_key: str | None = None
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str = DEFAULT_OPENROUTER_TEXT_SUMMARY_MODEL
    api_url: str = DEFAULT_OPENROUTER_API_URL
    timeout_seconds: float = 60.0
    max_tokens: int = DEFAULT_OPENROUTER_TEXT_SUMMARY_MAX_TOKENS

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv(self.api_key_env)
        if not self.api_key:
            raise ValueError(f"Missing OpenRouter API key. Set {self.api_key_env} or pass api_key.")
        self.api_key = self.api_key.strip()
        self.usage_calls: list[dict[str, Any]] = []

    def summarize(
        self,
        source: Mapping[str, Any],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Return normalized summary evidence for one supplied text source."""
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on optional environment
            raise RuntimeError("Install requests to use OpenRouter text summarization.") from exc

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": build_openrouter_text_summary_messages(
                    source,
                    as_of_date=as_of_date,
                ),
                "response_format": {"type": "json_object"},
                "max_tokens": int(self.max_tokens),
                "temperature": 0.0,
            },
            timeout=float(self.timeout_seconds),
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            status_code = getattr(response, "status_code", None)
            if status_code in {401, 403}:
                raise RuntimeError(
                    "OpenRouter authentication failed. Confirm OPENROUTER_API_KEY is valid "
                    "and that model access/privacy settings allow this request. The key value "
                    "was not logged."
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
            return _fallback_summary_from_source(
                source,
                model=self.model,
                as_of_date=as_of_date,
                warning="openrouter_empty_response_fallback",
            )
        try:
            parsed = _extract_json_object(content)
        except (json.JSONDecodeError, ValueError) as exc:
            result = _fallback_summary_from_source(
                source,
                model=self.model,
                as_of_date=as_of_date,
                warning="openrouter_malformed_json_fallback",
            )
            result["malformed_json_error"] = str(exc)
            return result
        return normalize_openrouter_text_summary(
            parsed,
            source=source,
            model=self.model,
            as_of_date=as_of_date,
        )

    def synthesize_summaries(
        self,
        source: Mapping[str, Any],
        *,
        primary_summary: Mapping[str, Any],
        comparison_summary: Mapping[str, Any],
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize two independent source-text summaries into review evidence."""
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on optional environment
            raise RuntimeError("Install requests to use OpenRouter summary synthesis.") from exc

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": build_openrouter_summary_synthesis_messages(
                    source,
                    primary_summary=primary_summary,
                    comparison_summary=comparison_summary,
                    as_of_date=as_of_date,
                ),
                "response_format": {"type": "json_object"},
                "max_tokens": int(self.max_tokens),
                "temperature": 0.0,
            },
            timeout=float(self.timeout_seconds),
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            status_code = getattr(response, "status_code", None)
            if status_code in {401, 403}:
                raise RuntimeError(
                    "OpenRouter authentication failed. Confirm OPENROUTER_API_KEY is valid "
                    "and that model access/privacy settings allow this request. The key value "
                    "was not logged."
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
        try:
            parsed = _extract_json_object(content)
        except (json.JSONDecodeError, ValueError) as exc:
            return {
                "source_type": "openrouter_summary_synthesis",
                "provider": "openrouter",
                "model": self.model,
                "symbol": _normalize_symbol(source.get("symbol")),
                "as_of_date": str(as_of_date or date.today().isoformat()),
                "executive_summary": _compact_text(
                    str(primary_summary.get("summary") or comparison_summary.get("summary") or ""),
                    limit=320,
                ),
                "consensus_facts": [],
                "disagreement_notes": ["Synthesis model returned malformed JSON."],
                "strongest_risk_flags": [],
                "confidence": 0.2,
                "human_review_required": True,
                "warnings": ["openrouter_synthesis_malformed_json_fallback"],
                "malformed_json_error": str(exc),
            }
        return normalize_openrouter_summary_synthesis(
            parsed,
            source=source,
            model=self.model,
            as_of_date=as_of_date,
        )

    def _record_usage(self, payload: Mapping[str, Any]) -> None:
        usage = dict(payload.get("usage") or {})
        prompt_tokens = _int_value(usage.get("prompt_tokens"))
        completion_tokens = _int_value(usage.get("completion_tokens"))
        if completion_tokens == 0:
            completion_tokens = _int_value(usage.get("output_tokens"))
        total_tokens = _int_value(usage.get("total_tokens")) or prompt_tokens + completion_tokens
        cost = _float_value(usage.get("cost"))
        self.usage_calls.append(
            {
                "provider": "openrouter",
                "model": self.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated_total_cost_usd": round(cost, 6),
            }
        )

    def usage_summary(self) -> dict[str, Any]:
        """Return provider-reported usage for this client instance."""
        total_cost = sum(float(call["estimated_total_cost_usd"]) for call in self.usage_calls)
        return {
            "provider": "openrouter",
            "model": self.model,
            "request_count": len(self.usage_calls),
            "prompt_tokens": sum(int(call["prompt_tokens"]) for call in self.usage_calls),
            "completion_tokens": sum(int(call["completion_tokens"]) for call in self.usage_calls),
            "total_tokens": sum(int(call["total_tokens"]) for call in self.usage_calls),
            "estimated_total_cost_usd": round(total_cost, 6),
        }


def build_openrouter_text_summary_messages(
    source: Mapping[str, Any],
    *,
    as_of_date: str | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for snippet/page-text-only evidence normalization."""
    target_date = as_of_date or date.today().isoformat()
    symbol = _normalize_symbol(source.get("symbol"))
    title = str(source.get("title") or source.get("source_title") or "").strip()
    url = str(source.get("url") or source.get("source_url") or "").strip()
    text = _compact_text(str(source.get("text") or source.get("page_text") or source.get("snippet") or ""), limit=12000)
    system = (
        "You summarize only the supplied source text for a long-term equity research "
        "evidence pipeline. Do not browse. Do not infer facts absent from the text. "
        "Do not make buy, sell, hold, add, reduce, rebalance, or sizing recommendations. "
        "Return valid compact JSON only."
    )
    user = {
        "task": "Normalize supplied webpage/article text into structured long-term evidence.",
        "as_of_date": target_date,
        "source": {
            "symbol": symbol,
            "source_title": title,
            "source_url": url,
            "publisher": str(source.get("publisher") or source.get("source") or "").strip(),
            "published_at": str(source.get("published_at") or source.get("date") or "").strip(),
            "search_query": str(source.get("search_query") or "").strip(),
            "search_rank": source.get("search_rank"),
            "text": text,
        },
        "instructions": [
            "Summarize only the supplied source text.",
            "Use basis=snippet_grounded for article_evidence_summaries.",
            "If the text is thin, add warnings and lower confidence.",
            "Keep every string under 220 characters.",
            "Use catalyst tags only when supported by the text.",
        ],
        "required_output": {
            "symbol": symbol,
            "source_title": title,
            "source_url": url,
            "summary": "concise summary grounded only in the supplied text",
            "thesis_relevance": "high/medium/low/unknown",
            "catalyst_tags": ["tag supported by source text"],
            "quality_growth_notes": "durability/growth evidence from source text",
            "valuation_cautions": "valuation or margin-of-safety caveats from source text",
            "article_evidence_summaries": [
                {
                    "title": title or "source title",
                    "url": url,
                    "summary": "snippet-grounded source summary",
                    "thesis_relevance": "why this matters",
                    "key_facts": ["fact from supplied text"],
                    "risk_flags": ["risk from supplied text"],
                    "confidence": 0.0,
                    "basis": "snippet_grounded",
                }
            ],
            "warnings": [],
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, sort_keys=True)},
    ]


def build_openrouter_summary_synthesis_messages(
    source: Mapping[str, Any],
    *,
    primary_summary: Mapping[str, Any],
    comparison_summary: Mapping[str, Any],
    as_of_date: str | None = None,
) -> list[dict[str, str]]:
    """Build a prompt that compares two summary outputs without rereading source text."""
    target_date = as_of_date or date.today().isoformat()
    system = (
        "You are a source-evidence synthesis reviewer for a long-term equity research "
        "pipeline. Compare two independent structured summaries of the same supplied "
        "source. Do not browse. Do not re-summarize the original article text. Do not "
        "invent facts that neither summary contains. Do not make buy, sell, hold, add, "
        "reduce, rebalance, or sizing recommendations. Return valid compact JSON only."
    )
    user = {
        "task": "Compare two independent structured summaries and produce one executive evidence summary.",
        "as_of_date": target_date,
        "source": {
            "symbol": _normalize_symbol(source.get("symbol")),
            "source_title": str(source.get("title") or source.get("source_title") or "").strip(),
            "source_url": str(source.get("url") or source.get("source_url") or "").strip(),
        },
        "primary_summary": dict(primary_summary),
        "comparison_summary": dict(comparison_summary),
        "instructions": [
            "List facts both summaries support as consensus_facts.",
            "List material differences, omissions, or confidence conflicts as disagreement_notes.",
            "Preserve the strongest risk/caveat flags from either summary.",
            "Set human_review_required=true for material disagreement, thin/unofficial sources, or low confidence.",
            "Keep every string under 220 characters.",
        ],
        "required_output": {
            "symbol": "upper-case ticker",
            "executive_summary": "final concise evidence summary",
            "consensus_facts": ["fact supported by both summaries"],
            "disagreement_notes": ["material difference or omission"],
            "strongest_risk_flags": ["risk/caveat from either summary"],
            "confidence": 0.0,
            "human_review_required": False,
            "warnings": [],
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, sort_keys=True)},
    ]


def normalize_openrouter_text_summary(
    raw: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    model: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Normalize model JSON into the local evidence-summary contract."""
    symbol = _normalize_symbol(raw.get("symbol") or source.get("symbol"))
    source_title = str(raw.get("source_title") or source.get("title") or source.get("source_title") or "").strip()
    source_url = str(raw.get("source_url") or source.get("url") or source.get("source_url") or "").strip()
    article_summaries = [
        _normalize_article_evidence_summary(item, default_title=source_title, default_url=source_url)
        for item in _mapping_list(raw.get("article_evidence_summaries"))
    ]
    if not article_summaries:
        article_summaries = [
            _fallback_article_summary(
                title=source_title,
                url=source_url,
                text=str(raw.get("summary") or source.get("text") or source.get("page_text") or source.get("snippet") or ""),
                basis="snippet_grounded_fallback",
            )
        ]
    source_urls = _dedupe(_valid_urls([source_url, *[item.get("url", "") for item in article_summaries]]))
    return {
        "source_type": "openrouter_text_summary",
        "provider": "openrouter",
        "model": model,
        "symbol": symbol,
        "as_of_date": str(raw.get("as_of_date") or as_of_date or date.today().isoformat()),
        "source_title": source_title,
        "source_url": source_url,
        "source_urls": source_urls,
        "summary": str(raw.get("summary") or "").strip(),
        "thesis_relevance": _normalize_relevance(raw.get("thesis_relevance")),
        "catalyst_tags": _string_list(raw.get("catalyst_tags")),
        "quality_growth_notes": str(raw.get("quality_growth_notes") or "").strip(),
        "valuation_cautions": str(raw.get("valuation_cautions") or "").strip(),
        "article_evidence_summaries": article_summaries,
        "warnings": _dedupe(_string_list(raw.get("warnings"))),
    }


def normalize_openrouter_summary_synthesis(
    raw: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    model: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Normalize a two-summary synthesis payload."""
    return {
        "source_type": "openrouter_summary_synthesis",
        "provider": "openrouter",
        "model": model,
        "symbol": _normalize_symbol(raw.get("symbol") or source.get("symbol")),
        "as_of_date": str(raw.get("as_of_date") or as_of_date or date.today().isoformat()),
        "source_title": str(source.get("title") or source.get("source_title") or "").strip(),
        "source_url": str(source.get("url") or source.get("source_url") or "").strip(),
        "source_urls": _valid_urls([source.get("url") or source.get("source_url") or ""]),
        "executive_summary": str(raw.get("executive_summary") or "").strip(),
        "consensus_facts": _string_list(raw.get("consensus_facts")),
        "disagreement_notes": _string_list(raw.get("disagreement_notes")),
        "strongest_risk_flags": _string_list(raw.get("strongest_risk_flags")),
        "confidence": _bounded_float(raw.get("confidence"), default=0.0),
        "human_review_required": _bool_value(raw.get("human_review_required")),
        "warnings": _dedupe(_string_list(raw.get("warnings"))),
    }


def summarize_with_dual_openrouter_models(
    source: Mapping[str, Any],
    *,
    primary_client: Any,
    comparison_client: Any,
    synth_client: Any,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Run primary summary, comparison summary, and compact synthesis."""
    stages = []
    start_total = perf_counter()

    primary_result, primary_elapsed = _timed_call(
        lambda: primary_client.summarize(source, as_of_date=as_of_date)
    )
    stages.append(
        _stage_record(
            "primary_summary",
            model=getattr(primary_client, "model", ""),
            elapsed_seconds=primary_elapsed,
            result=primary_result,
            usage=primary_client.usage_summary(),
        )
    )

    comparison_result, comparison_elapsed = _timed_call(
        lambda: comparison_client.summarize(source, as_of_date=as_of_date)
    )
    stages.append(
        _stage_record(
            "comparison_summary",
            model=getattr(comparison_client, "model", ""),
            elapsed_seconds=comparison_elapsed,
            result=comparison_result,
            usage=comparison_client.usage_summary(),
        )
    )

    synthesis_result, synthesis_elapsed = _timed_call(
        lambda: synth_client.synthesize_summaries(
            source,
            primary_summary=primary_result,
            comparison_summary=comparison_result,
            as_of_date=as_of_date,
        )
    )
    stages.append(
        _stage_record(
            "synthesis",
            model=getattr(synth_client, "model", ""),
            elapsed_seconds=synthesis_elapsed,
            result=synthesis_result,
            usage=synth_client.usage_summary(),
        )
    )
    return {
        "source_type": "openrouter_dual_summary_synthesis_eval",
        "provider": "openrouter",
        "symbol": _normalize_symbol(source.get("symbol")),
        "as_of_date": str(as_of_date or date.today().isoformat()),
        "source": {
            "title": str(source.get("title") or source.get("source_title") or "").strip(),
            "url": str(source.get("url") or source.get("source_url") or "").strip(),
        },
        "stages": stages,
        "final_synthesis": synthesis_result,
        "totals": _usage_totals(stages) | {
            "elapsed_seconds": round(perf_counter() - start_total, 3),
        },
        "order_submission_enabled": False,
    }


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
        raise ValueError("OpenRouter text summary must return a JSON object.")
    return parsed


def _fallback_summary_from_source(
    source: Mapping[str, Any],
    *,
    model: str,
    as_of_date: str | None,
    warning: str,
) -> dict[str, Any]:
    source_title = str(source.get("title") or source.get("source_title") or "").strip()
    source_url = str(source.get("url") or source.get("source_url") or "").strip()
    text = str(source.get("text") or source.get("page_text") or source.get("snippet") or "")
    summary = _compact_text(text, limit=320)
    article = _fallback_article_summary(
        title=source_title,
        url=source_url,
        text=text,
        basis="snippet_grounded_fallback",
    )
    return {
        "source_type": "openrouter_text_summary",
        "provider": "openrouter",
        "model": model,
        "symbol": _normalize_symbol(source.get("symbol")),
        "as_of_date": str(as_of_date or date.today().isoformat()),
        "source_title": source_title,
        "source_url": source_url,
        "source_urls": _valid_urls([source_url]),
        "summary": summary,
        "thesis_relevance": "unknown",
        "catalyst_tags": [],
        "quality_growth_notes": "",
        "valuation_cautions": "",
        "article_evidence_summaries": [article],
        "warnings": [warning],
    }


def _normalize_article_evidence_summary(
    value: Mapping[str, Any],
    *,
    default_title: str,
    default_url: str,
) -> dict[str, Any]:
    basis = str(value.get("basis") or "snippet_grounded").strip()
    if basis == "source_text":
        basis = "snippet_grounded"
    url = str(value.get("url") or default_url).strip()
    if url and not _is_valid_url(url):
        url = ""
    return {
        "title": str(value.get("title") or default_title).strip(),
        "url": url,
        "source": str(value.get("source") or "").strip(),
        "date": str(value.get("date") or "").strip(),
        "summary": str(value.get("summary") or "").strip(),
        "thesis_relevance": str(value.get("thesis_relevance") or "").strip(),
        "key_facts": _string_list(value.get("key_facts")),
        "risk_flags": _string_list(value.get("risk_flags")),
        "confidence": _bounded_float(value.get("confidence"), default=0.0),
        "basis": basis or "snippet_grounded",
    }


def _fallback_article_summary(
    *,
    title: str,
    url: str,
    text: str,
    basis: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "url": url if _is_valid_url(url) else "",
        "source": "",
        "date": "",
        "summary": _compact_text(text, limit=220),
        "thesis_relevance": "Requires review; generated from supplied text fallback.",
        "key_facts": [],
        "risk_flags": [],
        "confidence": 0.25,
        "basis": basis,
    }


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_relevance(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"high", "medium", "low", "unknown"} else "unknown"


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _timed_call(callable_obj: Any) -> tuple[Any, float]:
    start = perf_counter()
    result = callable_obj()
    return result, round(perf_counter() - start, 3)


def _stage_record(
    stage: str,
    *,
    model: str,
    elapsed_seconds: float,
    result: Mapping[str, Any],
    usage: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "model": model,
        "elapsed_seconds": elapsed_seconds,
        "usage": dict(usage),
        "result": dict(result),
    }


def _usage_totals(stages: list[Mapping[str, Any]]) -> dict[str, Any]:
    usages = [dict(stage.get("usage") or {}) for stage in stages]
    return {
        "estimated_total_cost_usd": round(
            sum(float(usage.get("estimated_total_cost_usd") or 0.0) for usage in usages),
            6,
        ),
        "prompt_tokens": sum(_int_value(usage.get("prompt_tokens")) for usage in usages),
        "completion_tokens": sum(_int_value(usage.get("completion_tokens")) for usage in usages),
        "total_tokens": sum(_int_value(usage.get("total_tokens")) for usage in usages),
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _valid_urls(values: list[Any]) -> list[str]:
    return [str(value).strip() for value in values if _is_valid_url(str(value).strip())]


def _is_valid_url(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith(("http://", "https://")) and "..." not in text


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


def _compact_text(value: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


__all__ = [
    "DEFAULT_OPENROUTER_API_URL",
    "DEFAULT_OPENROUTER_TEXT_SUMMARY_MODEL",
    "OpenRouterTextSummaryClient",
    "build_openrouter_summary_synthesis_messages",
    "build_openrouter_text_summary_messages",
    "normalize_openrouter_summary_synthesis",
    "normalize_openrouter_text_summary",
    "summarize_with_dual_openrouter_models",
]
