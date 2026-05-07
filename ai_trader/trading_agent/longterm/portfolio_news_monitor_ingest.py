"""Validate and summarize saved portfolio news monitor reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def build_portfolio_news_monitor_ingest_summary(path: str | Path) -> dict[str, Any]:
    """Load a monitor report and return a compact scheduler/dashboard summary."""
    report_path = Path(path)
    payload = _load_report(report_path)
    queue = [dict(row) for row in payload.get("enrichment_needed_queue") or [] if isinstance(row, Mapping)]
    followup_ideas = build_portfolio_news_followup_ideas(payload)
    symbols = sorted({str(row.get("symbol") or "").upper() for row in queue if row.get("symbol")})
    high_impact_rows = [row for row in queue if "high" in str(row.get("impact_category") or "").lower()]
    review_rows = [row for row in queue if str(row.get("thesis_impact_hint") or "") == "review_required"]
    high_impact_symbols_with_decisions = sorted(
        {
            str(row.get("symbol") or "").upper()
            for row in high_impact_rows
            if row.get("symbol") and row.get("linked_decision_id")
        }
    )
    return {
        "schema_version": 1,
        "status": "completed",
        "source_path": str(report_path),
        "generated_at": str(payload.get("generated_at") or ""),
        "published_after": str(payload.get("published_after") or ""),
        "order_submission_enabled": False,
        "llm_calls_enabled": False,
        "monitored_count": _int_value(payload.get("monitored_count")),
        "articles_checked": _int_value(payload.get("articles_checked")),
        "queue_count": len(queue),
        "high_impact_count": len(high_impact_rows),
        "review_trigger_count": len(review_rows),
        "symbols": symbols,
        "high_impact_symbols_with_decisions": high_impact_symbols_with_decisions,
        "followup_idea_count": len(followup_ideas),
        "followup_symbols": [str(idea.get("symbol") or "") for idea in followup_ideas],
        "warnings": [str(item) for item in payload.get("warnings") or []],
        "top_triggers": _top_triggers(queue),
    }


def build_portfolio_news_followup_ideas(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert monitor queue rows into research-intake compatible follow-up ideas."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in report.get("enrichment_needed_queue") or []:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(dict(row))

    ideas: list[dict[str, Any]] = []
    for symbol in sorted(grouped):
        rows = _dedupe_rows(grouped[symbol])
        rows.sort(key=lambda row: (-_float_value(row.get("relevance_score")), str(row.get("title") or "")))
        top_articles = _top_triggers(rows)[:3]
        company_name = _first_text(rows, "company_name") or symbol
        business_context = _first_text(rows, "business_context")
        linked_decision_id = _first_text(rows, "linked_decision_id")
        latest_recommendation = _first_text(rows, "latest_recommendation")
        high_impact_count = sum(1 for row in rows if "high" in str(row.get("impact_category") or "").lower())
        review_trigger_count = sum(1 for row in rows if str(row.get("thesis_impact_hint") or "") == "review_required")
        idea = {
            "symbol": symbol,
            "company_name": company_name,
            "idea_source": "portfolio_news_monitor",
            "business_summary": business_context or f"News-triggered follow-up for {symbol}.",
            "thesis_summary": (
                f"Review {symbol} because the portfolio news monitor found "
                f"{len(rows)} relevant article(s), including {high_impact_count} high-impact trigger(s)."
            ),
            "source_notes": _source_notes(symbol, rows[:3], linked_decision_id, latest_recommendation),
            "portfolio_news_monitor_metadata": {
                "schema_version": 1,
                "trigger_count": len(rows),
                "high_impact_count": high_impact_count,
                "review_trigger_count": review_trigger_count,
                "linked_decision_id": linked_decision_id,
                "latest_recommendation": latest_recommendation,
                "top_articles": top_articles,
            },
        }
        _validate_followup_idea(idea)
        ideas.append(idea)
    return ideas


def write_portfolio_news_followup_ideas(ideas: list[Mapping[str, Any]], path: str | Path) -> None:
    """Persist grouped follow-up ideas for later bounded enrichment/research."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([dict(idea) for idea in ideas], indent=2, sort_keys=True), encoding="utf-8")


def write_portfolio_news_monitor_ingest_summary(summary: Mapping[str, Any], path: str | Path) -> None:
    """Persist a compact monitor ingest summary."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(summary), indent=2, sort_keys=True), encoding="utf-8")


def load_portfolio_news_monitor_report(path: str | Path) -> dict[str, Any]:
    """Load and validate a saved portfolio news monitor report."""
    return _load_report(Path(path))


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"portfolio news monitor report not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"portfolio news monitor report is not valid JSON: {path}") from exc
    except OSError as exc:
        raise ValueError(f"portfolio news monitor report could not be read: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"portfolio news monitor report must contain a JSON object: {path}")
    queue = payload.get("enrichment_needed_queue")
    if queue is not None and not isinstance(queue, list):
        raise ValueError("portfolio news monitor enrichment_needed_queue must be a list.")
    return dict(payload)


def _top_triggers(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        queue,
        key=lambda row: (
            -_float_value(row.get("relevance_score")),
            str(row.get("symbol") or ""),
            str(row.get("title") or ""),
        ),
    )
    return [
        {
            "symbol": str(row.get("symbol") or "").upper(),
            "title": str(row.get("title") or ""),
            "relevance_score": _float_value(row.get("relevance_score")),
            "impact_category": str(row.get("impact_category") or ""),
            "summary": str(row.get("summary") or ""),
            "url": str(row.get("url") or ""),
            "source": str(row.get("source") or ""),
            "linked_decision_id": str(row.get("linked_decision_id") or ""),
            "thesis_impact_hint": str(row.get("thesis_impact_hint") or ""),
            "next_step": str(row.get("next_step") or ""),
        }
        for row in ordered[:5]
    ]


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("url") or ""), str(row.get("title") or ""))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _first_text(rows: list[dict[str, Any]], key: str) -> str:
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_notes(
    symbol: str,
    rows: list[dict[str, Any]],
    linked_decision_id: str,
    latest_recommendation: str,
) -> list[str]:
    notes = [
        f"Portfolio news monitor follow-up for {symbol}: {len(rows)} top article(s) summarized below.",
    ]
    if linked_decision_id:
        notes.append(f"Linked latest decision: {linked_decision_id}.")
    if latest_recommendation:
        notes.append(f"Latest recommendation context: {latest_recommendation}.")
    for row in rows:
        parts = [
            f"Article: {str(row.get('title') or '').strip() or 'Untitled'}",
            f"impact={str(row.get('impact_category') or 'unknown')}",
            f"relevance={_float_value(row.get('relevance_score')):.2f}",
        ]
        summary = str(row.get("summary") or "").strip()
        url = str(row.get("url") or "").strip()
        hint = str(row.get("thesis_impact_hint") or "").strip()
        if hint:
            parts.append(f"thesis_hint={hint}")
        if summary:
            parts.append(f"summary={summary}")
        if url:
            parts.append(f"url={url}")
        notes.append("; ".join(parts))
    return notes


def _validate_followup_idea(idea: Mapping[str, Any]) -> None:
    packet = create_research_packet_from_idea(
        idea,
        profile=PortfolioProfile(account_strategy_mode="paper_non_taxable"),
        idea_source="portfolio_news_monitor",
    )
    warnings = packet.completeness_warnings()
    if warnings:
        raise ValueError("portfolio news follow-up idea failed packet validation: " + "; ".join(warnings))


def _int_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "build_portfolio_news_followup_ideas",
    "build_portfolio_news_monitor_ingest_summary",
    "load_portfolio_news_monitor_report",
    "write_portfolio_news_followup_ideas",
    "write_portfolio_news_monitor_ingest_summary",
]
