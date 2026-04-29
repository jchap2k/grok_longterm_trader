"""Defensive posture helpers for long-term portfolio decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DefensiveAction(str, Enum):
    """High-level defensive actions for the active sleeve."""

    STAY_OFFENSIVE = "stay_offensive"
    HOLD_DEFENSIVE = "hold_defensive"
    MOVE_TO_CASH = "move_to_cash"
    MOVE_TO_DEFENSIVE_PARKING = "move_to_defensive_parking"
    REDEPLOY = "redeploy"


@dataclass
class DefensiveDecision:
    """Result of defensive posture evaluation."""

    action: DefensiveAction
    rationale: str


def evaluate_defensive_posture(
    *,
    account_strategy_mode: str,
    vix_level: float,
    broad_trend_broken: bool,
    leadership_failing: bool,
    recovery_confirmed: bool = False,
    currently_defensive: bool = False,
) -> DefensiveDecision:
    """
    Evaluate whether the active sleeve should stay offensive, go defensive, or redeploy.

    This is intentionally conservative and uses VIX as a danger flag, not a
    standalone timing signal.
    """
    mode = (account_strategy_mode or "").lower()
    vix = float(vix_level or 0.0)

    extreme_volatility = vix >= 50.0
    elevated_volatility = vix >= 30.0
    structural_break = broad_trend_broken and leadership_failing

    if mode == "roth_ira" and extreme_volatility and structural_break:
        return DefensiveDecision(
            action=DefensiveAction.MOVE_TO_CASH,
            rationale=(
                "Extreme volatility with broken trend and failing leadership "
                "supports capital preservation in cash for the active sleeve."
            ),
        )

    if currently_defensive:
        if recovery_confirmed and not broad_trend_broken and not leadership_failing and vix <= 25.0:
            return DefensiveDecision(
                action=DefensiveAction.REDEPLOY,
                rationale=(
                    "Volatility has cooled and recovery is confirmed, so phased "
                    "redeployment is reasonable."
                ),
            )
        return DefensiveDecision(
            action=DefensiveAction.HOLD_DEFENSIVE,
            rationale=(
                "Defensive posture should be maintained because recovery is not "
                "fully confirmed."
            ),
        )

    if structural_break and elevated_volatility:
        return DefensiveDecision(
            action=DefensiveAction.MOVE_TO_DEFENSIVE_PARKING,
            rationale=(
                "Broad deterioration suggests reducing risk, but conditions do not "
                "yet justify an extreme-volatility cash exit."
            ),
        )

    return DefensiveDecision(
        action=DefensiveAction.STAY_OFFENSIVE,
        rationale="No defensive override is currently justified.",
    )
