"""
Correlation Tracker - Portfolio Correlation Analysis

Tracks correlation between positions to prevent over-concentration in correlated assets.
Helps diversify risk and avoid amplified drawdowns.

Author: AI Day Trader Team
Date: January 18, 2026
"""

import logging
import math
from datetime import date
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import statistics

logger = logging.getLogger(__name__)


class CorrelationTracker:
    """
    Analyzes correlation between portfolio positions.
    """

    # Known high correlations (simplified - in production would calculate from historical data)
    KNOWN_CORRELATIONS = {
        # Tech stocks
        ('AAPL', 'MSFT'): 0.75,
        ('AAPL', 'GOOGL'): 0.70,
        ('MSFT', 'GOOGL'): 0.72,
        ('NVDA', 'AMD'): 0.85,  # Very high correlation
        ('NVDA', 'AVGO'): 0.70,
        ('AMD', 'INTC'): 0.65,

        # Semiconductors
        ('NVDA', 'TSM'): 0.75,
        ('AMD', 'TSM'): 0.70,

        # EV/Auto
        ('TSLA', 'RIVN'): 0.60,
        ('TSLA', 'LCID'): 0.55,

        # SPY correlations (market beta)
        ('SPY', 'QQQ'): 0.85,
        ('SPY', 'DIA'): 0.90,
        ('SPY', 'IWM'): 0.75,

        # Indices to individual stocks (approximate)
        ('SPY', 'AAPL'): 0.60,
        ('SPY', 'MSFT'): 0.65,
        ('SPY', 'NVDA'): 0.55,
        ('QQQ', 'AAPL'): 0.75,
        ('QQQ', 'MSFT'): 0.80,
        ('QQQ', 'NVDA'): 0.70,
    }

    def __init__(self, data_provider=None, max_correlation: float = 0.7):
        """
        Initialize correlation tracker.

        Args:
            data_provider: Market data provider for calculating correlations
            max_correlation: Maximum allowed correlation between positions (0.7 = 70%)
        """
        self.data_provider = data_provider
        self.max_correlation = max_correlation
        # Cache: {(sym1, sym2, date_str): correlation} - refreshed daily
        self._correlation_cache: Dict[Tuple, float] = {}

    def analyze_portfolio_correlation(self, positions: Dict[str, int]) -> Dict[str, Any]:
        """
        Analyze correlation within current portfolio.

        Args:
            positions: Dictionary of {symbol: quantity}

        Returns:
            Analysis dictionary with:
            - correlation_matrix: Pairwise correlations
            - highly_correlated_pairs: Pairs above threshold
            - diversification_score: 0-100
            - warnings: List of correlation warnings
        """
        if not positions or len(positions) < 2:
            return {
                'correlation_matrix': {},
                'highly_correlated_pairs': [],
                'diversification_score': 100,
                'warnings': [],
                'analysis': 'Single or no positions - no correlation risk'
            }

        symbols = list(positions.keys())
        correlation_matrix = {}
        highly_correlated_pairs = []
        warnings = []

        # Calculate pairwise correlations
        for i, symbol1 in enumerate(symbols):
            for symbol2 in symbols[i+1:]:
                correlation = self._get_correlation(symbol1, symbol2)
                pair = tuple(sorted([symbol1, symbol2]))
                correlation_matrix[f"{symbol1}-{symbol2}"] = correlation

                # Check if correlation is too high
                if correlation >= self.max_correlation:
                    highly_correlated_pairs.append({
                        'pair': f"{symbol1}/{symbol2}",
                        'correlation': round(correlation, 2),
                        'severity': 'HIGH' if correlation >= 0.8 else 'MEDIUM'
                    })
                    warnings.append(
                        f"⚠️ {symbol1} and {symbol2} are highly correlated ({correlation:.2f}). "
                        "Consider reducing exposure to one."
                    )

        # Calculate diversification score
        diversification_score = self._calculate_diversification_score(
            correlation_matrix, len(symbols)
        )

        # Generate overall analysis
        analysis = self._generate_correlation_analysis(
            len(symbols), len(highly_correlated_pairs), diversification_score
        )

        return {
            'correlation_matrix': correlation_matrix,
            'highly_correlated_pairs': highly_correlated_pairs,
            'diversification_score': round(diversification_score, 1),
            'warnings': warnings,
            'analysis': analysis,
            'position_count': len(symbols),
            'data_source': 'live' if self.data_provider is not None else 'static'
        }

    def check_new_position_correlation(self, new_symbol: str,
                                      existing_positions: Dict[str, int]) -> Dict[str, Any]:
        """
        Check if adding a new position would create correlation risk.

        Args:
            new_symbol: Symbol to potentially add
            existing_positions: Current positions {symbol: quantity}

        Returns:
            Analysis with recommendation
        """
        if not existing_positions:
            return {
                'allowed': True,
                'correlation_warnings': [],
                'recommendation': 'No existing positions - safe to add'
            }

        correlation_warnings = []
        max_correlation_found = 0.0
        most_correlated_symbol = None

        # Check correlation with each existing position
        for existing_symbol in existing_positions.keys():
            correlation = self._get_correlation(new_symbol, existing_symbol)

            if correlation >= self.max_correlation:
                correlation_warnings.append(
                    f"High correlation with {existing_symbol} ({correlation:.2f})"
                )
                if correlation > max_correlation_found:
                    max_correlation_found = correlation
                    most_correlated_symbol = existing_symbol

        # Determine if position should be allowed
        allowed = len(correlation_warnings) == 0

        if not allowed:
            recommendation = (
                f"⚠️ NOT RECOMMENDED: {new_symbol} is highly correlated with "
                f"{most_correlated_symbol} ({max_correlation_found:.2f}). "
                f"This would increase portfolio concentration risk."
            )
        elif max_correlation_found > 0.5:
            recommendation = (
                f"⚠️ CAUTION: {new_symbol} has moderate correlation with existing positions "
                f"(max {max_correlation_found:.2f}). Consider smaller position size."
            )
        else:
            recommendation = f"✅ {new_symbol} has low correlation with existing positions. Safe to add."

        return {
            'allowed': allowed,
            'correlation_warnings': correlation_warnings,
            'max_correlation': round(max_correlation_found, 2),
            'most_correlated_with': most_correlated_symbol,
            'recommendation': recommendation
        }

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Get correlation between two symbols.

        Tries in order:
        1. Today's cached result (avoid redundant API calls within same session)
        2. Live calculation from 20 days of daily closes via data_provider
        3. Static KNOWN_CORRELATIONS lookup
        4. Sector-based estimate fallback

        Args:
            symbol1: First symbol
            symbol2: Second symbol

        Returns:
            Correlation coefficient (-1.0 to 1.0)
        """
        pair = tuple(sorted([symbol1, symbol2]))
        today_str = date.today().isoformat()
        cache_key = (pair[0], pair[1], today_str)

        # 1. Return cached result if available for today
        if cache_key in self._correlation_cache:
            return self._correlation_cache[cache_key]

        # 2. Try live calculation from historical closes
        if self.data_provider is not None:
            try:
                corr = self._calculate_live_correlation(symbol1, symbol2, days=20)
                if corr is not None:
                    self._correlation_cache[cache_key] = corr
                    logger.debug(f"Live correlation {symbol1}/{symbol2}: {corr:.3f}")
                    return corr
            except Exception as e:
                logger.debug(f"Live correlation failed for {symbol1}/{symbol2}: {e}")

        # 3. Static known correlations
        if pair in self.KNOWN_CORRELATIONS:
            return self.KNOWN_CORRELATIONS[pair]

        # 4. Sector-based estimate
        return self._estimate_correlation(symbol1, symbol2)

    def _calculate_live_correlation(self, symbol1: str, symbol2: str, days: int = 20) -> Optional[float]:
        """
        Calculate Pearson correlation from recent daily closing prices.

        Args:
            symbol1: First symbol
            symbol2: Second symbol
            days: Number of trading days of history to use

        Returns:
            Pearson r (-1.0 to 1.0), or None if insufficient data
        """
        # Fetch with extra buffer days to ensure we get enough trading days
        fetch_days = days + 10

        bars1 = self.data_provider.get_historical_data(symbol1, days_back=fetch_days, timeframe="1D")
        bars2 = self.data_provider.get_historical_data(symbol2, days_back=fetch_days, timeframe="1D")

        if not bars1 or not bars2:
            return None

        # Extract closing prices aligned by position (most recent N bars)
        closes1 = [b["close"] for b in bars1 if b.get("close")]
        closes2 = [b["close"] for b in bars2 if b.get("close")]

        # Use the overlap length, capped at days
        n = min(len(closes1), len(closes2), days)
        if n < 5:
            return None  # Not enough data for meaningful correlation

        # Use the most recent n bars
        c1 = closes1[-n:]
        c2 = closes2[-n:]

        # Compute Pearson r from returns (more meaningful than raw price correlation)
        if len(c1) < 2 or len(c2) < 2:
            return None

        # Daily returns
        r1 = [(c1[i] - c1[i-1]) / c1[i-1] for i in range(1, len(c1)) if c1[i-1] != 0]
        r2 = [(c2[i] - c2[i-1]) / c2[i-1] for i in range(1, len(c2)) if c2[i-1] != 0]

        n_ret = min(len(r1), len(r2))
        if n_ret < 4:
            return None

        r1 = r1[-n_ret:]
        r2 = r2[-n_ret:]

        mean1 = sum(r1) / n_ret
        mean2 = sum(r2) / n_ret

        cov = sum((r1[i] - mean1) * (r2[i] - mean2) for i in range(n_ret)) / n_ret
        std1 = math.sqrt(sum((x - mean1) ** 2 for x in r1) / n_ret)
        std2 = math.sqrt(sum((x - mean2) ** 2 for x in r2) / n_ret)

        if std1 == 0 or std2 == 0:
            return None

        pearson_r = cov / (std1 * std2)
        # Clamp to [-1, 1] to handle floating point edge cases
        return max(-1.0, min(1.0, pearson_r))

    def _estimate_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Estimate correlation based on sector/industry.

        This is a simplified heuristic. In production, would calculate from historical data.

        Args:
            symbol1: First symbol
            symbol2: Second symbol

        Returns:
            Estimated correlation
        """
        # Sector groupings
        tech_giants = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']
        semiconductors = ['NVDA', 'AMD', 'INTC', 'TSM', 'AVGO', 'QCOM']
        ev_auto = ['TSLA', 'RIVN', 'LCID', 'F', 'GM']
        finance = ['JPM', 'BAC', 'WFC', 'GS', 'MS']
        healthcare = ['JNJ', 'UNH', 'PFE', 'ABBV', 'MRK']
        indices = ['SPY', 'QQQ', 'DIA', 'IWM']

        sectors = [tech_giants, semiconductors, ev_auto, finance, healthcare, indices]

        # Check if both in same sector
        for sector in sectors:
            if symbol1 in sector and symbol2 in sector:
                return 0.65  # Moderate-high correlation within sector

        # Check if one is an index
        if symbol1 in indices or symbol2 in indices:
            return 0.50  # Moderate correlation with market

        # Different sectors - assume low correlation
        return 0.30

    def _calculate_diversification_score(self, correlation_matrix: Dict[str, float],
                                        position_count: int) -> float:
        """
        Calculate portfolio diversification score (0-100).

        Higher score = better diversification

        Args:
            correlation_matrix: Pairwise correlations
            position_count: Number of positions

        Returns:
            Diversification score 0-100
        """
        if position_count == 1:
            return 100  # Single position, no correlation

        if not correlation_matrix:
            return 100

        # Average correlation across all pairs
        correlations = list(correlation_matrix.values())
        avg_correlation = sum(correlations) / len(correlations)

        # Base score from average correlation
        # Low correlation = high score
        base_score = (1 - avg_correlation) * 100

        # Bonus for number of positions (more positions = better diversity, up to a point)
        position_bonus = min(20, position_count * 2)

        # Penalty for highly correlated pairs
        high_corr_pairs = sum(1 for c in correlations if c >= 0.7)
        high_corr_penalty = high_corr_pairs * 10

        final_score = base_score + position_bonus - high_corr_penalty

        return max(0, min(100, final_score))

    def _generate_correlation_analysis(self, position_count: int,
                                       high_corr_count: int,
                                       diversification_score: float) -> str:
        """
        Generate human-readable correlation analysis.

        Args:
            position_count: Number of positions
            high_corr_count: Number of highly correlated pairs
            diversification_score: Diversification score

        Returns:
            Analysis text
        """
        if position_count == 1:
            return "Portfolio has single position - no correlation risk."

        analysis_parts = [
            f"Portfolio has {position_count} positions."
        ]

        if high_corr_count == 0:
            analysis_parts.append("No highly correlated pairs detected. Good diversification.")
        else:
            analysis_parts.append(
                f"Warning: {high_corr_count} highly correlated pair(s) detected. "
                "Portfolio may experience amplified moves."
            )

        # Score interpretation
        if diversification_score >= 80:
            analysis_parts.append("Excellent diversification.")
        elif diversification_score >= 60:
            analysis_parts.append("Good diversification.")
        elif diversification_score >= 40:
            analysis_parts.append("Moderate diversification - consider reducing correlated positions.")
        else:
            analysis_parts.append("Poor diversification - high concentration risk.")

        return " ".join(analysis_parts)

    def get_sector_exposure(self, positions: Dict[str, int]) -> Dict[str, List[str]]:
        """
        Categorize positions by sector.

        Args:
            positions: Dictionary of {symbol: quantity}

        Returns:
            Dictionary of {sector: [symbols]}
        """
        sector_map = {
            'Technology': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'AMD', 'INTC'],
            'Semiconductors': ['NVDA', 'AMD', 'INTC', 'TSM', 'AVGO', 'QCOM', 'MU'],
            'EV/Auto': ['TSLA', 'RIVN', 'LCID', 'F', 'GM'],
            'Finance': ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'C'],
            'Healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV', 'MRK'],
            'Energy': ['XOM', 'CVX', 'COP', 'SLB'],
            'Indices': ['SPY', 'QQQ', 'DIA', 'IWM']
        }

        exposure = defaultdict(list)
        for symbol in positions.keys():
            categorized = False
            for sector, symbols in sector_map.items():
                if symbol in symbols:
                    exposure[sector].append(symbol)
                    categorized = True
                    break
            if not categorized:
                exposure['Other'].append(symbol)

        return dict(exposure)


def analyze_portfolio_correlation(positions: Dict[str, int], data_provider=None) -> Dict[str, Any]:
    """
    Convenience function to analyze portfolio correlation.

    Args:
        positions: Dictionary of {symbol: quantity}
        data_provider: Market data provider

    Returns:
        Correlation analysis dictionary
    """
    tracker = CorrelationTracker(data_provider)
    return tracker.analyze_portfolio_correlation(positions)
