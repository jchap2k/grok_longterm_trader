"""
Market Data Provider - Real-time market data using Schwab API

This module provides a unified interface for fetching real-time market data.
Uses Schwab broker API which provides real-time quotes (unlike Alpaca's 15-min delay).
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
import logging
import sys

from brokers.schwab_broker import SchwabBroker
from brokers.base_broker import Quote
from config.credentials_manager import get_credentials_manager

# Add utils directory to path for market_calendar import
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.market_calendar import get_market_calendar


logger = logging.getLogger(__name__)


class MarketDataProvider:
    """
    Provides real-time market data using Schwab API.

    This is separate from the trading broker to allow:
    - Using Schwab real-time data with Alpaca paper trading
    - Centralized data access for all market information
    - Easy switching between data sources if needed
    """

    def __init__(self, data_source: str = "schwab"):
        """
        Initialize market data provider.

        Args:
            data_source: Data source to use ("schwab" or "alpaca")
        """
        self.data_source = data_source
        self.broker: Optional[SchwabBroker] = None
        self.connected = False
        self.market_calendar = get_market_calendar()

        logger.info(f"Initializing MarketDataProvider with source: {data_source}")

    def connect(self) -> bool:
        """
        Connect to market data source.

        Returns:
            True if connected successfully
        """
        try:
            if self.data_source == "schwab":
                # Get Schwab credentials
                creds = get_credentials_manager()
                schwab_creds = creds.get_schwab_credentials()

                if not schwab_creds['app_key'] or not schwab_creds['app_secret']:
                    logger.error("Schwab credentials not found")
                    return False

                # Initialize Schwab broker for data
                self.broker = SchwabBroker(
                    app_key=schwab_creds['app_key'],
                    app_secret=schwab_creds['app_secret'],
                    refresh_token=schwab_creds.get('refresh_token')
                )

                # Connect
                if self.broker.connect():
                    self.connected = True
                    logger.info("[OK] Market data provider connected (Schwab - REAL-TIME)")
                    return True
                else:
                    logger.error("Failed to connect market data provider")
                    return False

            elif self.data_source == "alpaca":
                # Could implement Alpaca data provider here if needed
                logger.warning("Alpaca data source has 15-minute delay on free tier")
                # TODO: Implement AlpacaBroker for data if needed
                return False

            else:
                logger.error(f"Unknown data source: {self.data_source}")
                return False

        except Exception as e:
            logger.error(f"Error connecting market data provider: {e}")
            return False

    def disconnect(self):
        """Disconnect from market data source."""
        if self.broker:
            self.broker.disconnect()
        self.connected = False
        logger.info("Market data provider disconnected")

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get real-time quote for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Quote data dictionary with price, bid, ask, timestamp
        """
        if not self.connected or not self.broker:
            raise RuntimeError("Market data provider not connected")

        try:
            quote = self.broker.get_quote(symbol)

            return {
                "symbol": quote.symbol,
                "price": quote.price,
                "prev_close": quote.prev_close,  # Previous day's closing price
                "bid": quote.bid,
                "ask": quote.ask,
                "bid_size": quote.bid_size,
                "ask_size": quote.ask_size,
                "timestamp": quote.timestamp.isoformat(),
                "source": f"{self.data_source}_realtime"
            }

        except Exception as e:
            logger.error(f"Error fetching quote for {symbol}: {e}")
            raise

    def get_historical_data(
        self,
        symbol: str,
        days_back: int = 10,
        timeframe: str = "1D"
    ) -> List[Dict[str, Any]]:
        """
        Get historical price data.

        Args:
            symbol: Stock ticker symbol
            days_back: Number of days of history to fetch
            timeframe: Timeframe (1Min, 5Min, 15Min, 1H, 1D)

        Returns:
            List of price bars with OHLCV data
        """
        if not self.connected or not self.broker:
            raise RuntimeError("Market data provider not connected")

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)

            bars = self.broker.get_historical_data(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                timeframe=timeframe
            )

            # Convert to standard format
            result = []
            for bar in bars:
                result.append({
                    "timestamp": bar["timestamp"].isoformat(),
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"]
                })

            return result

        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            raise

    def is_market_open(self) -> bool:
        """
        Check if market is currently open (accounts for holidays and early closes).

        Returns:
            True if market is currently open for trading
        """
        from zoneinfo import ZoneInfo

        # Get current Eastern Time
        now_et = datetime.now(ZoneInfo("America/New_York"))

        # Check if today is a trading day (not weekend, not holiday)
        is_trading_day, reason = self.market_calendar.is_market_open_today(now_et)
        if not is_trading_day:
            logger.debug(f"Market closed: {reason}")
            return False

        # Check if within market hours
        current_time = now_et.time()
        market_open_time = dt_time(9, 30)  # 9:30 AM ET

        # Get market close time (handles early close days)
        market_close_time = self.market_calendar.get_market_close_time(now_et.date())

        # Check if current time is within market hours
        if market_open_time <= current_time < market_close_time:
            return True
        else:
            if current_time < market_open_time:
                logger.debug(f"Market not yet open (opens at {market_open_time})")
            else:
                logger.debug(f"Market closed for the day (closed at {market_close_time})")
            return False

    def is_early_close_today(self) -> bool:
        """
        Check if today is an early close day (market closes at 1:00 PM ET instead of 4:00 PM).

        Returns:
            True if today is an early close day
        """
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()
        is_early, _ = self.market_calendar.is_early_close_day(today)
        return is_early

    def get_market_close_time_today(self) -> dt_time:
        """
        Get today's market close time (accounts for early close days).

        Returns:
            Market close time (Eastern Time)
        """
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()
        return self.market_calendar.get_market_close_time(today)

    def get_market_time_info(self) -> Dict[str, Any]:
        """
        Get current market time and status.

        Returns:
            Dictionary with current time, market status, and time to close
        """
        is_open = self.is_market_open()
        current_time = datetime.now()

        # Calculate minutes to close (assuming 4:00 PM ET / 1:00 PM PST close)
        market_close = current_time.replace(hour=13, minute=0, second=0, microsecond=0)  # 1:00 PM PST
        if current_time > market_close:
            # Market already closed
            minutes_to_close = 0
        else:
            minutes_to_close = int((market_close - current_time).total_seconds() / 60)

        return {
            "current_time": current_time.strftime("%Y-%m-%d %I:%M %p PST"),
            "is_open": is_open,
            "minutes_to_close": minutes_to_close if is_open else 0,
            "data_source": self.data_source
        }

    def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Get quotes for multiple symbols.

        Args:
            symbols: List of ticker symbols

        Returns:
            Dictionary mapping symbols to quote data
        """
        quotes = {}
        for symbol in symbols:
            try:
                quotes[symbol] = self.get_quote(symbol)
            except Exception as e:
                logger.error(f"Error fetching quote for {symbol}: {e}")
                quotes[symbol] = {"error": str(e)}

        return quotes

    def calculate_atr(self, symbol: str, period: int = 14) -> float:
        """
        Calculate Average True Range for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: ATR period (default 14 days)

        Returns:
            ATR value
        """
        try:
            # Get daily historical data
            bars = self.get_historical_data(
                symbol=symbol,
                days_back=period + 5,  # Extra days for calculation
                timeframe="1D"
            )

            if len(bars) < period:
                raise ValueError(f"Not enough data to calculate ATR (need {period}, got {len(bars)})")

            # Calculate True Range for each day
            true_ranges = []
            for i in range(1, len(bars)):
                high = bars[i]["high"]
                low = bars[i]["low"]
                prev_close = bars[i-1]["close"]

                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)

            # Calculate ATR as average of last 'period' true ranges
            atr = sum(true_ranges[-period:]) / period
            return round(atr, 2)

        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            raise


# Singleton instance
_data_provider_instance: Optional[MarketDataProvider] = None


def get_market_data_provider(data_source: str = "schwab") -> MarketDataProvider:
    """
    Get or create the global market data provider instance.

    Args:
        data_source: Data source to use

    Returns:
        MarketDataProvider instance
    """
    global _data_provider_instance

    if _data_provider_instance is None:
        _data_provider_instance = MarketDataProvider(data_source=data_source)

    return _data_provider_instance
