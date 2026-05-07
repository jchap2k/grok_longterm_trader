"""Daily portfolio/watchlist news monitor for scheduler-ready enrichment triggers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.news_relevance_enrichment import rank_relevant_news
from longterm.portfolio_state import PortfolioState


NowFunc = Callable[[], datetime]


@dataclass(frozen=True)
class PortfolioNewsMonitorInputs:
    """Inputs for a deterministic news-monitor pass."""

    portfolio_state: PortfolioState | Mapping[str, Any] | None = None
    watchlist_ideas: list[Mapping[str, Any]] = field(default_factory=list)
    articles_by_symbol: Mapping[str, list[Mapping[str, Any]]] = field(default_factory=dict)
    journal_db: str | Path | None = None
    relevance_threshold: float = 0.55
    max_articles_per_symbol: int = 5
    include_protected_symbols: bool = False
    published_after: str = ""


def build_portfolio_news_monitor_report(
    inputs: PortfolioNewsMonitorInputs,
    *,
    now_func: NowFunc | None = None,
) -> dict[str, Any]:
    """Build a no-submit enrichment-needed queue from saved news rows."""
    now = now_func or _utc_now
    generated_at = _format_timestamp(now())
    portfolio = _coerce_portfolio(inputs.portfolio_state)
    latest_by_symbol = _latest_journal_rows(inputs.journal_db)
    monitored = _monitored_ideas(
        portfolio=portfolio,
        watchlist_ideas=inputs.watchlist_ideas,
        latest_by_symbol=latest_by_symbol,
        include_protected_symbols=inputs.include_protected_symbols,
    )
    warnings: list[str] = []
    if not monitored:
        warnings.append("no_symbols_to_monitor")

    queue: list[dict[str, Any]] = []
    articles_checked = 0
    for item in monitored:
        symbol = item["symbol"]
        raw_articles = [dict(article) for article in inputs.articles_by_symbol.get(symbol, [])]
        articles_checked += len(raw_articles)
        relevant = rank_relevant_news(
            symbol,
            raw_articles,
            business_context=str(item.get("business_context") or ""),
            company_name=str(item.get("company_name") or ""),
            max_items=max(1, int(inputs.max_articles_per_symbol or 1)),
            as_of_date=generated_at[:10],
        )
        for article in relevant:
            score = float(article.get("relevance_score") or 0.0)
            if score < float(inputs.relevance_threshold):
                continue
            queue.append(_queue_row(item, article, generated_at=generated_at))

    queue.sort(
        key=lambda row: (
            row["trigger_sort"],
            -float(row["relevance_score"]),
            row["symbol"],
        )
    )
    for row in queue:
        row.pop("trigger_sort", None)

    high_impact_count = sum(1 for row in queue if "High" in str(row.get("impact_category") or ""))
    return {
        "schema_version": 1,
        "status": "completed",
        "generated_at": generated_at,
        "published_after": inputs.published_after,
        "order_submission_enabled": False,
        "llm_calls_enabled": False,
        "monitored_symbols": [item["symbol"] for item in monitored],
        "monitored_count": len(monitored),
        "articles_checked": articles_checked,
        "enrichment_needed_count": len(queue),
        "high_impact_count": high_impact_count,
        "enrichment_needed_queue": queue,
        "warnings": warnings,
    }


def load_portfolio_news_inputs(
    *,
    portfolio_state_path: str | Path | None = None,
    watchlist_ideas_path: str | Path | None = None,
    snapshot_file: str | Path | None = None,
    journal_db: str | Path | None = None,
    relevance_threshold: float = 0.55,
    max_articles_per_symbol: int = 5,
    include_protected_symbols: bool = False,
    published_after: str = "",
) -> PortfolioNewsMonitorInputs:
    """Load monitor inputs from local JSON artifacts."""
    portfolio = PortfolioState.from_file(portfolio_state_path) if portfolio_state_path else None
    return PortfolioNewsMonitorInputs(
        portfolio_state=portfolio,
        watchlist_ideas=_load_json_list(watchlist_ideas_path) if watchlist_ideas_path else [],
        articles_by_symbol=_load_symbol_articles(snapshot_file) if snapshot_file else {},
        journal_db=journal_db,
        relevance_threshold=relevance_threshold,
        max_articles_per_symbol=max_articles_per_symbol,
        include_protected_symbols=include_protected_symbols,
        published_after=published_after,
    )


def write_portfolio_news_monitor_report(report: Mapping[str, Any], path: str | Path) -> None:
    """Write a monitor report as formatted JSON."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(report), indent=2, sort_keys=True), encoding="utf-8")


def _monitored_ideas(
    *,
    portfolio: PortfolioState | None,
    watchlist_ideas: list[Mapping[str, Any]],
    latest_by_symbol: Mapping[str, Mapping[str, Any]],
    include_protected_symbols: bool,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    protected = set(portfolio.protected_symbols if portfolio else [])
    if portfolio:
        for holding in portfolio.holdings:
            symbol = holding.symbol.upper()
            if not symbol or (symbol in protected and not include_protected_symbols):
                continue
            latest = dict(latest_by_symbol.get(symbol, {}))
            items.append(
                {
                    "symbol": symbol,
                    "trigger_type": "portfolio_news",
                    "company_name": str(latest.get("company_name") or ""),
                    "business_context": str(latest.get("key_thesis") or ""),
                    "linked_decision_id": str(latest.get("decision_id") or ""),
                    "latest_recommendation": str(latest.get("recommendation") or ""),
                }
            )
            seen.add(symbol)
    for idea in watchlist_ideas:
        symbol = str(idea.get("symbol") or "").upper()
        if not symbol or symbol in seen or (symbol in protected and not include_protected_symbols):
            continue
        latest = dict(latest_by_symbol.get(symbol, {}))
        business_context = " ".join(
            str(value)
            for value in (
                idea.get("business_summary"),
                idea.get("thesis_summary"),
                idea.get("primary_growth_driver"),
                idea.get("industry_context"),
                " ".join(str(note) for note in idea.get("source_notes") or []),
                latest.get("key_thesis"),
            )
            if value
        )
        items.append(
            {
                "symbol": symbol,
                "trigger_type": "watchlist_news",
                "company_name": str(idea.get("company_name") or latest.get("company_name") or ""),
                "business_context": business_context,
                "linked_decision_id": str(latest.get("decision_id") or ""),
                "latest_recommendation": str(latest.get("recommendation") or ""),
            }
        )
        seen.add(symbol)
    return items


def _queue_row(item: Mapping[str, Any], article: Mapping[str, Any], *, generated_at: str) -> dict[str, Any]:
    impact = str(article.get("impact_category") or "")
    return {
        "symbol": item["symbol"],
        "company_name": str(item.get("company_name") or item["symbol"]),
        "business_context": str(item.get("business_context") or ""),
        "trigger_type": item["trigger_type"],
        "trigger_sort": 0 if item["trigger_type"] == "portfolio_news" else 1,
        "relevance_score": float(article.get("relevance_score") or 0.0),
        "impact_category": impact,
        "title": str(article.get("title") or ""),
        "url": str(article.get("url") or ""),
        "source": str(article.get("source") or ""),
        "published_at": str(article.get("date") or ""),
        "summary": str(article.get("summary") or ""),
        "linked_decision_id": str(item.get("linked_decision_id") or ""),
        "latest_recommendation": str(item.get("latest_recommendation") or ""),
        "thesis_impact_hint": _thesis_impact_hint(impact),
        "next_step": "schedule_deeper_enrichment",
        "llm_escalation_allowed": False,
        "generated_at": generated_at,
    }


def _thesis_impact_hint(impact_category: str) -> str:
    lowered = impact_category.lower()
    if "regulatory" in lowered or "management" in lowered or "m&a" in lowered:
        return "review_required"
    if "high" in lowered:
        return "potential_confirmation"
    return "review_required"


def _latest_journal_rows(journal_db: str | Path | None) -> dict[str, dict[str, Any]]:
    if not journal_db:
        return {}
    path = Path(journal_db)
    if not path.exists():
        return {}
    journal = LongTermDecisionJournal(path)
    rows = journal.list_recommendation_table(limit=1000)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in latest:
            latest[symbol] = dict(row)
    return latest


def _coerce_portfolio(value: PortfolioState | Mapping[str, Any] | None) -> PortfolioState | None:
    if value is None:
        return None
    if isinstance(value, PortfolioState):
        return value
    return PortfolioState(**dict(value))


def _load_json_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Watchlist idea file must contain a JSON list.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _load_symbol_articles(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("News snapshot file must contain a symbol-keyed JSON object.")
    return {
        str(symbol).upper(): [dict(item) for item in rows if isinstance(item, Mapping)]
        for symbol, rows in payload.items()
        if isinstance(rows, list)
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "PortfolioNewsMonitorInputs",
    "build_portfolio_news_monitor_report",
    "load_portfolio_news_inputs",
    "write_portfolio_news_monitor_report",
]
