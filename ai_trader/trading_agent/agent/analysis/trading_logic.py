"""
Trading Logic - Risk calculations and trading decision support

Extracted from ClaudeTradingAgent monolith (Phase 1 refactor)
Manages: ATR calculations, position sizing, risk management calculations
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TradingLogic:
    """
    Handles trading analysis and risk calculations.

    Responsibilities:
    - Calculate ATR-based stop loss percentages
    - Position sizing calculations
    - Risk management calculations
    - Trading decision support logic

    Note: Technical indicators (RSI, Bollinger Bands, Volume Profile)
    are already extracted to components/technical_indicators.py
    """

    def __init__(self, data_provider=None):
        """
        Initialize trading logic.

        Args:
            data_provider: Market data provider instance (for ATR calculations)
        """
        self.data_provider = data_provider

    def get_atr_stop_percent(
        self,
        symbol: str,
        current_price: float,
        multiplier: float = 1.5
    ) -> float:
        """
        Calculate ATR-based stop loss percentage for a symbol.

        Args:
            symbol: Stock symbol
            current_price: Current price of the stock
            multiplier: ATR multiplier (1.5 for SL orders, 2.0 for emergency rule)

        Returns:
            Stop loss percentage (e.g., 2.5 for 2.5%)
            Falls back to 1.5% if ATR cannot be calculated
        """
        try:
            if self.data_provider and hasattr(self.data_provider, 'calculate_atr'):
                atr = self.data_provider.calculate_atr(symbol, period=14)
                if atr and current_price > 0:
                    # Convert ATR to percentage of current price
                    atr_percent = (atr / current_price) * 100
                    # Apply multiplier
                    stop_percent = atr_percent * multiplier
                    # Enforce floor (1%) and ceiling (3%)
                    stop_percent = max(1.0, min(3.0, stop_percent))
                    logger.debug(
                        f"{symbol}: ATR=${atr:.2f}, ATR%={atr_percent:.2f}%, "
                        f"Stop%={stop_percent:.2f}% (multiplier={multiplier})"
                    )
                    return stop_percent
        except Exception as e:
            logger.warning(f"Could not calculate ATR for {symbol}: {e}")

        # Fallback to default
        return 1.5  # Default 1.5% if ATR unavailable

    def calculate_position_size(
        self,
        account_value: float,
        risk_percent: float,
        entry_price: float,
        stop_loss_price: float,
        max_position_percent: float = 25.0
    ) -> int:
        """
        Calculate position size based on risk management rules.

        Args:
            account_value: Total account value
            risk_percent: Percent of account to risk (e.g., 1.5 for 1.5%)
            entry_price: Planned entry price
            stop_loss_price: Planned stop loss price
            max_position_percent: Max percent of account for single position

        Returns:
            Number of shares to buy (0 if invalid inputs)
        """
        if entry_price <= 0 or stop_loss_price <= 0 or account_value <= 0:
            logger.warning("Invalid inputs for position size calculation")
            return 0

        if stop_loss_price >= entry_price:
            logger.warning("Stop loss must be below entry price")
            return 0

        # Calculate risk per share
        risk_per_share = entry_price - stop_loss_price

        # Calculate dollar risk
        dollar_risk = account_value * (risk_percent / 100)

        # Calculate shares based on risk
        shares = int(dollar_risk / risk_per_share)

        # Apply max position size constraint
        max_shares_by_value = int((account_value * (max_position_percent / 100)) / entry_price)
        shares = min(shares, max_shares_by_value)

        # Ensure at least 1 share if we got this far
        shares = max(1, shares)

        logger.debug(
            f"Position size: {shares} shares @ ${entry_price:.2f}, "
            f"risk=${dollar_risk:.2f}, max_shares={max_shares_by_value}"
        )

        return shares

    def calculate_take_profit_price(
        self,
        entry_price: float,
        target_percent: float = 3.0,
        conviction: int = 5
    ) -> float:
        """
        Calculate take profit price based on target percent and conviction.

        Higher conviction = higher profit target.

        Args:
            entry_price: Entry price
            target_percent: Base target percent (default 3%)
            conviction: Conviction score 1-10

        Returns:
            Take profit price
        """
        if entry_price <= 0:
            return 0.0

        # Adjust target based on conviction
        # Conviction 9-10: +5% target
        # Conviction 7-8: +4% target
        # Conviction 5-6: +3% target
        # Conviction 1-4: +2% target
        if conviction >= 9:
            target_percent = 5.0
        elif conviction >= 7:
            target_percent = 4.0
        elif conviction >= 5:
            target_percent = 3.0
        else:
            target_percent = 2.0

        take_profit = entry_price * (1 + target_percent / 100)

        logger.debug(
            f"Take profit: ${take_profit:.2f} (entry=${entry_price:.2f}, "
            f"target={target_percent}%, conviction={conviction})"
        )

        return take_profit

    def calculate_stop_loss_price(
        self,
        entry_price: float,
        stop_percent: float = 1.5
    ) -> float:
        """
        Calculate stop loss price.

        Args:
            entry_price: Entry price
            stop_percent: Stop loss percent (default 1.5%)

        Returns:
            Stop loss price
        """
        if entry_price <= 0:
            return 0.0

        stop_loss = entry_price * (1 - stop_percent / 100)

        logger.debug(
            f"Stop loss: ${stop_loss:.2f} (entry=${entry_price:.2f}, "
            f"stop_percent={stop_percent}%)"
        )

        return stop_loss

    def should_scale_out(
        self,
        current_price: float,
        entry_price: float,
        take_profit_price: float,
        scale_out_percent: float = 50.0
    ) -> bool:
        """
        Determine if position should be scaled out (partial profit).

        Typically scale out at 50% of profit target.

        Args:
            current_price: Current market price
            entry_price: Entry price
            take_profit_price: Take profit target
            scale_out_percent: Percent of profit target to trigger scale (default 50%)

        Returns:
            True if should scale out
        """
        if current_price <= entry_price:
            return False

        profit_range = take_profit_price - entry_price
        scale_out_target = entry_price + (profit_range * (scale_out_percent / 100))

        should_scale = current_price >= scale_out_target

        if should_scale:
            logger.info(
                f"Scale out trigger: ${current_price:.2f} >= ${scale_out_target:.2f} "
                f"({scale_out_percent}% of target)"
            )

        return should_scale

    def calculate_risk_reward_ratio(
        self,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float
    ) -> float:
        """
        Calculate risk/reward ratio.

        Args:
            entry_price: Entry price
            take_profit_price: Take profit target
            stop_loss_price: Stop loss price

        Returns:
            Risk/reward ratio (e.g., 2.0 means 2:1 reward/risk)
        """
        if entry_price <= 0 or stop_loss_price >= entry_price:
            return 0.0

        risk = entry_price - stop_loss_price
        reward = take_profit_price - entry_price

        if risk == 0:
            return 0.0

        ratio = reward / risk

        logger.debug(
            f"Risk/Reward: {ratio:.2f}:1 (risk=${risk:.2f}, reward=${reward:.2f})"
        )

        return ratio

    def validate_trade_setup(
        self,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        min_risk_reward: float = 1.5
    ) -> tuple[bool, str]:
        """
        Validate a trade setup meets minimum requirements.

        Args:
            entry_price: Entry price
            take_profit_price: Take profit target
            stop_loss_price: Stop loss price
            min_risk_reward: Minimum risk/reward ratio (default 1.5:1)

        Returns:
            Tuple of (is_valid, reason)
        """
        # Check prices are positive
        if entry_price <= 0:
            return False, "Entry price must be positive"

        if stop_loss_price <= 0:
            return False, "Stop loss must be positive"

        if take_profit_price <= 0:
            return False, "Take profit must be positive"

        # Check stop loss is below entry
        if stop_loss_price >= entry_price:
            return False, "Stop loss must be below entry price"

        # Check take profit is above entry
        if take_profit_price <= entry_price:
            return False, "Take profit must be above entry price"

        # Check risk/reward ratio
        ratio = self.calculate_risk_reward_ratio(entry_price, take_profit_price, stop_loss_price)
        if ratio < min_risk_reward:
            return False, f"Risk/reward ratio {ratio:.2f}:1 is below minimum {min_risk_reward}:1"

        return True, "Valid trade setup"

    def calculate_dynamic_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        strategy: Optional[str] = None,
        broker=None,
        learning_db=None,
    ) -> Dict[str, Any]:
        """
        Wire DynamicPositionSizer (Kelly + 5-multiplier stack) into the production path.

        Called by tool_executor.tool_calculate_dynamic_position_size() when TradingLogic
        is available. broker and learning_db are passed at call time (not constructor) so
        TradingLogic constructor signature stays simple.

        Multipliers applied by DynamicPositionSizer:
          - Volatility:         0.5x (extreme) - 1.2x (low)
          - Streak:             0.6x (losing)  - 1.2x (winning), from last 10 swing trades
          - 20% per-position cap, 3% total portfolio risk cap
          - Kelly Criterion (skipped if no strategy_performance)

        Returns dict with 'position_size' key (shares) for backward compatibility.
        Falls back safely if broker/learning_db unavailable or DB query fails.

        Grok review: 90% confidence, proceed_with_notes (2026-03-12)
        """
        from analytics.dynamic_sizing import DynamicPositionSizer, SizingMode

        # --- Step 1: Get account value ---
        account_value = 0.0
        if broker:
            try:
                account = broker.get_account_info()
                account_value = float(account.portfolio_value)
            except Exception as e:
                logger.warning("[DynamicSizing] Could not get account info for %s: %s", symbol, e)
                return {"error": "Could not get account value for dynamic sizing", "position_size": 0}

        if account_value <= 0:
            logger.warning("[DynamicSizing] Invalid account_value=%s for %s - aborting", account_value, symbol)
            return {"error": "Invalid account value", "position_size": 0}

        # --- Step 2: Get volatility regime from VIX level ---
        # DynamicPositionSizer expects: "low" / "medium" / "high" / "extreme"
        # Map VIX: <16=low, 16-25=medium, 25-35=high, >35=extreme
        volatility_regime = "medium"
        try:
            from analytics.market_regime_filter import MarketRegimeFilter
            rf = MarketRegimeFilter()
            detail = rf.get_swing_regime_detail()
            vix = float(detail.get("vix_level", 20.0))
            if vix < 16:
                volatility_regime = "low"
            elif vix <= 25:
                volatility_regime = "medium"
            elif vix <= 35:
                volatility_regime = "high"
            else:
                volatility_regime = "extreme"
            logger.debug("[DynamicSizing] VIX=%.1f -> volatility_regime=%s", vix, volatility_regime)
        except Exception as e:
            logger.debug("[DynamicSizing] Could not fetch regime for VIX mapping, using 'medium': %s", e)

        # --- Step 3: Fetch recent swing trade history for streak multiplier ---
        # get_swing_trades() already filters to hold_duration='swing'
        # pnl_pct_net maps to pnl_percent for DynamicPositionSizer compatibility
        trade_history: Optional[List[Dict]] = None
        if learning_db:
            try:
                recent = learning_db.get_swing_trades(days_lookback=60)
                if recent:
                    last_10 = recent[-10:]
                    trade_history = [{"pnl_percent": float(t.get("pnl_pct_net", 0))} for t in last_10]
                    logger.debug("[DynamicSizing] Loaded %d swing trades for streak calc", len(trade_history))
            except Exception as e:
                logger.warning("[DynamicSizing] Could not fetch trade history for streak: %s", e)
                # Safe fallback: streak=1.0x (neutral) when trade_history is None

        # --- Step 4: Calculate dynamic size ---
        sizer = DynamicPositionSizer(base_risk_percent=1.0, mode=SizingMode.MODERATE)
        result = sizer.calculate_position_size(
            account_value=account_value,
            entry_price=entry_price,
            stop_price=stop_price,
            trade_history=trade_history,
            volatility_regime=volatility_regime,
        )

        shares = int(result.get("shares", 0))
        logger.info(
            "[DynamicSizing] %s: %d shares | regime=%s | risk=%.2f%% | adjustments=%s",
            symbol,
            shares,
            volatility_regime,
            result.get("risk_percent", 0),
            result.get("adjustments_applied", []),
        )

        return {
            "position_size": shares,          # backward compat alias
            "shares": shares,
            "risk_amount": result.get("risk_amount", 0),
            "position_value": result.get("position_value", 0),
            "risk_percent": result.get("risk_percent", 0),
            "adjustments_applied": result.get("adjustments_applied", []),
            "kelly_fraction": result.get("kelly_fraction"),
            "volatility_regime": volatility_regime,
            "method": "dynamic_kelly_swing",
        }
