"""Basic tests for TierRouter."""

import pytest

from longterm.tier_router import route_enrichment_tier, should_force_tier_3
from longterm.tier_definitions import TIER_0_DETERMINISTIC, TIER_1_LIGHT, TIER_2_STANDARD, TIER_3_DEEP


def test_low_score_goes_to_tier_0():
    result = route_enrichment_tier(
        research_selection_score=28.0,
        reviewer_average_score=48.0,
    )
    assert result.tier == TIER_0_DETERMINISTIC
    assert any("Low blended score" in r for r in result.reasons)


def test_good_score_goes_to_tier_2():
    result = route_enrichment_tier(
        research_selection_score=62.0,
        reviewer_average_score=71.0,
    )
    assert result.tier == TIER_2_STANDARD


def test_fast_grower_with_solid_score_gets_tier_3():
    result = route_enrichment_tier(
        research_selection_score=59.0,
        reviewer_average_score=68.0,
        company_category="fast_grower",
    )
    assert result.tier == TIER_3_DEEP
    assert any("fast_grower" in r for r in result.reasons)


def test_hard_mos_objection_drops_tier():
    result = route_enrichment_tier(
        research_selection_score=68.0,
        reviewer_average_score=72.0,
        has_hard_mos_objection=True,
    )
    assert result.tier <= TIER_1_LIGHT


def test_high_conviction_override_forces_tier_3():
    result = route_enrichment_tier(
        research_selection_score=42.0,
        high_conviction_override=True,
    )
    assert result.tier == TIER_3_DEEP
    assert any("override" in r.lower() for r in result.reasons)


def test_kronos_can_lift_from_tier_0():
    result = route_enrichment_tier(
        research_selection_score=32.0,
        kronos_advisory_strength=0.75,
    )
    assert result.tier == TIER_1_LIGHT
    assert any("Kronos" in r for r in result.reasons)


def test_large_portfolio_impact_increases_tier():
    result = route_enrichment_tier(
        research_selection_score=51.0,
        portfolio_impact_pct=7.5,
    )
    assert result.tier >= TIER_2_STANDARD


def test_should_force_tier_3_logic():
    assert should_force_tier_3(
        research_selection_score=65.0,
        reviewer_average_score=88.0,
        portfolio_impact_pct=9.0,
    ) is True

    assert should_force_tier_3(
        research_selection_score=40.0,
        reviewer_average_score=50.0,
    ) is False
