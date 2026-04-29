"""
Backtest Engine - Phase 4 of Backtest Validation System

Validates learned patterns by testing them against historical trade data.

The engine:
1. Takes a pattern (e.g., "conviction >= 9 + news catalyst")
2. Scans historical trades from the database
3. Identifies which trades match the pattern conditions
4. Calculates performance metrics (win rate, avg return, Sharpe ratio)
5. Returns validation results with confidence score

Usage:
    engine = BacktestEngine(learning_db)
    pattern = {
        'conditions': {
            'conviction': '>=9',
            'catalyst_type': 'news'
        }
    }
    result = engine.backtest_pattern(pattern, days_lookback=30)
"""

import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import math

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Validates trading patterns by backtesting them against historical trades.
    """
    
    def __init__(self, learning_db):
        """
        Initialize the backtest engine.
        
        Args:
            learning_db: LearningDatabase instance with trade history
        """
        self.learning_db = learning_db
    
    def backtest_pattern(self, pattern: Dict, days_lookback: int = 30,
                        min_sample_size: int = 5) -> Dict:
        """
        Backtest a pattern against historical trades.
        
        Args:
            pattern: Pattern dictionary with 'conditions' and 'hypothesis'
            days_lookback: How many days of history to test (default: 30)
            min_sample_size: Minimum trades needed for valid backtest
        
        Returns:
            Dictionary with backtest results:
            {
                'sample_size': int,
                'win_rate': float,
                'avg_return': float,
                'median_return': float,
                'max_return': float,
                'max_loss': float,
                'sharpe_ratio': float,
                'matched_trades': list,
                'validation_date': str,
                'lookback_days': int,
                'pattern_summary': str
            }
        """
        logger.info(f"Starting backtest for pattern: {pattern.get('hypothesis', 'N/A')}")
        logger.info(f"  Lookback: {days_lookback} days")
        
        # Get historical trades
        cutoff_date = (datetime.now() - timedelta(days=days_lookback)).strftime('%Y-%m-%d')
        historical_trades = self._fetch_historical_trades(cutoff_date)
        
        logger.info(f"  Fetched {len(historical_trades)} historical trades since {cutoff_date}")
        
        if not historical_trades:
            logger.warning("No historical trades found for backtest")
            return self._empty_result(pattern, days_lookback)
        
        # Match trades against pattern conditions
        matched_trades = self._match_trades_to_pattern(historical_trades, pattern)
        
        logger.info(f"  Matched {len(matched_trades)} trades to pattern")
        
        if len(matched_trades) < min_sample_size:
            logger.warning(f"Insufficient sample size: {len(matched_trades)} < {min_sample_size}")
            return self._insufficient_data_result(pattern, matched_trades, days_lookback)
        
        # Calculate performance metrics
        metrics = self._calculate_metrics(matched_trades)
        
        # Build result
        result = {
            'sample_size': len(matched_trades),
            'win_rate': metrics['win_rate'],
            'avg_return': metrics['avg_return'],
            'median_return': metrics['median_return'],
            'max_return': metrics['max_return'],
            'max_loss': metrics['max_loss'],
            'sharpe_ratio': metrics['sharpe_ratio'],
            'total_return': metrics['total_return'],
            'matched_trades': [t['id'] for t in matched_trades],
            'matched_symbols': list(set(t['symbol'] for t in matched_trades)),
            'validation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'lookback_days': days_lookback,
            'pattern_summary': pattern.get('hypothesis', 'Unknown pattern')
        }
        
        # Log results
        logger.info(f"Backtest complete:")
        logger.info(f"  Sample size: {result['sample_size']}")
        logger.info(f"  Win rate: {result['win_rate']:.1%}")
        logger.info(f"  Avg return: {result['avg_return']:.2%}")
        logger.info(f"  Sharpe ratio: {result['sharpe_ratio']:.2f}")
        
        return result
    
    def _fetch_historical_trades(self, cutoff_date: str) -> List[Dict]:
        """Fetch trades from database since cutoff date."""
        try:
            with self.learning_db._get_connection() as conn:
                cursor = conn.cursor()
                
                # Query trades from trade_journal (aliases map actual column names to expected keys)
                cursor.execute("""
                    SELECT
                        id, symbol, entry_time, exit_time,
                        entry_price, exit_price,
                        shares AS quantity,
                        actual_pnl AS pnl,
                        CASE WHEN entry_price > 0 AND shares > 0
                             THEN (actual_pnl / (entry_price * shares)) * 100
                             ELSE 0 END AS pnl_percent,
                        'CLOSED' AS status,
                        confidence_level AS conviction,
                        catalyst,
                        why_entered AS reasoning,
                        setup_type AS entry_signal,
                        exit_reason AS exit_signal
                    FROM trade_journal
                    WHERE date(exit_time) >= date(?)
                    AND exit_price IS NOT NULL
                    ORDER BY exit_time ASC
                """, (cutoff_date,))
                
                columns = [desc[0] for desc in cursor.description]
                trades = []
                
                for row in cursor.fetchall():
                    trade = dict(zip(columns, row))
                    
                    # Parse JSON fields if present
                    if trade.get('entry_signal'):
                        try:
                            trade['entry_signal'] = json.loads(trade['entry_signal'])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    if trade.get('exit_signal'):
                        try:
                            trade['exit_signal'] = json.loads(trade['exit_signal'])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    trades.append(trade)
                
                return trades
            
        except Exception as e:
            logger.error(f"Error fetching historical trades: {e}")
            return []
    
    def _match_trades_to_pattern(self, trades: List[Dict], 
                                 pattern: Dict) -> List[Dict]:
        """
        Match trades against pattern conditions.
        
        Returns list of trades that satisfy ALL conditions in the pattern.
        """
        conditions = pattern.get('conditions', {})
        
        if not conditions:
            logger.warning("Pattern has no conditions to match")
            return []
        
        matched_trades = []
        
        for trade in trades:
            if self._trade_matches_conditions(trade, conditions):
                matched_trades.append(trade)
        
        return matched_trades
    
    def _trade_matches_conditions(self, trade: Dict, 
                                  conditions: Dict) -> bool:
        """
        Check if a single trade matches all pattern conditions.
        
        Conditions can be:
        - conviction: '>=9', '==8', etc.
        - catalyst_type: 'news', 'earnings', 'momentum', etc.
        - catalyst: substring match (e.g., 'news' in catalyst string)
        - daily_trend: 'uptrend', 'downtrend'
        - time_of_day: 'morning', 'afternoon'
        - entry_type: 'breakout', 'pullback', etc.
        - pattern: 'gap_fade', etc. (for avoid patterns)
        """
        for condition_name, condition_value in conditions.items():
            
            if condition_name == 'conviction':
                if not self._check_numeric_condition(
                    trade.get('conviction'), condition_value
                ):
                    return False
            
            elif condition_name == 'catalyst_type':
                catalyst = str(trade.get('catalyst', '')).lower()
                if condition_value.lower() not in catalyst:
                    return False
            
            elif condition_name == 'catalyst':
                catalyst = str(trade.get('catalyst', '')).lower()
                if condition_value.lower() not in catalyst:
                    return False
            
            elif condition_name == 'daily_trend':
                # Check if reasoning mentions trend
                reasoning = str(trade.get('reasoning', '')).lower()
                if condition_value.lower() not in reasoning:
                    # Also check entry_signal if available
                    entry_signal = trade.get('entry_signal', {})
                    if isinstance(entry_signal, dict):
                        trend = entry_signal.get('trend', '').lower()
                        if condition_value.lower() not in trend:
                            return False
                    else:
                        return False
            
            elif condition_name == 'time_of_day':
                # Parse entry time to check if morning/afternoon
                entry_time = trade.get('entry_time')
                if entry_time:
                    try:
                        time_obj = datetime.fromisoformat(entry_time)
                        hour = time_obj.hour
                        
                        if condition_value == 'morning' and hour >= 12:
                            return False
                        elif condition_value == 'afternoon' and hour < 12:
                            return False
                    except (ValueError, TypeError):
                        return False
            
            elif condition_name == 'entry_type':
                reasoning = str(trade.get('reasoning', '')).lower()
                if condition_value.lower() not in reasoning:
                    return False
            
            elif condition_name == 'pattern':
                # For avoid patterns (e.g., 'gap_fade')
                reasoning = str(trade.get('reasoning', '')).lower()
                catalyst = str(trade.get('catalyst', '')).lower()
                
                if condition_value.lower() not in reasoning and \
                   condition_value.lower() not in catalyst:
                    return False
            
            elif condition_name == 'gap_size':
                # Check if trade involved a gap
                reasoning = str(trade.get('reasoning', '')).lower()
                if 'gap' not in reasoning:
                    return False
            
            elif condition_name in ['daily_return_10d', 'momentum']:
                # These would require historical data - for now, skip
                # (Could be enhanced in future with historical data fetcher)
                continue
            
            elif condition_name == 'volume':
                # Volume conditions - would need historical data
                continue
            
            elif condition_name == 'market_regime':
                # Would need market context - skip for now
                continue
            
            else:
                # Unknown condition - log warning but don't fail
                logger.debug(f"Unknown condition: {condition_name}")
                continue
        
        # All conditions passed
        return True
    
    def _check_numeric_condition(self, value: Any, condition: str) -> bool:
        """
        Check if a numeric value satisfies a condition.
        
        Examples:
        - value=9, condition='>=9' → True
        - value=8, condition='>=9' → False
        - value=7, condition='==7' → True
        """
        if value is None:
            return False
        
        try:
            value = float(value)
        except (ValueError, TypeError):
            return False
        
        # Parse condition (e.g., '>=9', '==8', '<5')
        condition = condition.strip()
        
        if condition.startswith('>='):
            threshold = float(condition[2:])
            return value >= threshold
        elif condition.startswith('<='):
            threshold = float(condition[2:])
            return value <= threshold
        elif condition.startswith('>'):
            threshold = float(condition[1:])
            return value > threshold
        elif condition.startswith('<'):
            threshold = float(condition[1:])
            return value < threshold
        elif condition.startswith('=='):
            threshold = float(condition[2:])
            return abs(value - threshold) < 0.001  # Float comparison
        else:
            # Try direct comparison
            try:
                threshold = float(condition)
                return abs(value - threshold) < 0.001
            except ValueError:
                return False
    
    def _calculate_metrics(self, trades: List[Dict]) -> Dict:
        """
        Calculate performance metrics for matched trades.
        
        Returns:
        - win_rate: Percentage of winning trades
        - avg_return: Average return per trade
        - median_return: Median return
        - max_return: Best trade
        - max_loss: Worst trade
        - sharpe_ratio: Risk-adjusted return
        - total_return: Sum of all returns
        """
        if not trades:
            return self._zero_metrics()
        
        returns = []
        wins = 0
        
        for trade in trades:
            pnl_pct = trade.get('pnl_percent')
            
            if pnl_pct is None:
                # Calculate from prices if not stored
                entry = trade.get('entry_price')
                exit_price = trade.get('exit_price')
                
                if entry and exit_price and entry > 0:
                    pnl_pct = ((exit_price - entry) / entry) * 100
                else:
                    continue
            
            returns.append(pnl_pct / 100)  # Convert to decimal
            
            if pnl_pct > 0:
                wins += 1
        
        if not returns:
            return self._zero_metrics()
        
        # Calculate metrics
        win_rate = wins / len(returns)
        avg_return = sum(returns) / len(returns)
        total_return = sum(returns)
        
        # Median
        sorted_returns = sorted(returns)
        n = len(sorted_returns)
        if n % 2 == 0:
            median_return = (sorted_returns[n//2 - 1] + sorted_returns[n//2]) / 2
        else:
            median_return = sorted_returns[n//2]
        
        max_return = max(returns)
        max_loss = min(returns)
        
        # Sharpe ratio (annualized, assuming ~250 trading days)
        if len(returns) > 1:
            std_dev = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1))
            
            if std_dev > 0:
                # Annualize: sqrt(252) * (avg / std)
                sharpe_ratio = (avg_return / std_dev) * math.sqrt(252)
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0
        
        return {
            'win_rate': win_rate,
            'avg_return': avg_return,
            'median_return': median_return,
            'max_return': max_return,
            'max_loss': max_loss,
            'sharpe_ratio': sharpe_ratio,
            'total_return': total_return
        }
    
    def _zero_metrics(self) -> Dict:
        """Return zero metrics when no data."""
        return {
            'win_rate': 0.0,
            'avg_return': 0.0,
            'median_return': 0.0,
            'max_return': 0.0,
            'max_loss': 0.0,
            'sharpe_ratio': 0.0,
            'total_return': 0.0
        }
    
    def _empty_result(self, pattern: Dict, lookback_days: int) -> Dict:
        """Return empty result when no trades found."""
        return {
            'sample_size': 0,
            'win_rate': 0.0,
            'avg_return': 0.0,
            'median_return': 0.0,
            'max_return': 0.0,
            'max_loss': 0.0,
            'sharpe_ratio': 0.0,
            'total_return': 0.0,
            'matched_trades': [],
            'matched_symbols': [],
            'validation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'lookback_days': lookback_days,
            'pattern_summary': pattern.get('hypothesis', 'Unknown pattern'),
            'error': 'No historical trades found'
        }
    
    def _insufficient_data_result(self, pattern: Dict, matched_trades: List[Dict],
                                  lookback_days: int) -> Dict:
        """Return result when sample size too small."""
        metrics = self._calculate_metrics(matched_trades) if matched_trades else self._zero_metrics()
        
        return {
            'sample_size': len(matched_trades),
            'win_rate': metrics['win_rate'],
            'avg_return': metrics['avg_return'],
            'median_return': metrics['median_return'],
            'max_return': metrics['max_return'],
            'max_loss': metrics['max_loss'],
            'sharpe_ratio': metrics['sharpe_ratio'],
            'total_return': metrics['total_return'],
            'matched_trades': [t['id'] for t in matched_trades],
            'matched_symbols': list(set(t['symbol'] for t in matched_trades)),
            'validation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'lookback_days': lookback_days,
            'pattern_summary': pattern.get('hypothesis', 'Unknown pattern'),
            'error': 'Insufficient sample size for validation'
        }
    
    def calculate_confidence_score(self, backtest_results: Dict, num_patterns_tested: int = 1) -> str:
        """
        Calculate confidence level based on backtest results with statistical significance testing.

        Uses binomial test to verify that win rate is statistically significantly better than
        random chance (50%). This prevents promoting patterns that just got lucky.

        Applies Bonferroni correction for multiple testing when testing many patterns at once:
        - If testing N patterns, p-value thresholds are divided by N
        - This controls the family-wise error rate (prevents false positives)

        Confidence levels:
        - HIGH: Strong evidence (65%+ win rate, 3%+ avg return, 10+ trades, p<0.05/N)
        - MEDIUM: Moderate evidence (55%+ win rate, 2%+ avg return, 7+ trades, p<0.10/N)
        - LOW: Weak evidence (50%+ win rate, 5+ trades, but not statistically significant)
        - REJECTED: Failed validation or not statistically different from random

        Args:
            backtest_results: Results from backtest_pattern()
            num_patterns_tested: Number of patterns tested simultaneously (for Bonferroni correction)
                               Default is 1 (no correction). Pass actual count when batch testing.

        Returns:
            Confidence string: 'HIGH', 'MEDIUM', 'LOW', or 'REJECTED'
        """
        sample_size = backtest_results.get('sample_size', 0)
        win_rate = backtest_results.get('win_rate', 0)
        avg_return = backtest_results.get('avg_return', 0)
        sharpe_ratio = backtest_results.get('sharpe_ratio', 0)
        max_loss = backtest_results.get('max_loss', 0)
        
        # Minimum sample size check
        if sample_size < 5:
            logger.info(f"Confidence: REJECTED - Insufficient sample size ({sample_size} < 5)")
            return 'REJECTED'
        
        # Check for catastrophic losses (even with good win rate)
        if max_loss < -0.15:  # -15% max loss
            logger.info(f"Confidence: REJECTED - Catastrophic max loss ({max_loss:.1%})")
            return 'REJECTED'
        
        # Statistical significance test: Is this win rate significantly better than 50%?
        p_value = self._binomial_test(sample_size, win_rate)

        # Apply Bonferroni correction for multiple testing
        # Divide significance thresholds by number of patterns tested
        alpha_high = 0.05 / num_patterns_tested if num_patterns_tested > 1 else 0.05
        alpha_medium = 0.10 / num_patterns_tested if num_patterns_tested > 1 else 0.10
        alpha_low = 0.20 / num_patterns_tested if num_patterns_tested > 1 else 0.20

        if num_patterns_tested > 1:
            logger.info(f"  Bonferroni correction: Testing {num_patterns_tested} patterns, "
                       f"alpha_high={alpha_high:.4f}, alpha_medium={alpha_medium:.4f}")

        logger.info(f"  Statistical test: win_rate={win_rate:.1%}, n={sample_size}, p-value={p_value:.4f}")

        # HIGH confidence requires both strong performance AND statistical significance
        if (win_rate >= 0.65 and avg_return >= 0.03 and sample_size >= 10):
            if p_value < alpha_high:  # Statistically significant at corrected level
                # Additional boost from Sharpe
                if sharpe_ratio >= 2.0:
                    logger.info(f"Confidence: HIGH - Excellent metrics + Sharpe {sharpe_ratio:.2f} (p={p_value:.4f})")
                    return 'HIGH'
                logger.info(f"Confidence: HIGH - Strong performance with statistical significance (p={p_value:.4f})")
                return 'HIGH'
            else:
                # Good performance but not statistically significant - downgrade
                logger.info(f"Confidence: MEDIUM - Good metrics but not statistically significant (p={p_value:.4f})")
                return 'MEDIUM'
        
        # MEDIUM confidence requires moderate performance AND reasonable significance
        if (win_rate >= 0.55 and avg_return >= 0.02 and sample_size >= 7):
            if p_value < alpha_medium:  # Statistically significant at corrected level
                logger.info(f"Confidence: MEDIUM - Good performance with moderate significance (p={p_value:.4f})")
                return 'MEDIUM'
            else:
                # Marginal performance, not significant
                logger.info(f"Confidence: LOW - Marginal performance, not statistically significant (p={p_value:.4f})")
                return 'LOW'
        
        # LOW confidence - pattern shows promise but needs more evidence
        if (win_rate >= 0.50 and sample_size >= 5):
            if p_value < alpha_low:  # Shows some promise (corrected threshold)
                logger.info(f"Confidence: LOW - Shows promise but needs more evidence (p={p_value:.4f})")
                return 'LOW'
            else:
                logger.info(f"Confidence: REJECTED - Not distinguishable from random (p={p_value:.4f})")
                return 'REJECTED'
        
        # Failed validation
        logger.info(f"Confidence: REJECTED - Poor performance (WR: {win_rate:.1%}, Ret: {avg_return:.2%}, p={p_value:.4f})")
        return 'REJECTED'
    
    def _binomial_test(self, n: int, observed_win_rate: float) -> float:
        """
        Perform binomial test to check if win rate is significantly better than 50%.
        
        H0: True win rate = 0.50 (random/coin flip)
        H1: True win rate > 0.50 (pattern has edge)
        
        Args:
            n: Sample size (number of trades)
            observed_win_rate: Observed win rate (0.0 to 1.0)
        
        Returns:
            p-value: Probability of observing this win rate by chance
                    Lower p-value = more confident pattern is not random
        """
        try:
            from scipy.stats import binomtest
            
            # Calculate number of wins
            k_wins = int(round(n * observed_win_rate))
            
            # Test if significantly better than 50/50
            # alternative='greater' tests if win rate is significantly > 0.5
            result = binomtest(k_wins, n, p=0.5, alternative='greater')
            
            return result.pvalue
            
        except ImportError:
            # scipy not available - fall back to conservative approximation
            logger.warning("scipy not available, using conservative approximation for p-value")
            return self._approximate_binomial_pvalue(n, observed_win_rate)
        except Exception as e:
            logger.error(f"Error in binomial test: {e}")
            # Return conservative p-value (not significant)
            return 1.0
    
    def _approximate_binomial_pvalue(self, n: int, observed_win_rate: float) -> float:
        """
        Conservative approximation of binomial p-value when scipy not available.
        
        Uses normal approximation to binomial for n >= 5.
        """
        try:
            # Normal approximation: z = (p - 0.5) / sqrt(0.5 * 0.5 / n)
            if n < 5:
                return 1.0  # Too small for approximation
            
            z_score = (observed_win_rate - 0.5) / math.sqrt(0.25 / n)
            
            # Approximate p-value from z-score (one-tailed)
            # Using rough approximation: p ≈ 0.5 * exp(-z^2/2) for z > 0
            if z_score <= 0:
                return 1.0  # Win rate not better than 50%
            
            # Conservative approximation
            p_value = 0.5 * math.exp(-z_score**2 / 2)
            
            return min(1.0, p_value)
            
        except Exception as e:
            logger.error(f"Error in p-value approximation: {e}")
            return 1.0  # Conservative: assume not significant


# Convenience function for quick backtesting
def backtest_pattern(learning_db, pattern: Dict, days_lookback: int = 30) -> Dict:
    """
    Convenience function to backtest a pattern.
    
    Usage:
        from analytics.backtest_engine import backtest_pattern
        
        result = backtest_pattern(learning_db, {
            'conditions': {'conviction': '>=9'},
            'hypothesis': 'High conviction trades work'
        })
    """
    engine = BacktestEngine(learning_db)
    return engine.backtest_pattern(pattern, days_lookback)