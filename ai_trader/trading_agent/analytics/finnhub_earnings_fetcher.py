"""
Finnhub Earnings Fetcher - Primary catalyst data source.

Replaces Alpaca News API which returned pre-announcement articles causing
negative/invalid catalyst ages and phantom trades in simulation.

Finnhub provides:
- Confirmed earnings dates only (no pre-announcement noise)
- Actual EPS vs estimate + surprise %
- Revenue actual vs estimate
- Earnings timing (amc = after market close, bmo = before market open)
- Free tier: 60 calls/minute - sufficient for 50 symbol universe

API docs: https://finnhub.io/docs/api/earnings-calendar

Key storage: ai_trader/trading_agent/config/finnhub_api_key.txt
Rate limit: 60 calls/minute on free tier
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Config path relative to this file
_CONFIG_DIR = Path(__file__).parent.parent / "config"
_KEY_FILE = _CONFIG_DIR / "finnhub_api_key.txt"
_BASE_URL = "https://finnhub.io/api/v1"

# Rate limiting: free tier is 60 calls/minute
# Divided across MAX_WORKERS=3 parallel processes (each process has own memory).
# 55 / 3 = 18 calls/minute per process - total across 3 workers stays under 55.
# Within a single process, the module-level sliding window enforces this limit
# across all threads sharing that process.
_RATE_LIMIT_CALLS = 18          # per-process limit (55 total / 3 workers)
_RATE_LIMIT_WINDOW = 60.0       # seconds
_call_timestamps: deque = deque()  # timestamps of recent calls
_rate_lock = threading.Lock()   # protects _call_timestamps across threads


def _rate_limited_call():
    """
    Block until it is safe to make another Finnhub API call.
    Enforces 55 calls/60s across ALL FinnhubEarningsFetcher instances
    using a shared module-level sliding window counter.
    Thread-safe via _rate_lock.
    """
    while True:
        with _rate_lock:
            now = time.monotonic()
            # Remove timestamps older than 60s
            while _call_timestamps and now - _call_timestamps[0] >= _RATE_LIMIT_WINDOW:
                _call_timestamps.popleft()

            if len(_call_timestamps) < _RATE_LIMIT_CALLS:
                # Safe to call - record this call and proceed
                _call_timestamps.append(now)
                return

            # Too many calls in window - calculate wait time
            oldest = _call_timestamps[0]
            wait = _RATE_LIMIT_WINDOW - (now - oldest) + 0.05  # small buffer

        # Wait outside the lock so other threads can check/proceed
        logger.debug(f"Finnhub rate limit: waiting {wait:.1f}s")
        time.sleep(wait)


def _load_api_key() -> Optional[str]:
    """Load Finnhub API key from config file or environment variable."""
    key = os.getenv("FINNHUB_API_KEY")
    if key:
        return key.strip()
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_text().strip()
        if key:
            return key
    return None


class FinnhubEarningsFetcher:
    """
    Fetches earnings catalyst data from Finnhub API.

    Advantages over Alpaca News API:
    - Returns confirmed past earnings only (no pre-announcement articles)
    - Structured EPS/revenue data - no text parsing needed
    - gap_pct and vix_level populated via Schwab price data (same as before)
    - No negative catalyst age issues
    - 60 calls/minute free tier - 50 symbols in ~50s

    Usage:
        fetcher = FinnhubEarningsFetcher()
        catalysts = fetcher.fetch_earnings_for_universe(
            symbols=['HOOD', 'SHOP', 'SMCI'],
            start_date=datetime(2025, 11, 1),
            end_date=datetime(2025, 11, 30)
        )
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or _load_api_key()
        if not self.api_key:
            raise ValueError(
                "Finnhub API key not found. Set FINNHUB_API_KEY env var "
                "or put key in ai_trader/trading_agent/config/finnhub_api_key.txt"
            )

    def _rate_limit(self):
        """
        Enforce Finnhub free tier limit (55 calls/60s) across all instances
        and all threads. Delegates to module-level sliding window counter.
        """
        _rate_limited_call()

    def get_earnings_surprises(self, symbol: str) -> list:
        """
        Fetch historical earnings surprises for a symbol.

        Returns list of dicts with keys:
            actual, estimate, period, quarter, surprise,
            surprisePercent, symbol, year

        Endpoint: GET /stock/earnings
        Free tier: yes
        """
        self._rate_limit()
        try:
            r = requests.get(
                f"{_BASE_URL}/stock/earnings",
                params={"symbol": symbol, "token": self.api_key},
                timeout=10
            )
            if r.status_code == 200:
                return r.json() or []
            elif r.status_code == 429:
                logger.warning(f"Finnhub rate limit hit for {symbol}, waiting 60s")
                time.sleep(60)
                return self.get_earnings_surprises(symbol)
            else:
                logger.debug(f"Finnhub {symbol}: status {r.status_code}")
                return []
        except Exception as e:
            logger.debug(f"Finnhub {symbol}: {e}")
            return []

    def get_earnings_calendar(self, from_date: str, to_date: str,
                              symbol: Optional[str] = None) -> list:
        """
        Fetch earnings calendar for a date range.

        Returns list of dicts with keys:
            date, epsActual, epsEstimate, hour, quarter,
            revenueActual, revenueEstimate, symbol, year

        hour values: 'amc' (after market close), 'bmo' (before market open),
                     'dmh' (during market hours)

        Endpoint: GET /calendar/earnings
        Note: date range batch - one call for all symbols in range.
        """
        self._rate_limit()
        params = {"from": from_date, "to": to_date, "token": self.api_key}
        if symbol:
            params["symbol"] = symbol
        try:
            r = requests.get(
                f"{_BASE_URL}/calendar/earnings",
                params=params,
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("earningsCalendar", [])
            elif r.status_code == 429:
                logger.warning("Finnhub rate limit hit on calendar, waiting 60s")
                time.sleep(60)
                return self.get_earnings_calendar(from_date, to_date, symbol)
            else:
                logger.debug(f"Finnhub calendar: status {r.status_code}")
                return []
        except Exception as e:
            logger.debug(f"Finnhub calendar: {e}")
            return []

    def fetch_earnings_for_universe(
        self,
        symbols: list,
        start_date: datetime,
        end_date: datetime,
        min_eps_actual: Optional[float] = None
    ) -> list:
        """
        Fetch earnings events for a list of symbols within a date range.

        Returns list of catalyst dicts compatible with historical_data_fetcher format:
            {
                'symbol': str,
                'date': 'YYYY-MM-DD',         # earnings announcement date
                'catalyst_type': 'earnings',
                'eps_actual': float or None,
                'eps_estimate': float or None,
                'eps_surprise_pct': float or None,
                'revenue_actual': float or None,
                'revenue_estimate': float or None,
                'earnings_hour': str,           # 'amc', 'bmo', 'dmh'
                'source': 'finnhub'
            }

        gap_pct and vix_level are NOT populated here - they require price data
        from Schwab/yfinance and are added by historical_data_fetcher after this call.

        Strategy:
        1. Try earnings calendar batch (one call for date range)
        2. Fall back to per-symbol earnings surprises if calendar not available
        """
        from_str = start_date.strftime("%Y-%m-%d")
        to_str = end_date.strftime("%Y-%m-%d")

        catalysts = []
        symbol_set = set(s.upper() for s in symbols)

        # Strategy 1: Try batch calendar endpoint first (1 API call for whole range)
        logger.info(f"Finnhub: fetching earnings calendar {from_str} to {to_str}")
        calendar_data = self.get_earnings_calendar(from_str, to_str)

        if calendar_data:
            for item in calendar_data:
                sym = item.get("symbol", "").upper()
                if sym not in symbol_set:
                    continue
                date_str = item.get("date")
                if not date_str:
                    continue

                eps_actual = item.get("epsActual")
                eps_estimate = item.get("epsEstimate")

                # Skip if no actual EPS - means earnings haven't happened yet
                if eps_actual is None:
                    logger.debug(f"Finnhub: {sym} {date_str} skipped - no actual EPS")
                    continue

                surprise_pct = None
                if eps_actual is not None and eps_estimate and eps_estimate != 0:
                    surprise_pct = round(
                        (eps_actual - eps_estimate) / abs(eps_estimate) * 100, 2
                    )

                catalysts.append({
                    "symbol": sym,
                    "date": date_str,
                    "catalyst_type": "earnings",
                    "eps_actual": eps_actual,
                    "eps_estimate": eps_estimate,
                    "eps_surprise_pct": surprise_pct,
                    "revenue_actual": item.get("revenueActual"),
                    "revenue_estimate": item.get("revenueEstimate"),
                    "earnings_hour": item.get("hour", "unknown"),
                    "source": "finnhub_calendar"
                })

            logger.info(
                f"Finnhub calendar: {len(catalysts)} confirmed earnings "
                f"for {len(symbol_set)} symbols"
            )
            return catalysts

        # Strategy 2: Fall back to per-symbol earnings surprises
        logger.info("Finnhub calendar unavailable, falling back to per-symbol earnings")
        start_dt = start_date.date()
        end_dt = end_date.date()

        for symbol in symbols:
            surprises = self.get_earnings_surprises(symbol)
            for item in surprises:
                period_str = item.get("period", "")
                if not period_str:
                    continue
                try:
                    period_date = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                if not (start_dt <= period_date <= end_dt):
                    continue

                eps_actual = item.get("actual")
                eps_estimate = item.get("estimate")
                surprise_pct = item.get("surprisePercent")

                catalysts.append({
                    "symbol": symbol.upper(),
                    "date": period_str,
                    "catalyst_type": "earnings",
                    "eps_actual": eps_actual,
                    "eps_estimate": eps_estimate,
                    "eps_surprise_pct": round(surprise_pct, 2) if surprise_pct else None,
                    "revenue_actual": None,
                    "revenue_estimate": None,
                    "earnings_hour": "unknown",
                    "source": "finnhub_surprises"
                })

        logger.info(
            f"Finnhub surprises: {len(catalysts)} earnings events for "
            f"{len(symbols)} symbols"
        )
        return catalysts


def test_finnhub_fetcher():
    """Quick test - run directly: python -m ai_trader.trading_agent.analytics.finnhub_earnings_fetcher"""
    import json
    fetcher = FinnhubEarningsFetcher()

    symbols = ["HOOD", "SHOP", "CHWY", "AFRM", "ABNB", "SMCI", "LULU"]
    start = datetime(2025, 10, 1)
    end = datetime(2026, 2, 16)

    print(f"Fetching earnings for {len(symbols)} symbols {start.date()} to {end.date()}...")
    catalysts = fetcher.fetch_earnings_for_universe(symbols, start, end)

    print(f"\nFound {len(catalysts)} earnings events:")
    for c in catalysts:
        surprise = f"{c['eps_surprise_pct']:+.1f}%" if c['eps_surprise_pct'] else "N/A"
        print(
            f"  {c['symbol']:6s} {c['date']} | "
            f"actual={c['eps_actual']} est={c['eps_estimate']} "
            f"surprise={surprise} | hour={c['earnings_hour']} | src={c['source']}"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_finnhub_fetcher()
