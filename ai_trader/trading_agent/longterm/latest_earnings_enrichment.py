"""Latest earnings context enrichment for long-term research ideas."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping


EARNINGS_TERMS = ("earnings", "quarter", "q1", "q2", "q3", "q4", "revenue", "eps", "guidance")
POSITIVE_TERMS = ("growth", "improved", "higher", "record", "accelerat", "robotaxi", "software", "subscription")
NEGATIVE_TERMS = ("pressure", "decline", "lower", "miss", "cut", "capex", "spending", "margin", "demand")


def enrich_idea_with_latest_earnings(idea: Mapping[str, Any], *, as_of_date: str | None = None) -> dict[str, Any]:
    """Attach latest earnings context to a single idea."""
    payload = dict(idea)
    payload["symbol"] = str(payload.get("symbol") or "").upper()
    enrichment = build_latest_earnings_enrichment(payload, as_of_date=as_of_date)
    payload["latest_earnings_enrichment"] = enrichment
    notes = _note_list(payload.get("source_notes"))
    notes.append(
        f"Latest earnings enrichment: confidence={enrichment['confidence']}, sources={len(enrichment['source_urls'])}."
    )
    payload["source_notes"] = _dedupe(notes)
    return payload


def enrich_ideas_with_latest_earnings(
    ideas: list[Mapping[str, Any]],
    *,
    as_of_date: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = ideas[:limit] if limit is not None else ideas
    return [enrich_idea_with_latest_earnings(idea, as_of_date=as_of_date) for idea in selected]


def build_latest_earnings_enrichment(idea: Mapping[str, Any], *, as_of_date: str | None = None) -> dict[str, Any]:
    """Build source-backed latest earnings context from news and provider metrics."""
    earnings_articles = _earnings_articles(idea.get("relevant_news") or [])
    financial_takeaways = _financial_takeaways(idea.get("fundamental_metrics") or {})
    positive = _theme_sentences(earnings_articles, POSITIVE_TERMS)
    negative = _theme_sentences(earnings_articles, NEGATIVE_TERMS)
    source_urls = _dedupe([str(item.get("url") or "") for item in earnings_articles if item.get("url")])
    warnings = []
    if not earnings_articles:
        warnings.append("missing_earnings_article")
    if not financial_takeaways:
        warnings.append("missing_financial_takeaways")
    confidence = _confidence(earnings_articles, financial_takeaways, warnings)
    summary = _summary(earnings_articles, financial_takeaways)
    return {
        "symbol": str(idea.get("symbol") or "").upper(),
        "as_of_date": as_of_date or date.today().isoformat(),
        "source_type": "python_latest_earnings_enrichment",
        "basis": "source_filtered_articles_and_provider_metrics",
        "quarter": _quarter_label(earnings_articles),
        "summary": summary,
        "key_financial_takeaways": financial_takeaways,
        "thesis_positive_developments": positive,
        "thesis_negative_developments": negative,
        "source_articles": [_article_payload(item) for item in earnings_articles[:3]],
        "source_urls": source_urls,
        "confidence": confidence,
        "warnings": warnings,
    }


def _earnings_articles(raw_articles: Any) -> list[dict[str, Any]]:
    articles = [dict(item) for item in raw_articles if isinstance(item, Mapping)]
    filtered = []
    for article in articles:
        text = f"{article.get('title', '')} {article.get('summary', '')} {article.get('impact_category', '')}".lower()
        if "earnings" in str(article.get("impact_category") or "").lower() or any(term in text for term in EARNINGS_TERMS):
            filtered.append(article)
    filtered.sort(key=lambda item: (float(item.get("relevance_score") or 0), str(item.get("date") or "")), reverse=True)
    return filtered


def _financial_takeaways(metrics: Mapping[str, Any]) -> list[str]:
    financials = metrics.get("financials_ttm") or {}
    profitability = metrics.get("profitability_ttm") or {}
    takeaways = []
    labels = (
        ("Revenue", financials.get("revenue")),
        ("Net Income", financials.get("net_income")),
        ("Free Cash Flow", financials.get("free_cash_flow")),
        ("Capital Expenditure", financials.get("capital_expenditure")),
        ("Operating Margin", profitability.get("operating_margin")),
        ("FCF Margin", profitability.get("free_cash_flow_margin")),
    )
    for label, value in labels:
        if value:
            takeaways.append(f"{label}: {value}")
    return takeaways


def _theme_sentences(articles: list[Mapping[str, Any]], terms: tuple[str, ...]) -> list[str]:
    themes = []
    for article in articles:
        text = str(article.get("summary") or article.get("title") or "").strip()
        lower = text.lower()
        if text and any(term in lower for term in terms):
            themes.append(text)
    return _dedupe(themes)[:4]


def _summary(articles: list[Mapping[str, Any]], takeaways: list[str]) -> str:
    if articles:
        lead = str(articles[0].get("summary") or articles[0].get("title") or "").strip()
        if lead:
            return lead
    if takeaways:
        return "; ".join(takeaways[:3])
    return ""


def _quarter_label(articles: list[Mapping[str, Any]]) -> str:
    if not articles:
        return "unknown"
    text = f"{articles[0].get('title', '')} {articles[0].get('summary', '')}".lower()
    for quarter in ("q1", "q2", "q3", "q4"):
        if quarter in text:
            return quarter.upper()
    return "latest_available"


def _confidence(articles: list[Mapping[str, Any]], takeaways: list[str], warnings: list[str]) -> float:
    score = 0.25
    if articles:
        score += 0.35
    if len(articles) >= 2:
        score += 0.10
    if takeaways:
        score += 0.20
    if warnings:
        score -= 0.15 * len(warnings)
    return round(max(0.0, min(0.95, score)), 2)


def _article_payload(article: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": str(article.get("title") or ""),
        "url": str(article.get("url") or ""),
        "date": str(article.get("date") or ""),
        "source": str(article.get("source") or ""),
        "summary": str(article.get("summary") or ""),
        "relevance_score": article.get("relevance_score"),
    }


def _note_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


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


__all__ = [
    "build_latest_earnings_enrichment",
    "enrich_idea_with_latest_earnings",
    "enrich_ideas_with_latest_earnings",
]
