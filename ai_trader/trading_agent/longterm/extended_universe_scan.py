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
    min_coverage_percent_for_enrichment: float = 80.0,
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
    readiness = _enrichment_readiness(
        coverage,
        min_coverage_percent_for_enrichment=float(min_coverage_percent_for_enrichment),
    )
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
        moneyball_score = float(row["moneyball_score"])
        quant_score = float(row["quant_score"])
        percentile = round((rank / len(ranked)) * 100, 2) if ranked else 0.0
        is_passed = rank <= pass_count
        payload["python_first_pass_scan"] = {
            "schema_version": 1,
            "decision": "advance_to_enrichment" if is_passed else "defer_after_python_scan",
            "rank": rank,
            "score": score,
            "rank_score": score,
            "moneyball_score": moneyball_score,
            "quant_score": quant_score,
            "score_basis": "70pct_moneyball_30pct_quant",
            "rank_reason": _rank_reason(moneyball_score=moneyball_score, quant_score=quant_score, rank_score=score),
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
        **readiness,
        "passed_count": len(passed),
        "deferred_count": len(deferred),
        "top_percent": float(top_percent),
        "min_pass_count": int(min_pass_count),
        "max_pass_count": max_pass_count,
        "pass_count_target": pass_count,
        "rank_score_basis": "70pct_moneyball_30pct_quant",
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


def build_python_first_pass_markdown(
    passed_ideas: list[Mapping[str, Any]],
    deferred_ideas: list[Mapping[str, Any]],
    summary: Mapping[str, Any],
    *,
    title: str = "Extended Universe Python First Pass",
    limit: int = 15,
) -> str:
    """Render a human-readable scan report for overnight operator review."""
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- Scanned: {summary.get('scanned_count', 0)}",
        f"- Passed to enrichment: {summary.get('passed_count', 0)}",
        f"- Deferred: {summary.get('deferred_count', 0)}",
        f"- Top-percent cutoff: {summary.get('top_percent', 'n/a')}%",
        f"- Readiness: {_readiness_label(summary)}",
        (
            "- Coverage: "
            f"{summary.get('fundamentals_coverage_count', 0)}/{summary.get('scanned_count', 0)} "
            f"({summary.get('fundamentals_coverage_percent', 0.0)}%)"
        ),
        (
            "- Remaining fetches: "
            f"{summary.get('fundamentals_remaining_fetch_count', summary.get('fundamentals_missing_count', 0))}; "
            f"estimated runs remaining: {_runs_remaining_text(summary)}"
        ),
        f"- Cache hits/fetches: {summary.get('fundamentals_cache_hits', 0)} / {summary.get('fundamentals_cache_fetches', 0)}",
        "",
    ]
    fetch_errors = [dict(item) for item in summary.get("fundamentals_fetch_errors") or [] if isinstance(item, Mapping)]
    skipped = [str(item) for item in summary.get("fundamentals_fetch_skipped_symbols") or [] if item]
    missing = [str(item) for item in summary.get("fundamentals_missing_symbols") or [] if item]
    if fetch_errors or skipped or missing:
        lines.extend(["## Coverage Notes", ""])
        if fetch_errors:
            lines.append("Fetch errors:")
            lines.extend(f"- `{item.get('symbol')}`: {item.get('error')}" for item in fetch_errors[:limit])
        if skipped:
            lines.append(f"Fetch-limited symbols still waiting: {', '.join(f'`{symbol}`' for symbol in skipped[:limit])}")
        if missing:
            lines.append(f"Missing fundamentals: {', '.join(f'`{symbol}`' for symbol in missing[:limit])}")
        lines.append("")
    lines.extend(["## Passed To Enrichment", ""])
    lines.extend(_idea_table(passed_ideas[:limit]))
    lines.extend(["", "## Deferred After Python Scan", ""])
    lines.extend(_idea_table(deferred_ideas[:limit]))
    lines.extend(["", "## Next Command", "", "```powershell", str(summary.get("next_enrichment_command") or ""), "```", ""])
    return "\n".join(lines)


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
        moneyball_score = _moneyball_score(scorecard)
        quant_score = _quant_score(scorecard)
        rows.append(
            {
                "index": index,
                "score": _rank_score(moneyball_score=moneyball_score, quant_score=quant_score),
                "moneyball_score": moneyball_score,
                "quant_score": quant_score,
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


def _enrichment_readiness(
    coverage: Mapping[str, Any],
    *,
    min_coverage_percent_for_enrichment: float,
) -> dict[str, Any]:
    coverage_percent = float(coverage.get("fundamentals_coverage_percent") or 0.0)
    required = max(0.0, min(100.0, float(min_coverage_percent_for_enrichment)))
    ready = coverage_percent >= required
    return {
        "min_coverage_percent_for_enrichment": required,
        "ready_for_expensive_enrichment": ready,
        "scan_recommendation": "run_evidence_enrichment_on_passed"
        if ready
        else "continue_fundamentals_cache_fill",
        "readiness_reason": (
            f"Fundamentals coverage {coverage_percent:.2f}% meets required {required:.1f}%."
            if ready
            else f"Fundamentals coverage {coverage_percent:.2f}% is below required {required:.1f}%."
        ),
    }


def _readiness_label(summary: Mapping[str, Any]) -> str:
    return "ready" if summary.get("ready_for_expensive_enrichment") else "not ready"


def _runs_remaining_text(summary: Mapping[str, Any]) -> str:
    value = summary.get("fundamentals_estimated_fetch_runs_remaining")
    return "unknown" if value is None else str(value)


def _idea_table(ideas: list[Mapping[str, Any]]) -> list[str]:
    if not ideas:
        return ["No rows."]
    lines = [
        "| Symbol | Rank | Rank Score | Moneyball | Quant | Quality | Growth | Valuation | Safety | Decision | Reason |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for idea in ideas:
        scorecard = idea.get("quality_growth_scorecard") or {}
        scan = idea.get("python_first_pass_scan") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(idea.get("symbol")),
                    _cell(scan.get("rank")),
                    _cell(scan.get("rank_score", scan.get("score"))),
                    _cell(scan.get("moneyball_score")),
                    _cell(scan.get("quant_score")),
                    _cell(scorecard.get("quality_score")),
                    _cell(scorecard.get("growth_score")),
                    _cell(scorecard.get("valuation_score")),
                    _cell(scorecard.get("safety_score")),
                    _cell(scan.get("decision")),
                    _cell(scan.get("reason")),
                ]
            )
            + " |"
        )
    return lines


def _cell(value: Any) -> str:
    text = "n/a" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _moneyball_score(scorecard: Mapping[str, Any]) -> float:
    try:
        return float(scorecard.get("superscore") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _quant_score(scorecard: Mapping[str, Any]) -> float:
    quality = _component_score(scorecard, "quality_score")
    growth = _component_score(scorecard, "growth_score")
    valuation = _component_score(scorecard, "valuation_score")
    safety = _component_score(scorecard, "safety_score")
    return round((quality * 0.35) + (growth * 0.30) + (valuation * 0.20) + (safety * 0.15), 1)


def _component_score(scorecard: Mapping[str, Any], key: str) -> float:
    try:
        return float(scorecard.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rank_score(*, moneyball_score: float, quant_score: float) -> float:
    return round((moneyball_score * 0.70) + (quant_score * 0.30), 1)


def _rank_reason(*, moneyball_score: float, quant_score: float, rank_score: float) -> str:
    return (
        f"Moneyball {moneyball_score:.1f} weighted 70%; "
        f"Quant {quant_score:.1f} weighted 30%; rank score {rank_score:.1f}."
    )


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
    "build_python_first_pass_markdown",
    "fetch_yfinance_fundamental_metrics",
    "run_python_first_pass_scan",
]
