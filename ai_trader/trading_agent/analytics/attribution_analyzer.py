"""
Attribution Analyzer - Enhanced Queries on attribution_metrics

Phase 2A implementation following Grok's simplified approach.
Uses existing attribution_metrics table instead of creating pattern_performance table.

Key features per Grok's recommendations:
- Profit factor (≥1.5) as primary metric
- Expectancy (≥+0.3% per trade) as secondary
- 30-trade minimum for VALIDATED status
- Bonferroni correction for multiple testing
- Top 10 patterns by profit factor
"""

import sqlite3
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from scipy import stats
import math

logger = logging.getLogger(__name__)


@dataclass
class PatternMetrics:
    """Metrics for a validated pattern."""
    pattern_id: str  # e.g., "news_catalyst+open+trending_up"
    pattern_description: str

    # Dimension values
    strategy_type: Optional[str] = None
    entry_time_bucket: Optional[str] = None
    market_regime: Optional[str] = None
    conviction_level: Optional[str] = None
    exit_type: Optional[str] = None
    symbol: Optional[str] = None

    # Core metrics
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0

    # Grok's primary metrics
    profit_factor: float = 0.0  # total_wins / abs(total_losses)
    expectancy: float = 0.0     # win_rate × avg_win - loss_rate × avg_loss

    # P&L
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    avg_net_pnl: float = 0.0

    # Statistical significance
    p_value: float = 1.0
    is_significant: bool = False

    # Validation status
    status: str = "INSUFFICIENT_DATA"  # INSUFFICIENT_DATA, PROVISIONAL, VALIDATED, DEGRADED

    # Period
    first_trade_date: Optional[date] = None
    last_trade_date: Optional[date] = None


class AttributionAnalyzer:
    """
    Analyzes attribution data using enhanced queries on attribution_metrics.

    Phase 2A simplified approach - no new tables, just smart queries.
    """

    def __init__(self, db_path: str = None):
        """
        Initialize analyzer.

        Args:
            db_path: Path to trading_performance.db (default: auto-detect)
        """
        if db_path is None:
            script_dir = Path(__file__).parent
            db_path = script_dir.parent.parent.parent / "trading_performance.db"

        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)

        # Grok's validation thresholds
        self.MIN_PROVISIONAL = 10  # trades
        self.MIN_VALIDATED = 30    # trades (Grok: was 20, now 30)
        self.MIN_PROFIT_FACTOR = 1.5  # Grok's primary metric
        self.MIN_EXPECTANCY = 0.003   # +0.3% per trade
        self.MIN_WIN_RATE = 0.55      # 55% (Grok: was 50%, now 55%)
        self.ALPHA = 0.05             # Statistical significance

    def get_validated_patterns(
        self,
        min_trades: int = None,
        period_start: date = None,
        period_end: date = None,
        top_n: int = 10  # Grok: limit to top 10 for token budget
    ) -> List[PatternMetrics]:
        """
        Get validated patterns using enhanced queries.

        Per Grok's recommendations:
        - Primary: profit_factor ≥ 1.5
        - Secondary: expectancy ≥ +0.3%
        - Tertiary: win_rate ≥ 55%
        - Minimum: 30 trades
        - Statistical: p < 0.05 (binomial test)

        Args:
            min_trades: Minimum trades (default: 30 for VALIDATED)
            period_start: Filter by date range
            period_end: Filter by date range
            top_n: Return top N by profit factor (default 10)

        Returns:
            List of validated patterns, ranked by profit factor
        """
        if min_trades is None:
            min_trades = self.MIN_VALIDATED

        cursor = self.conn.cursor()

        # Query attribution_metrics for dimension combinations
        # We aggregate across period to get cumulative metrics
        query = """
            SELECT
                strategy_type,
                entry_time_bucket,
                market_regime,
                conviction_level,
                exit_type,
                symbol,
                SUM(trade_count) as total_trades,
                SUM(win_count) as total_wins,
                SUM(loss_count) as total_losses,
                SUM(gross_pnl) as total_gross_pnl,
                SUM(net_pnl) as total_net_pnl,
                MIN(period) as first_date,
                MAX(period) as last_date
            FROM attribution_metrics
            WHERE period_type = 'daily'
        """

        params = []
        if period_start:
            query += " AND period >= ?"
            params.append(period_start.isoformat())
        if period_end:
            query += " AND period <= ?"
            params.append(period_end.isoformat())

        # Group by dimension combinations (non-null dimensions only)
        query += """
            GROUP BY strategy_type, entry_time_bucket, market_regime,
                     conviction_level, exit_type, symbol
            HAVING total_trades >= ?
        """
        params.append(min_trades)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        patterns = []
        for row in rows:
            (strategy_type, entry_time_bucket, market_regime, conviction_level,
             exit_type, symbol, total_trades, total_wins, total_losses,
             total_gross_pnl, total_net_pnl, first_date, last_date) = row

            # Calculate metrics
            win_rate = total_wins / total_trades if total_trades > 0 else 0.0

            # Get detailed trade data to calculate avg win/loss and profit factor
            trade_details = self._get_trade_details_for_pattern(
                strategy_type, entry_time_bucket, market_regime,
                conviction_level, exit_type, symbol,
                period_start, period_end
            )

            if not trade_details:
                continue

            wins = [t for t in trade_details if t > 0]
            losses = [t for t in trade_details if t <= 0]

            avg_win = sum(wins) / len(wins) if wins else 0.0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

            # Grok's primary metric: Profit factor
            total_win_pnl = sum(wins)
            total_loss_pnl = abs(sum(losses))
            profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else 0.0

            # Grok's secondary metric: Expectancy
            expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
            expectancy_pct = expectancy / abs(avg_loss) if avg_loss > 0 else 0.0

            # Statistical significance (binomial test vs 50% win rate)
            p_value = self._binomial_test(total_wins, total_trades, 0.50)

            # Bonferroni correction (divide alpha by number of patterns tested)
            # We'll apply this when filtering, not per-pattern
            is_significant = p_value < self.ALPHA

            # Determine validation status
            status = self._determine_status(
                total_trades, profit_factor, expectancy_pct,
                win_rate, is_significant
            )

            # Build pattern ID
            pattern_parts = []
            if strategy_type: pattern_parts.append(strategy_type)
            if entry_time_bucket: pattern_parts.append(entry_time_bucket)
            if market_regime: pattern_parts.append(market_regime)
            if conviction_level: pattern_parts.append(f"conv_{conviction_level}")
            if exit_type: pattern_parts.append(f"exit_{exit_type}")
            if symbol: pattern_parts.append(symbol)

            pattern_id = "+".join(pattern_parts) if pattern_parts else "overall"

            # Build description
            desc_parts = []
            if strategy_type: desc_parts.append(strategy_type.replace('_', ' '))
            if entry_time_bucket: desc_parts.append(f"in {entry_time_bucket}")
            if market_regime: desc_parts.append(f"({market_regime})")
            if conviction_level: desc_parts.append(f"[{conviction_level} conviction]")

            pattern_description = " ".join(desc_parts) if desc_parts else "Overall performance"

            pattern = PatternMetrics(
                pattern_id=pattern_id,
                pattern_description=pattern_description,
                strategy_type=strategy_type,
                entry_time_bucket=entry_time_bucket,
                market_regime=market_regime,
                conviction_level=conviction_level,
                exit_type=exit_type,
                symbol=symbol,
                trade_count=total_trades,
                win_count=total_wins,
                loss_count=total_losses,
                win_rate=win_rate,
                profit_factor=profit_factor,
                expectancy=expectancy_pct,
                gross_pnl=total_gross_pnl,
                net_pnl=total_net_pnl,
                avg_net_pnl=total_net_pnl / total_trades if total_trades > 0 else 0.0,
                p_value=p_value,
                is_significant=is_significant,
                status=status,
                first_trade_date=date.fromisoformat(first_date) if first_date else None,
                last_trade_date=date.fromisoformat(last_date) if last_date else None
            )

            patterns.append(pattern)

        # Apply Bonferroni correction
        if patterns:
            corrected_alpha = self.ALPHA / len(patterns)
            for p in patterns:
                p.is_significant = p.p_value < corrected_alpha

        # Sort by profit factor (Grok's primary metric)
        patterns.sort(key=lambda p: p.profit_factor, reverse=True)

        # Return top N (Grok: limit to 10 for token budget)
        return patterns[:top_n]

    def _get_trade_details_for_pattern(
        self,
        strategy_type: Optional[str],
        entry_time_bucket: Optional[str],
        market_regime: Optional[str],
        conviction_level: Optional[str],
        exit_type: Optional[str],
        symbol: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date]
    ) -> List[float]:
        """Get individual trade net P&L for a pattern."""
        cursor = self.conn.cursor()

        query = "SELECT net_pnl FROM trade_attributes WHERE 1=1"
        params = []

        if strategy_type:
            query += " AND strategy_type = ?"
            params.append(strategy_type)
        if entry_time_bucket:
            query += " AND entry_time_bucket = ?"
            params.append(entry_time_bucket)
        if market_regime:
            query += " AND market_regime = ?"
            params.append(market_regime)
        if conviction_level:
            query += " AND conviction_level = ?"
            params.append(conviction_level)
        if exit_type:
            query += " AND exit_type = ?"
            params.append(exit_type)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if period_start:
            query += " AND period >= ?"
            params.append(period_start.isoformat())
        if period_end:
            query += " AND period <= ?"
            params.append(period_end.isoformat())

        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall() if row[0] is not None]

    def _binomial_test(self, successes: int, trials: int, p0: float = 0.5) -> float:
        """
        Binomial test for statistical significance.

        Tests if win rate is significantly different from p0 (default 50%).
        """
        if trials == 0:
            return 1.0

        # Two-tailed binomial test
        try:
            result = stats.binomtest(successes, trials, p0, alternative='two-sided')
            return result.pvalue
        except Exception:
            # Fallback to normal approximation for large samples
            p = successes / trials
            se = math.sqrt(p0 * (1 - p0) / trials)
            z = (p - p0) / se if se > 0 else 0
            p_value = 2 * (1 - stats.norm.cdf(abs(z)))
            return p_value

    def _determine_status(
        self,
        trade_count: int,
        profit_factor: float,
        expectancy_pct: float,
        win_rate: float,
        is_significant: bool
    ) -> str:
        """
        Determine validation status per Grok's recommendations.

        INSUFFICIENT_DATA: < 10 trades
        PROVISIONAL: 10-29 trades
        VALIDATED: ≥30 trades, PF≥1.5, expectancy≥0.3%, WR≥55%, p<0.05
        DEGRADED: Was validated but metrics degraded
        """
        if trade_count < self.MIN_PROVISIONAL:
            return "INSUFFICIENT_DATA"

        if trade_count < self.MIN_VALIDATED:
            return "PROVISIONAL"

        # VALIDATED criteria (all must pass)
        if (profit_factor >= self.MIN_PROFIT_FACTOR and
            expectancy_pct >= self.MIN_EXPECTANCY and
            win_rate >= self.MIN_WIN_RATE and
            is_significant):
            return "VALIDATED"

        # If 30+ trades but doesn't meet criteria, it's provisional
        # (DEGRADED requires tracking historical peak performance - Phase 2B)
        return "PROVISIONAL"

    def get_pattern_context_for_eod(
        self,
        today_symbols: List[str] = None,
        today_strategies: List[str] = None,
        current_regime: str = None,
        top_n: int = 10
    ) -> str:
        """
        Get pattern context for EOD lesson generator.

        Per Grok: Limit to top 10, filter by relevance.

        Args:
            today_symbols: Filter to patterns involving these symbols
            today_strategies: Filter to these strategies
            current_regime: Filter to current market regime
            top_n: Max patterns to return (default 10)

        Returns:
            Formatted context string for EOD prompt
        """
        # Get all validated patterns (30+ trades)
        all_patterns = self.get_validated_patterns(
            min_trades=self.MIN_VALIDATED,
            top_n=50  # Get more, then filter
        )

        if not all_patterns:
            return "No validated patterns yet (need 30+ trades per pattern)."

        # Filter by relevance
        relevant_patterns = []
        for p in all_patterns:
            # Check symbol relevance
            if today_symbols and p.symbol:
                if p.symbol not in today_symbols:
                    continue

            # Check strategy relevance
            if today_strategies and p.strategy_type:
                if p.strategy_type not in today_strategies:
                    continue

            # Check regime relevance
            if current_regime and p.market_regime:
                if p.market_regime != current_regime and p.market_regime != 'unknown':
                    continue

            relevant_patterns.append(p)

        # If filtering removed everything, use top patterns regardless
        if not relevant_patterns:
            relevant_patterns = all_patterns[:top_n]
        else:
            relevant_patterns = relevant_patterns[:top_n]

        # Format as context
        context_lines = ["VALIDATED PATTERNS (from attribution system):", ""]

        validated = [p for p in relevant_patterns if p.status == "VALIDATED"]
        provisional = [p for p in relevant_patterns if p.status == "PROVISIONAL"]

        if validated:
            context_lines.append(f"✅ VALIDATED ({len(validated)} patterns, 30+ trades each):")
            for p in validated[:5]:  # Top 5 validated
                context_lines.append(
                    f"  - {p.pattern_description}: "
                    f"{p.trade_count} trades, {p.win_rate:.1%} WR, "
                    f"PF={p.profit_factor:.2f}, avg ${p.avg_net_pnl:,.2f}"
                )
            context_lines.append("")

        if provisional:
            context_lines.append(f"⚠️ PROVISIONAL ({len(provisional)} patterns, 10-29 trades):")
            for p in provisional[:3]:  # Top 3 provisional
                context_lines.append(
                    f"  - {p.pattern_description}: "
                    f"{p.trade_count} trades so far, {p.win_rate:.1%} WR, "
                    f"avg ${p.avg_net_pnl:,.2f}"
                )
            context_lines.append("")

        context_lines.append(
            "Consider: Do today's trades support or contradict these patterns? "
            "Should confidence scores reflect pattern alignment?"
        )

        return "\n".join(context_lines)

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


# Example usage / testing
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    analyzer = AttributionAnalyzer()

    print("\n" + "="*70)
    print("VALIDATED PATTERNS (Profit Factor ≥ 1.5, 30+ trades)")
    print("="*70 + "\n")

    patterns = analyzer.get_validated_patterns(top_n=10)

    if not patterns:
        print("No patterns with 30+ trades yet. Run after a few trading days.\n")
    else:
        for i, p in enumerate(patterns, 1):
            print(f"{i}. {p.pattern_id} ({p.status})")
            print(f"   {p.pattern_description}")
            print(f"   Trades: {p.trade_count} ({p.win_count}W / {p.loss_count}L)")
            print(f"   Win Rate: {p.win_rate:.1%}")
            print(f"   Profit Factor: {p.profit_factor:.2f} ⭐ PRIMARY")
            print(f"   Expectancy: {p.expectancy:.1%} per trade")
            print(f"   Net P&L: ${p.net_pnl:,.2f} (avg ${p.avg_net_pnl:,.2f})")
            print(f"   Statistical: p={p.p_value:.4f} ({'SIG' if p.is_significant else 'not sig'})")
            print(f"   Period: {p.first_trade_date} to {p.last_trade_date}")
            print()

    print("\n" + "="*70)
    print("EOD CONTEXT (for lesson generator)")
    print("="*70 + "\n")

    context = analyzer.get_pattern_context_for_eod(
        today_symbols=["CGNX", "CROX"],
        today_strategies=["news_catalyst", "momentum"],
        current_regime="trending_up",
        top_n=10
    )

    print(context)
    print()

    analyzer.close()
