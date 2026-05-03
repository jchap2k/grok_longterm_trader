"""Pure-Python first-pass scan for broad long-term universes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from longterm.fundamental_metrics_enrichment import (
    enrich_ideas_with_fundamental_metrics,
    fetch_yfinance_fundamental_metrics,
)
from longterm.quality_growth_scorecard import enrich_ideas_with_quality_growth_scorecard


@dataclass(frozen=True)
class ExtendedUniverseScanResult:
    """Output from the cheap first-pass screen before expensive enrichment."""

    scanned_ideas: list[dict[str, Any]] = field(default_factory=list)
    passed_ideas: list[dict[str, Any]] = field(default_factory=list)
    deferred_ideas: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def run_python_first_pass_scan(
    ideas: list[Mapping[str, Any]],
    *,
    metrics_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    fetch_metrics: Callable[[str], Mapping[str, Any]] | None = None,
    top_percent: float = 10.0,
    min_pass_count: int = 1,
    max_pass_count: int | None = None,
    limit: int | None = None,
    as_of_date: str | None = None,
) -> ExtendedUniverseScanResult:
    """Rank broad-universe ideas and advance the top relative slice.

    The default is intentionally percentile-based, not a hard all-or-nothing
    threshold. That keeps the universe funnel open during ugly market regimes
    while still reserving expensive enrichment for the strongest candidates.
    """
    selected = [dict(idea) for idea in (ideas[:limit] if limit is not None else ideas)]
    hydrated = _attach_fundamentals(
        selected,
        metrics_by_symbol=metrics_by_symbol,
        fetch_metrics=fetch_metrics,
        as_of_date=as_of_date,
    )
    scored = enrich_ideas_with_quality_growth_scorecard(hydrated, as_of_date=as_of_date)
    ranked = _rank_scored_ideas(scored)
    coverage = _fundamentals_coverage(scored)
    pass_count = _pass_count(
        len(ranked),
        top_percent=top_percent,
        min_pass_count=min_pass_count,
        max_pass_count=max_pass_count,
    )
    passed = []
    deferred = []
    for row in ranked:
        payload = dict(row["idea"])
        rank = int(row["rank"])
        score = float(row["score"])
        percentile = round((rank / len(ranked)) * 100, 2) if ranked else 0.0
        is_passed = rank <= pass_count
        payload["python_first_pass_scan"] = {
            "schema_version": 1,
            "decision": "advance_to_enrichment" if is_passed else "defer_after_python_scan",
            "rank": rank,
            "score": score,
            "percentile": percentile,
            "top_percent_cutoff": float(top_percent),
            "reason": _scan_reason(
                is_passed=is_passed,
                top_percent=float(top_percent),
                rank=rank,
                pass_count=pass_count,
                score=score,
                warnings=(payload.get("quality_growth_scorecard") or {}).get("warnings") or [],
            ),
        }
        payload["screening_stage"] = "python_first_pass_scan"
        payload["source_notes"] = _append_note(
            payload.get("source_notes"),
            (
                "Extended-universe Python first pass: ranked by deterministic "
                "quality-growth score before expensive news/Grok/committee enrichment."
            ),
        )
        if is_passed:
            passed.append(payload)
        else:
            deferred.append(payload)
    summary = {
        "schema_version": 1,
        "mode": "extended_universe_python_first_pass_scan",
        "input_count": len(ideas),
        "scanned_count": len(ranked),
        **coverage,
        "passed_count": len(passed),
        "deferred_count": len(deferred),
        "top_percent": float(top_percent),
        "min_pass_count": int(min_pass_count),
        "max_pass_count": max_pass_count,
        "pass_count_target": pass_count,
        "passed_symbols": [idea["symbol"] for idea in passed if idea.get("symbol")],
        "deferred_symbols": [idea["symbol"] for idea in deferred if idea.get("symbol")],
        "next_enrichment_command": _next_enrichment_command(),
    }
    return ExtendedUniverseScanResult(
        scanned_ideas=[dict(row["idea"]) for row in ranked],
        passed_ideas=passed,
        deferred_ideas=deferred,
        summary=summary,
    )


def _attach_fundamentals(
    ideas: list[Mapping[str, Any]],
    *,
    metrics_by_symbol: Mapping[str, Mapping[str, Any]] | None,
    fetch_metrics: Callable[[str], Mapping[str, Any]] | None,
    as_of_date: str | None,
) -> list[dict[str, Any]]:
    if all(idea.get("fundamental_metrics") for idea in ideas):
        return [dict(idea) for idea in ideas]
    snapshots = _normalize_snapshots(metrics_by_symbol or {})
    if fetch_metrics is not None:
        for idea in ideas:
            symbol = str(idea.get("symbol") or "").upper()
            if symbol and symbol not in snapshots:
                fetched = dict(fetch_metrics(symbol))
                if fetched:
                    snapshots[symbol] = fetched
    if not snapshots:
        return [dict(idea) for idea in ideas]
    if _all_normalized_fundamental_metrics(snapshots):
        enriched = []
        for idea in ideas:
            payload = dict(idea)
            symbol = str(payload.get("symbol") or "").upper()
            metrics = snapshots.get(symbol)
            if metrics:
                payload["symbol"] = symbol
                payload["fundamental_metrics"] = dict(metrics)
                payload["source_notes"] = _append_note(
                    payload.get("source_notes"),
                    "Python fundamental metrics: loaded from normalized first-pass scan snapshot.",
                )
            enriched.append(payload)
        return enriched
    return enrich_ideas_with_fundamental_metrics(ideas, snapshots, as_of_date=as_of_date)


def _normalize_snapshots(snapshots: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(symbol).upper(): dict(value) for symbol, value in snapshots.items() if isinstance(value, Mapping)}


def _all_normalized_fundamental_metrics(snapshots: Mapping[str, Mapping[str, Any]]) -> bool:
    return bool(snapshots) and all(
        str(value.get("source_type") or "") == "python_fundamental_metrics"
        for value in snapshots.values()
    )


def _rank_scored_ideas(ideas: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, idea in enumerate(ideas):
        scorecard = idea.get("quality_growth_scorecard") or {}
        rows.append(
            {
                "index": index,
                "score": _score(scorecard),
                "symbol": str(idea.get("symbol") or "").upper(),
                "idea": dict(idea),
            }
        )
    rows.sort(key=lambda row: (-row["score"], row["index"], row["symbol"]))
    ranked = []
    for rank, row in enumerate(rows, start=1):
        ranked.append({**row, "rank": rank})
    return ranked


def _fundamentals_coverage(ideas: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(ideas)
    covered = []
    missing = []
    for idea in ideas:
        symbol = str(idea.get("symbol") or "").upper()
        if idea.get("fundamental_metrics"):
            covered.append(symbol)
        elif symbol:
            missing.append(symbol)
    return {
        "fundamentals_coverage_count": len(covered),
        "fundamentals_missing_count": len(missing),
        "fundamentals_coverage_percent": round((len(covered) / total) * 100, 2) if total else 0.0,
        "fundamentals_missing_symbols": missing,
    }


def _score(scorecard: Mapping[str, Any]) -> float:
    try:
        return float(scorecard.get("superscore") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pass_count(
    total: int,
    *,
    top_percent: float,
    min_pass_count: int,
    max_pass_count: int | None,
) -> int:
    if total <= 0:
        return 0
    bounded_percent = max(0.0, min(100.0, float(top_percent)))
    target = math.ceil(total * (bounded_percent / 100.0))
    target = max(int(min_pass_count), target)
    if max_pass_count is not None:
        target = min(target, max(0, int(max_pass_count)))
    return max(0, min(total, target))


def _scan_reason(
    *,
    is_passed: bool,
    top_percent: float,
    rank: int,
    pass_count: int,
    score: float,
    warnings: list[str],
) -> str:
    base = (
        f"Advanced by relative top {top_percent:.1f}% Python scan "
        f"(rank {rank} of {pass_count}, score {score:.1f})."
        if is_passed
        else f"Deferred after Python scan; outside top {top_percent:.1f}% survivor slice (rank {rank}, score {score:.1f})."
    )
    warning_text = ", ".join(str(item) for item in warnings if item)
    return f"{base} Warnings: {warning_text}." if warning_text else base


def _append_note(value: Any, note: str) -> list[str]:
    notes = []
    if isinstance(value, str):
        notes.append(value)
    elif value:
        notes.extend(str(item) for item in value)
    if note not in notes:
        notes.append(note)
    return notes


def _next_enrichment_command() -> str:
    return (
        "python scripts/longterm_evidence_enrichment_pipeline.py "
        "--idea-batch path\\to\\extended_watchlist.python_scan_passed.json "
        "--fundamentals-provider yfinance "
        "--polygon-news --news-cache-path path\\to\\polygon_news_cache.json "
        "--rate-limit-batch-size 5 --rate-limit-pause-seconds 66 "
        "--output path\\to\\extended_watchlist.evidence_ready.json "
        "--summary-output path\\to\\extended_watchlist.evidence_summary.json"
    )


__all__ = [
    "ExtendedUniverseScanResult",
    "fetch_yfinance_fundamental_metrics",
    "run_python_first_pass_scan",
]
