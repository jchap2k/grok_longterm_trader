"""
Dynamic Position Sizing - Kelly Criterion & Adaptive Sizing

Adjusts position sizes based on:
- Strategy win rate and performance
- Market volatility
- Recent win/loss streak
- Kelly Criterion for optimal sizing

Author: AI Day Trader Team
Date: January 18, 2026
"""

import logging
from typing import Dict, Any, Optional, List
from enum import Enum

logger = logging.getLogger(__name__)


class SizingMode(Enum):
    """Position sizing modes."""
    CONSERVATIVE = "conservative"  # 0.5x Kelly
    MODERATE = "moderate"          # 1.0x Kelly (default)
    AGGRESSIVE = "aggressive"      # 1.5x Kelly (risky)


class DynamicPositionSizer:
    """
    Calculates optimal position sizes using multiple factors.
    """

    def __init__(self, base_risk_percent: float = 1.0, mode: SizingMode = SizingMode.MODERATE):
        """
        Initialize dynamic position sizer.

        Args:
            base_risk_percent: Base risk per trade (default 1%)
            mode: Sizing mode (conservative/moderate/aggressive)
        """
        self.base_risk_percent = base_risk_percent
        self.mode = mode
        self.recent_trades = []  # Track for streak detection

    def calculate_position_size(
        self,
        account_value: float,
        entry_price: float,
        stop_price: float,
        strategy_performance: Optional[Dict[str, Any]] = None,
        volatility_regime: str = "medium",
        correlation_risk: float = 0.0,
        trade_history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Calculate optimal position size considering multiple factors.

        Args:
            account_value: Total portfolio value (Gate 1 = Option A: risk is % of total portfolio, not just tradeable capital)
            entry_price: Planned entry price
            stop_price: Stop loss price
            strategy_performance: Strategy metrics (win rate, avg R:R, etc.)
            volatility_regime: Current volatility (low/medium/high/extreme)
            correlation_risk: Correlation with existing positions (0.0-1.0)
            trade_history: Recent closed trades for streak detection.
                           Each entry should have 'pnl' or 'pnl_percent' key.
                           If None, streak multiplier defaults to 1.0.

        Returns:
            Dictionary with:
            - shares: Number of shares to buy
            - risk_amount: Dollar risk
            - position_value: Total position value
            - risk_percent: Actual risk as % of account
            - adjustments_applied: List of adjustments made
            - kelly_fraction: Kelly Criterion result
        """
        adjustments_applied = []

        # Populate streak data from caller-provided trade history
        if trade_history is not None:
            # Normalize to expected format: ensure 'pnl' key exists
            self.recent_trades = []
            for t in trade_history[-10:]:  # Use last 10 trades
                pnl = t.get('pnl') or t.get('pnl_percent') or t.get('realized_pnl_percent', 0)
                self.recent_trades.append({'pnl': pnl})

        # Start with base risk
        risk_percent = self.base_risk_percent
        adjustments_applied.append(f"Base risk: {risk_percent}%")

        # 1. Strategy Performance Multiplier
        if strategy_performance:
            strategy_multiplier = self._get_strategy_multiplier(strategy_performance)
            risk_percent *= strategy_multiplier
            adjustments_applied.append(
                f"Strategy multiplier: {strategy_multiplier:.2f}x "
                f"(win rate: {strategy_performance.get('win_rate', 0):.1f}%)"
            )

        # 2. Volatility Adjustment
        volatility_multiplier = self._get_volatility_multiplier(volatility_regime)
        risk_percent *= volatility_multiplier
        adjustments_applied.append(
            f"Volatility multiplier: {volatility_multiplier:.2f}x ({volatility_regime} vol)"
        )

        # 3. Streak Adjustment (reduce after losses)
        streak_multiplier = self._get_streak_multiplier()
        risk_percent *= streak_multiplier
        if streak_multiplier != 1.0:
            adjustments_applied.append(
                f"Streak adjustment: {streak_multiplier:.2f}x (recent performance)"
            )

        # 4. Correlation Penalty
        if correlation_risk > 0.5:
            correlation_multiplier = 1.0 - (correlation_risk * 0.3)  # Up to 30% reduction
            risk_percent *= correlation_multiplier
            adjustments_applied.append(
                f"Correlation penalty: {correlation_multiplier:.2f}x (correlation: {correlation_risk:.2f})"
            )

        # 5. Kelly Criterion (if we have strategy data)
        kelly_fraction = None
        if strategy_performance:
            kelly_fraction = self._calculate_kelly_fraction(strategy_performance)
            if kelly_fraction > 0:
                # Apply Kelly (with fraction based on mode)
                kelly_multiplier = self._get_kelly_mode_multiplier()
                kelly_risk = kelly_fraction * kelly_multiplier
                risk_percent = min(risk_percent, kelly_risk)
                adjustments_applied.append(
                    f"Kelly Criterion: {kelly_risk:.2f}% (fraction: {kelly_fraction:.2f})"
                )

        # Cap at maximum risk per trade
        max_risk = 3.0  # Never risk more than 3% on single trade
        if risk_percent > max_risk:
            adjustments_applied.append(f"Capped at max risk: {max_risk}%")
            risk_percent = max_risk

        # Calculate position size
        risk_amount = account_value * (risk_percent / 100)
        risk_per_share = abs(entry_price - stop_price)

        if risk_per_share <= 0:
            logger.error("Invalid risk per share (entry = stop price)")
            return {
                'shares': 0,
                'risk_amount': 0,
                'position_value': 0,
                'risk_percent': 0,
                'adjustments_applied': ["ERROR: Invalid stop price"],
                'kelly_fraction': None
            }

        shares = int(risk_amount / risk_per_share)
        position_value = shares * entry_price

        # Final validation: don't exceed 20% of active capital
        max_position_value = account_value * 0.20
        if position_value > max_position_value:
            shares = int(max_position_value / entry_price)
            position_value = shares * entry_price
            adjustments_applied.append("Position size capped at 20% of active capital")

        actual_risk_percent = (shares * risk_per_share / account_value) * 100

        return {
            'shares': shares,
            'risk_amount': round(shares * risk_per_share, 2),
            'position_value': round(position_value, 2),
            'risk_percent': round(actual_risk_percent, 2),
            'adjustments_applied': adjustments_applied,
            'kelly_fraction': round(kelly_fraction, 3) if kelly_fraction else None
        }

    def _get_strategy_multiplier(self, performance: Dict[str, Any]) -> float:
        """
        Get position size multiplier based on strategy performance.

        Better performing strategies get larger positions.

        Args:
            performance: Strategy performance metrics

        Returns:
            Multiplier (0.5 to 1.5)
        """
        win_rate = performance.get('win_rate', 50)
        confidence = performance.get('confidence_score', 50)
        total_trades = performance.get('total_trades', 0)

        # Need minimum sample size
        if total_trades < 5:
            return 0.8  # Reduce size for unproven strategy

        # Base multiplier on win rate
        if win_rate >= 65:
            multiplier = 1.3
        elif win_rate >= 55:
            multiplier = 1.1
        elif win_rate >= 45:
            multiplier = 1.0
        elif win_rate >= 35:
            multiplier = 0.8
        else:
            multiplier = 0.6

        # Adjust for confidence
        if confidence >= 70:
            multiplier *= 1.1
        elif confidence < 40:
            multiplier *= 0.9

        return min(1.5, max(0.5, multiplier))

    def _get_volatility_multiplier(self, volatility_regime: str) -> float:
        """
        Get position size multiplier based on volatility.

        Lower positions in high volatility.

        Args:
            volatility_regime: low/medium/high/extreme

        Returns:
            Multiplier (0.5 to 1.2)
        """
        multipliers = {
            'low': 1.2,      # Can size up
            'medium': 1.0,   # Normal
            'high': 0.75,    # Size down
            'extreme': 0.5   # Significantly reduce
        }
        return multipliers.get(volatility_regime.lower(), 1.0)

    def _get_streak_multiplier(self) -> float:
        """
        Adjust size based on recent win/loss streak.

        Uses actual trade history passed via trade_history param in calculate_position_size().
        Populated from agent.trade_log or trading_performance.db.

        Returns:
            Multiplier (0.6 to 1.2)
        """
        if len(self.recent_trades) < 3:
            return 1.0  # Not enough data for reliable streak detection

        # Look at last 5 closed trades
        recent = self.recent_trades[-5:]
        losses = sum(1 for t in recent if t.get('pnl', 0) < 0)
        wins = len(recent) - losses

        # Losing streaks - reduce size to protect capital
        if losses >= 3:
            return 0.6   # 40% reduction: 3+ losses in last 5
        elif losses >= 2:
            return 0.8   # 20% reduction: 2 losses in last 5
        elif losses >= 1:
            return 0.9   # 10% reduction: 1 loss in last 5

        # Winning streak - slight increase, avoid overconfidence
        if wins >= 4:
            return 1.2   # 20% boost: 4-5 wins in last 5

        return 1.0

    def _calculate_kelly_fraction(self, performance: Dict[str, Any]) -> float:
        """
        Calculate Kelly Criterion fraction.

        Kelly % = W - [(1-W) / R]
        Where:
            W = Win rate (as decimal)
            R = Win/Loss ratio (avg win / avg loss)

        Args:
            performance: Strategy performance with win rate and R:R

        Returns:
            Kelly fraction as percent (0-100)
        """
        win_rate = performance.get('win_rate', 0) / 100  # Convert to decimal
        avg_rr = performance.get('avg_rr', 1.0)

        if win_rate <= 0 or avg_rr <= 0:
            return 0

        # Kelly formula
        kelly = win_rate - ((1 - win_rate) / avg_rr)

        # Kelly gives fraction of capital
        # Convert to percent and cap at reasonable values
        kelly_percent = kelly * 100

        # Kelly can suggest very large positions - cap it
        return max(0, min(10, kelly_percent))  # Cap at 10%

    def _get_kelly_mode_multiplier(self) -> float:
        """
        Get Kelly multiplier based on sizing mode.

        Returns:
            Multiplier for Kelly fraction
        """
        multipliers = {
            SizingMode.CONSERVATIVE: 0.5,  # Half Kelly (safer)
            SizingMode.MODERATE: 0.75,     # 3/4 Kelly
            SizingMode.AGGRESSIVE: 1.0     # Full Kelly (risky)
        }
        return multipliers.get(self.mode, 0.75)

    def record_trade_result(self, trade: Dict[str, Any]):
        """
        Record trade result for streak tracking.

        Args:
            trade: Trade dictionary with at minimum 'pnl' key
        """
        self.recent_trades.append(trade)

        # Keep only last 10 trades for efficiency
        if len(self.recent_trades) > 10:
            self.recent_trades = self.recent_trades[-10:]

    def get_sizing_summary(self) -> str:
        """
        Get summary of current sizing configuration.

        Returns:
            Summary string
        """
        summary_parts = [
            f"Dynamic Position Sizing Configuration:",
            f"  Base Risk: {self.base_risk_percent}%",
            f"  Mode: {self.mode.value}",
            f"  Recent Trades Tracked: {len(self.recent_trades)}"
        ]

        if self.recent_trades:
            recent_pnl = [t.get('pnl', 0) for t in self.recent_trades]
            wins = sum(1 for p in recent_pnl if p > 0)
            losses = len(recent_pnl) - wins
            summary_parts.append(f"  Recent Performance: {wins}W-{losses}L")

        return "\n".join(summary_parts)


def calculate_optimal_position_size(
    account_value: float,
    entry_price: float,
    stop_price: float,
    strategy_performance: Optional[Dict[str, Any]] = None,
    volatility_regime: str = "medium",
    correlation_risk: float = 0.0,
    base_risk_percent: float = 1.0,
    trade_history: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Convenience function for position size calculation.

    Args:
        account_value: Active trading capital (NOT total account - should exclude base/protected capital)
        entry_price: Entry price
        stop_price: Stop loss price
        strategy_performance: Strategy metrics
        volatility_regime: Current volatility
        correlation_risk: Correlation with existing positions
        base_risk_percent: Base risk per trade
        trade_history: Recent closed trades for streak detection.
                       Each entry should have 'pnl' or 'pnl_percent' key.

    Returns:
        Position sizing recommendation
    """
    sizer = DynamicPositionSizer(base_risk_percent=base_risk_percent)
    return sizer.calculate_position_size(
        account_value=account_value,
        entry_price=entry_price,
        stop_price=stop_price,
        strategy_performance=strategy_performance,
        volatility_regime=volatility_regime,
        correlation_risk=correlation_risk,
        trade_history=trade_history
    )
