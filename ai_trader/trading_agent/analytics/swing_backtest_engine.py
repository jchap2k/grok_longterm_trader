"""
SwingBacktestEngine - Validates swing patterns against historical trade data.

Key difference from intraday backtest_engine.py:
  - Matches on entry_catalyst, hold_type, vix_mode, exit_reason
  - Queries trade_journal WHERE hold_duration='swing'
  - Returns win_rate, avg_return, avg_hold_days per pattern

Usage:
    engine = SwingBacktestEngine(learning_db)
    result = engine.backtest_pattern({
        "conditions": {"entry_catalyst": "FORCESWING", "vix_mode": "standard"}
    })
"""

from typing import Dict, List
import logging
import math

logger = logging.getLogger(__name__)


class SwingBacktestEngine:

    MIN_SAMPLE_SIZE = 10  # minimum trades needed for statistical validity

    def __init__(self, learning_db):
        """
        Args:
            learning_db: LearningDatabase instance with get_swing_trades() method
        """
        self.db = learning_db

    def backtest_pattern(
        self,
        pattern: dict,
        days_lookback: int = 120,
        min_sample_size: int = None,
    ) -> dict:
        """
        Validate a swing pattern against historical swing trades.

        Args:
            pattern:        Dict with "conditions" key mapping field->value
            days_lookback:  How far back to look in trade_journal (120 days default - swing trades
                            close infrequently so a longer window is needed for sample adequacy)
            min_sample_size: Minimum trades required (default: MIN_SAMPLE_SIZE)

        Returns:
            dict with: valid (bool), sample_size, win_rate, avg_return,
                       max_return, max_loss, avg_hold_days, top_catalysts, reason
        """
        if min_sample_size is None:
            min_sample_size = self.MIN_SAMPLE_SIZE

        trades  = self.db.get_swing_trades(days_lookback=days_lookback)
        matched = self._match_trades(trades, pattern.get("conditions", {}))

        if len(matched) < min_sample_size:
            return {
                "valid":       False,
                "reason":      f"insufficient sample: {len(matched)}/{min_sample_size}",
                "sample_size": len(matched),
            }

        returns    = [float(t.get("pnl_pct_net", 0) or 0) for t in matched]
        wins       = [r for r in returns if r > 0]
        hold_days  = [int(t.get("hold_days", 0) or 0) for t in matched]
        win_rate   = len(wins) / len(returns)
        p_value    = self._binomial_pvalue(len(matched), win_rate)

        return {
            "valid":           True,
            "sample_size":     len(matched),
            "win_rate":        win_rate,
            "avg_return":      sum(returns) / len(returns),
            "max_return":      max(returns),
            "max_loss":        min(returns),
            "avg_hold_days":   sum(hold_days) / len(hold_days) if hold_days else 0,
            "top_catalysts":   self._count_catalysts(matched),
            "p_value":         round(p_value, 4),
            "significant":     p_value < 0.05,
            "reason":          "sufficient sample",
        }

    def _match_trades(self, trades: list, conditions: dict) -> list:
        """Return trades where ALL conditions match."""
        if not conditions:
            return trades
        matched = []
        for t in trades:
            if all(str(t.get(k, "")) == str(v) for k, v in conditions.items()):
                matched.append(t)
        return matched

    @staticmethod
    def _binomial_pvalue(n: int, observed_win_rate: float) -> float:
        """
        One-sided binomial test: probability of observing >= this many wins
        under H0 that the true win rate is 0.50 (coin-flip baseline).
        Returns p-value in [0, 1]. Lower = more significant (p < 0.05 is significant).
        """
        k_wins = int(round(n * observed_win_rate))
        try:
            from scipy.stats import binomtest
            result = binomtest(k_wins, n, p=0.5, alternative='greater')
            return float(result.pvalue)
        except ImportError:
            pass
        except Exception:
            pass
        # Fallback: normal approximation (accurate for n >= 10)
        if n < 5:
            return 1.0
        p0  = 0.5
        z   = (observed_win_rate - p0) / (p0 * (1.0 - p0) / n) ** 0.5
        if z <= 0:
            return 1.0
        return min(1.0, 0.5 * math.exp(-z * z / 2.0))

    @staticmethod
    def _count_catalysts(trades: list) -> dict:
        """Count occurrences of each entry_catalyst, sorted descending."""
        counts: Dict[str, int] = {}
        for t in trades:
            c = t.get("entry_catalyst", "unknown")
            counts[c] = counts.get(c, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))
