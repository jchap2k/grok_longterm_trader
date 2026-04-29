"""
PDT (Pattern Day Trader) Manager

Handles PDT rule tracking for accounts under $25k.
Tracks day trades in rolling 5-business-day window and enforces limits.

PDT Rule: Accounts under $25k are limited to 3 day trades per 5 business days.
A "day trade" is buying and selling the same security on the same day.

Extracted from ClaudeTradingAgent to reduce complexity.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)


class PDTManager:
    """
    Manages PDT (Pattern Day Trader) tracking and limits.

    For accounts under $25k, enforces 3 day trades per 5 business days limit.
    """

    def __init__(self):
        """Initialize PDT manager with default settings."""
        self.pdt_enabled = False
        self.pdt_max_trades = 3
        self.pdt_preferred_days = ["Tuesday", "Wednesday", "Thursday"]
        self.pdt_day_trades: List[Dict] = []  # List of day trade records

    def load_config(self, broker_config: dict):
        """
        Load PDT settings from broker config.

        Args:
            broker_config: Broker configuration dict with safety.pdt_protection section
        """
        safety = broker_config.get("safety", {})
        pdt_config = safety.get("pdt_protection", {})

        self.pdt_enabled = pdt_config.get("enabled", False) and pdt_config.get("account_under_25k", False)
        self.pdt_max_trades = pdt_config.get("max_day_trades_per_5_days", 3)
        self.pdt_preferred_days = pdt_config.get("preferred_trading_days", ["Tuesday", "Wednesday", "Thursday"])

        if self.pdt_enabled:
            status = self.get_status()
            logger.info(f"PDT Protection enabled: {status['message']}")

    def cleanup_old_trades(self):
        """Remove day trades older than 5 business days from tracking."""
        if not self.pdt_day_trades:
            return

        today = datetime.now().date()

        # Calculate date 5 business days ago (roughly 7 calendar days to be safe)
        cutoff_date = today - timedelta(days=7)

        original_count = len(self.pdt_day_trades)
        self.pdt_day_trades = [
            trade for trade in self.pdt_day_trades
            if datetime.fromisoformat(trade["date"]).date() > cutoff_date
        ]

        removed = original_count - len(self.pdt_day_trades)
        if removed > 0:
            logger.info(f"Cleaned up {removed} old PDT trades (older than 5 business days)")

    def count_trades_in_window(self) -> int:
        """
        Count day trades in the rolling 5 business day window.

        Returns:
            Number of day trades in last 5 business days
        """
        if not self.pdt_day_trades:
            return 0

        today = datetime.now().date()

        # 5 business days is approximately 7 calendar days
        cutoff_date = today - timedelta(days=7)

        count = sum(
            1 for trade in self.pdt_day_trades
            if datetime.fromisoformat(trade["date"]).date() > cutoff_date
        )
        return count

    def get_trades_remaining(self) -> int:
        """
        Get number of day trades remaining before hitting PDT limit.

        Returns:
            Number of day trades remaining (999 if unlimited)
        """
        if not self.pdt_enabled:
            return 999  # Unlimited

        used = self.count_trades_in_window()
        return max(0, self.pdt_max_trades - used)

    def is_preferred_trading_day(self) -> bool:
        """
        Check if today is a preferred trading day for PDT-limited accounts.

        Returns:
            True if today is in preferred_days list
        """
        day_name = datetime.now().strftime("%A")
        return day_name in self.pdt_preferred_days

    def record_day_trade(self, symbol: str):
        """
        Record a completed day trade (buy + sell same day).

        Args:
            symbol: Stock symbol that was day traded
        """
        trade_record = {
            "date": datetime.now().date().isoformat(),
            "symbol": symbol,
            "timestamp": datetime.now().isoformat()
        }
        self.pdt_day_trades.append(trade_record)

        remaining = self.get_trades_remaining()
        logger.info(f"PDT: Recorded day trade for {symbol}. {remaining} trades remaining in 5-day window.")

    def get_status(self) -> Dict:
        """
        Get current PDT status for display/decision making.

        Returns:
            Dict with PDT status info (enabled, trades_used, trades_remaining, etc.)
        """
        if not self.pdt_enabled:
            return {
                "enabled": False,
                "message": "PDT protection disabled (account $25k+)"
            }

        trades_used = self.count_trades_in_window()
        trades_remaining = self.get_trades_remaining()
        is_preferred_day = self.is_preferred_trading_day()
        day_name = datetime.now().strftime("%A")

        return {
            "enabled": True,
            "trades_used": trades_used,
            "trades_remaining": trades_remaining,
            "max_trades": self.pdt_max_trades,
            "is_preferred_day": is_preferred_day,
            "today": day_name,
            "preferred_days": self.pdt_preferred_days,
            "can_trade": trades_remaining > 0,
            "should_trade": trades_remaining > 0 and is_preferred_day,
            "message": self._get_message(trades_remaining, is_preferred_day, day_name)
        }

    def _get_message(self, remaining: int, is_preferred: bool, day_name: str) -> str:
        """
        Generate human-readable PDT status message.

        Args:
            remaining: Trades remaining
            is_preferred: Whether today is a preferred day
            day_name: Name of current day

        Returns:
            Human-readable status message
        """
        if remaining == 0:
            return f"PDT LIMIT REACHED: No day trades remaining. Wait for older trades to expire."

        if not is_preferred:
            return (
                f"PDT WARNING: Today is {day_name} (not a preferred trading day). "
                f"{remaining} trades remaining. Consider saving for Tue/Wed/Thu."
            )

        return f"PDT OK: {remaining} day trades remaining. {day_name} is a preferred trading day."

    def to_dict(self) -> Dict:
        """
        Serialize PDT manager state to dict for persistence.

        Returns:
            Dict with pdt_day_trades list
        """
        return {
            "pdt_day_trades": self.pdt_day_trades
        }

    def from_dict(self, state: Dict):
        """
        Load PDT manager state from dict.

        Args:
            state: Dict with pdt_day_trades list
        """
        self.pdt_day_trades = state.get("pdt_day_trades", [])
