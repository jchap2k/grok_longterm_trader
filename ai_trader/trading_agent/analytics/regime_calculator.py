"""
Regime Calculator - Determines market regime for pattern validation

Calculates market conditions to segment pattern validation:
- VIX bucket (low/medium/high volatility)
- SPY trend (bull/bear/flat market direction)
- Sector strength (future enhancement)

Prevents learning bull market patterns and applying them in bear markets.

Usage:
    calculator = RegimeCalculator(broker)
    regime = calculator.get_current_regime()
    # Returns: {'vix_bucket': 'low', 'spy_trend': 'bull', 'sector_strength': None}
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    logger.warning("pandas not available - regime calculation limited")


class RegimeCalculator:
    """
    Calculate market regime for pattern validation segmentation.

    Determines if current market is:
    - Low/medium/high volatility (VIX)
    - Bull/bear/flat trend (SPY)
    - Sector relative strength (future)
    """

    def __init__(self, broker=None, vix_source='manual'):
        """
        Initialize regime calculator.

        Args:
            broker: Broker instance for data fetching (optional)
            vix_source: 'broker', 'yahoo', or 'manual' (default: manual)
        """
        self.broker = broker
        self.vix_source = vix_source

        # VIX thresholds
        self.VIX_LOW_THRESHOLD = 15.0
        self.VIX_HIGH_THRESHOLD = 25.0

        # SPY trend thresholds
        self.TREND_BULL_THRESHOLD = 1.02  # 2% above SMA20
        self.TREND_BEAR_THRESHOLD = 0.98  # 2% below SMA20

        logger.info("RegimeCalculator initialized")
        logger.info(f"  VIX source: {vix_source}")
        logger.info(f"  VIX thresholds: <{self.VIX_LOW_THRESHOLD} low, >{self.VIX_HIGH_THRESHOLD} high")

    def get_current_regime(self) -> Dict[str, Optional[str]]:
        """
        Get current market regime.

        Returns:
            {
                'vix_bucket': 'low' | 'medium' | 'high' | None,
                'spy_trend': 'bull' | 'bear' | 'flat' | None,
                'sector_strength': None  # future enhancement
            }
        """
        regime = {
            'vix_bucket': None,
            'spy_trend': None,
            'sector_strength': None
        }

        try:
            # Get VIX bucket
            vix_value = self._get_vix()
            if vix_value is not None:
                regime['vix_bucket'] = self._calculate_vix_bucket(vix_value)
                logger.debug(f"VIX: {vix_value:.2f} → {regime['vix_bucket']}")

            # Get SPY trend
            spy_trend = self._get_spy_trend()
            if spy_trend is not None:
                regime['spy_trend'] = spy_trend
                logger.debug(f"SPY trend: {spy_trend}")

        except Exception as e:
            logger.warning(f"Error calculating regime: {e}")

        return regime

    def _get_vix(self) -> Optional[float]:
        """
        Get current VIX value.

        Returns:
            VIX value or None if unavailable
        """
        # For now, return None - VIX will be added to backtest cache later
        # TODO: Implement VIX fetching from broker or Yahoo Finance
        logger.debug("VIX fetching not yet implemented, returning None")
        return None

    def _calculate_vix_bucket(self, vix: float) -> str:
        """
        Calculate VIX volatility bucket.

        Args:
            vix: VIX value

        Returns:
            'low' (<15), 'medium' (15-25), or 'high' (>25)
        """
        if vix < self.VIX_LOW_THRESHOLD:
            return 'low'
        elif vix < self.VIX_HIGH_THRESHOLD:
            return 'medium'
        else:
            return 'high'

    def _get_spy_trend(self) -> Optional[str]:
        """
        Get SPY trend direction.

        Compares current price vs 20-day SMA:
        - Bull: >2% above SMA20
        - Bear: >2% below SMA20
        - Flat: within +/-2% of SMA20

        Returns:
            'bull', 'bear', 'flat', or None if unavailable
        """
        if not PANDAS_AVAILABLE:
            return None

        try:
            # Try to fetch SPY data from broker
            if self.broker and hasattr(self.broker, 'get_bars'):
                spy_data = self._fetch_spy_from_broker()
            else:
                # Fallback to Yahoo Finance
                spy_data = self._fetch_spy_from_yahoo()

            if spy_data is None or spy_data.empty:
                return None

            # Calculate 20-day SMA
            spy_data['sma_20'] = spy_data['close'].rolling(window=20).mean()

            # Get latest values
            current_price = spy_data['close'].iloc[-1]
            sma_20 = spy_data['sma_20'].iloc[-1]

            if pd.isna(sma_20):
                logger.warning("SPY SMA20 is NaN (insufficient data)")
                return None

            # Calculate ratio
            price_ratio = current_price / sma_20

            # Determine trend
            if price_ratio > self.TREND_BULL_THRESHOLD:
                trend = 'bull'
            elif price_ratio < self.TREND_BEAR_THRESHOLD:
                trend = 'bear'
            else:
                trend = 'flat'

            logger.debug(f"SPY: ${current_price:.2f}, SMA20: ${sma_20:.2f}, ratio: {price_ratio:.4f} → {trend}")
            return trend

        except Exception as e:
            logger.debug(f"Error fetching SPY trend: {e}")
            return None

    def _fetch_spy_from_broker(self) -> Optional[pd.DataFrame]:
        """Fetch SPY data from broker."""
        try:
            # Calculate date range (need 30 days for 20-day SMA)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=40)  # Extra buffer for weekends

            bars = self.broker.get_bars(
                symbol='SPY',
                timeframe='1Day',
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d')
            )

            if not bars:
                return None

            # Convert to DataFrame
            data = []
            for bar in bars:
                data.append({
                    'date': bar.timestamp,
                    'close': float(bar.close)
                })

            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date'])

            return df

        except Exception as e:
            logger.debug(f"Broker SPY fetch failed: {e}")
            return None

    def _fetch_spy_from_yahoo(self) -> Optional[pd.DataFrame]:
        """Fetch SPY data from Yahoo Finance."""
        try:
            import yfinance as yf

            # Fetch 40 days of data (for 20-day SMA with buffer)
            ticker = yf.Ticker('SPY')
            df = ticker.history(period='40d', interval='1d')

            if df.empty:
                return None

            # Standardize columns
            df = df.reset_index()
            df = df.rename(columns={'Date': 'date', 'Close': 'close'})
            df = df[['date', 'close']]

            return df

        except Exception as e:
            logger.debug(f"Yahoo SPY fetch failed: {e}")
            return None

    def get_regime_description(self, regime: Dict[str, Optional[str]]) -> str:
        """
        Generate human-readable description of regime.

        Args:
            regime: Regime dictionary

        Returns:
            Human-readable description
        """
        parts = []

        if regime['vix_bucket']:
            vol_desc = {
                'low': 'Low volatility (calm market)',
                'medium': 'Medium volatility (normal conditions)',
                'high': 'High volatility (stressed market)'
            }
            parts.append(vol_desc.get(regime['vix_bucket'], regime['vix_bucket']))

        if regime['spy_trend']:
            trend_desc = {
                'bull': 'Bull market (uptrend)',
                'bear': 'Bear market (downtrend)',
                'flat': 'Flat market (sideways)'
            }
            parts.append(trend_desc.get(regime['spy_trend'], regime['spy_trend']))

        if not parts:
            return "Unknown regime (no data available)"

        return ", ".join(parts)


def test_regime_calculator():
    """Quick test of regime calculator."""
    print("Testing Regime Calculator...")
    print("=" * 60)

    calculator = RegimeCalculator(vix_source='manual')

    print("\n1. Get current regime:")
    regime = calculator.get_current_regime()
    print(f"   VIX bucket: {regime['vix_bucket']}")
    print(f"   SPY trend: {regime['spy_trend']}")
    print(f"   Description: {calculator.get_regime_description(regime)}")

    print("\n2. Test VIX bucketing:")
    test_vix_values = [12.0, 18.0, 30.0]
    for vix in test_vix_values:
        bucket = calculator._calculate_vix_bucket(vix)
        print(f"   VIX {vix:.1f} -> {bucket}")

    print("\n3. Test trend detection (requires data):")
    spy_trend = calculator._get_spy_trend()
    if spy_trend:
        print(f"   Current SPY trend: {spy_trend}")
    else:
        print("   SPY trend unavailable (no data source)")

    print("\nRegime Calculator test complete!")
    print("=" * 60)


if __name__ == '__main__':
    test_regime_calculator()
