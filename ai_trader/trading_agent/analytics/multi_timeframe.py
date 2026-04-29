"""
Multi-Timeframe Analysis

Analyzes price action across multiple timeframes to:
- Identify daily trend before intraday trades
- Find key support/resistance from higher timeframes
- Improve trade direction (trade with daily trend)

Author: AI Day Trader Team
Date: January 18, 2026
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class MultiTimeframeAnalyzer:
    """
    Analyzes multiple timeframes to provide better trading context.
    """

    def __init__(self, data_provider=None):
        """
        Initialize multi-timeframe analyzer.

        Args:
            data_provider: Market data provider for historical data
        """
        self.data_provider = data_provider

    def analyze_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Analyze symbol across multiple timeframes.

        Args:
            symbol: Stock symbol to analyze

        Returns:
            Multi-timeframe analysis dictionary
        """
        if not self.data_provider:
            logger.warning("No data provider - returning mock analysis")
            return self._get_mock_analysis(symbol)

        try:
            # Get data for multiple timeframes
            daily_data = self._get_daily_analysis(symbol)
            intraday_data = self._get_intraday_analysis(symbol)

            # Combine analyses
            analysis = {
                'symbol': symbol,
                'daily': daily_data,
                'intraday': intraday_data,
                'trade_direction': self._determine_trade_direction(daily_data, intraday_data),
                'key_levels': self._identify_key_levels(daily_data, intraday_data),
                'recommendation': self._generate_recommendation(daily_data, intraday_data),
                'timestamp': datetime.now().isoformat()
            }

            return analysis

        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return self._get_mock_analysis(symbol)

    def _get_daily_analysis(self, symbol: str) -> Dict[str, Any]:
        """
        Analyze daily timeframe.

        Args:
            symbol: Symbol to analyze

        Returns:
            Daily analysis dictionary
        """
        try:
            # Get 30 days of daily bars
            bars = self.data_provider.get_historical_data(
                symbol=symbol,
                days_back=30,
                timeframe='1Day'
            )

            if not bars or len(bars) < 5:
                return {'trend': 'unknown', 'strength': 0}

            # Calculate trend
            recent_bars = bars[-10:]  # Last 10 days
            first_close = recent_bars[0].get('close', 0)
            last_close = recent_bars[-1].get('close', 0)

            price_change_pct = ((last_close - first_close) / first_close * 100) if first_close else 0

            # Determine trend
            if price_change_pct > 5:
                trend = 'strong_uptrend'
                strength = min(100, abs(price_change_pct) * 10)
            elif price_change_pct > 2:
                trend = 'uptrend'
                strength = abs(price_change_pct) * 15
            elif price_change_pct < -5:
                trend = 'strong_downtrend'
                strength = min(100, abs(price_change_pct) * 10)
            elif price_change_pct < -2:
                trend = 'downtrend'
                strength = abs(price_change_pct) * 15
            else:
                trend = 'ranging'
                strength = 20

            # Find support and resistance from daily chart
            highs = [bar.get('high', 0) for bar in recent_bars]
            lows = [bar.get('low', 0) for bar in recent_bars]

            resistance = max(highs) if highs else last_close * 1.05
            support = min(lows) if lows else last_close * 0.95

            return {
                'trend': trend,
                'strength': round(strength, 1),
                'price_change_10d': round(price_change_pct, 2),
                'resistance': round(resistance, 2),
                'support': round(support, 2),
                'current_price': round(last_close, 2)
            }

        except Exception as e:
            logger.error(f"Error in daily analysis: {e}")
            return {'trend': 'unknown', 'strength': 0}

    def _get_intraday_analysis(self, symbol: str) -> Dict[str, Any]:
        """
        Analyze intraday timeframe (5-minute bars).

        Args:
            symbol: Symbol to analyze

        Returns:
            Intraday analysis dictionary
        """
        try:
            # Get today's 5-min bars
            bars = self.data_provider.get_historical_data(
                symbol=symbol,
                days_back=1,  # 1 day of 5-min data
                timeframe='5Min'
            )

            if not bars or len(bars) < 5:
                return {'trend': 'unknown', 'momentum': 0}

            # Calculate intraday momentum
            recent_bars = bars[-12:]  # Last hour
            first_close = recent_bars[0].get('close', 0)
            last_close = recent_bars[-1].get('close', 0)

            momentum = ((last_close - first_close) / first_close * 100) if first_close else 0

            if momentum > 1:
                trend = 'bullish'
            elif momentum < -1:
                trend = 'bearish'
            else:
                trend = 'neutral'

            # Find intraday support/resistance
            highs = [bar.get('high', 0) for bar in recent_bars]
            lows = [bar.get('low', 0) for bar in recent_bars]

            return {
                'trend': trend,
                'momentum': round(momentum, 2),
                'intraday_high': round(max(highs), 2) if highs else 0,
                'intraday_low': round(min(lows), 2) if lows else 0,
                'current_price': round(last_close, 2)
            }

        except Exception as e:
            logger.error(f"Error in intraday analysis: {e}")
            return {'trend': 'unknown', 'momentum': 0}

    def _determine_trade_direction(self, daily: Dict, intraday: Dict) -> str:
        """
        Determine recommended trade direction based on multiple timeframes.

        Best trades align with daily trend.

        Args:
            daily: Daily analysis
            intraday: Intraday analysis

        Returns:
            'long_bias', 'short_bias', or 'neutral'
        """
        daily_trend = daily.get('trend', 'unknown')
        intraday_trend = intraday.get('trend', 'unknown')

        # Strong daily uptrend
        if daily_trend in ['strong_uptrend', 'uptrend']:
            return 'long_bias'
        # Strong daily downtrend
        elif daily_trend in ['strong_downtrend', 'downtrend']:
            return 'short_bias'
        # Ranging on daily, use intraday
        elif intraday_trend == 'bullish':
            return 'long_bias'
        elif intraday_trend == 'bearish':
            return 'short_bias'
        else:
            return 'neutral'

    def _identify_key_levels(self, daily: Dict, intraday: Dict) -> Dict[str, float]:
        """
        Identify key support and resistance levels from multiple timeframes.

        Args:
            daily: Daily analysis
            intraday: Intraday analysis

        Returns:
            Dictionary of key levels
        """
        # Daily levels are more significant
        daily_support = daily.get('support', 0)
        daily_resistance = daily.get('resistance', 0)

        # Intraday levels for short-term trades
        intraday_low = intraday.get('intraday_low', 0)
        intraday_high = intraday.get('intraday_high', 0)

        return {
            'daily_resistance': daily_resistance,
            'daily_support': daily_support,
            'intraday_resistance': intraday_high,
            'intraday_support': intraday_low
        }

    def _generate_recommendation(self, daily: Dict, intraday: Dict) -> str:
        """
        Generate trading recommendation based on multi-timeframe analysis.

        Args:
            daily: Daily analysis
            intraday: Intraday analysis

        Returns:
            Recommendation string
        """
        daily_trend = daily.get('trend', 'unknown')
        intraday_trend = intraday.get('trend', 'unknown')
        trade_direction = self._determine_trade_direction(daily, intraday)

        recommendations = []

        # Daily trend analysis
        if daily_trend in ['strong_uptrend', 'uptrend']:
            recommendations.append(
                f"✅ Daily trend is {daily_trend.replace('_', ' ')} "
                f"({daily.get('price_change_10d', 0):+.1f}% over 10 days)"
            )
            recommendations.append("Bias: LONG - Look for pullbacks to buy")
        elif daily_trend in ['strong_downtrend', 'downtrend']:
            recommendations.append(
                f"⚠️ Daily trend is {daily_trend.replace('_', ' ')} "
                f"({daily.get('price_change_10d', 0):+.1f}% over 10 days)"
            )
            recommendations.append("Bias: SHORT or avoid longs")
        else:
            recommendations.append("Daily trend is ranging - use intraday signals")

        # Intraday momentum
        momentum = intraday.get('momentum', 0)
        if abs(momentum) > 0.5:
            recommendations.append(
                f"Intraday momentum: {momentum:+.1f}% (last hour) - {intraday_trend}"
            )

        # Key levels
        daily_resistance = daily.get('resistance', 0)
        daily_support = daily.get('support', 0)
        current = daily.get('current_price', 0)

        if current and daily_resistance and daily_support:
            if current >= daily_resistance * 0.98:
                recommendations.append(
                    f"⚠️ Price near daily resistance (${daily_resistance:.2f}) - watch for rejection"
                )
            elif current <= daily_support * 1.02:
                recommendations.append(
                    f"✅ Price near daily support (${daily_support:.2f}) - watch for bounce"
                )

        return " | ".join(recommendations)

    def _get_mock_analysis(self, symbol: str) -> Dict[str, Any]:
        """
        Return mock analysis when data provider not available.

        Args:
            symbol: Symbol

        Returns:
            Mock analysis dictionary
        """
        return {
            'symbol': symbol,
            'daily': {
                'trend': 'ranging',
                'strength': 40,
                'price_change_10d': 0,
                'resistance': 0,
                'support': 0
            },
            'intraday': {
                'trend': 'neutral',
                'momentum': 0
            },
            'trade_direction': 'neutral',
            'key_levels': {},
            'recommendation': 'Insufficient data for multi-timeframe analysis',
            'timestamp': datetime.now().isoformat()
        }


def analyze_multi_timeframe(symbol: str, data_provider=None) -> Dict[str, Any]:
    """
    Convenience function for multi-timeframe analysis.

    Args:
        symbol: Symbol to analyze
        data_provider: Market data provider

    Returns:
        Multi-timeframe analysis dictionary
    """
    analyzer = MultiTimeframeAnalyzer(data_provider)
    return analyzer.analyze_symbol(symbol)
