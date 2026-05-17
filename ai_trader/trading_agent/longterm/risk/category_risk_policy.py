"""Centralized policy for Lynch-style company category risk adjustments."""

from typing import Any

# === Configuration ===
CATEGORY_MULTIPLIERS: dict[str, float] = {
    "fast_grower": 1.10,
    "stalwart": 1.00,
    "slow_grower": 0.90,
    "cyclical": 0.75,
    "turnaround": 0.75,
    "asset_play": 0.75,
}

HARD_FLOOR_RATIO = 0.55


def apply_category_risk_adjustment(
    suggested_size_pct: float,
    company_category: str | None,
    *,
    already_adjusted: bool = False,
) -> tuple[float, dict[str, Any]]:
    """
    Applies Lynch-style category risk adjustment with a hard floor.

    The adjustment is skipped if `already_adjusted=True` (prevents double application
    when Buy Promotion has already applied the multiplier).

    Returns:
        (adjusted_size, metadata)
        metadata contains:
            - applied: bool
            - multiplier: float
            - floor_hit: bool
            - category: str
            - original_size: float
            - reason: str
    """
    if already_adjusted:
        return suggested_size_pct, {
            "applied": False,
            "multiplier": 1.0,
            "floor_hit": False,
            "category": company_category or "unknown",
            "original_size": suggested_size_pct,
            "reason": "already_adjusted",
        }

    if not company_category:
        return suggested_size_pct, {
            "applied": False,
            "multiplier": 1.0,
            "floor_hit": False,
            "category": "unknown",
            "original_size": suggested_size_pct,
            "reason": "no_category",
        }

    cat = company_category.lower()
    multiplier = CATEGORY_MULTIPLIERS.get(cat, 1.0)

    if multiplier == 1.0:
        return suggested_size_pct, {
            "applied": False,
            "multiplier": 1.0,
            "floor_hit": False,
            "category": cat,
            "original_size": suggested_size_pct,
            "reason": "no_adjustment_needed",
        }

    adjusted = round(suggested_size_pct * multiplier, 2)
    min_allowed = round(suggested_size_pct * HARD_FLOOR_RATIO, 2)
    floor_hit = False

    if adjusted < min_allowed:
        adjusted = min_allowed
        floor_hit = True

    return adjusted, {
        "applied": True,
        "multiplier": multiplier,
        "floor_hit": floor_hit,
        "category": cat,
        "original_size": suggested_size_pct,
        "reason": "category_risk_adjustment",
    }
