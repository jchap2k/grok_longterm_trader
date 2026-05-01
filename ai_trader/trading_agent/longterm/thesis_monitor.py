"""Thesis review monitoring for long-term positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from research.research_packet import ResearchPacket


@dataclass(frozen=True)
class ThesisStatus:
    review_due: bool
    days_since_review: int
    thesis_state: str
    matched_invalidation_conditions: list[str]
    reason: str


class ThesisMonitor:
    """Detect due reviews and obvious thesis breaks from supplied evidence."""

    CADENCE_DAYS = {
        "weekly": 7,
        "biweekly": 14,
        "monthly": 30,
        "quarterly": 90,
    }
    WEAKENING_SIGNALS = [
        "margin pressure",
        "slowing growth",
        "debt concern",
        "guidance cut",
        "share loss",
        "market share loss",
        "churn",
        "regulatory pressure",
        "management turnover",
    ]

    def __init__(self, *, today: date | None = None):
        self.today = today or date.today()

    def evaluate(
        self,
        packet: ResearchPacket,
        *,
        last_review_date: date,
        current_evidence: list[str] | None = None,
    ) -> ThesisStatus:
        days_since = (self.today - last_review_date).days
        cadence_days = self.CADENCE_DAYS.get((packet.review_cadence or "monthly").lower(), 30)
        evidence_text = " ".join(current_evidence or []).lower()
        matched = [
            condition
            for condition in packet.invalidation_conditions
            if condition and condition.lower() in evidence_text
        ]
        weakening_matches = [
            signal for signal in self.WEAKENING_SIGNALS if signal in evidence_text
        ]
        if matched:
            thesis_state = "broken"
            matched_conditions = matched
            reason = "One or more invalidation conditions matched current evidence."
        elif weakening_matches:
            thesis_state = "weakening"
            matched_conditions = weakening_matches
            reason = "Current evidence contains thesis-weakening risk language."
        elif days_since >= cadence_days:
            thesis_state = "stale"
            matched_conditions = []
            reason = "Review cadence has elapsed without fresh confirming evidence."
        else:
            thesis_state = "healthy"
            matched_conditions = []
            reason = "No invalidation or weakening condition matched current evidence."
        return ThesisStatus(
            review_due=days_since >= cadence_days,
            days_since_review=days_since,
            thesis_state=thesis_state,
            matched_invalidation_conditions=matched_conditions,
            reason=reason,
        )
