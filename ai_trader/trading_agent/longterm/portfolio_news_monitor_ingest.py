"""Validate and summarize saved portfolio news monitor reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def build_portfolio_news_monitor_ingest_summary(path: str | Path) -> dict[str, Any]:
    """Load a monitor report and return a compact scheduler/dashboard summary."""
    report_path = Path(path)
    payload = _load_report(report_path)
    queue = [dict(row) for row in payload.get("enrichment_needed_queue") or [] if isinstance(row, Mapping)]
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
        "warnings": [str(item) for item in payload.get("warnings") or []],
        "top_triggers": _top_triggers(queue),
    }


def write_portfolio_news_monitor_ingest_summary(summary: Mapping[str, Any], path: str | Path) -> None:
    """Persist a compact monitor ingest summary."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(summary), indent=2, sort_keys=True), encoding="utf-8")


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
            "linked_decision_id": str(row.get("linked_decision_id") or ""),
            "thesis_impact_hint": str(row.get("thesis_impact_hint") or ""),
            "next_step": str(row.get("next_step") or ""),
        }
        for row in ordered[:5]
    ]


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
    "build_portfolio_news_monitor_ingest_summary",
    "write_portfolio_news_monitor_ingest_summary",
]
