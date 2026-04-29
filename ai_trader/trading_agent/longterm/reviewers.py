"""Deterministic reviewer helpers for long-term research packets."""

from __future__ import annotations

from dataclasses import dataclass, field

from research.research_packet import ResearchPacket


@dataclass(frozen=True)
class ReviewResult:
    reviewer: str
    score: float
    passed: bool
    support: list[str] = field(default_factory=list)
    objections: list[str] = field(default_factory=list)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


class BusinessStoryReviewer:
    """Check whether the business story is understandable and thesis-shaped."""

    def review(self, packet: ResearchPacket) -> ReviewResult:
        score = 20.0
        support: list[str] = []
        objections: list[str] = []

        if len(packet.business_summary.split()) >= 8:
            score += 20
            support.append("Business summary is specific enough to understand.")
        else:
            objections.append("Business summary is too thin.")

        if len(packet.thesis_summary.split()) >= 8:
            score += 20
            support.append("Clear thesis explains what should persist or improve.")
        else:
            objections.append("Thesis is vague.")

        if packet.primary_growth_driver:
            score += 15
            support.append("Primary growth driver is identified.")
        else:
            objections.append("Primary growth driver is missing.")

        if packet.industry_context:
            score += 10
            support.append("Industry context is present.")

        if packet.confirming_signals and packet.invalidation_conditions:
            score += 15
            support.append("Thesis has both confirming signals and invalidation conditions.")
        else:
            objections.append("Thesis needs confirming signals and invalidation conditions.")

        return ReviewResult(
            reviewer="BusinessStoryReviewer",
            score=min(score, 100.0),
            passed=score >= 70,
            support=support,
            objections=objections,
        )


class BalanceSheetReviewer:
    """Score balance-sheet language for obvious long-term fragility."""

    GOOD_TERMS = ("net cash", "strong free cash flow", "manageable debt", "low debt", "cash rich")
    BAD_TERMS = ("high leverage", "refinancing risk", "weak cash flow", "debt stress", "liquidity risk")

    def review(self, packet: ResearchPacket) -> ReviewResult:
        text = packet.balance_sheet_assessment or ""
        score = 55.0
        support: list[str] = []
        objections: list[str] = []

        if not text:
            return ReviewResult(
                reviewer="BalanceSheetReviewer",
                score=40.0,
                passed=False,
                objections=["Balance-sheet assessment is missing."],
            )

        if _contains_any(text, self.GOOD_TERMS):
            score += 30
            support.append("Balance sheet appears resilient.")

        if _contains_any(text, self.BAD_TERMS):
            score -= 35
            objections.append("Leverage or financing stress is present.")

        return ReviewResult(
            reviewer="BalanceSheetReviewer",
            score=max(0.0, min(score, 100.0)),
            passed=score >= 65,
            support=support,
            objections=objections,
        )


class QualityAtReasonablePriceReviewer:
    """Combine quality and valuation without letting one fully excuse the other."""

    def review(self, packet: ResearchPacket) -> ReviewResult:
        quality = float(packet.quality_score or 0.0)
        valuation = float(packet.valuation_score or 0.0)
        score = round((quality * 0.6) + (valuation * 0.4), 2)
        support: list[str] = []
        objections: list[str] = []

        if quality >= 75:
            support.append("Quality score supports long-term ownership.")
        else:
            objections.append("Quality score is not high enough for this strategy.")

        if valuation >= 60:
            support.append("Valuation is acceptable relative to quality.")
        else:
            objections.append("Valuation score is too weak for a disciplined entry.")

        return ReviewResult(
            reviewer="QualityAtReasonablePriceReviewer",
            score=score,
            passed=quality >= 75 and valuation >= 60 and score >= 70,
            support=support,
            objections=objections,
        )
