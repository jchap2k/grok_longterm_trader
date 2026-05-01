"""Outcome analysis for future rebalance-score tuning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.review_status import THESIS_RISK_BUCKETS, review_risk_bucket


@dataclass(frozen=True)
class BucketOutcomeSummary:
    bucket: str
    evaluated_count: int = 0
    pending_count: int = 0
    average_excess_return_pct: float = 0.0
    beat_rate_pct: float = 0.0
    confidence_weighted_excess_return_pct: float = 0.0
    interpretation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RebalanceOutcomeReport:
    evaluated_decisions: int
    pending_outcomes: int
    bucket_summaries: list[BucketOutcomeSummary]
    rubric_reference: str
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_decisions": self.evaluated_decisions,
            "pending_outcomes": self.pending_outcomes,
            "bucket_summaries": [summary.to_dict() for summary in self.bucket_summaries],
            "rubric_reference": self.rubric_reference,
            "notes": self.notes,
        }


class RebalanceOutcomeAnalyzer:
    """Summarize evaluated decisions by thesis/review-risk bucket."""

    def __init__(self, journal: LongTermDecisionJournal):
        self.journal = journal

    def build(self, *, limit: int = 100) -> RebalanceOutcomeReport:
        rows = self.journal.list_recent_decisions(limit=limit)
        latest_reviews = self.journal.latest_thesis_review_by_symbol()
        grouped: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in THESIS_RISK_BUCKETS}
        pending_by_bucket: dict[str, int] = {bucket: 0 for bucket in THESIS_RISK_BUCKETS}

        for row in rows:
            bucket = _bucket_for_row(row, latest_reviews)
            if row.get("excess_return_pct") is None:
                pending_by_bucket[bucket] += 1
                continue
            grouped[bucket].append(row)

        summaries = [
            _summarize_bucket(bucket, grouped[bucket], pending_by_bucket[bucket])
            for bucket in THESIS_RISK_BUCKETS
            if grouped[bucket] or pending_by_bucket[bucket]
        ]
        evaluated_count = sum(summary.evaluated_count for summary in summaries)
        pending_count = sum(summary.pending_count for summary in summaries)
        return RebalanceOutcomeReport(
            evaluated_decisions=evaluated_count,
            pending_outcomes=pending_count,
            bucket_summaries=summaries,
            rubric_reference="Interpret alongside ai_trader/rules/active_rules.txt quality, valuation, thesis-breaker, and FXAIX benchmark guardrails.",
            notes=[
                "Report is read-only and does not change rebalance weights.",
                "Pending outcomes are counted separately so unevaluated decisions do not bias averages.",
                "Use this as evidence before changing review-risk source-score adjustments.",
            ],
        )


def build_rebalance_outcome_markdown(report: RebalanceOutcomeReport) -> str:
    lines = [
        "# Rebalance Outcome Analysis",
        "",
        "Read-only evidence for future review-aware rebalance scoring. No planner weights were changed.",
        "",
        f"- Evaluated decisions: {report.evaluated_decisions}",
        f"- Pending outcomes: {report.pending_outcomes}",
        f"- Rubric reference: {report.rubric_reference}",
        "",
        "| Bucket | Evaluated | Pending | Avg Excess Return | Beat Rate | Confidence-Weighted Excess |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in report.bucket_summaries:
        lines.append(
            "| {bucket} | {evaluated} | {pending} | {avg:.2f}% | {beat:.2f}% | {weighted:.2f}% |".format(
                bucket=summary.bucket,
                evaluated=summary.evaluated_count,
                pending=summary.pending_count,
                avg=summary.average_excess_return_pct,
                beat=summary.beat_rate_pct,
                weighted=summary.confidence_weighted_excess_return_pct,
            )
        )
    lines.extend(["", "## Interpretation", ""])
    for summary in report.bucket_summaries:
        if summary.interpretation:
            lines.append(f"- {summary.bucket}: {summary.interpretation}")
    if not any(summary.interpretation for summary in report.bucket_summaries):
        lines.append("- Not enough evaluated outcomes yet for a bucket-specific read.")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report.notes)
    return "\n".join(lines) + "\n"


def _bucket_for_row(
    row: Mapping[str, Any],
    latest_reviews: Mapping[str, Mapping[str, Any]],
) -> str:
    symbol = str(row.get("symbol") or "").upper()
    review = latest_reviews.get(symbol)
    if not review:
        return "unreviewed"
    status = {
        "review_due": str(review.get("thesis_state") or "").lower() in {"broken", "weakening"},
        "thesis_state": review.get("thesis_state"),
    }
    return review_risk_bucket(status)


def _summarize_bucket(
    bucket: str,
    rows: list[Mapping[str, Any]],
    pending_count: int,
) -> BucketOutcomeSummary:
    if not rows:
        return BucketOutcomeSummary(
            bucket=bucket,
            pending_count=pending_count,
            interpretation="No evaluated outcomes yet.",
        )
    excess_returns = [float(row.get("excess_return_pct") or 0.0) for row in rows]
    confidences = [max(0.0, float(row.get("confidence") or 0.0)) for row in rows]
    confidence_total = sum(confidences)
    weighted = (
        sum(excess * confidence for excess, confidence in zip(excess_returns, confidences))
        / confidence_total
        if confidence_total
        else sum(excess_returns) / len(excess_returns)
    )
    average = sum(excess_returns) / len(excess_returns)
    beat_rate = sum(1 for value in excess_returns if value > 0.0) / len(excess_returns) * 100.0
    return BucketOutcomeSummary(
        bucket=bucket,
        evaluated_count=len(rows),
        pending_count=pending_count,
        average_excess_return_pct=round(average, 4),
        beat_rate_pct=round(beat_rate, 4),
        confidence_weighted_excess_return_pct=round(weighted, 4),
        interpretation=_interpret_bucket(bucket, average, len(rows)),
    )


def _interpret_bucket(bucket: str, average_excess_return_pct: float, count: int) -> str:
    if count < 3:
        sample_note = "sample is still small; treat as directional only"
    else:
        sample_note = "sample is large enough to start comparing against future weight changes"
    if bucket in {"broken", "weakening", "stale", "review_due"} and average_excess_return_pct < 0:
        return (
            "underperformed FXAIX; this supports giving review-risk holdings "
            f"a higher source score, but {sample_note}."
        )
    if bucket in {"broken", "weakening", "stale", "review_due"}:
        return (
            "did not underperform FXAIX in this sample; be careful about increasing "
            f"review-risk penalties, and {sample_note}."
        )
    if bucket == "healthy" and average_excess_return_pct > 0:
        return f"outperformed FXAIX; healthy thesis status appears useful, but {sample_note}."
    return f"{sample_note}."


__all__ = [
    "BucketOutcomeSummary",
    "RebalanceOutcomeAnalyzer",
    "RebalanceOutcomeReport",
    "build_rebalance_outcome_markdown",
]
