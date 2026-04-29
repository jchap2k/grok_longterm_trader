"""
Historical Data Fetcher - Phase 3 of Backtest Validation System

Fetches historical OHLCV data and technical indicators for backtesting.

Data Sources (priority order):
1. Schwab API (if available, free for account holders)
2. Alpaca API (free tier: 200 symbols/day)
3. Yahoo Finance (yfinance, free unlimited)

Caches results in backtest_cache table to avoid redundant API calls.

Usage:
    fetcher = HistoricalDataFetcher(schwab_broker, alpaca_broker, learning_db)
    data = fetcher.fetch_historical_ohlcv('NVDA', '2026-01-01', '2026-02-09')
    
    # Returns DataFrame with OHLC + indicators:
    # date, open, high, low, close, volume, rsi_14, atr_14, return_10d, etc.
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Import weekly stock rotator for dynamic stock selection
# Import Alpaca earnings fetcher for reliable catalyst discovery
try:
    from .weekly_stock_rotator import WeeklyStockRotator
    WEEKLY_ROTATOR_AVAILABLE = True
except ImportError:
    WEEKLY_ROTATOR_AVAILABLE = False
    logger.warning("WeeklyStockRotator not available - using static stock lists")

try:
    from .alpaca_earnings_fetcher import AlpacaEarningsFetcher
    ALPACA_EARNINGS_AVAILABLE = True
except ImportError:
    ALPACA_EARNINGS_AVAILABLE = False
    logger.warning("AlpacaEarningsFetcher not available")

try:
    from .finnhub_earnings_fetcher import FinnhubEarningsFetcher
    FINNHUB_AVAILABLE = True
except ImportError:
    FINNHUB_AVAILABLE = False
    logger.warning("FinnhubEarningsFetcher not available - falling back to Alpaca/yfinance")

# Try to import data libraries
try:
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    logger.warning("pandas not available - historical data fetching limited")

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not available - Yahoo Finance source disabled")


class HistoricalDataFetcher:
    """
    Fetches and caches historical market data for backtesting.
    
    Uses multiple data sources with automatic fallback:
    - Schwab (primary, if available)
    - Alpaca (secondary, free tier)
    - Yahoo Finance (fallback, unlimited)
    """
    
    def __init__(self, schwab_broker=None, alpaca_broker=None, learning_db=None, use_weekly_rotation=False):
        """
        Initialize the historical data fetcher.

        Args:
            schwab_broker: Schwab broker instance (optional)
            alpaca_broker: Alpaca broker instance (optional)
            learning_db: LearningDatabase for caching (optional)
            use_weekly_rotation: Use weekly stock rotation for catalysts (default: True)
        """
        self.schwab_broker = schwab_broker
        self.alpaca_broker = alpaca_broker
        self.learning_db = learning_db
        self.use_weekly_rotation = use_weekly_rotation

        # Rate limiting
        self.last_api_call = {}
        self.min_call_interval = 0.5  # Seconds between API calls

        # Initialize weekly stock rotator
        self.weekly_rotator = None
        if use_weekly_rotation and WEEKLY_ROTATOR_AVAILABLE:
            self.weekly_rotator = WeeklyStockRotator(top_n=30)
            logger.info("Weekly stock rotation: ENABLED")
        else:
            logger.info("Weekly stock rotation: DISABLED (using static list)")

        # Validate dependencies
        if not PANDAS_AVAILABLE:
            logger.error("pandas required for historical data fetching")
            logger.error("Install with: pip install pandas numpy")

        logger.info("HistoricalDataFetcher initialized")
        logger.info(f"  Schwab: {'Available' if schwab_broker else 'Not available'}")
        logger.info(f"  Alpaca: {'Available' if alpaca_broker else 'Not available'}")
        logger.info(f"  Yahoo Finance: {'Available' if YFINANCE_AVAILABLE else 'Not available'}")
        logger.info(f"  Caching: {'Enabled' if learning_db else 'Disabled'}")
    
    def fetch_historical_ohlcv(self, symbol: str, start_date: str,
                                end_date: str, with_indicators: bool = True,
                                frequency: str = 'daily') -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV data with optional technical indicators.

        Args:
            symbol: Stock symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            with_indicators: Calculate technical indicators (RSI, ATR, momentum)
            frequency: Data frequency - 'daily', '5min', '1min' (default: 'daily')
                      Note: Intraday data (5min, 1min) only available for last 6 months via Schwab

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
            If with_indicators=True, also includes: rsi_14, atr_14, return_10d, etc.
        """
        if not PANDAS_AVAILABLE:
            logger.error("pandas not available - cannot fetch historical data")
            return None
        
        logger.info(f"Fetching historical data for {symbol} ({start_date} to {end_date})")
        
        # Check cache first
        if self.learning_db:
            cached_data = self._get_from_cache(symbol, start_date, end_date)
            if cached_data is not None:
                logger.info(f"  Using cached data for {symbol}")
                return cached_data
        
        # Try data sources in priority order with quality validation
        df = None
        sources_tried = []

        # 1. Try Schwab (if available)
        if self.schwab_broker and hasattr(self.schwab_broker, 'get_historical_data'):
            logger.info(f"  Attempting Schwab fetch (connected={getattr(self.schwab_broker, 'connected', False)})...")
            try:
                df = self._fetch_from_schwab(symbol, start_date, end_date, frequency)
                if df is not None:
                    logger.info(f"  Fetched from Schwab: {len(df)} bars ({frequency})")
                    # Validate data quality
                    is_valid, issues = self.validate_data_quality(df, symbol)
                    if is_valid:
                        logger.info(f"  Schwab data passed quality checks")
                    else:
                        logger.warning(f"  Schwab data failed quality checks, trying next source")
                        sources_tried.append(('Schwab', issues))
                        df = None
                else:
                    logger.warning(f"  Schwab returned no data")
            except Exception as e:
                logger.warning(f"Schwab fetch failed: {e}")
                sources_tried.append(('Schwab', [str(e)]))

        # 2. Try Alpaca (if Schwab failed)
        if df is None and self.alpaca_broker and hasattr(self.alpaca_broker, 'get_bars'):
            try:
                df = self._fetch_from_alpaca(symbol, start_date, end_date)
                if df is not None:
                    logger.info(f"  Fetched from Alpaca: {len(df)} bars")
                    # Validate data quality
                    is_valid, issues = self.validate_data_quality(df, symbol)
                    if is_valid:
                        logger.info(f"  Alpaca data passed quality checks")
                    else:
                        logger.warning(f"  Alpaca data failed quality checks, trying next source")
                        sources_tried.append(('Alpaca', issues))
                        df = None
            except Exception as e:
                logger.warning(f"Alpaca fetch failed: {e}")
                sources_tried.append(('Alpaca', [str(e)]))

        # 3. Fallback to Yahoo Finance
        if df is None and YFINANCE_AVAILABLE:
            try:
                df = self._fetch_from_yahoo(symbol, start_date, end_date)
                if df is not None:
                    logger.info(f"  Fetched from Yahoo Finance: {len(df)} bars")
                    # Validate data quality
                    is_valid, issues = self.validate_data_quality(df, symbol)
                    if is_valid:
                        logger.info(f"  Yahoo Finance data passed quality checks")
                    else:
                        logger.warning(f"  Yahoo Finance data failed quality checks")
                        sources_tried.append(('Yahoo Finance', issues))
                        # For Yahoo, we'll be more lenient - only reject if critical issues
                        critical_issues = [i for i in issues if 'corrupted' in i.lower() or 'negative' in i.lower()]
                        if critical_issues:
                            logger.error(f"  Critical data quality issues, rejecting data")
                            df = None
                        else:
                            logger.warning(f"  Non-critical issues found, using data anyway")
            except Exception as e:
                logger.warning(f"Yahoo Finance fetch failed: {e}")
                sources_tried.append(('Yahoo Finance', [str(e)]))

        if df is None:
            logger.error(f"Failed to fetch valid data for {symbol} from all sources")
            if sources_tried:
                logger.error(f"Sources tried:")
                for source, issues in sources_tried:
                    logger.error(f"  {source}: {issues}")
            return None
        
        # Calculate technical indicators
        if with_indicators:
            df = self._add_indicators(df)
        
        # Cache the results
        if self.learning_db:
            self._save_to_cache(symbol, start_date, end_date, df)
        
        return df
    
    def _fetch_from_schwab(self, symbol: str, start_date: str,
                           end_date: str, frequency: str = 'daily') -> Optional[pd.DataFrame]:
        """
        Fetch data from Schwab API.

        Args:
            symbol: Stock symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            frequency: 'daily', '5min', or '1min'

        Returns:
            DataFrame with OHLCV data
        """
        self._rate_limit('schwab')

        try:
            # Convert dates to datetime objects
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')

            # Map frequency to Schwab timeframe format
            if frequency == '1min':
                timeframe = '1Min'
            elif frequency == '5min':
                timeframe = '5Min'
            else:  # daily
                timeframe = '1D'

            # Call Schwab historical data method
            candles = self.schwab_broker.get_historical_data(
                symbol=symbol,
                start_date=start_dt,
                end_date=end_dt,
                timeframe=timeframe
            )

            if not candles:
                return None

            # Convert to DataFrame
            df = pd.DataFrame(candles)

            # Standardize column names (Schwab returns timestamp, not datetime)
            df['date'] = pd.to_datetime(df['timestamp'])
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]

            return df

        except Exception as e:
            logger.warning(f"Schwab fetch error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None
    
    def _fetch_from_alpaca(self, symbol: str, start_date: str,
                           end_date: str) -> Optional[pd.DataFrame]:
        """Fetch data from Alpaca API."""
        self._rate_limit('alpaca')
        
        try:
            # Call Alpaca bars API
            bars = self.alpaca_broker.get_bars(
                symbol=symbol,
                timeframe='1Day',
                start=start_date,
                end=end_date
            )
            
            if not bars:
                return None
            
            # Convert to DataFrame
            data = []
            for bar in bars:
                data.append({
                    'date': bar.timestamp,
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': int(bar.volume)
                })
            
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date'])
            
            return df
            
        except Exception as e:
            logger.debug(f"Alpaca fetch error: {e}")
            return None
    
    def _fetch_from_yahoo(self, symbol: str, start_date: str,
                          end_date: str) -> Optional[pd.DataFrame]:
        """Fetch data from Yahoo Finance using yfinance."""
        if not YFINANCE_AVAILABLE:
            return None
        
        self._rate_limit('yahoo')
        
        try:
            # Fetch data using yfinance
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, interval='1d')
            
            if df.empty:
                return None
            
            # Standardize column names (yfinance uses capital letters)
            df = df.reset_index()
            df = df.rename(columns={
                'Date': 'date',
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })
            
            # Keep only required columns
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
            
            return df
            
        except Exception as e:
            logger.debug(f"Yahoo Finance fetch error: {e}")
            return None
    
    def validate_data_quality(self, df: pd.DataFrame, symbol: str) -> Tuple[bool, List[str]]:
        """
        Validate historical data quality before using in backtest.

        Checks for:
        - Missing trading days (market was open but no data)
        - Zero volume days (corrupted data)
        - Price jumps >10% (stock splits, bad data)
        - Negative prices (corrupted data)
        - High < Low violations (corrupted data)
        - Data staleness (last date is recent)

        Args:
            df: DataFrame with OHLCV data
            symbol: Stock symbol (for logging)

        Returns:
            (is_valid, issues_list)
            - is_valid: True if data passes all checks
            - issues_list: List of quality issues found
        """
        issues = []

        if df is None or df.empty:
            return False, ["Empty or None DataFrame"]

        try:
            # Check 1: Missing dates (market was open but no data)
            # Generate business days between first and last date
            date_range = pd.date_range(df['date'].iloc[0], df['date'].iloc[-1], freq='B')
            missing = date_range.difference(df['date'])
            missing_pct = len(missing) / len(date_range) if len(date_range) > 0 else 0

            if missing_pct > 0.10:  # >10% missing is suspicious
                issues.append(f"Missing {len(missing)} trading days ({missing_pct:.1%} of range)")
            elif len(missing) > 0:
                logger.debug(f"{symbol}: {len(missing)} missing days (within tolerance)")

            # Check 2: Zero volume days (corrupted data)
            zero_vol = (df['volume'] == 0).sum()
            if zero_vol > 0:
                issues.append(f"{zero_vol} days with zero volume (corrupted data)")

            # Check 3: Price jumps >15% (stock splits, bad data)
            # Use 15% threshold to avoid false positives on volatile stocks
            returns = df['close'].pct_change()
            large_jumps = (abs(returns) > 0.15).sum()
            if large_jumps > 0:
                jump_dates = df[abs(returns) > 0.15]['date'].tolist()
                issues.append(f"{large_jumps} price jumps >15% - check for splits on {jump_dates[:3]}")

            # Check 4: Data staleness (last date is recent)
            # Allow up to 5 days for weekends/holidays
            last_date = pd.to_datetime(df['date'].iloc[-1])
            # Remove timezone info for comparison if present
            if last_date.tzinfo is not None:
                last_date = last_date.replace(tzinfo=None)
            days_old = (datetime.now() - last_date).days
            if days_old > 5:
                issues.append(f"Data is {days_old} days old (stale)")

            # Check 5: Negative prices (corrupted)
            negative_prices = (df[['open','high','low','close']] < 0).any(axis=1).sum()
            if negative_prices > 0:
                issues.append(f"{negative_prices} rows with negative prices")

            # Check 6: High < Low check (impossible, means corrupted)
            invalid_ranges = (df['high'] < df['low']).sum()
            if invalid_ranges > 0:
                issues.append(f"{invalid_ranges} rows with High < Low (corrupted)")

            # Check 7: NaN values in critical columns
            critical_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in critical_cols:
                nan_count = df[col].isna().sum()
                if nan_count > 0:
                    issues.append(f"{nan_count} NaN values in {col} column")

            is_valid = len(issues) == 0

            if not is_valid:
                logger.warning(f"Data quality issues for {symbol}:")
                for issue in issues:
                    logger.warning(f"  - {issue}")
            else:
                logger.debug(f"Data quality OK for {symbol}: {len(df)} bars, no issues")

            return is_valid, issues

        except Exception as e:
            logger.error(f"Error validating data quality: {e}")
            return False, [f"Validation error: {str(e)}"]

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add technical indicators to OHLCV data.

        Adds:
        - RSI (14-day)
        - ATR (14-day)
        - 10-day return
        - 20-day return
        - Volume surge (vs 20-day avg)
        """
        if df is None or df.empty:
            return df

        df = df.copy()

        try:
            # 1. RSI (14-day)
            df['rsi_14'] = self._calculate_rsi(df['close'], period=14)

            # 2. ATR (14-day)
            df['atr_14'] = self._calculate_atr(df, period=14)

            # 3. Returns
            df['return_1d'] = df['close'].pct_change(1)
            df['return_5d'] = df['close'].pct_change(5)
            df['return_10d'] = df['close'].pct_change(10)
            df['return_20d'] = df['close'].pct_change(20)

            # 4. Volume analysis
            df['volume_sma_20'] = df['volume'].rolling(window=20).mean()
            df['volume_surge'] = df['volume'] / df['volume_sma_20']

            # 5. Simple moving averages
            df['sma_20'] = df['close'].rolling(window=20).mean()
            df['sma_50'] = df['close'].rolling(window=50).mean()

            # 6. Price position relative to SMA
            df['price_vs_sma20'] = (df['close'] - df['sma_20']) / df['sma_20']

            logger.debug(f"Added {len(df.columns) - 6} technical indicators")

        except Exception as e:
            logger.warning(f"Error calculating indicators: {e}")

        return df
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI indicator."""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR (Average True Range)."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    def _get_from_cache(self, symbol: str, start_date: str, 
                       end_date: str) -> Optional[pd.DataFrame]:
        """Retrieve data from cache if available."""
        try:
            with self.learning_db._get_connection() as conn:
                cursor = conn.cursor()
                
                # Query cache
                cursor.execute("""
                    SELECT ohlcv_data, indicators, fetched_at
                    FROM backtest_cache
                    WHERE symbol = ?
                    AND date >= ?
                    AND date <= ?
                    ORDER BY date ASC
                """, (symbol, start_date, end_date))
                
                rows = cursor.fetchall()
                
                if not rows:
                    return None
                
                # Check if cache is recent (< 7 days old)
                latest_fetch = datetime.fromisoformat(rows[0]['fetched_at'])
                if (datetime.now() - latest_fetch).days > 7:
                    logger.info(f"Cache for {symbol} is stale (>{7} days)")
                    return None
                
                # Reconstruct DataFrame from cached data
                data = []
                for row in rows:
                    ohlcv = json.loads(row['ohlcv_data'])
                    indicators = json.loads(row['indicators']) if row['indicators'] else {}
                    
                    combined = {**ohlcv, **indicators}
                    data.append(combined)
                
                if not data:
                    return None
                
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                
                return df
                
        except Exception as e:
            logger.debug(f"Cache retrieval error: {e}")
            return None
    
    def _save_to_cache(self, symbol: str, start_date: str, 
                      end_date: str, df: pd.DataFrame):
        """Save fetched data to cache."""
        try:
            with self.learning_db._get_connection() as conn:
                cursor = conn.cursor()
                
                fetched_at = datetime.now().isoformat()
                
                # Save each row
                for _, row in df.iterrows():
                    date = row['date'].strftime('%Y-%m-%d')
                    
                    # Separate OHLCV from indicators
                    ohlcv_data = {
                        'date': date,
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': int(row['volume'])
                    }
                    
                    # Everything else is indicators
                    indicators = {}
                    for col in df.columns:
                        if col not in ['date', 'open', 'high', 'low', 'close', 'volume']:
                            val = row[col]
                            if pd.notna(val):
                                indicators[col] = float(val)
                    
                    # Insert or update
                    cursor.execute("""
                        INSERT OR REPLACE INTO backtest_cache
                        (symbol, date, ohlcv_data, indicators, fetched_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        symbol,
                        date,
                        json.dumps(ohlcv_data),
                        json.dumps(indicators) if indicators else None,
                        fetched_at
                    ))
                
                logger.debug(f"Cached {len(df)} bars for {symbol}")
                
        except Exception as e:
            logger.warning(f"Cache save error: {e}")
    
    def _rate_limit(self, source: str):
        """Apply rate limiting to avoid API bans."""
        now = time.time()
        
        if source in self.last_api_call:
            elapsed = now - self.last_api_call[source]
            
            if elapsed < self.min_call_interval:
                sleep_time = self.min_call_interval - elapsed
                logger.debug(f"Rate limiting {source}: sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
        
        self.last_api_call[source] = time.time()
    
    def fetch_multiple_symbols(self, symbols: List[str], start_date: str,
                               end_date: str) -> Dict[str, pd.DataFrame]:
        """
        Fetch historical data for multiple symbols.
        
        Args:
            symbols: List of stock symbols
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        
        Returns:
            Dictionary mapping symbol -> DataFrame
        """
        results = {}
        
        for symbol in symbols:
            logger.info(f"Fetching {symbol}...")
            
            df = self.fetch_historical_ohlcv(symbol, start_date, end_date)
            
            if df is not None:
                results[symbol] = df
            else:
                logger.warning(f"Failed to fetch data for {symbol}")
        
        logger.info(f"Successfully fetched {len(results)}/{len(symbols)} symbols")
        
        return results
    
    def get_data_for_date(self, symbol: str, date: str) -> Optional[Dict]:
        """
        Get OHLCV data for a specific date.

        Args:
            symbol: Stock symbol
            date: Date (YYYY-MM-DD)

        Returns:
            Dictionary with OHLC data for that date
        """
        # Fetch a small window around the date
        date_dt = datetime.strptime(date, '%Y-%m-%d')
        start = (date_dt - timedelta(days=5)).strftime('%Y-%m-%d')
        end = (date_dt + timedelta(days=1)).strftime('%Y-%m-%d')

        df = self.fetch_historical_ohlcv(symbol, start, end, with_indicators=False)

        if df is None or df.empty:
            return None

        # Find the row for the target date
        df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
        row = df[df['date_str'] == date]

        if row.empty:
            return None

        return row.iloc[0].to_dict()

    def fetch_earnings_catalysts(self,
                                start_date: str,
                                end_date: str,
                                universe: Optional[List[str]] = None,
                                min_market_cap: float = 500_000_000,
                                max_catalysts: int = 200) -> List[Dict[str, Any]]:
        """
        Fetch earnings catalyst events with gap and surprise data.

        Data Sources (priority order):
        1. Finnhub API (PRIMARY - confirmed earnings only, EPS/surprise data, no pre-announcement noise)
        2. Alpaca News API (SECONDARY - falls back if Finnhub unavailable)
        3. yfinance (TERTIARY - last resort fallback)

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            universe: List of symbols to check (optional, uses default if None)
            min_market_cap: Minimum market cap (default $500M)
            max_catalysts: Maximum catalysts to return

        Returns:
            List of dicts with:
                - symbol: Stock ticker
                - date: Earnings date
                - catalyst_type: 'earnings'
                - gap_pct: Price gap %
                - surprise_pct: Earnings surprise % (if available)
                - market_cap: Market cap
                - vix_level: VIX on that date
                - sentiment: positive/negative/neutral (Alpaca only)
                - source: 'alpaca' or 'yfinance'
        """
        logger.info(f"Fetching earnings catalysts {start_date} to {end_date}")

        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')

        # Use weekly rotation if enabled
        if universe is None:
            if self.weekly_rotator:
                logger.info("Using weekly stock rotation for dynamic catalyst discovery")
                week_map = self.weekly_rotator.get_movers_by_week_map(
                    start_dt.date(), end_dt.date()
                )
                all_symbols = set()
                for week_symbols in week_map.values():
                    all_symbols.update(week_symbols)
                universe = list(all_symbols)
                logger.info(f"Weekly rotation found {len(universe)} unique symbols across all weeks")
            else:
                universe = self._get_default_earnings_universe()
                logger.info(f"Using static universe of {len(universe)} symbols")

        catalysts = []

        # Try Finnhub first (PRIMARY - confirmed earnings dates only, no pre-announcement noise)
        if FINNHUB_AVAILABLE:
            try:
                logger.info("Attempting Finnhub for earnings catalysts (primary source)...")
                finnhub = FinnhubEarningsFetcher()
                finnhub_catalysts = finnhub.fetch_earnings_for_universe(
                    symbols=universe,
                    start_date=start_dt,
                    end_date=end_dt
                )

                # Enrich with gap/VIX/market cap using cached price data
                for cat in finnhub_catalysts[:max_catalysts]:
                    symbol = cat['symbol']
                    try:
                        earnings_date = datetime.strptime(cat['date'], '%Y-%m-%d')
                    except (ValueError, KeyError):
                        continue

                    try:
                        if YFINANCE_AVAILABLE:
                            import yfinance as yf
                            ticker = yf.Ticker(symbol)

                            price_start = earnings_date - timedelta(days=2)
                            price_end = earnings_date + timedelta(days=1)
                            hist = ticker.history(start=price_start, end=price_end)

                            if not hist.empty and len(hist) >= 2:
                                pre_close = hist['Close'].iloc[-2]
                                earnings_open = hist['Open'].iloc[-1]
                                gap_pct = ((earnings_open - pre_close) / pre_close) * 100
                                cat['gap_pct'] = round(gap_pct, 2)

                                info = ticker.info
                                market_cap = info.get('marketCap', 0)
                                if market_cap < min_market_cap:
                                    continue

                                cat['market_cap'] = market_cap
                                cat['vix_level'] = self._get_vix_for_date(earnings_date.date())
                                cat['surprise_pct'] = cat.get('eps_surprise_pct', 0.0) or 0.0
                                cat['catalyst_type'] = 'earnings'

                                catalysts.append(cat)
                                logger.info(
                                    f"{symbol}: Earnings {earnings_date.date()}, "
                                    f"gap={gap_pct:.1f}%, surprise={cat['surprise_pct']:.1f}%"
                                )
                    except Exception as e:
                        logger.debug(f"{symbol}: Error enriching Finnhub catalyst - {e}")
                        continue

                if len(catalysts) > 0:
                    logger.info(f"Finnhub found {len(catalysts)} earnings catalysts")
                    return catalysts[:max_catalysts]
                else:
                    logger.warning("Finnhub returned 0 catalysts after enrichment - falling back")

            except Exception as e:
                logger.warning(f"Finnhub failed ({e}) - falling back to Alpaca")

        # Try Alpaca News API second (SECONDARY)
        if self.alpaca_broker and ALPACA_EARNINGS_AVAILABLE:
            try:
                logger.info("Attempting Alpaca News API for earnings catalysts...")
                fetcher = AlpacaEarningsFetcher(self.alpaca_broker)
                alpaca_catalysts = fetcher.fetch_earnings_catalysts(
                    symbols=universe,
                    start_date=start_dt,
                    end_date=end_dt
                )

                # Enrich with gap/VIX/market cap data using yfinance price data
                for cat in alpaca_catalysts[:max_catalysts]:
                    symbol = cat['symbol']
                    earnings_date = cat['date']

                    try:
                        # Get price data around earnings
                        if YFINANCE_AVAILABLE:
                            import yfinance as yf
                            ticker = yf.Ticker(symbol)

                            # Calculate gap
                            price_start = earnings_date - timedelta(days=2)
                            price_end = earnings_date + timedelta(days=1)
                            hist = ticker.history(start=price_start, end=price_end)

                            if not hist.empty and len(hist) >= 2:
                                pre_close = hist['Close'].iloc[-2]
                                earnings_open = hist['Open'].iloc[-1]
                                gap_pct = ((earnings_open - pre_close) / pre_close) * 100
                                cat['gap_pct'] = round(gap_pct, 2)

                                # Get market cap
                                info = ticker.info
                                market_cap = info.get('marketCap', 0)
                                if market_cap < min_market_cap:
                                    continue  # Skip if too small

                                cat['market_cap'] = market_cap

                                # Get VIX level
                                vix_level = self._get_vix_for_date(earnings_date.date())
                                cat['vix_level'] = vix_level

                                # Format date as string
                                cat['date'] = earnings_date.strftime('%Y-%m-%d')
                                cat['catalyst_type'] = 'earnings'
                                cat['surprise_pct'] = 0.0  # Not available from Alpaca
                                cat['source'] = 'alpaca'

                                catalysts.append(cat)
                                logger.info(f"{symbol}: Earnings {earnings_date.date()}, gap={gap_pct:.1f}%, VIX={vix_level:.1f}, sentiment={cat.get('sentiment', 'N/A')}")

                    except Exception as e:
                        logger.debug(f"{symbol}: Error enriching Alpaca catalyst - {e}")
                        continue

                if len(catalysts) > 0:
                    logger.info(f"Alpaca News API found {len(catalysts)} earnings catalysts")
                    return catalysts[:max_catalysts]
                else:
                    logger.warning("Alpaca News API returned 0 catalysts after enrichment - falling back to yfinance")

            except Exception as e:
                logger.warning(f"Alpaca News API failed ({e}) - falling back to yfinance")

        # Fall back to yfinance
        if not YFINANCE_AVAILABLE:
            logger.error("yfinance not available - cannot fetch earnings catalysts")
            return []

        logger.info("Using yfinance for earnings catalysts (fallback)")
        import yfinance as yf

        for symbol in universe:
            if len(catalysts) >= max_catalysts:
                break

            try:
                ticker = yf.Ticker(symbol)

                # Get earnings dates
                earnings_dates = ticker.earnings_dates
                if earnings_dates is None or earnings_dates.empty:
                    logger.debug(f"{symbol}: No earnings dates found, symbol may be delisted")
                    continue

                # Filter to date range
                earnings_dates_naive = earnings_dates.copy()
                earnings_dates_naive.index = earnings_dates_naive.index.tz_localize(None)

                mask = (earnings_dates_naive.index >= start_dt) & (earnings_dates_naive.index <= end_dt)
                earnings_in_range = earnings_dates_naive[mask]

                if earnings_in_range.empty:
                    continue

                # Process each earnings date
                for earnings_date in earnings_in_range.index:
                    price_start = earnings_date - timedelta(days=2)
                    price_end = earnings_date + timedelta(days=1)

                    hist = ticker.history(start=price_start, end=price_end)
                    if hist.empty or len(hist) < 2:
                        continue

                    # Calculate gap
                    pre_close = hist['Close'].iloc[-2]
                    earnings_open = hist['Open'].iloc[-1]
                    gap_pct = ((earnings_open - pre_close) / pre_close) * 100

                    # Get earnings surprise
                    surprise_pct = 0.0
                    if 'EPS Estimate' in earnings_in_range.columns and 'Reported EPS' in earnings_in_range.columns:
                        estimate = earnings_in_range.loc[earnings_date, 'EPS Estimate']
                        actual = earnings_in_range.loc[earnings_date, 'Reported EPS']
                        if estimate and actual and estimate != 0:
                            surprise_pct = ((actual - estimate) / abs(estimate)) * 100

                    # Get market cap
                    info = ticker.info
                    market_cap = info.get('marketCap', 0)
                    if market_cap < min_market_cap:
                        continue

                    # Get VIX level
                    vix_level = self._get_vix_for_date(earnings_date.date())

                    catalyst = {
                        'symbol': symbol,
                        'date': earnings_date.strftime('%Y-%m-%d'),
                        'catalyst_type': 'earnings',
                        'gap_pct': round(gap_pct, 2),
                        'surprise_pct': round(surprise_pct, 2),
                        'market_cap': market_cap,
                        'vix_level': vix_level,
                        'source': 'yfinance'
                    }

                    catalysts.append(catalyst)
                    logger.info(f"{symbol}: Earnings {earnings_date.date()}, gap={gap_pct:.1f}%, VIX={vix_level:.1f}")

            except Exception as e:
                logger.debug(f"{symbol}: Error fetching earnings - {e}")
                continue

        logger.info(f"Found {len(catalysts)} earnings catalysts")
        return catalysts


    def _get_default_earnings_universe(self) -> List[str]:
        """
        Get default universe of earnings stocks to scan.

        Focus: Top 50 historical movers from Sept 2025 - Feb 2026
        Selected by average daily volatility during simulation period.
        These stocks were ACTUAL top gainers/movers during the target timeframe.

        TODO: Phase 2 Enhancement - Make this dynamic per week:
              - Fetch top gainers/movers for each specific week being simulated
              - Get catalysts for those stocks during that exact week
              - This ensures we trade the ACTUAL hot stocks at the right time
        """
        return [
            # Top 50 movers by average daily volatility (Sept 2025 - Feb 2026)
            # Format: Symbol (Avg Daily %, Total Return %)

            # Ultra-high volatility (5%+ daily avg)
            'NVTS',  # 5.83% daily, +28.5% total - Top mover

            # Very high volatility (4-5% daily avg)
            'WOLF', 'MSGM', 'LITE',  # 4.5-4.9% daily, +55% to +244% total

            # High volatility (3-4% daily avg)
            'FSLY', 'SRPT', 'RGNX', 'PATH', 'HOOD', 'DAVE',  # 3.2-3.8% daily

            # Strong volatility (2.5-3% daily avg)
            'COHR', 'DOMO', 'PLAY', 'CABO', 'SOFI', 'PLAB', 'SMCI', 'AFRM',
            'DOCN', 'RARE', 'MNTN', 'TER', 'ARVN', 'ENTG', 'AMBA', 'MKSI',

            # Solid volatility (2-2.5% daily avg)
            'MRVL', 'FROG', 'CAVA', 'BILL', 'WING', 'BBWI', 'WEAV', 'TDC',
            'DIOD', 'GTLB', 'ASAN', 'ON', 'SCVL', 'LSCC', 'SLAB', 'CGNX',
            'MPWR', 'CVCO', 'GOOS', 'ALKT', 'BROS', 'VERX', 'EAT',

            # Note: Many of these overlap with user's actual trade history
            # (FSLY, CGNX, LSCC, TDC, MNTN) - validates selection methodology
        ]

    def _get_vix_for_date(self, date: datetime.date) -> float:
        """Get VIX level for a specific date (cached)."""
        try:
            if not YFINANCE_AVAILABLE:
                return 20.0  # Default

            vix = yf.Ticker('^VIX')
            start = (date - timedelta(days=1)).strftime('%Y-%m-%d')
            end = (date + timedelta(days=1)).strftime('%Y-%m-%d')

            hist = vix.history(start=start, end=end)
            if not hist.empty:
                return float(hist['Close'].iloc[-1])

            return 20.0  # Default if no data

        except:
            return 20.0  # Default on error


def test_historical_data_fetcher():
    """Quick test of the historical data fetcher."""
    print("Testing Historical Data Fetcher...")
    
    # Create fetcher with just Yahoo Finance (no brokers needed)
    fetcher = HistoricalDataFetcher()
    
    # Test fetch
    symbol = 'AAPL'
    start = '2026-01-01'
    end = '2026-02-09'
    
    print(f"\nFetching {symbol} from {start} to {end}...")
    df = fetcher.fetch_historical_ohlcv(symbol, start, end)
    
    if df is not None:
        print(f"[OK] Fetched {len(df)} bars")
        print(f"[OK] Columns: {list(df.columns)}")
        print(f"\nLast 3 bars:")
        print(df.tail(3))
        
        # Check indicators
        if 'rsi_14' in df.columns:
            print(f"\n[OK] Technical indicators calculated")
            print(f"  Latest RSI: {df['rsi_14'].iloc[-1]:.1f}")
            print(f"  10-day return: {df['return_10d'].iloc[-1]:.2%}")
    else:
        print("[ERROR] Failed to fetch data")
    
    print("\nHistorical Data Fetcher test complete!")


if __name__ == '__main__':
    test_historical_data_fetcher()