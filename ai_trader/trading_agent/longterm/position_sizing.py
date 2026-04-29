"""Position sizing policy for the long-term active sleeve."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any

from portfolio.portfolio_profile import PortfolioProfile
from research.research_packet import ResearchPacket


@dataclass(frozen=True)
class SizingRecommendation:
    action: str
    target_size_pct: float
    estimated_dollars: float
    reason: str


class PositionSizingPolicy:
    """Start small, add on confirmation, and preserve protected holdings."""

    STARTER_CAP_PCT = 3.0
    CONFIRMED_CAP_PCT = 8.0

    def recommend(
        self,
        packet: ResearchPacket,
        *,
        profile: PortfolioProfile,
        decision: Mapping[str, Any],
        current_position_pct: float = 0.0,
        confirmation_count: int = 0,
    ) -> SizingRecommendation:
        symbol = packet.symbol.upper()
        current_position_pct = float(current_position_pct or 0.0)
        if symbol in profile.protected_symbols:
            return SizingRecommendation(
                action="PROTECTED_HOLD",
                target_size_pct=current_position_pct,
                estimated_dollars=round(profile.tradable_capital * current_position_pct / 100.0, 2),
                reason="Protected symbols are operationally untouchable.",
            )

        recommendation = str(decision.get("recommendation", "")).upper()
        confidence = int(decision.get("confidence") or 0)
        suggested = float(decision.get("suggested_size_pct") or 0.0)

        if recommendation in {"PASS", "SELL", "REDUCE"} or confidence < 70:
            return SizingRecommendation(
                action="PASS",
                target_size_pct=current_position_pct,
                estimated_dollars=round(profile.tradable_capital * current_position_pct / 100.0, 2),
                reason="Decision is not strong enough to add active capital.",
            )

        cap = self.CONFIRMED_CAP_PCT if confirmation_count >= 2 and confidence >= 85 else self.STARTER_CAP_PCT
        target = round(min(suggested or cap, cap), 2)
        action = "START" if current_position_pct <= 0 else "ADD"
        if target <= current_position_pct:
            action = "HOLD"
            target = current_position_pct

        return SizingRecommendation(
            action=action,
            target_size_pct=target,
            estimated_dollars=round(profile.tradable_capital * target / 100.0, 2),
            reason="Starter size unless conviction is confirmed by multiple signals.",
        )
