"""Deterministic research-queue selection for evidence-ready long-term ideas."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


FORMULA_VERSION = "research_selection_v1"
SCHEMA_VERSION = 1
DEFAULT_PROTECTED_SYMBOLS = {"FXAIX"}


@dataclass(frozen=True)
class ResearchSelectionResult:
    selected: list[dict[str, Any]]
    deferred: list[dict[str, Any]]
    ranked: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    summary: dict[str, Any]


def select_research_queue(
    ideas: Iterable[Mapping[str, Any]],
    *,
    campaign_id: str = "",
    current_symbols: Iterable[str] | None = None,
    recent_research_symbols: Iterable[str] | None = None,
    protected_symbols: Iterable[str] | None = None,
    top_percent: float = 20.0,
    min_count: int = 10,
    max_count: int = 50,
) -> ResearchSelectionResult:
    """Select a relative top slice for committee research.

    The scoring formula intentionally mirrors the active rules file at a
    deterministic pre-LLM level: prefer understandable quality-growth companies,
    valuation/safety discipline, balance-sheet resilience, current evidence, and
    recent earnings context while never advancing protected benchmark holdings.
    """

    idea_rows = [dict(item) for item in ideas if isinstance(item, Mapping)]
    current = _symbols(current_symbols)
    recent = _symbols(recent_research_symbols)
    protected = DEFAULT_PROTECTED_SYMBOLS | _symbols(protected_symbols)
    campaign_key = campaign_id or "manual"
    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for raw in idea_rows:
        row = dict(raw)
        symbol = _symbol(row)
        if not symbol:
            continue
        if symbol in protected:
            skipped.append(
                {
                    "symbol": symbol,
                    "skip_reason": "protected_symbol",
                    "selected_for_committee": False,
                }
            )
            continue
        scored.append(_with_selection_metadata(row, campaign_id=campaign_key, current=current, recent=recent))

    scored.sort(
        key=lambda item: (
            -float(item["research_selection"]["selection_score"]),
            str(item.get("symbol") or ""),
        )
    )
    selection_count = _selection_count(
        len(scored),
        top_percent=top_percent,
        min_count=min_count,
        max_count=max_count,
    )
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for index, row in enumerate(scored, start=1):
        metadata = dict(row["research_selection"])
        metadata["overall_rank"] = index
        if index <= selection_count:
            metadata["selected_rank"] = index
            metadata["deferred_rank"] = None
            metadata["selected_for_committee"] = True
            metadata["defer_reasons"] = []
            row["research_selection"] = metadata
            selected.append(row)
        else:
            metadata["selected_rank"] = None
            metadata["deferred_rank"] = index - selection_count
            metadata["selected_for_committee"] = False
            metadata["defer_reasons"] = _defer_reasons(row)
            row["research_selection"] = metadata
            deferred.append(row)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "mode": "research_selection",
        "formula_version": FORMULA_VERSION,
        "campaign_id": campaign_key,
        "input_count": len(idea_rows),
        "scored_count": len(scored),
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "ranked_count": len(scored),
        "skipped_count": len(skipped),
        "skipped_protected_symbols": [item["symbol"] for item in skipped if item.get("skip_reason") == "protected_symbol"],
        "top_percent": float(top_percent),
        "min_count": int(min_count),
        "max_count": int(max_count),
    }
    return ResearchSelectionResult(selected=selected, deferred=deferred, ranked=scored, skipped=skipped, summary=summary)


def _with_selection_metadata(
    idea: Mapping[str, Any],
    *,
    campaign_id: str,
    current: set[str],
    recent: set[str],
) -> dict[str, Any]:
    row = dict(idea)
    symbol = _symbol(row)
    evidence_hash = _compute_evidence_packet_hash(row)
    score, reasons, penalties, portfolio_context = _selection_score(row, current=current, recent=recent)
    selection_id = _hash_payload(
        {
            "campaign_id": campaign_id,
            "symbol": symbol,
            "evidence_packet_hash": evidence_hash,
            "formula_version": FORMULA_VERSION,
        }
    )[:20]
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "formula_version": FORMULA_VERSION,
        "campaign_id": campaign_id,
        "research_selection_id": f"rs-{selection_id}",
        "evidence_packet_hash": evidence_hash,
        "selection_score": score,
        "overall_rank": None,
        "selected_rank": None,
        "deferred_rank": None,
        "selected_for_committee": False,
        "portfolio_context": portfolio_context,
        "selection_reasons": reasons,
        "selection_penalties": penalties,
        "defer_reasons": [],
    }
    row["research_selection"] = metadata
    row["source_notes"] = _source_notes(row, metadata)
    row["evidence_brief"] = _evidence_brief(row, metadata)
    return row


def _selection_score(
    idea: Mapping[str, Any],
    *,
    current: set[str],
    recent: set[str],
) -> tuple[float, list[str], list[str], str]:
    symbol = _symbol(idea)
    scorecard = _mapping(idea.get("quality_growth_scorecard"))
    quality_growth = _scorecard_signal(scorecard)
    valuation_safety = _valuation_safety_signal(scorecard)
    article_evidence = _article_evidence_signal(idea.get("relevant_news") or [])
    earnings = _earnings_signal(_mapping(idea.get("latest_earnings")))
    evidence = _evidence_brief_signal(str(idea.get("evidence_brief") or ""))
    score = (
        quality_growth * 0.45
        + valuation_safety * 0.20
        + article_evidence * 0.15
        + earnings * 0.10
        + evidence * 0.10
    )
    reasons = [
        f"quality_growth={quality_growth:.1f}",
        f"valuation_safety={valuation_safety:.1f}",
        f"article_evidence={article_evidence:.1f}",
        f"earnings_context={earnings:.1f}",
        f"evidence_brief={evidence:.1f}",
    ]
    penalties: list[str] = []
    warnings = [str(item) for item in idea.get("enrichment_warnings") or idea.get("warnings") or [] if str(item)]
    if warnings:
        penalty = min(25.0, 8.0 * len(warnings))
        score -= penalty
        penalties.append(f"evidence warning penalty -{penalty:.1f}")
    portfolio_context = "new_name"
    if symbol in current:
        score -= 8.0
        portfolio_context = "current_holding"
        penalties.append("current holding deprioritized -8.0")
    elif symbol in recent:
        score -= 5.0
        portfolio_context = "recently_researched"
        penalties.append("recent research deprioritized -5.0")
    return round(max(0.0, min(100.0, score)), 2), reasons, penalties, portfolio_context


def _scorecard_signal(scorecard: Mapping[str, Any]) -> float:
    superscore = _number(scorecard.get("superscore"))
    if superscore > 0:
        return superscore
    quality = _number(scorecard.get("quality_score"))
    growth = _number(scorecard.get("growth_score"))
    valuation = _number(scorecard.get("valuation_score"))
    safety = _number(scorecard.get("safety_score"))
    return round((quality * 0.40) + (growth * 0.35) + (valuation * 0.10) + (safety * 0.15), 1)


def _valuation_safety_signal(scorecard: Mapping[str, Any]) -> float:
    valuation = _number(scorecard.get("valuation_score"))
    safety = _number(scorecard.get("safety_score"))
    quality = _number(scorecard.get("quality_score"))
    return round((valuation * 0.45) + (safety * 0.35) + (quality * 0.20), 1)


def _article_evidence_signal(news: Iterable[Any]) -> float:
    score = 0.0
    for item in news:
        if not isinstance(item, Mapping):
            continue
        impact = str(item.get("impact_category") or "").lower()
        score += 22.0 if "high" in impact else 15.0 if "medium" in impact else 8.0
        score += min(8.0, _number(item.get("relevance_score")) * 8.0)
    return round(min(100.0, score), 1)


def _earnings_signal(earnings: Mapping[str, Any]) -> float:
    if not earnings:
        return 0.0
    confidence = _number(earnings.get("confidence"))
    if 0 < confidence <= 1:
        return round(confidence * 100.0, 1)
    if confidence > 1:
        return min(100.0, confidence)
    return 65.0


def _evidence_brief_signal(brief: str) -> float:
    if not brief:
        return 0.0
    score = 30.0
    checks = {
        "research_evidence_brief_v1": 25.0,
        "Article evidence:": 25.0,
        "Scorecard": 10.0,
        "Warnings:": -15.0,
    }
    for text, weight in checks.items():
        if text in brief:
            score += weight
    return round(max(0.0, min(100.0, score)), 1)


def _selection_count(row_count: int, *, top_percent: float, min_count: int, max_count: int) -> int:
    if row_count <= 0:
        return 0
    relative = math.ceil(row_count * max(0.0, float(top_percent)) / 100.0)
    requested = max(int(min_count), relative)
    requested = min(int(max_count), requested)
    return max(1, min(row_count, requested))


def _defer_reasons(row: Mapping[str, Any]) -> list[str]:
    metadata = _mapping(row.get("research_selection"))
    reasons = list(metadata.get("selection_penalties") or [])
    if not reasons:
        reasons.append("below selected relative research slice")
    return [str(reason) for reason in reasons]


def _source_notes(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[str]:
    notes = [str(item) for item in row.get("source_notes") or [] if str(item)]
    notes.append(
        "Research selection: "
        f"research_selection_id={metadata['research_selection_id']}; "
        f"formula={FORMULA_VERSION}; "
        f"selection_score={metadata['selection_score']}; "
        f"evidence_hash={metadata['evidence_packet_hash'][:12]}"
    )
    return notes


def _evidence_brief(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    brief = str(row.get("evidence_brief") or "")
    line = (
        "Research selection: "
        f"{metadata['research_selection_id']} | "
        f"score {metadata['selection_score']} | "
        f"{metadata['portfolio_context']} | "
        f"{', '.join(metadata['selection_reasons'])}"
    )
    return f"{brief}\n{line}".strip()


def _compute_evidence_packet_hash(idea: Mapping[str, Any]) -> str:
    payload = {
        "symbol": _symbol(idea),
        "quality_growth_scorecard": idea.get("quality_growth_scorecard") or {},
        "latest_earnings": idea.get("latest_earnings") or {},
        "news_urls": [
            str(item.get("url") or "")
            for item in (idea.get("relevant_news") or [])
            if isinstance(item, Mapping)
        ],
        "warnings": idea.get("enrichment_warnings") or idea.get("warnings") or [],
        "evidence_brief": idea.get("evidence_brief") or "",
    }
    return _hash_payload(payload)


def _hash_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").upper().strip()


def _symbols(symbols: Iterable[str] | None) -> set[str]:
    return {str(symbol).upper().strip() for symbol in (symbols or []) if str(symbol).strip()}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float:
    try:
        if isinstance(value, str):
            value = value.replace("%", "").replace("x", "").replace("$", "").replace(",", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "FORMULA_VERSION",
    "ResearchSelectionResult",
    "select_research_queue",
]
