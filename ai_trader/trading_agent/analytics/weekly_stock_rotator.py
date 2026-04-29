#!/usr/bin/env python3
"""
Weekly Stock Rotator - Dynamically select FORCESWING-eligible stocks per week.

For each simulated week, fetches historical bars and identifies stocks that match
the FORCESWING entry criteria:
  - Within 15% of 52-week high (near-high consolidation, not recovering from crash)
  - Positive 3-month momentum (uptrend, not bounce)
  - Price $12-$500, volume >= 500K, market cap >= $500M

Returns top_n qualifying stocks ranked by (proximity to 52W high, 3M momentum).
No intentional losers or stress-test mix -- swing trading wants only clean setups;
the FORCESWING filter in candidate_scanner.py does the precise qualification work.
"""

import os
import datetime
import logging
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import pandas as pd

logger = logging.getLogger(__name__)


class WeeklyStockRotator:
    """Select FORCESWING-eligible stocks per week from a historical base universe."""

    # Maps each base-universe symbol to its representative sector ETF.
    # Used by _get_sector_rs_map() to compute 20-day RS vs SPY per sector.
    # If a symbol is missing, no penalty is applied (safe default).
    _SYMBOL_SECTOR_ETF: Dict[str, str] = {
        # Tech / semis -> XLK
        'NVTS': 'XLK', 'COHR': 'XLK', 'ENTG': 'XLK', 'MKSI': 'XLK',
        'TER':  'XLK', 'AMBA': 'XLK', 'LSCC': 'XLK', 'CGNX': 'XLK',
        'MPWR': 'XLK', 'SLAB': 'XLK', 'DIOD': 'XLK', 'PLAB': 'XLK',
        'ON':   'XLK', 'MRVL': 'XLK',
        # Software / cloud -> XLK
        'FSLY': 'XLK', 'PATH': 'XLK', 'DOCN': 'XLK', 'DOMO': 'XLK',
        'GTLB': 'XLK', 'ASAN': 'XLK', 'FROG': 'XLK', 'BILL': 'XLK',
        'WEAV': 'XLK', 'TDC':  'XLK', 'NET':  'XLK', 'DDOG': 'XLK',
        'MDB':  'XLK', 'VERX': 'XLK',
        # Consumer / retail -> XLY
        'PLAY': 'XLY', 'CABO': 'XLC', 'WING': 'XLY', 'CAVA': 'XLY',
        'BROS': 'XLY', 'EAT':  'XLY', 'SCVL': 'XLY', 'LULU': 'XLY',
        'CROX': 'XLY', 'CHWY': 'XLY', 'ETSY': 'XLY', 'CVCO': 'XLY',
        'GOOS': 'XLY', 'MAT':  'XLY', 'BBWI': 'XLY',
        # Biotech / healthcare -> XLV
        'SRPT': 'XLV', 'RGNX': 'XLV', 'ARVN': 'XLV', 'RARE': 'XLV', 'ALNY': 'XLV',
        # Fintech / payments -> XLF
        'SOFI': 'XLF', 'AFRM': 'XLF', 'HOOD': 'XLF', 'DAVE': 'XLF', 'ALKT': 'XLF',
        # Mobility / EV -> XLY
        'LYFT': 'XLY', 'UBER': 'XLY', 'RIVN': 'XLY',
        # Other high-beta mid-caps
        'SMCI': 'XLK', 'LITE': 'XLK',
        'CVNA': 'XLY', 'SHOP': 'XLK', 'ABNB': 'XLY',
        'COIN': 'XLF', 'SNAP': 'XLC', 'DASH': 'XLK', 'RBLX': 'XLC',
    }

    def __init__(
        self,
        min_price: float = 12.0,
        max_price: float = 500.0,
        min_volume: int = 500_000,
        min_market_cap: float = 500_000_000,
        min_proximity_to_high: float = 0.85,
        min_proximity_to_high_full: float | None = None,
        min_momentum_3m: float = 0.0,
        top_n: int = 30,
        score_weight_proximity: float = 0.60,
        score_weight_momentum: float = 0.40,
        broker=None,
    ):
        """
        Args:
            min_price:                  Swing price floor (default $12)
            max_price:                  Price ceiling to exclude mega-caps (default $500)
            min_volume:                 Minimum 20-day average daily volume (default 500K)
            min_market_cap:             Minimum market cap (default $500M) - applied via
                                        yfinance info; set to 0 to skip this check
            min_proximity_to_high:      Minimum close/52W_high ratio to qualify (default 0.85)
                                        e.g. 0.85 means within 15% of the 52-week high.
                                        Used in REDUCED/CASH regime (or always if _full is None).
            min_proximity_to_high_full: Tighter proximity threshold applied only when regime
                                        is FULL (e.g. 0.92 to restrict to top 8% near-high).
                                        If None, min_proximity_to_high is used for all regimes.
            min_momentum_3m:            Minimum 3-month return to qualify (default 0.0 = positive)
            top_n:                      Maximum stocks to return per week (default 30)
            score_weight_proximity:     Weight for proximity in candidate scoring (default 0.60)
            score_weight_momentum:      Weight for momentum in candidate scoring (default 0.40)
                                        H4 hypothesis: shift to 0.45/0.55 to favor momentum
            broker:                     Schwab broker instance; auto-initializes if None
        """
        self.min_price = min_price
        self.max_price = max_price
        self.min_volume = min_volume
        self.min_market_cap = min_market_cap
        self.min_proximity_to_high = min_proximity_to_high
        self.min_proximity_to_high_full = min_proximity_to_high_full
        self.min_momentum_3m = min_momentum_3m
        self.top_n = top_n
        self.score_weight_proximity = score_weight_proximity
        self.score_weight_momentum = score_weight_momentum
        self.broker = broker

        if self.broker is None:
            self._init_broker()

        symbols, sector_map = self._load_universe()
        self.base_universe = symbols
        # Merge dynamic sector map with hardcoded class-level entries.
        # Class-level dict entries (original 56 stocks) take priority over auto-detected
        # sectors since they were manually verified.
        self._SYMBOL_SECTOR_ETF = {**sector_map, **type(self)._SYMBOL_SECTOR_ETF}

    def _load_universe(self):
        """
        Load the screening universe from DynamicUniverseLoader.

        Returns (symbols, sector_etf_map). Falls back to _get_base_universe()
        if the loader fails (e.g. no network at init time).
        """
        try:
            from analytics.dynamic_universe_loader import load_universe
            symbols, sector_map = load_universe(force_refresh=False)
            if symbols:
                logger.info(
                    "[WeeklyStockRotator] Dynamic universe loaded: "
                    + str(len(symbols)) + " symbols"
                )
                return symbols, sector_map
        except Exception as e:
            logger.warning(
                "[WeeklyStockRotator] DynamicUniverseLoader failed (" + str(e) + ")"
                " - falling back to hardcoded base universe"
            )
        fallback = self._get_base_universe()
        return fallback, dict(type(self)._SYMBOL_SECTOR_ETF)

    def _get_base_universe(self) -> List[str]:
        """
        Base universe of stocks to evaluate each week.

        Covers liquid mid-caps across tech, consumer, biotech, and fintech --
        sectors that historically produce FORCESWING setups. The weekly scorer
        filters this down to only stocks actually near their highs with momentum
        during the simulated week.
        """
        return [
            # High-momentum tech / semis
            'NVTS', 'COHR', 'ENTG', 'MKSI', 'TER', 'AMBA', 'LSCC', 'CGNX',
            'MPWR', 'SLAB', 'DIOD', 'PLAB', 'ON', 'MRVL',

            # Software / cloud
            'FSLY', 'PATH', 'DOCN', 'DOMO', 'GTLB', 'ASAN', 'FROG', 'BILL',
            'WEAV', 'TDC', 'NET', 'DDOG', 'MDB', 'VERX',

            # Consumer / retail
            'PLAY', 'CABO', 'WING', 'CAVA', 'BROS', 'EAT', 'SCVL', 'LULU',
            'CROX', 'CHWY', 'ETSY', 'CVCO', 'GOOS', 'MAT', 'BBWI',

            # Biotech / healthcare
            'SRPT', 'RGNX', 'ARVN', 'RARE', 'ALNY',

            # Fintech / payments
            'SOFI', 'AFRM', 'HOOD', 'DAVE', 'ALKT',

            # Mobility / EV
            'LYFT', 'UBER', 'RIVN',

            # Other high-beta mid-caps
            'SMCI', 'LITE', 'CVNA', 'SHOP', 'ABNB',
            'COIN', 'SNAP', 'DASH', 'RBLX',
        ]

    def _init_broker(self):
        """Initialize Schwab broker for fetching historical data."""
        try:
            token_file = Path(__file__).parent.parent / "config" / "schwab_refresh_token.txt"
            with open(token_file, 'r') as f:
                refresh_token = f.read().strip()
            os.environ['SCHWAB_REFRESH_TOKEN'] = refresh_token

            from ai_trader.trading_agent.brokers.schwab_broker import SchwabBroker
            self.broker = SchwabBroker()
            if not self.broker.connect():
                logger.error("Failed to connect to Schwab broker")
                raise RuntimeError("Schwab broker connection failed")

            logger.info("Schwab broker initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Schwab broker: {e}")
            raise RuntimeError(f"Could not initialize data source: {e}")

    def _get_sector_rs_map(
        self,
        as_of_date: datetime.date,
        lookback_trading_days: int = 20,
    ) -> Dict[str, float]:
        """
        Compute 20-day sector ETF relative strength vs SPY for all sectors in
        the base universe.

        Returns:
            {etf_symbol: rs_ratio} where rs_ratio =
                (1 + etf_20d_return) / (1 + spy_20d_return).
            1.0 = neutral / missing data
            >1.0 = sector outperforming SPY
            <1.0 = sector underperforming SPY (scoring penalty applies)

        Look-ahead safe: all bars sliced to <= as_of_date.
        Defensive: returns {} on any error so callers apply no penalty.
        """
        try:
            etfs_needed = set(self._SYMBOL_SECTOR_ETF.values()) | {'SPY'}

            # Fetch enough calendar days to get 20 trading bars (allow for
            # weekends, holidays, and market closures).
            fetch_start = as_of_date - datetime.timedelta(days=45)
            fetch_end   = as_of_date + datetime.timedelta(days=1)
            start_dt = datetime.datetime.combine(fetch_start, datetime.datetime.min.time())
            end_dt   = datetime.datetime.combine(fetch_end,   datetime.datetime.min.time())

            bars_by_etf: Dict[str, pd.DataFrame] = {}
            for etf in etfs_needed:
                try:
                    hist = self.broker.get_historical_data_df(
                        symbol=etf,
                        start_date=start_dt,
                        end_date=end_dt,
                        timeframe='daily',
                    )
                    if hist.empty:
                        continue
                    hist_as_of = hist[hist.index.date <= as_of_date]
                    if len(hist_as_of) >= 5:
                        bars_by_etf[etf] = hist_as_of
                except Exception as e:
                    logger.debug(f"Sector RS: could not fetch {etf}: {e}")
                    continue

            if 'SPY' not in bars_by_etf:
                logger.debug("Sector RS: SPY data unavailable -- skipping RS filter")
                return {}

            spy_tail = bars_by_etf['SPY']['Close'].tail(lookback_trading_days)
            if len(spy_tail) < 5:
                return {}
            spy_open  = float(spy_tail.iloc[0])
            spy_close = float(spy_tail.iloc[-1])
            spy_return = (spy_close - spy_open) / spy_open if spy_open > 0 else 0.0

            rs_map: Dict[str, float] = {}
            for etf, hist in bars_by_etf.items():
                if etf == 'SPY':
                    continue
                try:
                    etf_tail  = hist['Close'].tail(lookback_trading_days)
                    if len(etf_tail) < 5:
                        continue
                    etf_open  = float(etf_tail.iloc[0])
                    etf_close = float(etf_tail.iloc[-1])
                    etf_return = (etf_close - etf_open) / etf_open if etf_open > 0 else 0.0
                    denominator = 1 + spy_return
                    rs_map[etf] = (1 + etf_return) / denominator if denominator != 0 else 1.0
                except Exception as e:
                    logger.debug(f"Sector RS: error computing RS for {etf}: {e}")
                    continue

            logger.debug(
                f"Sector RS ({as_of_date}): "
                + "  ".join(f"{k}={v:.3f}" for k, v in sorted(rs_map.items()))
            )
            return rs_map

        except Exception as e:
            logger.debug(f"Sector RS: _get_sector_rs_map failed: {e}")
            return {}

    def get_weekly_movers(
        self,
        week_start: datetime.date,
        week_end: datetime.date,
        min_proximity_override: float | None = None,
    ) -> List[str]:
        """
        Return stocks from the base universe that qualify as FORCESWING candidates
        for the given week.

        Qualification criteria:
          - close >= proximity_threshold * 52W_high  (near-high setup)
          - 3M return >= min_momentum_3m             (positive momentum)
          - 20-day avg volume >= min_volume
          - price within [min_price, max_price]

        Ranking: proximity_to_52W_high (primary), momentum_3m (secondary).

        Args:
            week_start:             Monday of the target week
            week_end:               Friday of the target week
            min_proximity_override: If provided, use this threshold instead of
                                    self.min_proximity_to_high. Pass the regime-
                                    appropriate value from the caller (e.g. harness
                                    passes min_proximity_to_high_full when FULL).

        Returns:
            List of qualifying symbols, ranked best-first, capped at top_n.
        """
        logger.info(f"Scanning FORCESWING universe for week {week_start} to {week_end}")

        # Need ~1 year of history to compute accurate 52W high.
        # Add 30 extra calendar days as buffer for weekends / holidays.
        fetch_start = week_end - datetime.timedelta(days=395)
        fetch_end   = week_end + datetime.timedelta(days=1)

        start_dt = datetime.datetime.combine(fetch_start, datetime.datetime.min.time())
        end_dt   = datetime.datetime.combine(fetch_end,   datetime.datetime.min.time())

        # Pre-compute sector RS map once per week (look-ahead safe via as_of_date=week_end).
        # Empty dict = broker unavailable; scoring falls back to no-penalty behavior.
        sector_rs_map = self._get_sector_rs_map(week_end)

        candidates = []

        for symbol in self.base_universe:
            try:
                hist = self.broker.get_historical_data_df(
                    symbol=symbol,
                    start_date=start_dt,
                    end_date=end_dt,
                    timeframe='daily',
                )

                if hist.empty or len(hist) < 20:
                    continue

                # ----------------------------------------------------------------
                # 1. Slice history to only data available as-of week_end
                #    (avoids look-ahead bias)
                # ----------------------------------------------------------------
                hist_as_of = hist[hist.index.date <= week_end]
                if hist_as_of.empty:
                    continue

                close = float(hist_as_of.iloc[-1]['Close'])

                # ----------------------------------------------------------------
                # 2. Price filter
                # ----------------------------------------------------------------
                if close < self.min_price or close > self.max_price:
                    continue

                # ----------------------------------------------------------------
                # 3. 20-day average volume filter
                # ----------------------------------------------------------------
                avg_volume_20d = float(hist_as_of['Volume'].tail(20).mean())
                if avg_volume_20d < self.min_volume:
                    continue

                # ----------------------------------------------------------------
                # 4. 52-week high proximity
                #    Use up to 252 trading bars of history.
                # ----------------------------------------------------------------
                lookback_252 = hist_as_of.tail(252)
                high_52w = float(lookback_252['High'].max())
                if high_52w <= 0:
                    continue

                proximity = close / high_52w  # 1.0 = AT 52-week high

                # ----------------------------------------------------------------
                # 4b. 52W high recency gate (Wave 1 fix)
                #     A high set > 120 calendar days ago is a slow bleed, not a
                #     healthy consolidation. Penalise 90-120 days; hard-skip > 120.
                # ----------------------------------------------------------------
                high_52w_idx = lookback_252['High'].idxmax()
                days_since_high = (week_end - high_52w_idx.date()).days
                if days_since_high > 120:
                    logger.debug(
                        f"{symbol}: skipped -- 52W high set {days_since_high} days ago (limit 120)"
                    )
                    continue
                recency_penalty = 0.15 if days_since_high > 90 else 0.0

                prox_threshold = (
                    min_proximity_override
                    if min_proximity_override is not None
                    else self.min_proximity_to_high
                )
                if proximity < prox_threshold:
                    logger.debug(
                        f"{symbol}: skipped -- {proximity:.2%} of 52W high "
                        f"(need >= {prox_threshold:.0%})"
                    )
                    continue

                # ----------------------------------------------------------------
                # 5. 3-month momentum (approx 63 trading days)
                # ----------------------------------------------------------------
                lookback_for_momentum = hist_as_of.iloc[:-1]  # exclude current week
                bars_3m = lookback_for_momentum.tail(63)

                if len(bars_3m) >= 20:
                    close_3m_ago = float(bars_3m.iloc[0]['Close'])
                    momentum_3m  = (close - close_3m_ago) / close_3m_ago if close_3m_ago > 0 else 0.0
                else:
                    momentum_3m = 0.0  # insufficient history -- treat as neutral

                if momentum_3m < self.min_momentum_3m:
                    logger.debug(
                        f"{symbol}: skipped -- 3M momentum {momentum_3m:+.1%} "
                        f"(need >= {self.min_momentum_3m:+.1%})"
                    )
                    continue

                # ----------------------------------------------------------------
                # 6. Score: weighted combination of proximity and momentum.
                #    Both range [0, 1] after clamping momentum.
                #    Higher score = better FORCESWING candidate quality.
                # ----------------------------------------------------------------
                # Clamp momentum at 0.65 -- parabolic names (up 100%+) should not
                # outscore clean 20-30% setups. Upper bound was 1.0 (Wave 1 fix).
                momentum_clamped = min(max(momentum_3m, 0.0), 0.65)

                # Sector RS penalty (Wave 1 fix): -0.10 when sector is lagging SPY.
                # Soft signal only -- does not hard-reject. Falls back to 0 if RS
                # data unavailable (sector_rs_map empty or ETF not mapped).
                sector_etf = self._SYMBOL_SECTOR_ETF.get(symbol)
                sector_rs  = sector_rs_map.get(sector_etf, 1.0) if sector_etf else 1.0
                sector_rs_penalty = 0.10 if sector_rs < 1.0 else 0.0

                score = (
                    proximity * self.score_weight_proximity
                    + momentum_clamped * self.score_weight_momentum
                    - recency_penalty
                    - sector_rs_penalty
                )

                candidates.append({
                    'symbol':         symbol,
                    'close':          close,
                    'high_52w':       high_52w,
                    'proximity':      proximity,
                    'momentum_3m':    momentum_3m,
                    'avg_volume_20d': avg_volume_20d,
                    'sector_etf':     sector_etf or 'N/A',
                    'sector_rs':      sector_rs,
                    'score':          score,
                })

                logger.debug(
                    f"{symbol}: proximity={proximity:.2%}  "
                    f"momentum_3m={momentum_3m:+.1%}  "
                    f"sector_rs={sector_rs:.3f}  "
                    f"score={score:.3f}  vol={avg_volume_20d/1e6:.1f}M"
                )

            except Exception as e:
                logger.debug(f"Error evaluating {symbol}: {e}")
                continue

        # Sort best candidates first
        candidates.sort(key=lambda x: x['score'], reverse=True)

        # Dynamic shortlist: return top self.top_n candidates (config-driven).
        # Replaced hardcoded 40 so hypothesis tests can expand the pool via
        # --universe.top_n N without changing this file.
        shortlist_n = min(self.top_n, len(candidates))
        symbols = [c['symbol'] for c in candidates[:shortlist_n]]

        logger.info(
            f"Week {week_start}: {len(candidates)} FORCESWING-eligible stocks found "
            f"(returning top {len(symbols)})"
        )
        if candidates:
            logger.info("  Top 5 candidates:")
            for c in candidates[:5]:
                logger.info(
                    f"    {c['symbol']}: proximity={c['proximity']:.1%}  "
                    f"momentum_3m={c['momentum_3m']:+.1%}  score={c['score']:.3f}"
                )

        return symbols

    def split_date_range_by_weeks(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> List[Tuple[datetime.date, datetime.date]]:
        """
        Split a date range into weekly chunks (Monday-Friday).

        Returns:
            List of (week_start, week_end) tuples.
        """
        weeks = []

        # Advance to first Monday
        current = start_date
        while current.weekday() != 0:
            current += datetime.timedelta(days=1)

        week_start = current
        while week_start <= end_date:
            week_end = min(week_start + datetime.timedelta(days=4), end_date)
            weeks.append((week_start, week_end))
            week_start += datetime.timedelta(days=7)

        logger.info(f"Split {start_date} to {end_date} into {len(weeks)} weeks")
        return weeks

    def get_movers_by_week_map(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> Dict[Tuple[datetime.date, datetime.date], List[str]]:
        """
        Build a mapping of (week_start, week_end) -> qualifying symbols
        for the entire simulation period.

        Pre-computes all weeks up front so the backtest harness can look up
        the correct symbol universe for any given date in O(1).
        """
        weeks = self.split_date_range_by_weeks(start_date, end_date)
        week_map = {}
        for week_start, week_end in weeks:
            week_map[(week_start, week_end)] = self.get_weekly_movers(week_start, week_end)
        return week_map

    def get_stocks_for_date(
        self,
        target_date: datetime.date,
        week_map: Dict[Tuple[datetime.date, datetime.date], List[str]],
    ) -> List[str]:
        """
        Return the qualifying symbol list for a specific simulation date.

        Args:
            target_date: The date being simulated
            week_map:    Pre-computed map from get_movers_by_week_map()

        Returns:
            List of symbols that passed FORCESWING pre-screening for this week.
            Falls back to full base_universe[:top_n] if date not found.
        """
        for (week_start, week_end), symbols in week_map.items():
            if week_start <= target_date <= week_end:
                return symbols

        logger.warning(
            f"No weekly universe found for {target_date} -- falling back to base universe"
        )
        return self.base_universe[:self.top_n]


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    rotator = WeeklyStockRotator(top_n=20)

    test_week_start = datetime.date(2026, 1, 6)
    test_week_end   = datetime.date(2026, 1, 10)

    print(f"\nFORCESWING universe for week {test_week_start} to {test_week_end}:")
    movers = rotator.get_weekly_movers(test_week_start, test_week_end)
    print(f"  {len(movers)} qualifying stocks:")
    for i, symbol in enumerate(movers, 1):
        print(f"    {i:2}. {symbol}")
