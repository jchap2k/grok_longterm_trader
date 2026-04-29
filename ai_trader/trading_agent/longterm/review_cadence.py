"""Review cadence policy for long-term holdings and candidates."""

from __future__ import annotations

from dataclasses import dataclass

from research.research_packet import CompanyCategory, ResearchPacket


@dataclass(frozen=True)
class CadenceAssignment:
    review_cadence: str
    expected_hold_horizon: str
    reason: str


class ReviewCadencePolicy:
    """Assign slower or faster review cadence based on category and risk."""

    BASE_CADENCE = {
        CompanyCategory.SLOW_GROWER: ("quarterly", "multi-year"),
        CompanyCategory.STALWART: ("monthly", "multi-year"),
        CompanyCategory.FAST_GROWER: ("monthly", "1-5 years"),
        CompanyCategory.CYCLICAL: ("monthly", "cycle-dependent"),
        CompanyCategory.TURNAROUND: ("biweekly", "thesis-dependent"),
        CompanyCategory.ASSET_PLAY: ("monthly", "catalyst-dependent"),
    }

    RISK_TERMS = ("debt", "leverage", "refinancing", "liquidity", "cyclical")

    def assign(self, packet: ResearchPacket) -> CadenceAssignment:
        category = packet.company_category or CompanyCategory.STALWART
        cadence, horizon = self.BASE_CADENCE[category]
        risk_text = packet.balance_sheet_assessment.lower()

        if any(term in risk_text for term in self.RISK_TERMS):
            cadence = "biweekly"
            reason = "Risk language requires a faster review cadence."
        else:
            reason = f"{category.value} default cadence."

        return CadenceAssignment(
            review_cadence=cadence,
            expected_hold_horizon=horizon,
            reason=reason,
        )
