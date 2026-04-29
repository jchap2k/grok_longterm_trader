"""
Market Regime Detection - Volatility and Trend Analysis

Detects current market conditions:
- Volatility regime (low/medium/high via VIX)
- Trend strength (via ADX)
- Market direction (trending up/down/sideways)
- Recommended strategies for current regime

Author: AI Day Trader Team
Date: January 18, 2026
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    """Volatility levels based on VIX."""
    LOW = "low"           # VIX < 15
    MEDIUM = "medium"     # VIX 15-25
    HIGH = "high"         # VIX 25-35
    EXTREME = "extreme"   # VIX > 35


class TrendRegime(Enum):
    """Market trend direction."""
    TRENDING_UP = "trending_up"       # Strong uptrend
    TRENDING_DOWN = "trending_down"   # Strong downtrend
    RANGING = "ranging"                # Sideways/choppy


class MarketRegimeDetector:
    """
    Detects current market regime and provides trading guidance.
    """

    def __init__(self, data_provider=None):
        """
        Initialize market regime detector.

        Args:
            data_provider: Market data provider for fetching quotes and historical data
        """
        self.data_provider = data_provider

    def get_market_regime(self) -> Dict[str, Any]:
        """
        Analyze current market regime.

        Returns:
            Dictionary with regime information:
            {
                'volatility_regime': 'low|medium|high|extreme',
                'trend_regime': 'trending_up|trending_down|ranging',
                'vix': 15.2,
                'adx': 25.0,
                'confidence': 0.85,
                'recommended_strategies': ['momentum', 'breakout'],
                'position_size_adjustment': 1.0,
                'analysis': 'Market shows...'
            }
        """
        # Get VIX for volatility
        vix_value = self._get_vix()
        volatility_regime = self._classify_volatility(vix_value)

        # Get SPY data for trend analysis
        trend_regime, adx_value = self._analyze_trend()

        # Determine recommended strategies
        recommended_strategies = self._get_recommended_strategies(
            volatility_regime, trend_regime
        )

        # Position size adjustment based on volatility
        position_size_adj = self._get_position_size_adjustment(volatility_regime)

        # Generate analysis text
        analysis = self._generate_analysis(
            volatility_regime, trend_regime, vix_value, adx_value
        )

        return {
            'volatility_regime': volatility_regime.value,
            'trend_regime': trend_regime.value,
            'vix': round(vix_value, 2),
            'adx': round(adx_value, 2) if adx_value else None,
            'confidence': self._calculate_confidence(vix_value, adx_value),
            'recommended_strategies': recommended_strategies,
            'position_size_adjustment': position_size_adj,
            'analysis': analysis,
            'timestamp': datetime.now().isoformat()
        }

    def _get_vix(self) -> float:
        """
        Get current VIX (Volatility Index) value.
        Source order: yfinance (primary) -> web scraping -> data provider -> default 18.5

        Returns:
            VIX value
        """
        # PRIMARY: yfinance - most reliable, used successfully elsewhere in codebase
        try:
            import yfinance as yf
            vix_ticker = yf.Ticker('^VIX')
            vix_data = vix_ticker.history(period='1d')
            if not vix_data.empty:
                vix_yf = float(vix_data['Close'].iloc[-1])
                if 5.0 <= vix_yf <= 80.0:
                    logger.info(f"VIX: {vix_yf:.1f} (yfinance)")
                    return vix_yf
                else:
                    logger.warning(f"yfinance VIX out of range: {vix_yf}")
        except ImportError:
            logger.debug("yfinance not installed - skipping primary VIX source")
        except Exception as e:
            logger.warning(f"yfinance VIX fetch failed: {e}")

        # SECONDARY: web scraping (HTML-based, fragile but free)
        vix_web = self._get_vix_from_web()

        # If web data available, use it (don't bother checking provider)
        if vix_web:
            logger.info(f"VIX: {vix_web:.1f} (from web)")
            return vix_web

        # TERTIARY: data provider (Schwab - fails for ^VIX but try anyway)
        logger.warning("yfinance and web VIX sources unavailable - trying data provider")
        
        if not self.data_provider:
            logger.warning("No data provider available - using default VIX 18.5")
            return 18.5

        try:
            # Try to get VIX quote from data provider
            quote = self.data_provider.get_quote('VIX')
            
            # VIX quote structure - try multiple possible price fields
            vix_provider = None
            for price_field in ['price', 'last', 'close']:
                if price_field in quote:
                    vix_provider = quote[price_field]
                    if vix_provider and vix_provider > 0:  # Valid price
                        break
            
            if vix_provider and 5.0 <= vix_provider <= 80.0:
                logger.warning(f"VIX: {vix_provider:.1f} (from provider - web unavailable)")
                return vix_provider
            else:
                logger.warning(f"Invalid VIX from provider: {vix_provider}")
                
        except Exception as e:
            logger.warning(f"Could not fetch VIX from data provider: {e}")
            
        # Last resort: use default
        logger.warning("All VIX sources failed - using default 18.5")
        return 18.5

    def _get_vix_from_web(self) -> Optional[float]:
        """
        Get VIX from web sources as fallback.
        
        Returns:
            VIX value or None if failed
        """
        try:
            import requests
            import re
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("Web scraping libraries not available for VIX fallback")
            return None

        # List of web sources to try
        sources = [
            {
                'name': 'Yahoo Finance',
                'url': 'https://finance.yahoo.com/quote/%5EVIX/',
                'pattern': r'data-symbol="\^VIX"[^>]*data-field="regularMarketPrice"[^>]*>([0-9.]+)',
                'css_selector': '[data-symbol="^VIX"] [data-field="regularMarketPrice"]'
            },
            {
                'name': 'MarketWatch', 
                'url': 'https://www.marketwatch.com/investing/index/vix',
                'pattern': r'<bg-quote[^>]*field="Last"[^>]*>([0-9.]+)',
                'css_selector': '.intraday__price .value'
            },
            {
                'name': 'CBOE Direct',
                'url': 'https://www.cboe.com/tradable_products/vix/',
                'pattern': r'VIX.*?([0-9]{1,2}\.[0-9]{2})',
                'css_selector': None
            }
        ]

        for source in sources:
            try:
                logger.debug(f"Trying to fetch VIX from {source['name']}")
                
                # Make request with headers to avoid blocking
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                response = requests.get(source['url'], headers=headers, timeout=10)
                response.raise_for_status()
                
                # Try CSS selector first if available
                vix_value = None
                if source['css_selector']:
                    try:
                        soup = BeautifulSoup(response.text, 'html.parser')
                        element = soup.select_one(source['css_selector'])
                        if element:
                            text = element.get_text(strip=True)
                            vix_match = re.search(r'([0-9.]+)', text)
                            if vix_match:
                                vix_value = float(vix_match.group(1))
                    except Exception as e:
                        logger.debug(f"CSS selector failed for {source['name']}: {e}")
                
                # Try regex pattern if CSS selector failed
                if not vix_value and source['pattern']:
                    matches = re.findall(source['pattern'], response.text)
                    if matches:
                        vix_value = float(matches[0])
                
                # Validate VIX value (reasonable range)
                if vix_value and 5.0 <= vix_value <= 80.0:
                    logger.info(f"VIX (from {source['name']}): {vix_value}")
                    return vix_value
                elif vix_value:
                    logger.warning(f"VIX value {vix_value} from {source['name']} seems unrealistic")
                    
            except requests.RequestException as e:
                logger.debug(f"Network error fetching VIX from {source['name']}: {e}")
            except Exception as e:
                logger.debug(f"Error parsing VIX from {source['name']}: {e}")
        
        logger.warning("All web sources failed for VIX")
        return None

    def _classify_volatility(self, vix: float) -> VolatilityRegime:
        """
        Classify volatility regime based on VIX.

        Args:
            vix: VIX value

        Returns:
            VolatilityRegime enum
        """
        if vix < 15:
            return VolatilityRegime.LOW
        elif vix < 25:
            return VolatilityRegime.MEDIUM
        elif vix < 35:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.EXTREME

    def _analyze_trend(self) -> tuple[TrendRegime, Optional[float]]:
        """
        Analyze market trend using SPY and calculate ADX.

        Returns:
            Tuple of (TrendRegime, ADX value)
        """
        if not self.data_provider:
            logger.warning("No data provider - using default ranging regime")
            return TrendRegime.RANGING, 20.0

        try:
            # Get SPY historical data for trend analysis
            # Need at least 14 days for ADX calculation
            bars = self.data_provider.get_historical_data(
                symbol='SPY',
                days_back=30,
                timeframe='1Day'
            )

            if not bars or len(bars) < 14:
                logger.warning("Insufficient data for trend analysis")
                return TrendRegime.RANGING, 20.0

            # Calculate ADX (simplified version)
            adx = self._calculate_adx(bars)

            # Determine trend direction
            trend_regime = self._determine_trend_direction(bars, adx)

            return trend_regime, adx

        except Exception as e:
            logger.error(f"Error analyzing trend: {e}")
            return TrendRegime.RANGING, 20.0

    def _calculate_adx(self, bars: List[Dict]) -> float:
        """
        Calculate Average Directional Index (ADX).

        Simplified ADX calculation:
        - ADX < 20: Weak trend (ranging)
        - ADX 20-40: Moderate trend
        - ADX > 40: Strong trend

        Args:
            bars: Historical price bars

        Returns:
            ADX value
        """
        if len(bars) < 14:
            return 20.0

        # Simplified ADX: use price momentum as proxy
        # In production, would calculate true ADX with +DI and -DI
        try:
            recent_bars = bars[-14:]
            prices = [bar.get('close', 0) for bar in recent_bars]

            # Calculate average true range (simplified)
            ranges = []
            for i in range(1, len(recent_bars)):
                high = recent_bars[i].get('high', 0)
                low = recent_bars[i].get('low', 0)
                prev_close = recent_bars[i-1].get('close', 0)
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                ranges.append(tr)

            avg_range = sum(ranges) / len(ranges) if ranges else 0

            # Price momentum
            price_change = abs(prices[-1] - prices[0])
            momentum = (price_change / prices[0] * 100) if prices[0] else 0

            # Rough ADX proxy: momentum relative to volatility
            adx_proxy = min(60, momentum / (avg_range / prices[-1] * 100) * 20) if avg_range > 0 else 20

            return adx_proxy

        except Exception as e:
            logger.error(f"Error calculating ADX: {e}")
            return 20.0

    def _determine_trend_direction(self, bars: List[Dict], adx: float) -> TrendRegime:
        """
        Determine if market is trending up, down, or ranging.

        Args:
            bars: Historical price bars
            adx: ADX value

        Returns:
            TrendRegime enum
        """
        # If ADX is low, market is ranging
        if adx < 20:
            return TrendRegime.RANGING

        # Check recent price action for direction
        if len(bars) < 5:
            return TrendRegime.RANGING

        recent = bars[-5:]
        first_close = recent[0].get('close', 0)
        last_close = recent[-1].get('close', 0)

        price_change_pct = ((last_close - first_close) / first_close * 100) if first_close else 0

        # Trending up if price up significantly
        if price_change_pct > 2:
            return TrendRegime.TRENDING_UP
        # Trending down if price down significantly
        elif price_change_pct < -2:
            return TrendRegime.TRENDING_DOWN
        else:
            return TrendRegime.RANGING

    def _get_recommended_strategies(self, volatility: VolatilityRegime,
                                   trend: TrendRegime) -> List[str]:
        """
        Recommend strategies based on market regime.

        Args:
            volatility: Current volatility regime
            trend: Current trend regime

        Returns:
            List of recommended strategy names
        """
        recommendations = []

        # Trending market strategies
        if trend == TrendRegime.TRENDING_UP:
            recommendations.extend(['momentum', 'breakout', 'pullback'])
        elif trend == TrendRegime.TRENDING_DOWN:
            recommendations.extend(['short_momentum', 'breakdown'])
        else:  # Ranging
            recommendations.extend(['bounce', 'mean_reversion', 'range_trading'])

        # Adjust for volatility
        if volatility == VolatilityRegime.LOW:
            # Low vol: can use tighter stops
            if 'scalp' not in recommendations:
                recommendations.append('scalp')
        elif volatility in [VolatilityRegime.HIGH, VolatilityRegime.EXTREME]:
            # High vol: wider stops, smaller positions
            # Remove strategies that need tight stops
            if 'scalp' in recommendations:
                recommendations.remove('scalp')

        # Always consider news catalyst in any regime
        if 'news_catalyst' not in recommendations:
            recommendations.append('news_catalyst')

        return recommendations

    def _get_position_size_adjustment(self, volatility: VolatilityRegime) -> float:
        """
        Get position size adjustment multiplier based on volatility.

        Args:
            volatility: Current volatility regime

        Returns:
            Multiplier for position size (0.5 to 1.2)
        """
        adjustments = {
            VolatilityRegime.LOW: 1.2,      # Can size up in low vol
            VolatilityRegime.MEDIUM: 1.0,   # Normal sizing
            VolatilityRegime.HIGH: 0.75,    # Size down in high vol
            VolatilityRegime.EXTREME: 0.5   # Significantly reduce in extreme vol
        }
        return adjustments.get(volatility, 1.0)

    def _calculate_confidence(self, vix: float, adx: Optional[float]) -> float:
        """
        Calculate confidence in regime detection (0.0 to 1.0).

        Higher confidence when:
        - VIX is clear in a regime (not near boundaries)
        - ADX is calculated successfully

        Args:
            vix: VIX value
            adx: ADX value (or None)

        Returns:
            Confidence score 0.0-1.0
        """
        confidence = 0.5  # Base confidence

        # VIX confidence
        if vix < 12 or vix > 40:
            # Clear low or high vol
            confidence += 0.3
        elif 15 <= vix <= 25:
            # Medium vol (less clear)
            confidence += 0.1

        # ADX confidence
        if adx is not None:
            if adx < 15 or adx > 40:
                # Clear ranging or trending
                confidence += 0.2
            else:
                confidence += 0.1

        return min(1.0, confidence)

    def _generate_analysis(self, volatility: VolatilityRegime, trend: TrendRegime,
                          vix: float, adx: Optional[float]) -> str:
        """
        Generate human-readable market analysis.

        Args:
            volatility: Volatility regime
            trend: Trend regime
            vix: VIX value
            adx: ADX value

        Returns:
            Analysis text
        """
        analysis_parts = []

        # Volatility analysis
        vol_descriptions = {
            VolatilityRegime.LOW: f"low volatility (VIX {vix:.1f})",
            VolatilityRegime.MEDIUM: f"moderate volatility (VIX {vix:.1f})",
            VolatilityRegime.HIGH: f"elevated volatility (VIX {vix:.1f})",
            VolatilityRegime.EXTREME: f"extreme volatility (VIX {vix:.1f})"
        }
        analysis_parts.append(f"Market shows {vol_descriptions.get(volatility, 'moderate volatility')}")

        # Trend analysis
        if adx:
            trend_descriptions = {
                TrendRegime.TRENDING_UP: f"in a strong uptrend (ADX {adx:.1f})",
                TrendRegime.TRENDING_DOWN: f"in a strong downtrend (ADX {adx:.1f})",
                TrendRegime.RANGING: f"in a ranging/choppy pattern (ADX {adx:.1f})"
            }
            analysis_parts.append(f"and is {trend_descriptions.get(trend, 'ranging')}")

        # Trading implications
        if volatility in [VolatilityRegime.HIGH, VolatilityRegime.EXTREME]:
            analysis_parts.append("Reduce position sizes and use wider stops.")
        elif volatility == VolatilityRegime.LOW:
            analysis_parts.append("Consider standard to slightly larger positions.")

        if trend == TrendRegime.RANGING:
            analysis_parts.append("Focus on mean reversion and range-bound strategies.")
        else:
            analysis_parts.append("Focus on momentum and trend-following strategies.")

        return " ".join(analysis_parts)


def get_current_market_regime(data_provider=None) -> Dict[str, Any]:
    """
    Convenience function to get current market regime.

    Args:
        data_provider: Market data provider

    Returns:
        Market regime dictionary
    """
    detector = MarketRegimeDetector(data_provider)
    return detector.get_market_regime()
