"""
Portfolio Circuit Breaker - Daily and weekly loss limits.

Pauses new trade entries when cumulative losses exceed thresholds:
- Daily:  -2% -> pause until end of trading day
- Weekly: -5% -> pause until next Monday

State is persisted to disk so restarts don't reset the breaker.
"""

import json
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PortfolioCircuitBreaker:
    """
    Tracks daily and weekly realized P&L and pauses trading when loss limits hit.

    Persists state to ai_trader_data/circuit_breaker_state.json so restarts
    do not clear an active pause.
    """

    # NOTE: Daily loss is checked at the portfolio level in claude_trading_agent.py
    # (current_value vs starting_portfolio_value = correct math). The CB tracks
    # per-trade P&L sums which is NOT the same as portfolio drawdown - so the
    # daily limit here is intentionally disabled (set very high).
    # Weekly and gain-lock still use per-trade sums as a rough proxy.
    DAILY_LOSS_LIMIT_PCT = -100.0  # Disabled - portfolio-level check in trading agent handles daily
    WEEKLY_LOSS_LIMIT_PCT = -6.0   # Pause week if cumulative trade losses reach this
    DAILY_GAIN_LOCK_PCT = 5.0      # Pause new entries today if cumulative gains reach this

    def __init__(self, data_dir: Path = None):
        if data_dir is None:
            script_dir = Path(__file__).parent
            data_dir = script_dir.parent.parent / "ai_trader_data"
        self.state_file = data_dir / "circuit_breaker_state.json"
        self._state = self._load_state()
        self._reset_if_new_period()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_trading_allowed(self) -> bool:
        """Return True if new positions can be opened."""
        self._reset_if_new_period()
        paused_until_str = self._state.get("paused_until")
        if not paused_until_str:
            return True
        paused_until = datetime.fromisoformat(paused_until_str)
        if datetime.now() >= paused_until:
            self._state["paused_until"] = None
            self._save_state()
            return True
        return False

    def get_pause_reason(self) -> Optional[str]:
        """Return human-readable reason for current pause, or None."""
        if self.is_trading_allowed():
            return None
        paused_until = datetime.fromisoformat(self._state["paused_until"])
        reason = self._state.get("pause_reason", "circuit breaker active")
        return f"{reason} (resumes {paused_until.strftime('%a %H:%M')})"

    def record_trade_pnl(self, realized_pnl_pct: float, symbol: str = ""):
        """
        Record a realized P&L from a closed trade and check limits.

        Args:
            realized_pnl_pct: P&L as a percentage (e.g. -1.5 for -1.5%)
            symbol: Symbol for logging
        """
        self._reset_if_new_period()

        self._state["daily_pnl_pct"] += realized_pnl_pct
        self._state["weekly_pnl_pct"] += realized_pnl_pct
        self._save_state()

        daily = self._state["daily_pnl_pct"]
        weekly = self._state["weekly_pnl_pct"]

        tag = f" ({symbol})" if symbol else ""
        logger.info(
            f"Circuit breaker P&L update{tag}: trade={realized_pnl_pct:+.2f}%, "
            f"daily={daily:+.2f}%, weekly={weekly:+.2f}%"
        )

        # Check weekly first (more severe)
        if weekly <= self.WEEKLY_LOSS_LIMIT_PCT:
            next_monday = self._next_monday()
            self._pause_until(next_monday, f"Weekly loss limit hit ({weekly:.1f}%)")
            return

        # Check daily loss
        if daily <= self.DAILY_LOSS_LIMIT_PCT:
            end_of_day = datetime.now().replace(hour=23, minute=59, second=0)
            self._pause_until(end_of_day, f"Daily loss limit hit ({daily:.1f}%)")
            return

        # Check daily gain lock (upside circuit breaker)
        if daily >= self.DAILY_GAIN_LOCK_PCT:
            end_of_day = datetime.now().replace(hour=23, minute=59, second=0)
            self._pause_until(end_of_day, f"Daily gain lock hit (+{daily:.1f}%) - protecting profits")
            logger.info(
                f"UPSIDE CIRCUIT BREAKER: +{daily:.1f}% daily gain. "
                f"New entries paused for the rest of the day to protect profits."
            )
            return

    def get_status_dict(self) -> dict:
        """Return current state as a dict for dashboard/logging."""
        self._reset_if_new_period()
        return {
            "trading_allowed": self.is_trading_allowed(),
            "daily_pnl_pct": round(self._state.get("daily_pnl_pct", 0.0), 2),
            "weekly_pnl_pct": round(self._state.get("weekly_pnl_pct", 0.0), 2),
            "daily_loss_limit_pct": self.DAILY_LOSS_LIMIT_PCT,
            "weekly_loss_limit_pct": self.WEEKLY_LOSS_LIMIT_PCT,
            "daily_gain_lock_pct": self.DAILY_GAIN_LOCK_PCT,
            "paused_until": self._state.get("paused_until"),
            "pause_reason": self._state.get("pause_reason"),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pause_until(self, until: datetime, reason: str):
        self._state["paused_until"] = until.isoformat()
        self._state["pause_reason"] = reason
        self._save_state()
        logger.critical(
            f"CIRCUIT BREAKER TRIGGERED: {reason}. "
            f"Trading paused until {until.strftime('%a %Y-%m-%d %H:%M')}."
        )

    def _reset_if_new_period(self):
        """Reset daily counter on new day, weekly counter on new Monday."""
        today_str = date.today().isoformat()
        week_str = self._week_key()

        changed = False

        if self._state.get("last_daily_date") != today_str:
            self._state["daily_pnl_pct"] = 0.0
            self._state["last_daily_date"] = today_str
            changed = True
            logger.debug(f"Circuit breaker: daily P&L reset for {today_str}")

        if self._state.get("last_weekly_key") != week_str:
            self._state["weekly_pnl_pct"] = 0.0
            self._state["last_weekly_key"] = week_str
            changed = True
            logger.debug(f"Circuit breaker: weekly P&L reset for week {week_str}")

        if changed:
            self._save_state()

    def _week_key(self) -> str:
        """ISO week key e.g. '2026-W07'"""
        d = date.today()
        return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"

    def _next_monday(self) -> datetime:
        today = date.today()
        days_until_monday = (7 - today.weekday()) % 7 or 7
        next_mon = today + timedelta(days=days_until_monday)
        return datetime(next_mon.year, next_mon.month, next_mon.day, 6, 25)  # 6:25 AM - pre-market

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load circuit breaker state: {e}")
        return {
            "daily_pnl_pct": 0.0,
            "weekly_pnl_pct": 0.0,
            "last_daily_date": None,
            "last_weekly_key": None,
            "paused_until": None,
            "pause_reason": None,
        }

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save circuit breaker state: {e}")
