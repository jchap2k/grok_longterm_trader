"""
TierRouter - Core decision logic for the Tiered Enrichment Strategy.

This module is intentionally kept pure (no I/O, no LLM calls) so it can be
unit tested in complete isolation and backtested against historical data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .tier_definitions import (
    TIER_0_DETERMINISTIC,
    TIER_1_LIGHT,
    TIER_2_STANDARD,
    TIER_3_DEEP,
    get_tier_name,
)
from .tier_threshold_stats import TierThresholdStats


@dataclass(frozen=True)
class TierRoutingResult:
    tier: int
    reasons: List[str]

    def __str__(self) -> str:
        return f"Tier {self.tier} ({get_tier_name(self.tier)}) - {', '.join(self.reasons[:2])}"


def route_enrichment_tier(
    *,
    # Core signals from research selection
    research_selection_score: float,
    company_category: Optional[str] = None,
    margin_of_safety_score: Optional[float] = None,
    # Deterministic reviewer signals
    reviewer_average_score: Optional[float] = None,
    has_hard_quality_objection: bool = False,
    has_hard_mos_objection: bool = False,
    # Context
    is_existing_holding: bool = False,
    portfolio_impact_pct: Optional[float] = None,
    # Advisory signals
    kronos_advisory_strength: Optional[float] = None,
    # Override
    high_conviction_override: bool = False,
    # Dynamic threshold stats (optional)
    threshold_stats: Optional[TierThresholdStats] = None,
) -> TierRoutingResult:
    """
    Decide which enrichment tier an idea should receive.

    This function is deliberately kept pure so it can be unit tested
    and backtested extensively.
    """
    reasons: List[str] = []

    # High-conviction override takes absolute priority
    if high_conviction_override:
        reasons.append("High-conviction override active")
        return TierRoutingResult(TIER_3_DEEP, reasons)

    # Calculate base score from research selection + reviewer strength
    base_score = research_selection_score
    if reviewer_average_score is not None:
        # Blend research selection with reviewer strength (reviewers are very important)
        base_score = (research_selection_score * 0.6) + (reviewer_average_score * 0.4)

    # Start with a default tier based on blended score
    if base_score < 38:
        tier = TIER_0_DETERMINISTIC
        reasons.append(f"Low blended score ({base_score:.1f})")
    elif base_score < 55:
        tier = TIER_1_LIGHT
        reasons.append(f"Moderate blended score ({base_score:.1f})")
    else:
        tier = TIER_2_STANDARD
        reasons.append(f"Good blended score ({base_score:.1f})")

    # Company category adjustments
    if company_category in ("fast_grower", "stalwart"):
        if tier < TIER_2_STANDARD:
            tier = max(tier, TIER_1_LIGHT)
            reasons.append(f"Category boost: {company_category}")
        if base_score >= 58 and tier == TIER_2_STANDARD:
            tier = TIER_3_DEEP
            reasons.append(f"Strong {company_category} with solid score")
    elif company_category in ("cyclical", "turnaround"):
        if tier > TIER_1_LIGHT:
            tier = TIER_1_LIGHT
            reasons.append(f"Category caution: {company_category}")

    # Margin of Safety influence
    if margin_of_safety_score is not None:
        if margin_of_safety_score >= 70 and tier == TIER_2_STANDARD:
            tier = TIER_3_DEEP
            reasons.append("Strong margin of safety")
        elif margin_of_safety_score < 45 and tier >= TIER_2_STANDARD:
            tier = TIER_1_LIGHT
            reasons.append("Weak margin of safety")

    # Hard objections from deterministic reviewers
    if has_hard_quality_objection or has_hard_mos_objection:
        tier = min(tier, TIER_1_LIGHT)
        reasons.append("Hard objection from deterministic reviewers")

    # Kronos advisory as a positive signal (helps lift from Tier 0 to Tier 1, and can help borderline cases)
    if kronos_advisory_strength is not None and kronos_advisory_strength >= 0.55:
        if tier == TIER_0_DETERMINISTIC:
            tier = TIER_1_LIGHT
            reasons.append(f"Kronos advisory lift (strength={kronos_advisory_strength:.2f})")
        elif tier == TIER_1_LIGHT and kronos_advisory_strength >= 0.75:
            tier = TIER_2_STANDARD
            reasons.append(f"Strong Kronos advisory promoted to Tier 2 (strength={kronos_advisory_strength:.2f})")

    # Portfolio impact consideration (large positions deserve more scrutiny)
    if portfolio_impact_pct and portfolio_impact_pct >= 5.0:
        if tier == TIER_1_LIGHT:
            tier = TIER_2_STANDARD
            reasons.append("Large portfolio impact → higher tier")

    # Re-underwriting existing holdings: be slightly more conservative on Tier 0
    if is_existing_holding and tier == TIER_0_DETERMINISTIC:
        tier = TIER_1_LIGHT
        reasons.append("Existing holding → avoid complete Tier 0")

    # Apply dynamic Tier 2 floor if stats are available
    if tier >= TIER_2_STANDARD and threshold_stats is not None:
        dynamic_floor = threshold_stats.get_tier2_floor()
        if dynamic_floor is not None and base_score < dynamic_floor:
            tier = TIER_1_LIGHT
            reasons.append(f"Dropped below dynamic Tier 2 floor ({dynamic_floor:.1f})")

    # Final safety clamp
    tier = max(TIER_0_DETERMINISTIC, min(TIER_3_DEEP, tier))

    return TierRoutingResult(tier=tier, reasons=reasons)


def should_force_tier_3(
    *,
    research_selection_score: float,
    reviewer_average_score: Optional[float] = None,
    margin_of_safety_score: Optional[float] = None,
    portfolio_impact_pct: Optional[float] = None,
    high_conviction_override: bool = False,
) -> bool:
    """Helper to decide if something should be forced to Tier 3."""
    if high_conviction_override:
        return True
    if portfolio_impact_pct and portfolio_impact_pct >= 8.0:
        return True
    if reviewer_average_score and reviewer_average_score >= 85 and research_selection_score >= 60:
        return True
    if margin_of_safety_score and margin_of_safety_score >= 75:
        return True
    return False
