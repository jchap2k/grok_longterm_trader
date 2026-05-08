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


class MarginOfSafetyReviewer:
    """Graham-style advisory check for overpayment and permanent-loss risk."""

    NORMALIZED_SUPPORT_TERMS = (
        "normalized earnings",
        "normalized free cash flow",
        "free cash flow",
        "earnings yield",
        "reasonable p/e",
        "reasonable pe",
        "reasonable valuation",
        "staged buying",
        "staged sizing",
        "starter position",
    )
    OVERPAYMENT_TERMS = (
        "extreme p/e",
        "extreme pe",
        "overvalued",
        "overpayment",
        "euphoria",
        "priced for perfection",
        "optimistic forward estimates",
        "valuation mistake",
    )
    PERMANENT_LOSS_TERMS = (
        "high leverage",
        "dilution",
        "weak cash conversion",
        "accounting",
        "fraud",
        "disruption",
        "refinancing risk",
        "thesis fragility",
    )

    def review(self, packet: ResearchPacket) -> ReviewResult:
        quality = float(packet.quality_score or 0.0)
        valuation = float(packet.valuation_score or 0.0)
        text = _packet_text(packet)
        score = 50.0
        support: list[str] = []
        objections: list[str] = []

        if valuation >= 70:
            score += 20
            support.append("Margin of safety appears supported by valuation discipline.")
        elif valuation >= 50:
            score += 10
            support.append("Margin of safety is plausible but should be sized carefully.")
        elif valuation > 0:
            score -= 20
            objections.append("Overpayment risk: valuation score leaves little margin of safety.")
        else:
            score -= 10
            objections.append("Margin of safety is unclear because valuation evidence is missing.")

        if quality >= 80 and valuation >= 45:
            score += 8
            support.append("Business quality can support a moderate premium if evidence holds.")
        elif quality >= 80 and valuation < 45:
            objections.append("High quality does not fully offset weak price support.")

        if _contains_any(text, self.NORMALIZED_SUPPORT_TERMS):
            score += 12
            support.append("Normalized earnings or cash-flow support is present.")
        else:
            objections.append("Normalized earnings/cash-flow support is not explicit.")

        if _contains_any(text, BalanceSheetReviewer.GOOD_TERMS):
            score += 8
            support.append("Balance sheet reduces permanent capital loss risk.")

        if _contains_any(text, self.OVERPAYMENT_TERMS):
            score -= 20
            objections.append("Overpayment risk: market quote may already assume too much.")

        if _contains_any(text, self.PERMANENT_LOSS_TERMS):
            score -= 22
            objections.append("Permanent capital loss risk needs explicit thesis review.")

        if not support:
            objections.append("No Graham-style margin-of-safety support was identified.")

        bounded = max(0.0, min(score, 100.0))
        return ReviewResult(
            reviewer="MarginOfSafetyReviewer",
            score=round(bounded, 2),
            passed=bounded >= 60.0,
            support=support,
            objections=objections,
        )


class QualityDurabilityReviewer:
    """Look for quality-investing patterns and common quality traps."""

    QUALITY_PATTERNS = (
        "recurring revenue",
        "installed base",
        "pricing power",
        "market share",
        "share gains",
        "switching costs",
        "stable oligopoly",
        "rational competition",
        "brand strength",
        "distribution advantage",
        "cost to replicate",
        "strong free cash flow",
    )
    QUALITY_TRAPS = (
        "cyclical",
        "high leverage",
        "dependency",
        "one customer",
        "technological disruption",
        "customer preference",
        "good-enough",
        "price-war",
        "weak cash conversion",
        "fragmented",
    )

    def review(self, packet: ResearchPacket) -> ReviewResult:
        text = " ".join(
            [
                packet.business_summary,
                packet.thesis_summary,
                packet.primary_growth_driver,
                packet.industry_context,
                packet.balance_sheet_assessment,
            ]
        )
        score = 50.0
        support: list[str] = []
        objections: list[str] = []

        for pattern in self.QUALITY_PATTERNS:
            if pattern in text.lower():
                score += 8
                support.append(f"Quality pattern present: {pattern}.")

        for trap in self.QUALITY_TRAPS:
            if trap in text.lower():
                score -= 10
                objections.append(f"Quality trap risk present: {trap}.")

        if not support:
            objections.append("No durable quality pattern is clearly identified.")

        return ReviewResult(
            reviewer="QualityDurabilityReviewer",
            score=max(0.0, min(score, 100.0)),
            passed=score >= 70 and bool(support),
            support=support,
            objections=objections,
        )


def _packet_text(packet: ResearchPacket) -> str:
    return " ".join(
        [
            packet.business_summary,
            packet.thesis_summary,
            packet.primary_growth_driver,
            packet.industry_context,
            packet.balance_sheet_assessment,
            " ".join(packet.source_notes),
            " ".join(packet.confirming_signals),
            " ".join(packet.invalidation_conditions),
            packet.evidence_brief,
        ]
    ).lower()
