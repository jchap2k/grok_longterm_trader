"""Safe proposed action planning for long-term decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from longterm.portfolio_state import PortfolioState
from longterm.risk.category_risk_policy import apply_category_risk_adjustment
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
    recommended_size_pct: float | None = None   # From staged entry (Priority 2)


class ActionPlanner:
    """Convert a CGH decision into a non-executing proposed trade intent."""

    def plan(
        self,
        packet: ResearchPacket,
        *,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        decision: Mapping[str, Any],
        recommended_size_pct: float | None = None,                    # From staged entry
        category_adjustment_already_applied: bool = False,            # From Buy Promotion (Option A)
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

        # Priority 2: Prefer staged/reduced size when provided (e.g. starter position)
        effective_size_pct = recommended_size_pct if recommended_size_pct is not None and recommended_size_pct > 0 else suggested_size_pct

        # Apply company_category risk adjustment as final backstop (only if enabled in profile)
        company_cat = ""
        adjustment_metadata = {}

        if getattr(profile, "enable_category_risk_sizing", False):
            if hasattr(packet, "company_category") and packet.company_category:
                company_cat = packet.company_category.value if hasattr(packet.company_category, "value") else str(packet.company_category)

            effective_size_pct, adjustment_metadata = apply_category_risk_adjustment(
                effective_size_pct,
                company_cat,
                already_adjusted=category_adjustment_already_applied,
            )

        target_value = round(profile.tradable_capital * effective_size_pct / 100.0, 2)

        if recommendation in {"BUY", "ADD"}:
            trade_value = round(max(0.0, target_value - current_value), 2)
            shortfall = round(max(0.0, trade_value - portfolio_state.cash), 2)

            reason = (
                "Cash is sufficient for planned active-sleeve buy."
                if shortfall <= 0
                else "High-conviction idea exceeds available active-sleeve cash."
            )
            if recommended_size_pct is not None and recommended_size_pct < suggested_size_pct:
                reason = f"Staged starter entry: {effective_size_pct}% (full size would be {suggested_size_pct}%). " + reason
            elif company_cat and adjustment_metadata.get("applied"):
                reason = f"Category risk adjustment ({company_cat}): {effective_size_pct}% of portfolio. " + reason

            return PlannedAction(
                symbol=symbol,
                action=recommendation,
                order_intent="BUY" if trade_value > 0 else "NONE",
                target_value=target_value,
                trade_value=trade_value,
                cash_shortfall=shortfall,
                allowed=shortfall <= 0 and trade_value > 0,
                capital_needed_alert=shortfall > 0,
                reason=reason,
                recommended_size_pct=recommended_size_pct,
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
                recommended_size_pct=recommended_size_pct,
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
            recommended_size_pct=recommended_size_pct,
        )
