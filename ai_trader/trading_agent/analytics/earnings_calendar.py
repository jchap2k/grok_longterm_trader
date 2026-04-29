"""
EarningsCalendar - Safety gate for swing trading positions.

Critical rule: Never hold a swing position through earnings.
This module checks upcoming earnings dates for candidates and
open positions to prevent accidental binary-event exposure.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EarningsCheckResult:
    safe: bool
    reason: str
    days_until_earnings: Optional[int]
    earnings_date: Optional[date]


class EarningsCalendar:
    """
    Checks upcoming earnings dates for swing trade safety gating.

    Entry rule: Reject any candidate within SAFE_BUFFER_DAYS of earnings
                (unless entry_type is PEAD - which IS an earnings play).

    Position rule: Check all open positions daily; flag EARNINGS_PROXIMITY
                   when days_until_earnings <= 3, EARNINGS_IMMEDIATE when <= 1.
    """

    SAFE_BUFFER_DAYS = 5   # reject new entries within 5 days of earnings
    PROXIMITY_DAYS   = 3   # flag open positions within 3 days
    IMMEDIATE_DAYS   = 1   # immediate exit signal within 1 day
    CACHE_TTL_DAYS   = 1   # re-fetch earnings date after 1 day

    def __init__(self, alpaca_client=None):
        self._alpaca = alpaca_client
        # Cache: {symbol: (earnings_date_or_None, fetched_on_date)}
        self._cache: dict = {}

    def get_earnings_date(self, symbol: str) -> Optional[date]:
        """
        Return the next earnings date for symbol, or None if unknown.
        Results are cached for CACHE_TTL_DAYS.
        """
        today = date.today()
        if symbol in self._cache:
            cached_date, fetched_on = self._cache[symbol]
            if (today - fetched_on).days < self.CACHE_TTL_DAYS:
                return cached_date

        try:
            earnings_dt = self._fetch_from_alpaca(symbol)
        except Exception as e:
            logger.warning("EarningsCalendar: fetch failed for %s: %s", symbol, e)
            earnings_dt = None

        self._cache[symbol] = (earnings_dt, today)
        return earnings_dt

    def days_until_earnings(self, symbol: str) -> Optional[int]:
        """Return number of calendar days until next earnings, or None if unknown."""
        dt = self.get_earnings_date(symbol)
        if dt is None:
            return None
        return (dt - date.today()).days

    def is_safe_to_enter(self, symbol: str, entry_type: str) -> EarningsCheckResult:
        """
        Check if a new swing entry is safe given earnings proximity.

        Args:
            symbol: Stock ticker
            entry_type: FORCESWING | UPGRADE | BREAKOUT | ROTATION | PEAD

        Returns:
            EarningsCheckResult with safe=True/False and reason string.

        PEAD always returns safe=True (it IS the earnings play - entered post-announcement).
        Unknown earnings date returns safe=True with a warning in the reason.
        """
        if entry_type == "PEAD":
            return EarningsCheckResult(
                safe=True,
                reason="PEAD entry - post-earnings play, earnings gate bypassed",
                days_until_earnings=None,
                earnings_date=None,
            )

        dt = self.get_earnings_date(symbol)

        if dt is None:
            return EarningsCheckResult(
                safe=True,
                reason="earnings date unknown - monitor position closely",
                days_until_earnings=None,
                earnings_date=None,
            )

        days = (dt - date.today()).days

        if days <= self.IMMEDIATE_DAYS:
            return EarningsCheckResult(
                safe=False,
                reason="EARNINGS_IMMEDIATE: {}d until earnings on {}".format(days, dt),
                days_until_earnings=days,
                earnings_date=dt,
            )

        if days <= self.SAFE_BUFFER_DAYS:
            return EarningsCheckResult(
                safe=False,
                reason="EARNINGS_PROXIMITY: {}d until earnings on {}".format(days, dt),
                days_until_earnings=days,
                earnings_date=dt,
            )

        return EarningsCheckResult(
            safe=True,
            reason="earnings {}d away ({}) - safe to enter".format(days, dt),
            days_until_earnings=days,
            earnings_date=dt,
        )

    def check_open_position(self, symbol: str, hold_type: str) -> EarningsCheckResult:
        """
        Daily check for an OPEN position. More aggressive thresholds than entry check.
        Used by SwingExitEngine to flag EARNINGS_PROXIMITY exits.

        PEAD positions use hold_type='pead' and bypass this check.
        """
        if hold_type == "pead":
            return EarningsCheckResult(
                safe=True,
                reason="PEAD position - earnings drift is the thesis",
                days_until_earnings=None,
                earnings_date=None,
            )
        return self.is_safe_to_enter(symbol, entry_type="FORCESWING")

    def _fetch_from_alpaca(self, symbol: str) -> Optional[date]:
        """
        Fetch next earnings date via yfinance Ticker.calendar.
        Method name preserved for compatibility; yfinance is the actual source.
        Returns None if no future earnings date is found (safe fallback).
        """
        try:
            import yfinance as yf
            cal = yf.Ticker(symbol).calendar
            if cal is None:
                return None
            # calendar is a dict: {"Earnings Date": [Timestamp, ...], ...}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if not ed:
                return None
            today = date.today()
            for dt in (ed if hasattr(ed, "__iter__") else [ed]):
                try:
                    d = dt.date() if hasattr(dt, "date") else date.fromisoformat(str(dt)[:10])
                    if d >= today:
                        return d
                except Exception:
                    continue
        except Exception as e:
            logger.debug("EarningsCalendar._fetch_from_alpaca(%s) yfinance: %s", symbol, e)
        return None
