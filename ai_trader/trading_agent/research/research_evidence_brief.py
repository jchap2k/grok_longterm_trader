"""Compact evidence brief builder for enriched long-term research ideas."""

from __future__ import annotations

from typing import Any, Mapping


EVIDENCE_BRIEF_VERSION = "research_evidence_brief_v1"
DEFAULT_MAX_CHARS = 4000
DEFAULT_MAX_NEWS_ITEMS = 3
DEFAULT_MAX_LIST_ITEMS = 3

TEMPLATE = """\
research_evidence_brief_v1 | SYMBOL
Fundamentals: factual provider metrics aligned to quality, growth, valuation, and balance-sheet rules.
Scorecard: deterministic non-Fool quality-growth scorecard, not proprietary Fool data.
Latest earnings: source-filtered earnings context with confidence and thesis developments.
Primary news: primary-company articles only, with source, impact, relevance, and subject score.
Article evidence: snippet-grounded Grok summaries of the primary articles when present.
Grok catalyst synthesis: source-backed catalyst narrative when present, labeled separately.
Warnings: thin coverage, provider gaps, valuation/safety concerns, and model/source caveats.
"""


def build_research_evidence_brief(
    idea: Mapping[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_news_items: int = DEFAULT_MAX_NEWS_ITEMS,
) -> str:
    """Build a concise, versioned evidence brief from transient enrichment fields.

    The brief is research context only. It must not alter ranking, sizing,
    preview eligibility, broker behavior, or durable decision history. Keep the
    section order stable so reviewers and LLM committee prompts can learn the
    shape without depending on provider-specific raw blobs.
    """
    symbol = str(idea.get("symbol") or "").upper()
    sections: list[str] = []

    fundamentals = _fundamentals_section(idea.get("fundamental_metrics"))
    if fundamentals:
        sections.append(f"Fundamentals: {fundamentals}")

    scorecard = _scorecard_section(idea.get("quality_growth_scorecard"))
    if scorecard:
        sections.append(f"Scorecard: {scorecard}")

    earnings = _earnings_section(idea.get("latest_earnings_enrichment"))
    if earnings:
        sections.append(f"Latest earnings: {earnings}")

    news = _news_section(idea.get("relevant_news"), max_items=max_news_items)
    if news:
        sections.append(f"Primary news: {news}")

    grok = _grok_section(idea.get("grok_research_enrichment"))
    article_evidence = _article_evidence_section(idea.get("grok_research_enrichment"))
    if article_evidence:
        sections.append(f"Article evidence: {article_evidence}")
    if grok:
        sections.append(f"Grok catalyst synthesis: {grok}")

    warnings = _warnings_section(idea)
    if warnings:
        sections.append(f"Warnings: {warnings}")

    if not sections:
        return ""

    header = f"{EVIDENCE_BRIEF_VERSION} | {symbol or 'UNKNOWN'}"
    return _truncate("\n".join([header, *sections]), max_chars)


def _fundamentals_section(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    growth = _mapping(value.get("revenue_growth_cagr"))
    valuation = _mapping(value.get("valuation_ttm"))
    profitability = _mapping(value.get("profitability_ttm"))
    financials = _mapping(value.get("financials_ttm"))
    parts = [
        _metric("3yr revenue growth", growth.get("3_yr_revenue_growth")),
        _metric("3yr EBITDA growth", growth.get("3_yr_ebitda_growth")),
        _metric("P/E", valuation.get("price_earnings")),
        _metric("EV/EBITDA", valuation.get("ev_ebitda")),
        _metric("gross margin", profitability.get("gross_margin")),
        _metric("operating margin", profitability.get("operating_margin")),
        _metric("debt/equity", profitability.get("debt_equity")),
        _metric("TTM revenue", financials.get("revenue")),
        _metric("TTM FCF", financials.get("free_cash_flow")),
    ]
    return _join_parts(parts)


def _scorecard_section(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    parts = [
        _metric("super", value.get("superscore")),
        _metric("type", value.get("investing_type")),
        _metric("quality", value.get("quality_score")),
        _metric("growth", value.get("growth_score")),
        _metric("valuation", value.get("valuation_score")),
        _metric("safety", value.get("safety_score")),
        _metric("drawdown", value.get("estimated_drawdown_band")),
    ]
    reasons = _list_items(value.get("score_reasons"), limit=DEFAULT_MAX_LIST_ITEMS)
    if reasons:
        parts.append(f"reasons {', '.join(reasons)}")
    return _join_parts(parts)


def _earnings_section(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    parts = [
        _metric("quarter", value.get("quarter")),
        _metric("confidence", value.get("confidence")),
        _metric("summary", value.get("summary")),
    ]
    takeaways = _list_items(value.get("key_financial_takeaways"), limit=DEFAULT_MAX_LIST_ITEMS)
    positives = _list_items(value.get("thesis_positive_developments"), limit=2)
    negatives = _list_items(value.get("thesis_negative_developments"), limit=2)
    if takeaways:
        parts.append(f"takeaways {', '.join(takeaways)}")
    if positives:
        parts.append(f"positive {', '.join(positives)}")
    if negatives:
        parts.append(f"negative {', '.join(negatives)}")
    return _join_parts(parts)


def _news_section(value: Any, *, max_items: int) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value[: max(0, max_items)]:
        if not isinstance(item, Mapping):
            continue
        title = _clean(item.get("title"))
        if not title:
            continue
        meta = _join_parts(
            [
                _metric("date", item.get("date")),
                _metric("source", item.get("source")),
                _metric("impact", item.get("impact_category")),
                _metric("relevance", item.get("relevance_score")),
                _metric("subject", item.get("primary_subject_score")),
            ],
            separator=", ",
        )
        parts.append(f"{title} ({meta})" if meta else title)
    return " | ".join(parts)


def _grok_section(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    parts = [_metric("confidence", value.get("confidence"))]
    catalysts = _list_items(value.get("thesis_relevant_catalysts"), limit=DEFAULT_MAX_LIST_ITEMS)
    bull = _list_items(value.get("bull_cases"), limit=2)
    bear = _list_items(value.get("bear_cases"), limit=2)
    risks = _list_items(value.get("risk_flags"), limit=2)
    if catalysts:
        parts.append(f"catalysts {', '.join(catalysts)}")
    if bull:
        parts.append(f"bull {', '.join(bull)}")
    if bear:
        parts.append(f"bear {', '.join(bear)}")
    if risks:
        parts.append(f"risks {', '.join(risks)}")
    return _join_parts(parts)


def _article_evidence_section(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    articles = value.get("article_evidence_summaries")
    if not isinstance(articles, list):
        return ""
    parts = []
    for item in articles[:DEFAULT_MAX_NEWS_ITEMS]:
        if not isinstance(item, Mapping):
            continue
        title = _clean(item.get("title"))
        summary = _clean(item.get("summary"))
        if not title and not summary:
            continue
        meta = _join_parts(
            [
                _metric("source", item.get("source")),
                _metric("date", item.get("date")),
                _metric("confidence", item.get("confidence")),
                _metric("basis", item.get("basis")),
            ],
            separator=", ",
        )
        facts = _list_items(item.get("key_facts"), limit=2)
        risks = _list_items(item.get("risk_flags"), limit=2)
        article_parts = [
            title,
            summary,
            _metric("thesis relevance", item.get("thesis_relevance")),
        ]
        if facts:
            article_parts.append(f"facts {', '.join(facts)}")
        if risks:
            article_parts.append(f"risks {', '.join(risks)}")
        body = _join_parts(article_parts)
        parts.append(f"{body} ({meta})" if meta else body)
    return " | ".join(parts)


def _warnings_section(idea: Mapping[str, Any]) -> str:
    warnings: list[str] = []
    for key in (
        "fundamental_metrics",
        "quality_growth_scorecard",
        "latest_earnings_enrichment",
        "grok_research_enrichment",
    ):
        value = idea.get(key)
        if isinstance(value, Mapping):
            warnings.extend(_list_items(value.get("warnings"), limit=4))
    return _join_parts(warnings)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _metric(label: str, value: Any) -> str:
    cleaned = _clean(value)
    return f"{label} {cleaned}" if cleaned else ""


def _list_items(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item) for item in value[:limit] if _clean(item)]


def _join_parts(parts: list[str], *, separator: str = "; ") -> str:
    return separator.join(part for part in parts if part)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return _truncate_inline(text, 240)


def _truncate_inline(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "..."
    return text[: max_chars - len(suffix)].rstrip() + suffix


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    suffix = "\n[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)].rstrip() + suffix


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_NEWS_ITEMS",
    "EVIDENCE_BRIEF_VERSION",
    "TEMPLATE",
    "build_research_evidence_brief",
]
