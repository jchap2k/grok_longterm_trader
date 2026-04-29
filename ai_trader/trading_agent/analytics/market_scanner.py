"""
Market Scanner - Direct API fetching for stock discovery.

Replaces expensive Grok API calls for Yahoo/TradingView discovery with direct
Python HTTP requests. Grok only receives the pre-structured candidate list.

Token savings: ~75% reduction on discovery calls (3 API calls -> 0 for Yahoo/TV).

Sources:
- Yahoo Finance API (gainers, most-active, losers)
- TradingView Scanner API (unusual volume, pre-market, volatile)

Usage:
    scanner = MarketScanner()
    candidates = scanner.fetch_all_candidates(is_premarket=True)
    # Returns list of dicts with symbol, price, change_pct, volume, source
"""

import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Request headers
YAHOO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TV_HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://www.tradingview.com',
    'Referer': 'https://www.tradingview.com/'
}

# Yahoo Finance screener API
YAHOO_SCREENER_URL = 'https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved'
YAHOO_FIELDS = 'symbol,regularMarketPrice,regularMarketChangePercent,regularMarketVolume,averageDailyVolume3Month'

# TradingView scanner API
TV_SCANNER_URL = 'https://scanner.tradingview.com/america/scan'

# Price/volume filters applied server-side on TradingView
TV_PRICE_MIN = 5
TV_PRICE_MAX = 500   # Raised from 200 - matches scheduler filter ceiling
TV_VOLUME_MIN = 750000
TV_REL_VOL_MIN = 3  # 3x average for unusual volume

# Swing-specific server-side filters
TV_SWING_PRICE_MIN = 12      # $12 floor - matches FORCESWING price gate
TV_SWING_VOLUME_MIN = 500000 # 500K ADV floor - matches FORCESWING ADV gate
TV_SWING_MARKET_CAP_MIN = 500_000_000  # $500M floor - no micro-caps
TV_SWING_PULLBACK_1M_MIN = -8
TV_SWING_PULLBACK_1M_MAX = -1
TV_SWING_PULLBACK_3M_MIN = 10
TV_SWING_PULLBACK_Y_MIN = 25
TV_SWING_UPTREND_3M_MIN = 12
TV_SWING_UPTREND_3M_MAX = 45
TV_SWING_UPTREND_1M_MIN = -3
TV_SWING_UPTREND_1M_MAX = 3


class MarketScanner:
    """
    Direct HTTP fetching for market discovery.

    Replaces Grok browse_page / web_search calls for Yahoo Finance and
    TradingView. Results are clean structured dicts ready for the agent.
    """

    def __init__(self, timeout: int = 10, alpaca_client=None):
        self.timeout = timeout
        self._alpaca_client = alpaca_client

    def fetch_yahoo_candidates(self) -> list:
        """
        Fetch top gainers, most active, and losers from Yahoo Finance API.

        Returns:
            List of candidate dicts with symbol, price, change_pct, volume, source
        """
        screeners = [
            ('day_gainers', 'yahoo_gainers'),
            ('most_actives', 'yahoo_active'),
            ('day_losers', 'yahoo_losers'),
        ]

        candidates = []
        for scr_id, source_name in screeners:
            try:
                params = {
                    'scrIds': scr_id,
                    'count': 25,
                    'fields': YAHOO_FIELDS
                }
                r = requests.get(
                    YAHOO_SCREENER_URL,
                    headers=YAHOO_HEADERS,
                    params=params,
                    timeout=self.timeout
                )
                if r.status_code != 200:
                    logger.warning(f"Yahoo {source_name}: HTTP {r.status_code}")
                    continue

                data = r.json()
                quotes = data.get('finance', {}).get('result', [{}])[0].get('quotes', [])

                count = 0
                for q in quotes:
                    symbol = q.get('symbol', '')
                    # Skip non-equity (crypto, ETFs with dots/dashes)
                    if not symbol or '-' in symbol or '.' in symbol:
                        continue
                    # Skip if clearly not a stock ticker
                    if len(symbol) > 5:
                        continue

                    price = q.get('regularMarketPrice', 0) or 0
                    change_pct = q.get('regularMarketChangePercent', 0) or 0
                    volume = q.get('regularMarketVolume', 0) or 0
                    avg_volume = q.get('averageDailyVolume3Month', 0) or 0

                    candidates.append({
                        'symbol': symbol,
                        'price': round(float(price), 2),
                        'change_pct': round(float(change_pct), 2),
                        'volume': int(volume),
                        'avg_volume': int(avg_volume),
                        'source': source_name
                    })
                    count += 1

                logger.info(f"Yahoo {source_name}: {count} candidates")

            except Exception as e:
                logger.warning(f"Yahoo {source_name} fetch failed: {e}")

        return candidates

    def fetch_tradingview_candidates(self, is_premarket: bool = False) -> list:
        """
        Fetch candidates from TradingView scanner API.

        Pre-market mode: Returns pre-market gainers with gap_pct.
        Regular hours: Returns unusual volume and most volatile.

        Args:
            is_premarket: True if before 6:30 AM PST

        Returns:
            List of candidate dicts
        """
        candidates = []

        if is_premarket:
            candidates.extend(self._fetch_tv_premarket_gainers())
        else:
            candidates.extend(self._fetch_tv_unusual_volume())
            candidates.extend(self._fetch_tv_volatile())

        return candidates

    def _fetch_tv_premarket_gainers(self) -> list:
        """Fetch pre-market gainers from TradingView."""
        try:
            payload = {
                'filter': [
                    {'left': 'exchange', 'operation': 'in_range', 'right': ['NASDAQ', 'NYSE']},
                    {'left': 'is_primary', 'operation': 'equal', 'right': True},
                    {'left': 'premarket_change', 'operation': 'greater', 'right': 2},
                    {'left': 'premarket_volume', 'operation': 'greater', 'right': 100000},
                    {'left': 'close', 'operation': 'in_range', 'right': [TV_PRICE_MIN, TV_PRICE_MAX]},
                ],
                'options': {'lang': 'en'},
                'markets': ['america'],
                'columns': ['name', 'close', 'premarket_change', 'premarket_close', 'premarket_volume', 'volume', 'relative_volume_10d_calc', 'ATR'],
                'sort': {'sortBy': 'premarket_change', 'sortOrder': 'desc'},
                'range': [0, 20]
            }
            return self._tv_scan(payload, 'tv_premarket_gainers',
                                 lambda d: {
                                     'symbol': d[0],
                                     # Use premarket_close as price so Grok sees current trading price
                                     # not prior close (which would lead to stale entry limits)
                                     'price': d[3] if d[3] else d[1],
                                     'prior_close': d[1],
                                     'change_pct': d[2],
                                     'gap_pct': round((d[3] - d[1]) / d[1] * 100, 2) if d[1] and d[3] else 0,
                                     'volume': d[5] or 0,
                                     'rel_volume': d[6] or 0,
                                     'atr': d[7] or 0,
                                 })
        except Exception as e:
            logger.warning(f"TV premarket gainers failed: {e}")
            return []

    def _fetch_tv_unusual_volume(self, limit: int = 20) -> list:
        """Fetch unusual volume stocks from TradingView."""
        try:
            payload = {
                'filter': [
                    {'left': 'exchange', 'operation': 'in_range', 'right': ['NASDAQ', 'NYSE']},
                    {'left': 'is_primary', 'operation': 'equal', 'right': True},
                    {'left': 'relative_volume_10d_calc', 'operation': 'greater', 'right': TV_REL_VOL_MIN},
                    {'left': 'volume', 'operation': 'greater', 'right': TV_VOLUME_MIN},
                    {'left': 'close', 'operation': 'in_range', 'right': [TV_PRICE_MIN, TV_PRICE_MAX]},
                ],
                'options': {'lang': 'en'},
                'markets': ['america'],
                'columns': ['name', 'close', 'change', 'volume', 'relative_volume_10d_calc', 'average_volume_10d_calc', 'ATR'],
                'sort': {'sortBy': 'relative_volume_10d_calc', 'sortOrder': 'desc'},
                'range': [0, max(1, int(limit))]
            }
            return self._tv_scan(payload, 'tv_unusual_volume',
                                 lambda d: {
                                     'symbol': d[0],
                                     'price': d[1],
                                     'change_pct': d[2],
                                     'volume': d[3] or 0,
                                     'rel_volume': d[4] or 0,
                                     'avg_volume': d[5] or 0,
                                     'atr': d[6] or 0,
                                 })
        except Exception as e:
            logger.warning(f"TV unusual volume failed: {e}")
            return []

    def _fetch_tv_volatile(self, limit: int = 20) -> list:
        """Fetch most volatile stocks from TradingView.

        Wave3/T11: Price floor raised from TV_PRICE_MIN ($5) to TV_SWING_PRICE_MIN ($12).
        Added Perf.3M > 0 filter to exclude downtrending volatile names.
        """
        try:
            payload = {
                'filter': [
                    {'left': 'exchange', 'operation': 'in_range', 'right': ['NASDAQ', 'NYSE']},
                    {'left': 'is_primary', 'operation': 'equal', 'right': True},
                    {'left': 'volume', 'operation': 'greater', 'right': TV_VOLUME_MIN},
                    {'left': 'close', 'operation': 'in_range', 'right': [TV_SWING_PRICE_MIN, TV_PRICE_MAX]},
                    {'left': 'Perf.3M', 'operation': 'greater', 'right': 0},
                ],
                'options': {'lang': 'en'},
                'markets': ['america'],
                'columns': ['name', 'close', 'change', 'volume', 'relative_volume_10d_calc',
                            'ATR', 'Perf.3M'],
                'sort': {'sortBy': 'ATR', 'sortOrder': 'desc'},
                'range': [0, max(1, int(limit))]
            }
            return self._tv_scan(payload, 'tv_volatile',
                                 lambda d: {
                                     'symbol': d[0],
                                     'price': d[1],
                                     'change_pct': d[2],
                                     'volume': d[3] or 0,
                                     'rel_volume': d[4] or 0,
                                     'atr': d[5] or 0,
                                     'perf_3m': d[6] or 0,
                                 })
        except Exception as e:
            logger.warning(f"TV volatile failed: {e}")
            return []

    def _fetch_tv_swing_pullback(self, limit: int = 25) -> list:
        """Fetch established leaders in a controlled 1-month pullback."""
        try:
            payload = {
                'filter': [
                    {'left': 'exchange', 'operation': 'in_range', 'right': ['NASDAQ', 'NYSE']},
                    {'left': 'is_primary', 'operation': 'equal', 'right': True},
                    {'left': 'Perf.Y', 'operation': 'greater', 'right': TV_SWING_PULLBACK_Y_MIN},
                    {'left': 'Perf.3M', 'operation': 'greater', 'right': TV_SWING_PULLBACK_3M_MIN},
                    {'left': 'Perf.1M', 'operation': 'in_range', 'right': [TV_SWING_PULLBACK_1M_MIN, TV_SWING_PULLBACK_1M_MAX]},
                    {'left': 'close', 'operation': 'in_range', 'right': [TV_SWING_PRICE_MIN, TV_PRICE_MAX]},
                    {'left': 'volume', 'operation': 'greater', 'right': TV_SWING_VOLUME_MIN},
                    {'left': 'market_cap_basic', 'operation': 'greater', 'right': TV_SWING_MARKET_CAP_MIN},
                ],
                'options': {'lang': 'en'},
                'markets': ['america'],
                'columns': ['name', 'close', 'change', 'volume', 'relative_volume_10d_calc',
                            'ATR', 'Perf.Y', 'Perf.3M', 'Perf.1M', 'market_cap_basic', 'sector'],
                'sort': {'sortBy': 'Perf.1M', 'sortOrder': 'desc'},
                'range': [0, max(1, int(limit))]
            }
            return self._tv_scan(payload, 'tv_swing_pullback',
                                 lambda d: {
                                     'symbol': d[0],
                                     'price': d[1],
                                     'change_pct': d[2],
                                     'volume': d[3] or 0,
                                     'rel_volume': d[4] or 0,
                                     'atr': d[5] or 0,
                                     'perf_52w': d[6],
                                     'perf_3m': d[7],
                                     'perf_1m': d[8],
                                     'market_cap_basic': d[9],
                                     'sector': d[10],
                                 })
        except Exception as e:
            logger.warning(f"TV swing pullback failed: {e}")
            return []

    def _fetch_tv_swing_uptrend(self, limit: int = 25) -> list:
        """Fetch steady uptrend names that are not already in an obvious pullback."""
        try:
            payload = {
                'filter': [
                    {'left': 'exchange', 'operation': 'in_range', 'right': ['NASDAQ', 'NYSE']},
                    {'left': 'is_primary', 'operation': 'equal', 'right': True},
                    {'left': 'Perf.3M', 'operation': 'in_range', 'right': [TV_SWING_UPTREND_3M_MIN, TV_SWING_UPTREND_3M_MAX]},
                    {'left': 'Perf.1M', 'operation': 'in_range', 'right': [TV_SWING_UPTREND_1M_MIN, TV_SWING_UPTREND_1M_MAX]},
                    {'left': 'close', 'operation': 'in_range', 'right': [TV_SWING_PRICE_MIN, TV_PRICE_MAX]},
                    {'left': 'volume', 'operation': 'greater', 'right': TV_SWING_VOLUME_MIN},
                    {'left': 'market_cap_basic', 'operation': 'greater', 'right': TV_SWING_MARKET_CAP_MIN},
                ],
                'options': {'lang': 'en'},
                'markets': ['america'],
                'columns': ['name', 'close', 'change', 'volume', 'relative_volume_10d_calc',
                            'ATR', 'Perf.3M', 'Perf.1M', 'market_cap_basic', 'sector'],
                'sort': {'sortBy': 'Perf.3M', 'sortOrder': 'desc'},
                'range': [0, max(1, int(limit))]
            }
            return self._tv_scan(payload, 'tv_swing_uptrend',
                                 lambda d: {
                                     'symbol': d[0],
                                     'price': d[1],
                                     'change_pct': d[2],
                                     'volume': d[3] or 0,
                                     'rel_volume': d[4] or 0,
                                     'atr': d[5] or 0,
                                     'perf_3m': d[6],
                                     'perf_1m': d[7],
                                     'market_cap_basic': d[8],
                                     'sector': d[9],
                                 })
        except Exception as e:
            logger.warning(f"TV swing uptrend failed: {e}")
            return []

    def _fetch_tv_swing_near_high(self, limit: int = 25) -> list:
        """Fetch 52W leaders that pulled back 1-15%% from recent highs (secondary swing universe)."""
        try:
            payload = {
                'filter': [
                    {'left': 'exchange', 'operation': 'in_range', 'right': ['NASDAQ', 'NYSE']},
                    {'left': 'is_primary', 'operation': 'equal', 'right': True},
                    {'left': 'Perf.Y', 'operation': 'greater', 'right': 20},
                    {'left': 'Perf.3M', 'operation': 'greater', 'right': 5},
                    {'left': 'Perf.1M', 'operation': 'in_range', 'right': [-15, -1]},
                    {'left': 'close', 'operation': 'in_range', 'right': [TV_SWING_PRICE_MIN, TV_PRICE_MAX]},
                    {'left': 'volume', 'operation': 'greater', 'right': TV_SWING_VOLUME_MIN},
                    {'left': 'market_cap_basic', 'operation': 'greater', 'right': TV_SWING_MARKET_CAP_MIN},
                ],
                'options': {'lang': 'en'},
                'markets': ['america'],
                'columns': ['name', 'close', 'change', 'volume', 'relative_volume_10d_calc',
                            'ATR', 'Perf.Y', 'Perf.3M', 'Perf.1M', 'market_cap_basic', 'sector'],
                'sort': {'sortBy': 'Perf.1M', 'sortOrder': 'desc'},
                'range': [0, max(1, int(limit))]
            }
            return self._tv_scan(payload, 'tv_swing_near_high',
                                 lambda d: {
                                     'symbol': d[0],
                                     'price': d[1],
                                     'change_pct': d[2],
                                     'volume': d[3] or 0,
                                     'rel_volume': d[4] or 0,
                                     'atr': d[5] or 0,
                                     'perf_52w': d[6],
                                     'perf_3m': d[7],
                                     'perf_1m': d[8],
                                     'market_cap_basic': d[9],
                                     'sector': d[10],
                                 })
        except Exception as e:
            logger.warning(f"TV swing near-high failed: {e}")
            return []

    def fetch_swing_candidates(
        self,
        pullback_limit: int = 25,
        near_high_limit: int = 25,
        uptrend_limit: int = 25,
        volatile_limit: int = 20,
    ) -> list:
        """
        Swing-specific discovery. Returns stocks prioritized for FORCESWING pullbacks.

        Sources:
          1. tv_swing_pullback  - established leaders in a modest pullback (primary)
          2. tv_swing_near_high - 52W leaders pulling back from recent highs (secondary)
          3. tv_swing_uptrend   - steady uptrends not yet in clean pullback (tertiary)
          4. tv_volatile        - capped, low-priority coverage only

        Fallback: if deduped count < 5, adds from tv_unusual_volume sorted by rel_volume.
        Called by candidate_scanner.scan_market(is_swing=True).
        """
        candidates = []
        pullback = self._fetch_tv_swing_pullback(limit=pullback_limit)
        near_high = self._fetch_tv_swing_near_high(limit=near_high_limit)
        uptrend = self._fetch_tv_swing_uptrend(limit=uptrend_limit)
        volatile = self._fetch_tv_volatile(limit=volatile_limit)

        candidates.extend(pullback)
        candidates.extend(near_high)
        candidates.extend(uptrend)
        candidates.extend(volatile[:5])  # keep volatility as a small low-priority sleeve

        # Deduplicate by symbol, keeping first occurrence (pullback > near_high > uptrend > volatile)
        seen = set()
        deduped = []
        for c in candidates:
            sym = c.get('symbol')
            if sym and sym not in seen:
                seen.add(sym)
                deduped.append(c)

        # Wave3/T9: Sector RS post-filter - soft deprioritize lagging-sector stocks.
        # RS ratio < 0.95 means sector lagged SPY by >5% over ~20 days.
        # Defensive: if get_sector_etf_rs() raises or data is unavailable, skip silently.
        _SWING_SECTOR_ETF = {
            'Technology': 'XLK', 'Consumer Cyclical': 'XLY', 'Financials': 'XLF',
            'Health Care': 'XLV', 'Industrials': 'XLI', 'Communication Services': 'XLC',
            'Consumer Defensive': 'XLP', 'Energy': 'XLE', 'Utilities': 'XLU',
            'Real Estate': 'XLRE', 'Basic Materials': 'XLB',
        }
        try:
            checked_sectors: dict = {}
            priority, lagging = [], []
            for c in deduped:
                sector = c.get('sector')
                etf = _SWING_SECTOR_ETF.get(sector)
                if etf:
                    if etf not in checked_sectors:
                        checked_sectors[etf] = self.get_sector_etf_rs(etf)
                    rs = checked_sectors[etf]
                    if rs < 0.95:
                        c['sector_rs'] = rs
                        lagging.append(c)
                        continue
                # Priority path: sector unknown or RS >= 0.95
                c['sector_rs'] = checked_sectors.get(
                    _SWING_SECTOR_ETF.get(sector or '', ''), 1.0
                )
                priority.append(c)
            if lagging:
                logger.info(
                    "Wave3/T9: %d candidates in lagging sectors (RS<0.95) deprioritized",
                    len(lagging)
                )
            deduped = priority + lagging
        except Exception as e:
            logger.debug("Wave3/T9: sector RS filter skipped: %s", e)

        # Fallback: if very few candidates, add unusual-volume names for coverage
        if len(deduped) < 5:
            logger.warning(
                "SWING discovery: only %d candidates after dedup - adding unusual volume fallback",
                len(deduped)
            )
            unusual = self._fetch_tv_unusual_volume()
            unusual_sorted = sorted(unusual, key=lambda x: x.get('rel_volume', 0), reverse=True)
            for c in unusual_sorted:
                sym = c.get('symbol')
                if sym and sym not in seen:
                    c['source'] = 'tv_volatile_fallback'
                    seen.add(sym)
                    deduped.append(c)

        logger.info(
            "SWING discovery: %d candidates (%d pullback, %d near-high, %d uptrend, %d volatile)",
            len(deduped), len(pullback), len(near_high), len(uptrend), min(5, len(volatile))
        )
        return deduped

    def fetch_swing_research_candidates(self) -> list:
        """
        Broader overnight swing-research universe.

        This intentionally looks much wider than the live 5:45 AM scan so the
        overnight watchlist can surface pullbacks and near-misses before they
        become visible in the smaller live sleeves.
        """
        pullback = self._fetch_tv_swing_pullback(limit=120)
        near_high = self._fetch_tv_swing_near_high(limit=120)
        uptrend = self._fetch_tv_swing_uptrend(limit=120)
        unusual = self._fetch_tv_unusual_volume(limit=60)
        volatile = self._fetch_tv_volatile(limit=60)

        candidates = []
        candidates.extend(pullback)
        candidates.extend(near_high)
        candidates.extend(uptrend)
        candidates.extend(unusual)
        candidates.extend(volatile)

        seen = set()
        deduped = []
        for candidate in candidates:
            symbol = candidate.get("symbol")
            if symbol and symbol not in seen:
                seen.add(symbol)
                deduped.append(candidate)

        logger.info(
            "SWING overnight universe: %d candidates (%d pullback, %d near-high, %d uptrend, %d unusual-volume, %d volatile)",
            len(deduped),
            len(pullback),
            len(near_high),
            len(uptrend),
            len(unusual),
            len(volatile),
        )
        return deduped

    def _tv_scan(self, payload: dict, source_name: str, mapper) -> list:
        """
        Execute a TradingView scanner API call.

        Args:
            payload: Scanner request payload
            source_name: Source label for candidates
            mapper: Lambda to convert data array to dict

        Returns:
            List of candidate dicts
        """
        r = requests.post(
            TV_SCANNER_URL,
            json=payload,
            headers=TV_HEADERS,
            timeout=self.timeout
        )
        if r.status_code != 200:
            logger.warning(f"TradingView {source_name}: HTTP {r.status_code}")
            return []

        data = r.json()
        items = data.get('data', [])
        candidates = []

        for item in items:
            try:
                d = item.get('d', [])
                if not d or d[0] is None:
                    continue
                candidate = mapper(d)
                candidate['source'] = source_name
                # Round numeric fields
                for key in ('price', 'change_pct', 'gap_pct', 'rel_volume', 'atr'):
                    if key in candidate and candidate[key] is not None:
                        candidate[key] = round(float(candidate[key]), 2)
                candidates.append(candidate)
            except Exception as e:
                logger.debug(f"TV {source_name} item parse error: {e}")

        logger.info(f"TradingView {source_name}: {len(candidates)} candidates")
        return candidates

    def _fetch_tv_with_retry(self, payload: dict, source_name: str, mapper, max_retries: int = 3) -> list:
        """
        Retry wrapper for TradingView scanner API calls.

        Attempts up to max_retries times with 5s delay between attempts.
        Returns empty list if all attempts fail.
        """
        import time
        for attempt in range(max_retries):
            try:
                return self._tv_scan(payload, source_name, mapper)
            except Exception as e:
                logger.warning(
                    "[Scanner] TV %s attempt %d/%d failed: %s",
                    source_name, attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    time.sleep(5)
        logger.error(
            "[Scanner] TV %s failed after %d attempts, returning empty list",
            source_name, max_retries
        )
        return []

    def fetch_all_candidates(self, is_premarket: bool = False) -> list:
        """
        Fetch candidates from all direct sources (Yahoo + TradingView).

        Does NOT include catalyst discovery - that still requires Grok web_search.

        Args:
            is_premarket: True if before 6:30 AM PST

        Returns:
            Combined list of candidate dicts, deduped by symbol
        """
        all_candidates = []

        # Yahoo Finance (gainers, active, losers)
        yahoo = self.fetch_yahoo_candidates()
        all_candidates.extend(yahoo)

        # TradingView (unusual volume or premarket)
        tv = self.fetch_tradingview_candidates(is_premarket=is_premarket)
        all_candidates.extend(tv)

        logger.info(f"Direct fetch complete: {len(all_candidates)} total candidates "
                    f"({len(yahoo)} Yahoo, {len(tv)} TradingView)")

        return all_candidates

    def get_daily_bars(self, symbol: str, days: int = 25) -> list:
        """
        Fetch N daily OHLCV bars via Alpaca Data API or MarketDataProvider (Schwab).
        Returns list of dicts: [{"open": x, "high": x, "low": x, "close": x, "volume": x}, ...]
        Returns empty list if no client configured or on error.
        """
        # Convert desired trading bars to a conservative calendar-day lookback.
        # A flat +14 buffer is not enough for ADX warmup windows such as 45 bars.
        calendar_days = max(days + 14, int(days * 1.6) + 7)
        if self._alpaca_client is None:
            logger.warning("get_daily_bars: no data client configured")
            return []
        try:
            # MarketDataProvider (Schwab) path - uses get_historical_data()
            # Use dir() instead of hasattr(): plain MagicMock claims every
            # attribute exists, which can route Alpaca-shaped test clients here.
            if 'get_historical_data' in dir(self._alpaca_client):
                raw_bars = self._alpaca_client.get_historical_data(
                    symbol, days_back=calendar_days, timeframe="1D"
                )
                result = [
                    {
                        "open":   b["open"],
                        "high":   b["high"],
                        "low":    b["low"],
                        "close":  b["close"],
                        "volume": b["volume"],
                    }
                    for b in raw_bars
                ]
                return result[-days:]
            # Alpaca SDK path
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            end = datetime.now(tz=ZoneInfo("America/New_York"))
            start = end - timedelta(days=calendar_days)  # buffer for weekends/holidays
            raw_bars = self._alpaca_client.get_stock_bars(
                symbol,
                timeframe="1Day",
                start=start.isoformat(),
                end=end.isoformat(),
                limit=days,
            )
            return [
                {
                    "open":   b.open,
                    "high":   b.high,
                    "low":    b.low,
                    "close":  b.close,
                    "volume": b.volume,
                }
                for b in raw_bars
            ]
        except Exception as e:
            logger.warning(f"get_daily_bars({symbol}): {e}")
            return []


    # RS divergence threshold: ratio < 0.95 = stock lagging SPY by > 5%% over 20 days
    _RS_DIVERGENCE_THRESHOLD = 0.95

    def get_swing_technical_snapshot(self, symbol: str, days: int = 25) -> dict:
        """
        Compute RSI14, EMA21, and sector_rs_ok for exit condition checks.
        Returns defaults on missing/insufficient data (rsi=50 neutral, ema21=last_close*0.98, rs=True).

        sector_rs_ok: False when stock return / SPY return < 0.95 over last 20 days.
        Uses SPY as benchmark proxy (no sector_etf needed from caller).
        """
        bars = self.get_daily_bars(symbol, days=days)
        if len(bars) < 14:
            last_close = bars[-1]["close"] if bars else 100.0
            return {"rsi14": 50.0, "ema21": last_close * 0.98, "sector_rs_ok": True}
        closes = [b["close"] for b in bars]
        rsi14 = self._compute_rsi(closes, period=14)
        ema21 = self._compute_ema(closes, period=21)
        # RS vs SPY: ratio < threshold = stock lagging market (exit signal)
        rs_ratio = self.get_sector_etf_rs(symbol, spy_symbol="SPY", lookback_days=20)
        sector_rs_ok = rs_ratio >= self._RS_DIVERGENCE_THRESHOLD
        return {"rsi14": rsi14, "ema21": ema21, "sector_rs_ok": sector_rs_ok, "rs_ratio": round(rs_ratio, 4)}

    def _compute_rsi(self, closes: list, period: int = 14) -> float:
        """Wilder RSI. Returns 50.0 if insufficient data."""
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _compute_ema(self, closes: list, period: int = 21) -> float:
        """EMA of closes list. Returns last value if insufficient data."""
        if len(closes) < period:
            return float(closes[-1]) if closes else 0.0
        k = 2.0 / (period + 1)
        ema = float(closes[0])
        for c in closes[1:]:
            ema = float(c) * k + ema * (1 - k)
        return ema

    def get_sector_etf_rs(self, sector_etf: str, spy_symbol: str = "SPY",
                          lookback_days: int = 20) -> float:
        """
        Compute 4-week relative strength ratio: sector_etf_return / spy_return.
        Returns (1 + etf_return) / (1 + spy_return).
        Returns 1.0 (neutral) on any error or empty data.
        """
        try:
            etf_bars = self.get_daily_bars(sector_etf, days=lookback_days)
            spy_bars = self.get_daily_bars(spy_symbol, days=lookback_days)
            if not etf_bars or not spy_bars:
                return 1.0
            etf_return = (etf_bars[-1]["close"] - etf_bars[0]["close"]) / etf_bars[0]["close"]
            spy_return = (spy_bars[-1]["close"] - spy_bars[0]["close"]) / spy_bars[0]["close"]
            if spy_return == -1.0:
                return 1.0
            return (1 + etf_return) / (1 + spy_return)
        except Exception as e:
            logger.warning(f"get_sector_etf_rs({sector_etf}): {e}")
            return 1.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    is_pre = now.time() < datetime.strptime("06:30", "%H:%M").time()

    print(f"Market Scanner Test - {'PRE-MARKET' if is_pre else 'REGULAR HOURS'}")
    print("=" * 60)

    scanner = MarketScanner()
    candidates = scanner.fetch_all_candidates(is_premarket=is_pre)

    print(f"\nTotal candidates: {len(candidates)}")
    print("\nTop 20 by source:")

    by_source = {}
    for c in candidates:
        src = c.get('source', 'unknown')
        by_source.setdefault(src, []).append(c)

    for src, items in sorted(by_source.items()):
        print(f"\n{src} ({len(items)} stocks):")
        for c in items[:5]:
            print(f"  {c['symbol']:6s} ${c.get('price',0):>8.2f} "
                  f"{c.get('change_pct',0):+6.2f}% "
                  f"vol={c.get('volume',0):>12,} "
                  f"{'rel='+str(c.get('rel_volume',''))+'x' if c.get('rel_volume') else ''}")
