"""
Tier definitions and helpers for the Tiered Enrichment Strategy.

This module is the single source of truth for tier numbers and their meaning.
"""

from __future__ import annotations

from typing import List, Optional

# Tier constants
TIER_0_DETERMINISTIC = 0
TIER_1_LIGHT = 1
TIER_2_STANDARD = 2
TIER_3_DEEP = 3

ALL_TIERS = [TIER_0_DETERMINISTIC, TIER_1_LIGHT, TIER_2_STANDARD, TIER_3_DEEP]

TIER_NAMES = {
    TIER_0_DETERMINISTIC: "Deterministic Gate",
    TIER_1_LIGHT: "Light Enrichment",
    TIER_2_STANDARD: "Standard Perplexity",
    TIER_3_DEEP: "Deep / High-Conviction",
}


def get_tier_name(tier: Optional[int]) -> str:
    if tier is None:
        return "Unknown"
    return TIER_NAMES.get(tier, f"Unknown Tier {tier}")


def is_valid_tier(tier: Optional[int]) -> bool:
    return tier in ALL_TIERS or tier is None


def format_tier_for_display(tier: Optional[int], reasons: Optional[List[str]] = None) -> str:
    name = get_tier_name(tier)
    if not reasons:
        return f"Tier {tier} ({name})"
    reasons_str = "; ".join(reasons[:3])
    return f"Tier {tier} ({name}) — {reasons_str}"
