"""
Market Calendar - NYSE/NASDAQ Holiday Detection

Provides comprehensive holiday calendar for US stock markets with support for:
- Fixed holidays (New Year's, Independence Day, Christmas)
- Floating holidays (Martin Luther King Jr. Day, Presidents' Day, etc.)
- Early close days (day before Thanksgiving, Christmas Eve when not on weekend)
- Special closures and market events

Updated for 2026 and includes historical/future years.
"""

from datetime import datetime, date, time as dt_time
from typing import Dict, List, Optional, Tuple
import logging
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class MarketCalendar:
    """
    NYSE/NASDAQ market calendar with holiday detection and early close tracking.

    Market Hours (Eastern Time):
    - Regular: 9:30 AM - 4:00 PM
    - Early Close: 9:30 AM - 1:00 PM (typically day before Thanksgiving, Christmas Eve)
    """

    # Fixed holidays (month, day) - if falls on weekend, observed on adjacent weekday
    FIXED_HOLIDAYS = {
        "New Year's Day": (1, 1),
        "Independence Day": (7, 4),
        "Christmas": (12, 25),
    }

    # Early close days (month, day) - market closes at 1:00 PM ET
    EARLY_CLOSE_FIXED = {
        "Christmas Eve": (12, 24),  # Only if not Saturday/Sunday
        "Day After Thanksgiving": None,  # Calculated dynamically (4th Friday of November)
        "Independence Day Eve": (7, 3),  # Only if July 4 falls on weekday
    }

    def __init__(self):
        """Initialize market calendar."""
        self._cache = {}  # Cache for performance

    def get_nth_weekday_of_month(self, year: int, month: int, weekday: int, n: int) -> date:
        """
        Get the Nth occurrence of a weekday in a month.

        Args:
            year: Year
            month: Month (1-12)
            weekday: Day of week (0=Monday, 6=Sunday)
            n: Which occurrence (1=first, 2=second, etc.)

        Returns:
            Date of the Nth weekday
        """
        # Start with first day of month
        first_day = date(year, month, 1)

        # Find first occurrence of target weekday
        days_ahead = weekday - first_day.weekday()
        if days_ahead < 0:
            days_ahead += 7

        first_occurrence = first_day.day + days_ahead
        target_day = first_occurrence + (n - 1) * 7

        return date(year, month, target_day)

    def get_floating_holidays(self, year: int) -> Dict[str, date]:
        """
        Get floating holidays for a given year.

        Returns:
            Dictionary mapping holiday name to date
        """
        holidays = {}

        # Martin Luther King Jr. Day - 3rd Monday in January
        holidays["Martin Luther King Jr. Day"] = self.get_nth_weekday_of_month(year, 1, 0, 3)

        # Presidents' Day - 3rd Monday in February
        holidays["Presidents' Day"] = self.get_nth_weekday_of_month(year, 2, 0, 3)

        # Good Friday - Friday before Easter (complex calculation)
        holidays["Good Friday"] = self._calculate_good_friday(year)

        # Memorial Day - Last Monday in May
        # Find last Monday by getting 5th Monday (if exists) or 4th Monday
        try:
            holidays["Memorial Day"] = self.get_nth_weekday_of_month(year, 5, 0, 5)
        except ValueError:
            holidays["Memorial Day"] = self.get_nth_weekday_of_month(year, 5, 0, 4)

        # Labor Day - 1st Monday in September
        holidays["Labor Day"] = self.get_nth_weekday_of_month(year, 9, 0, 1)

        # Thanksgiving - 4th Thursday in November
        holidays["Thanksgiving"] = self.get_nth_weekday_of_month(year, 11, 3, 4)

        return holidays

    def _calculate_good_friday(self, year: int) -> date:
        """
        Calculate Good Friday date (Friday before Easter).

        Uses Computus algorithm for Easter calculation.
        """
        # Meeus/Jones/Butcher algorithm for Gregorian calendar
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1

        easter_sunday = date(year, month, day)

        # Good Friday is 2 days before Easter Sunday
        from datetime import timedelta
        good_friday = easter_sunday - timedelta(days=2)

        return good_friday

    def get_observed_holiday(self, year: int, month: int, day: int) -> Optional[date]:
        """
        Get observed date for a fixed holiday (handles weekend adjustment).

        If holiday falls on Saturday, observed on Friday.
        If holiday falls on Sunday, observed on Monday.

        Args:
            year: Year
            month: Month
            day: Day

        Returns:
            Observed date, or None if not applicable
        """
        holiday_date = date(year, month, day)
        weekday = holiday_date.weekday()

        if weekday == 5:  # Saturday
            # Observed on Friday
            from datetime import timedelta
            return holiday_date - timedelta(days=1)
        elif weekday == 6:  # Sunday
            # Observed on Monday
            from datetime import timedelta
            return holiday_date + timedelta(days=1)
        else:
            return holiday_date

    def get_all_holidays(self, year: int) -> Dict[str, date]:
        """
        Get all market holidays for a given year.

        Returns:
            Dictionary mapping holiday name to observed date
        """
        # Check cache
        cache_key = f"holidays_{year}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        holidays = {}

        # Add fixed holidays (with weekend adjustment)
        for name, (month, day) in self.FIXED_HOLIDAYS.items():
            observed = self.get_observed_holiday(year, month, day)
            if observed:
                holidays[name] = observed

        # Add floating holidays
        holidays.update(self.get_floating_holidays(year))

        # Cache result
        self._cache[cache_key] = holidays

        return holidays

    def is_market_holiday(self, check_date: date) -> Tuple[bool, Optional[str]]:
        """
        Check if a given date is a market holiday.

        Args:
            check_date: Date to check

        Returns:
            Tuple of (is_holiday, holiday_name)
        """
        holidays = self.get_all_holidays(check_date.year)

        for name, holiday_date in holidays.items():
            if check_date == holiday_date:
                return True, name

        return False, None

    def is_early_close_day(self, check_date: date) -> Tuple[bool, Optional[str]]:
        """
        Check if a given date is an early close day (market closes at 1:00 PM ET).

        Args:
            check_date: Date to check

        Returns:
            Tuple of (is_early_close, reason)
        """
        # Day after Thanksgiving (Friday after 4th Thursday in November)
        holidays = self.get_floating_holidays(check_date.year)
        thanksgiving = holidays.get("Thanksgiving")
        if thanksgiving:
            from datetime import timedelta
            day_after_thanksgiving = thanksgiving + timedelta(days=1)
            if check_date == day_after_thanksgiving:
                return True, "Day After Thanksgiving"

        # Christmas Eve (if not Saturday or Sunday)
        if check_date.month == 12 and check_date.day == 24:
            if check_date.weekday() < 5:  # Monday-Friday
                return True, "Christmas Eve"

        # Independence Day Eve (July 3, if July 4 falls on weekday)
        if check_date.month == 7 and check_date.day == 3:
            july_4 = date(check_date.year, 7, 4)
            if 0 <= july_4.weekday() <= 4:  # July 4 is Monday-Friday
                return True, "Independence Day Eve"

        return False, None

    def get_market_close_time(self, check_date: date) -> dt_time:
        """
        Get market close time for a given date.

        Args:
            check_date: Date to check

        Returns:
            Market close time (Eastern Time)
        """
        is_early, _ = self.is_early_close_day(check_date)

        if is_early:
            return dt_time(13, 0)  # 1:00 PM ET
        else:
            return dt_time(16, 0)  # 4:00 PM ET

    def is_market_open_today(self, check_datetime: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Check if market is open on a given date/time.

        Args:
            check_datetime: Datetime to check (defaults to now in Eastern Time)

        Returns:
            Tuple of (is_open, reason)
        """
        if check_datetime is None:
            check_datetime = datetime.now(ZoneInfo("America/New_York"))

        # Ensure timezone-aware
        if check_datetime.tzinfo is None:
            check_datetime = check_datetime.replace(tzinfo=ZoneInfo("America/New_York"))

        # Convert to Eastern Time
        et_datetime = check_datetime.astimezone(ZoneInfo("America/New_York"))
        check_date = et_datetime.date()

        # Check if weekend
        if check_date.weekday() >= 5:  # Saturday or Sunday
            return False, "Weekend"

        # Check if holiday
        is_holiday, holiday_name = self.is_market_holiday(check_date)
        if is_holiday:
            return False, f"Holiday: {holiday_name}"

        # Market is open on this date (but check time)
        return True, "Market Open"

    def get_next_trading_day(self, start_date: Optional[date] = None) -> date:
        """
        Get the next trading day (skips weekends and holidays).

        Args:
            start_date: Date to start from (defaults to today)

        Returns:
            Next trading day
        """
        if start_date is None:
            start_date = datetime.now(ZoneInfo("America/New_York")).date()

        from datetime import timedelta
        next_day = start_date + timedelta(days=1)

        # Keep advancing until we find a trading day
        max_iterations = 10  # Safety limit (handles long holiday weekends)
        for _ in range(max_iterations):
            # Check if weekend
            if next_day.weekday() >= 5:
                next_day += timedelta(days=1)
                continue

            # Check if holiday
            is_holiday, _ = self.is_market_holiday(next_day)
            if is_holiday:
                next_day += timedelta(days=1)
                continue

            # Found a trading day
            return next_day

        # Fallback (shouldn't happen)
        logger.warning(f"Could not find next trading day after {start_date} within {max_iterations} days")
        return next_day

    def get_trading_days_in_month(self, year: int, month: int) -> List[date]:
        """
        Get all trading days in a given month.

        Args:
            year: Year
            month: Month (1-12)

        Returns:
            List of trading days
        """
        from datetime import timedelta

        trading_days = []

        # Get all days in month
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)

        current_date = date(year, month, 1)

        while current_date < next_month:
            # Skip weekends
            if current_date.weekday() < 5:
                # Check if holiday
                is_holiday, _ = self.is_market_holiday(current_date)
                if not is_holiday:
                    trading_days.append(current_date)

            current_date += timedelta(days=1)

        return trading_days


# Singleton instance
_calendar_instance = None


def get_market_calendar() -> MarketCalendar:
    """Get singleton market calendar instance."""
    global _calendar_instance
    if _calendar_instance is None:
        _calendar_instance = MarketCalendar()
    return _calendar_instance


# Convenience functions
def is_market_holiday(check_date: Optional[date] = None) -> bool:
    """Check if given date is a market holiday."""
    if check_date is None:
        check_date = datetime.now(ZoneInfo("America/New_York")).date()

    calendar = get_market_calendar()
    is_holiday, _ = calendar.is_market_holiday(check_date)
    return is_holiday


def is_early_close(check_date: Optional[date] = None) -> bool:
    """Check if given date is an early close day."""
    if check_date is None:
        check_date = datetime.now(ZoneInfo("America/New_York")).date()

    calendar = get_market_calendar()
    is_early, _ = calendar.is_early_close_day(check_date)
    return is_early


def get_market_close_time(check_date: Optional[date] = None) -> dt_time:
    """Get market close time for given date."""
    if check_date is None:
        check_date = datetime.now(ZoneInfo("America/New_York")).date()

    calendar = get_market_calendar()
    return calendar.get_market_close_time(check_date)


# Testing function
if __name__ == "__main__":
    # Test calendar
    calendar = get_market_calendar()

    # Test 2026 holidays
    print("2026 Market Holidays:")
    print("=" * 60)
    holidays = calendar.get_all_holidays(2026)
    for name, holiday_date in sorted(holidays.items(), key=lambda x: x[1]):
        weekday_name = holiday_date.strftime("%A")
        print(f"{name:30s} {holiday_date} ({weekday_name})")

    print("\n2026 Early Close Days:")
    print("=" * 60)

    # Check each day in 2026 for early close
    from datetime import timedelta
    current_date = date(2026, 1, 1)
    end_date = date(2026, 12, 31)

    while current_date <= end_date:
        is_early, reason = calendar.is_early_close_day(current_date)
        if is_early:
            weekday_name = current_date.strftime("%A")
            print(f"{reason:30s} {current_date} ({weekday_name}) - Close at 1:00 PM ET")
        current_date += timedelta(days=1)

    print("\nNext Trading Day Test:")
    print("=" * 60)
    test_dates = [
        date(2026, 1, 1),   # New Year's Day (holiday)
        date(2026, 7, 3),   # Friday before July 4
        date(2026, 12, 25), # Christmas
    ]

    for test_date in test_dates:
        next_day = calendar.get_next_trading_day(test_date)
        print(f"After {test_date}: Next trading day is {next_day}")
