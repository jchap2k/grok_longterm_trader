"""Safe proposed action planning for long-term decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.research_packet import ResearchPacket


@dataclass(frozen=True)
class PlannedAction:
    symbol: str
    action: str
    order_intent: str
    target_value: float
    trade_value: float
    cash_shortfall: float
    allowed: bool
    capital_needed_alert: bool
    reason: str


class ActionPlanner:
    """Convert a CGH decision into a non-executing proposed trade intent."""

    def plan(
        self,
        packet: ResearchPacket,
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        decision: Mapping[str, Any],
    ) -> PlannedAction:
        symbol = packet.symbol.upper()
        recommendation = str(decision.get("recommendation", "")).upper()
        current_value = portfolio_state.holding_value(symbol)

        if symbol in profile.protected_symbols:
            return PlannedAction(
                symbol=symbol,
                action="PROTECTED_HOLD",
                order_intent="NONE",
                target_value=current_value,
                trade_value=0.0,
                cash_shortfall=0.0,
                allowed=False,
                capital_needed_alert=False,
                reason="Symbol is protected and cannot be sold, reduced, or rebalanced.",
            )

        suggested_size_pct = float(decision.get("suggested_size_pct") or 0.0)
        target_value = round(profile.tradable_capital * suggested_size_pct / 100.0, 2)

        if recommendation in {"BUY", "ADD"}:
            trade_value = round(max(0.0, target_value - current_value), 2)
            shortfall = round(max(0.0, trade_value - portfolio_state.cash), 2)
            return PlannedAction(
                symbol=symbol,
                action=recommendation,
                order_intent="BUY" if trade_value > 0 else "NONE",
                target_value=target_value,
                trade_value=trade_value,
                cash_shortfall=shortfall,
                allowed=shortfall <= 0 and trade_value > 0,
                capital_needed_alert=shortfall > 0,
                reason=(
                    "Cash is sufficient for planned active-sleeve buy."
                    if shortfall <= 0
                    else "High-conviction idea exceeds available active-sleeve cash."
                ),
            )

        if recommendation in {"REDUCE", "SELL"}:
            target = 0.0 if recommendation == "SELL" else target_value
            trade_value = round(max(0.0, current_value - target), 2)
            return PlannedAction(
                symbol=symbol,
                action=recommendation,
                order_intent="SELL" if trade_value > 0 else "NONE",
                target_value=target,
                trade_value=trade_value,
                cash_shortfall=0.0,
                allowed=trade_value > 0,
                capital_needed_alert=False,
                reason="Non-protected holding can be reduced inside active sleeve.",
            )

        return PlannedAction(
            symbol=symbol,
            action=recommendation or "HOLD",
            order_intent="NONE",
            target_value=current_value,
            trade_value=0.0,
            cash_shortfall=0.0,
            allowed=True,
            capital_needed_alert=False,
            reason="No trade intent required.",
        )
