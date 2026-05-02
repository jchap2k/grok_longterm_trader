"""Relevant-news enrichment for long-term research ideas."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


NOISE_PHRASES = (
    "stock moved",
    "stock is moving",
    "shares moved",
    "shares are moving",
    "why shares",
    "why stock",
    "price target",
    "premarket",
    "pre-market",
    "stock market today",
    "s&p 500",
    "nasdaq to records",
    "broad market roundup",
    "make you rich",
    "battle royale",
)

CATALYST_KEYWORDS = {
    "Earnings - High": ("earnings", "revenue", "margin", "guidance", "profit", "cash flow", "eps"),
    "Product/Tech - High": ("product", "launch", "ai", "software", "platform", "chip", "cloud"),
    "Major Contract - High": ("contract", "deal", "customer", "backlog", "partnership", "order"),
    "Regulatory - Medium": ("regulator", "regulatory", "approval", "probe", "investigation", "lawsuit"),
    "M&A - High": ("acquisition", "merger", "takeover", "buyout"),
    "Management - Medium": ("ceo", "cfo", "management", "resigns", "appoints"),
}

QUALITY_SOURCES = {
    "reuters": 0.18,
    "bloomberg": 0.18,
    "wall street journal": 0.16,
    "wsj": 0.16,
    "sec": 0.16,
    "company": 0.14,
    "yahoo finance": 0.10,
    "benzinga": 0.08,
    "seeking alpha": 0.06,
}


class NewsProvider(Protocol):
    """Minimal news provider interface."""

    def fetch_news(self, symbol: str, **kwargs: Any) -> list[Mapping[str, Any]]:
        """Fetch raw news rows for a symbol."""


@dataclass
class FakeNewsProvider:
    """Offline provider backed by symbol-keyed article rows."""

    articles_by_symbol: Mapping[str, list[Mapping[str, Any]]]

    def fetch_news(self, symbol: str, **kwargs: Any) -> list[Mapping[str, Any]]:
        return [dict(item) for item in self.articles_by_symbol.get(symbol.upper(), [])]


class PolygonNewsProvider:
    """Polygon reference-news provider for long-term ticker enrichment."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_env: str = "POLYGON_API_KEY",
        base_url: str = "https://api.polygon.io",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key or os.getenv(api_key_env)
        if not self.api_key:
            raise ValueError(f"Missing Polygon API key. Set {api_key_env} or pass api_key.")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_news(self, symbol: str, **kwargs: Any) -> list[Mapping[str, Any]]:
        import requests

        params: dict[str, Any] = {
            "ticker": symbol.upper(),
            "limit": int(kwargs.get("limit") or 10),
            "order": "desc",
            "sort": "published_utc",
            "apiKey": self.api_key,
        }
        published_after = kwargs.get("published_after")
        if published_after:
            params["published_utc.gte"] = str(published_after)
        response = requests.get(
            f"{self.base_url}/v2/reference/news",
            params=params,
            timeout=float(self.timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, Mapping) else []
        return [dict(item) for item in results or [] if isinstance(item, Mapping)]


class CachedNewsProvider:
    """Daily symbol cache wrapper for any news fetch function."""

    def __init__(
        self,
        *,
        fetch: Callable[..., list[Mapping[str, Any]]],
        cache_path: str | Path,
        today: str | None = None,
    ) -> None:
        self.fetch = fetch
        self.cache_path = Path(cache_path)
        self.today = today or date.today().isoformat()

    def fetch_news(self, symbol: str, **kwargs: Any) -> list[Mapping[str, Any]]:
        normalized = symbol.upper()
        cache = self._load_cache()
        cached = cache.get(normalized)
        if cached and cached.get("data_as_of") == self.today:
            return [dict(item) for item in cached.get("articles") or []]
        articles = [dict(item) for item in self.fetch(normalized, **kwargs)]
        cache[normalized] = {"data_as_of": self.today, "articles": articles}
        self._write_cache(cache)
        return articles

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _write_cache(self, payload: Mapping[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def enrich_idea_with_relevant_news(
    idea: Mapping[str, Any],
    *,
    provider: NewsProvider,
    as_of_date: str | None = None,
    max_items: int = 5,
    published_after: str | None = None,
) -> dict[str, Any]:
    """Add top long-term-relevant news rows to a research idea."""
    payload = dict(idea)
    symbol = str(payload.get("symbol") or "").upper()
    payload["symbol"] = symbol
    articles = provider.fetch_news(symbol, published_after=published_after, limit=max(10, max_items * 3))
    relevant = rank_relevant_news(
        symbol,
        articles,
        business_context=_idea_context(payload),
        company_name=str(payload.get("company_name") or ""),
        max_items=max_items,
        as_of_date=as_of_date,
    )
    payload["relevant_news"] = relevant
    if relevant:
        notes = _note_list(payload.get("source_notes"))
        titles = "; ".join(item["title"] for item in relevant[:3])
        notes.append(f"Latest relevant news: {titles}.")
        payload["source_notes"] = _dedupe(notes)
    return payload


def enrich_ideas_with_relevant_news(
    ideas: list[Mapping[str, Any]],
    *,
    provider: NewsProvider,
    as_of_date: str | None = None,
    max_items: int = 5,
    published_after: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = ideas[:limit] if limit is not None else ideas
    return [
        enrich_idea_with_relevant_news(
            idea,
            provider=provider,
            as_of_date=as_of_date,
            max_items=max_items,
            published_after=published_after,
        )
        for idea in selected
    ]


def enrich_ideas_with_relevant_news_paced(
    ideas: list[Mapping[str, Any]],
    *,
    provider: NewsProvider,
    batch_size: int = 5,
    pause_seconds: float = 66.0,
    sleep: Callable[[float], Any] = time.sleep,
    as_of_date: str | None = None,
    max_items: int = 5,
    published_after: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Enrich ideas while pausing between provider request batches."""
    selected = ideas[:limit] if limit is not None else ideas
    if batch_size <= 0:
        batch_size = len(selected) or 1
    enriched: list[dict[str, Any]] = []
    for index, idea in enumerate(selected, start=1):
        enriched.append(
            enrich_idea_with_relevant_news(
                idea,
                provider=provider,
                as_of_date=as_of_date,
                max_items=max_items,
                published_after=published_after,
            )
        )
        if index < len(selected) and index % batch_size == 0 and pause_seconds > 0:
            sleep(float(pause_seconds))
    return enriched


def rank_relevant_news(
    symbol: str,
    articles: list[Mapping[str, Any]],
    *,
    business_context: str = "",
    company_name: str = "",
    max_items: int = 5,
    as_of_date: str | None = None,
) -> list[dict[str, Any]]:
    """Filter, score, and normalize articles for long-term thesis relevance."""
    seen_urls = set()
    ranked: list[dict[str, Any]] = []
    for article in articles:
        normalized = _normalize_article(article)
        url = normalized["url"]
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        if _is_noise(normalized):
            continue
        primary_subject_score = _primary_subject_score(symbol, normalized, company_name)
        if not _passes_primary_subject_gate(symbol, normalized, primary_subject_score, company_name):
            continue
        score = _relevance_score(symbol, normalized, business_context)
        if score < 0.35:
            continue
        impact = _impact_category(normalized)
        ranked.append(
            {
                "title": normalized["title"],
                "url": url,
                "date": normalized["date"],
                "summary": normalized["summary"],
                "relevance_score": round(score, 3),
                "impact_category": impact,
                "source": normalized["source"],
                "tickers": normalized["tickers"],
                "primary_subject_score": round(primary_subject_score, 3),
                "as_of_date": as_of_date or date.today().isoformat(),
            }
        )
    ranked.sort(key=lambda item: (item["relevance_score"], _impact_weight(item["impact_category"])), reverse=True)
    return ranked[:max_items]


def _normalize_article(article: Mapping[str, Any]) -> dict[str, Any]:
    publisher = article.get("publisher")
    source = ""
    if isinstance(publisher, Mapping):
        source = str(publisher.get("name") or "")
    elif publisher:
        source = str(publisher)
    return {
        "title": str(article.get("title") or ""),
        "url": str(article.get("url") or article.get("article_url") or ""),
        "date": _article_date(article),
        "summary": str(article.get("description") or article.get("summary") or article.get("amp_url") or ""),
        "source": source,
        "tickers": [str(item).upper() for item in article.get("tickers") or []],
    }


def _article_date(article: Mapping[str, Any]) -> str:
    value = str(article.get("published_utc") or article.get("published_at") or article.get("date") or "")
    return value[:10] if value else ""


def _is_noise(article: Mapping[str, Any]) -> bool:
    title = str(article.get("title") or "").lower()
    summary = str(article.get("summary") or "").lower()
    text = f"{title} {summary}"
    return any(phrase in text for phrase in NOISE_PHRASES)


def _relevance_score(symbol: str, article: Mapping[str, Any], business_context: str) -> float:
    title = str(article.get("title") or "")
    summary = str(article.get("summary") or "")
    source = str(article.get("source") or "").lower()
    text = f"{title} {summary}".lower()
    score = 0.0
    if symbol.upper() in article.get("tickers", []):
        score += 0.22
    context_terms = _terms(business_context)
    if context_terms:
        overlap = sum(1 for term in context_terms if term in text)
        score += min(0.28, overlap * 0.035)
    catalyst_hits = sum(1 for words in CATALYST_KEYWORDS.values() for word in words if word in text)
    score += min(0.30, catalyst_hits * 0.045)
    score += _source_quality(source)
    return min(1.0, score)


def _primary_subject_score(symbol: str, article: Mapping[str, Any], company_name: str = "") -> float:
    title = str(article.get("title") or "").lower()
    summary = str(article.get("summary") or "").lower()
    tickers = [str(item).upper() for item in article.get("tickers") or []]
    symbol_text = symbol.lower()
    company_terms = _company_subject_terms(company_name)
    score = 0.0
    if symbol_text and symbol_text in title:
        score += 0.50
    elif symbol_text and symbol_text in summary[:240]:
        score += 0.25
    if any(term in title for term in company_terms):
        score += 0.50
    elif any(term in summary[:240] for term in company_terms):
        score += 0.25
    if tickers and tickers[0] == symbol.upper():
        score += 0.25
    if len(tickers) == 1 and symbol.upper() in tickers:
        score += 0.20
    return min(1.0, score)


def _passes_primary_subject_gate(
    symbol: str,
    article: Mapping[str, Any],
    primary_subject_score: float,
    company_name: str = "",
) -> bool:
    tickers = [str(item).upper() for item in article.get("tickers") or []]
    if tickers and symbol.upper() not in tickers:
        return False
    if len(tickers) > 1 and not _title_mentions_subject(symbol, article, company_name):
        return False
    if primary_subject_score >= 0.25:
        return True
    # If a provider returns only the requested ticker, allow high-context articles
    # whose title omits the company name, such as "This Cloud Stock Reports...".
    return len(tickers) == 1 and symbol.upper() in tickers


def _title_mentions_subject(symbol: str, article: Mapping[str, Any], company_name: str = "") -> bool:
    title = str(article.get("title") or "").lower()
    symbol_text = symbol.lower()
    return bool(
        (symbol_text and symbol_text in title)
        or any(term in title for term in _company_subject_terms(company_name))
    )


def _company_subject_terms(company_name: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(company_name)).split()
    stop = {
        "inc",
        "corp",
        "corporation",
        "company",
        "holdings",
        "holding",
        "class",
        "plc",
        "ltd",
        "limited",
        "the",
    }
    return {term for term in cleaned if len(term) >= 4 and term not in stop}


def _source_quality(source: str) -> float:
    normalized = source.lower()
    for name, boost in QUALITY_SOURCES.items():
        if name in normalized:
            return boost
    return 0.0


def _impact_category(article: Mapping[str, Any]) -> str:
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    best = "Other - Medium"
    best_count = 0
    for label, words in CATALYST_KEYWORDS.items():
        count = sum(1 for word in words if word in text)
        if count > best_count:
            best = label
            best_count = count
    return best


def _impact_weight(label: str) -> int:
    if "High" in label:
        return 3
    if "Medium" in label:
        return 2
    return 1


def _idea_context(payload: Mapping[str, Any]) -> str:
    fields = (
        payload.get("business_summary"),
        payload.get("thesis_summary"),
        payload.get("primary_growth_driver"),
        payload.get("industry_context"),
        " ".join(str(item) for item in payload.get("source_notes") or []),
    )
    return " ".join(str(field) for field in fields if field)


def _terms(text: str) -> set[str]:
    stop = {"and", "the", "with", "from", "that", "this", "into", "for", "its", "are", "can"}
    return {
        token
        for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
        if len(token) >= 4 and token not in stop
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
    "CachedNewsProvider",
    "FakeNewsProvider",
    "NewsProvider",
    "PolygonNewsProvider",
    "enrich_idea_with_relevant_news",
    "enrich_ideas_with_relevant_news",
    "enrich_ideas_with_relevant_news_paced",
    "rank_relevant_news",
]
