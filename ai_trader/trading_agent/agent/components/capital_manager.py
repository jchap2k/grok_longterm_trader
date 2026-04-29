"""
Capital Manager

Manages capital allocation limits for trading accounts.
Implements dynamic base capital protection (high water mark * 0.8).

This allows trading with a subset of the account while protecting capital gains.

Example: Account at $12k, high water was $15k
- Dynamic base = $15k * 0.8 = $12k
- Active capital = $12k - $12k = $0 (can't trade until account grows)

Extracted from ClaudeTradingAgent to reduce complexity.
"""

import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)


class CapitalManager:
    """
    Manages capital allocation and limits for trading.

    Implements dynamic base capital protection to preserve gains.
    """

    def __init__(self):
        """Initialize capital manager with default settings."""
        self.capital_limits_enabled = False
        self.base_capital = 0
        self.dynamic_base_enabled = True
        self.high_water_mark = 0.0
        self._state_lock = threading.Lock()

    def load_config(self, broker_config: dict):
        """
        Load capital limits settings from broker config.

        Args:
            broker_config: Broker configuration dict with safety.capital_limits section
        """
        safety = broker_config.get("safety", {})
        capital_config = safety.get("capital_limits", {})

        self.capital_limits_enabled = capital_config.get("enabled", False)
        self.base_capital = capital_config.get("base_capital", 0)
        self.dynamic_base_enabled = capital_config.get("dynamic_base_enabled", True)

        if self.capital_limits_enabled:
            logger.info(
                f"Capital Limits enabled: Base=${self.base_capital:,.0f} (dynamic: {self.dynamic_base_enabled}). "
                f"Active capital = account_value - dynamic_base (high_water * 0.8)"
            )

    def get_protected_value(self, broker_config: dict, broker_name: str) -> float:
        """
        Calculate total cost_basis of protected positions for the given broker.

        Args:
            broker_config: Broker configuration dict
            broker_name: Active broker name (e.g. 'schwab_live')

        Returns:
            Total cost_basis of protected positions for this broker (0.0 if none)
        """
        tracking = broker_config.get('safety', {}).get('protected_positions_tracking', {})
        total = 0.0
        for symbol, info in tracking.items():
            if symbol.startswith('_'):
                continue  # skip _note meta-keys
            if info.get('broker') == broker_name:
                total += float(info.get('cost_basis', 0.0))
        if total > 0:
            logger.info(f"Protected positions value: ${total:,.2f} (excluded from active capital)")
        return total

    def get_active_capital(self, account_value: float, protected_value: float = 0.0) -> float:
        """
        Calculate active (tradeable) capital.

        Active capital = account_value - dynamic_base_capital (high_water_mark * 0.8)
        High water mark updated to max historical account value.

        Args:
            account_value: Current total account value

        Returns:
            Amount available for trading (0 if below base)
        """
        if not self.capital_limits_enabled:
            return account_value - protected_value  # No limit, use managed portion

        with self._state_lock:
            managed_value = account_value - protected_value
            self.high_water_mark = max(self.high_water_mark, managed_value)
            dynamic_base = self.high_water_mark * 0.8 if self.dynamic_base_enabled else self.base_capital
            active_capital = max(0, managed_value - dynamic_base)
            logger.debug(f"Dynamic capital: high_water=${self.high_water_mark:.0f}, base=${dynamic_base:.0f}, active=${active_capital:.0f}, protected=${protected_value:.0f}")
            return active_capital

    def get_available_trading_capital(self, account_value: float, current_positions_value: float = 0, protected_value: float = 0.0) -> float:
        """
        Get the amount of capital available for NEW trades.

        Available = active_capital - current_positions_value

        Args:
            account_value: Current total account value
            current_positions_value: Total value of currently held positions

        Returns:
            Available capital for new trades
        """
        if not self.capital_limits_enabled:
            return float('inf')  # No limit

        active = self.get_active_capital(account_value, protected_value=protected_value)
        return max(0, active - current_positions_value)

    def get_status(self, account_value: float, current_positions_value: float = 0) -> Dict:
        """
        Get current capital limits status for display/decision making.

        Args:
            account_value: Current total account value
            current_positions_value: Total value of currently held positions

        Returns:
            Dict with capital limits status info
        """
        if not self.capital_limits_enabled:
            return {
                "enabled": False,
                "message": "Capital limits disabled - using full account"
            }

        active = self.get_active_capital(account_value)
        available = self.get_available_trading_capital(account_value, current_positions_value)
        used = current_positions_value
        utilization = (used / active * 100) if active > 0 else 0

        dynamic_base = self.high_water_mark * 0.8 if self.dynamic_base_enabled else self.base_capital

        return {
            "enabled": True,
            "base_capital": self.base_capital,
            "dynamic_base": dynamic_base,
            "high_water_mark": self.high_water_mark,
            "dynamic_enabled": self.dynamic_base_enabled,
            "account_value": account_value,
            "active_capital": active,
            "capital_in_positions": used,
            "capital_available": available,
            "utilization_percent": utilization,
            "message": (
                f"Account ${account_value:,.0f} - Dynamic Base ${dynamic_base:,.0f} (high_water ${self.high_water_mark:,.0f}) = "
                f"${active:,.0f} active. "
                f"${used:,.0f} used ({utilization:.1f}%), ${available:,.0f} avail"
            )
        }

    def to_dict(self) -> Dict:
        """
        Serialize capital manager state to dict for persistence.

        Returns:
            Dict with high_water_mark
        """
        return {
            "high_water_mark": self.high_water_mark
        }

    def from_dict(self, state: Dict):
        """
        Load capital manager state from dict.

        Args:
            state: Dict with high_water_mark
        """
        self.high_water_mark = state.get("high_water_mark", 0.0)

    def check_portfolio_heat(
        self,
        open_positions: list,
        new_position: dict,
        account_equity: float,
        max_heat_pct: float = 0.12,
    ) -> dict:
        """
        Portfolio heat gate - gatekeeper before any new swing entry.

        Portfolio heat = sum of (entry_price - current_stop) * shares
        across all open positions (clamped at 0 - no negative heat).

        Gate: total heat including the proposed new position must be
        <= max_heat_pct of account equity.

        Args:
            open_positions: List of dicts, each with entry_price, current_stop, shares
            new_position:   Proposed new position dict (same schema)
            account_equity: Total account equity in dollars
            max_heat_pct:   Maximum allowed heat as fraction of equity (default 12%)

        Returns:
            dict with keys: allowed (bool), current_heat, new_heat,
                            total_heat, heat_pct, max_heat_pct, reason (str)
        """
        def position_risk(p: dict) -> float:
            risk = (float(p["entry_price"]) - float(p["current_stop"])) * float(p["shares"])
            return max(0.0, risk)  # clamp at 0 (no negative heat)

        current_heat = sum(position_risk(p) for p in open_positions)
        new_heat     = position_risk(new_position)
        total_heat   = current_heat + new_heat
        heat_pct     = total_heat / account_equity if account_equity > 0 else 0.0

        allowed = heat_pct <= max_heat_pct
        return {
            "allowed":      allowed,
            "current_heat": current_heat,
            "new_heat":     new_heat,
            "total_heat":   total_heat,
            "heat_pct":     heat_pct,
            "max_heat_pct": max_heat_pct,
            "reason": (
                f"portfolio heat {heat_pct:.1%} exceeds {max_heat_pct:.0%} ceiling"
                if not allowed
                else "within heat limit"
            ),
        }
