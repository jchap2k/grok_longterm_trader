"""
Performance Attribution & Decomposition Module

Decomposes P&L across multiple dimensions to identify what actually works.
Follows Grok's refined design: 5-6 core dimensions + execution quality tracking.

Core Dimensions:
- strategy_type: News catalyst vs momentum vs breakout
- entry_time_bucket: pre_market, open, morning, midday, afternoon
- market_regime: Trending up/down/sideways, volatile/calm
- conviction_level: High/medium/low (from agent decision logs)
- exit_type: Target, stop, manual, eod
- symbol: For symbol-specific patterns

Execution Quality Tracking:
- slippage_bps: Difference between expected and filled price
- commission: Broker fees
- net_pnl: Gross P&L minus fees and slippage costs
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, date, time, timedelta
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
import json
import threading

logger = logging.getLogger(__name__)


@dataclass
class TradeAttribution:
    """Attribution tags for a single trade."""
    trade_id: str

    # Core dimensions (Phase 1)
    strategy_type: str  # "news_catalyst", "momentum", "breakout", "gap_fade"
    entry_time_bucket: str  # "pre_market", "open", "morning", "midday", "afternoon"
    market_regime: str  # "trending_up", "trending_down", "sideways", "volatile", "calm"
    conviction_level: str  # "high", "medium", "low"
    exit_type: str  # "target", "stop", "manual", "eod"
    symbol: str

    # Execution quality (Phase 1)
    entry_expected_price: Optional[float] = None
    entry_filled_price: Optional[float] = None
    slippage_bps: Optional[float] = None  # Basis points (1/100th of 1%)
    commission: Optional[float] = None
    gross_pnl: Optional[float] = None
    net_pnl: Optional[float] = None  # Gross - commission - slippage cost

    # Metadata
    period: date = None  # Trading day
    attributed_at: datetime = None


class PerformanceAttributionEngine:
    """
    Tracks and analyzes trade performance across multiple dimensions.

    Phase 1 Implementation (Grok's refined design):
    - 5-6 core dimensions only
    - Execution quality tracking
    - Hybrid sync/async: tags at trade time, aggregates at EOD
    - Proper database indexes for query performance
    """

    def __init__(self, db_path: str = None):
        """
        Initialize performance attribution engine.

        Args:
            db_path: Path to trading_performance.db (default: auto-detect)
        """
        if db_path is None:
            # Auto-detect database path
            script_dir = Path(__file__).parent
            db_path = script_dir.parent.parent.parent / "trading_performance.db"

        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()
        self._init_database()

    def _init_database(self):
        """Initialize attribution tables in trading_performance.db"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()

        # Trade attributes table (one row per trade)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL UNIQUE,

                -- Core dimensions (Phase 1)
                strategy_type TEXT NOT NULL,
                entry_time_bucket TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                conviction_level TEXT NOT NULL,
                exit_type TEXT NOT NULL,
                symbol TEXT NOT NULL,

                -- Execution quality
                entry_expected_price REAL,
                entry_filled_price REAL,
                slippage_bps REAL,
                commission REAL,
                gross_pnl REAL,
                net_pnl REAL,

                -- Metadata
                period DATE NOT NULL,
                attributed_at TEXT NOT NULL,

                FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
            )
        """)

        # Aggregated metrics table (for fast queries)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attribution_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Dimension values (NULL = all)
                strategy_type TEXT,
                entry_time_bucket TEXT,
                market_regime TEXT,
                conviction_level TEXT,
                exit_type TEXT,
                symbol TEXT,

                -- Time period
                period DATE NOT NULL,
                period_type TEXT NOT NULL,  -- "daily", "weekly", "monthly"

                -- Aggregated metrics
                trade_count INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,

                gross_pnl REAL DEFAULT 0.0,
                net_pnl REAL DEFAULT 0.0,
                avg_gross_pnl REAL DEFAULT 0.0,
                avg_net_pnl REAL DEFAULT 0.0,

                total_slippage_bps REAL DEFAULT 0.0,
                total_commission REAL DEFAULT 0.0,
                avg_slippage_bps REAL DEFAULT 0.0,

                largest_win REAL DEFAULT 0.0,
                largest_loss REAL DEFAULT 0.0,

                -- Statistical significance
                win_rate_ci_low REAL,  -- Wilson score confidence interval
                win_rate_ci_high REAL,

                -- Metadata
                last_updated TEXT NOT NULL,

                UNIQUE(strategy_type, entry_time_bucket, market_regime, conviction_level,
                       exit_type, symbol, period, period_type)
            )
        """)

        # Create indexes for query performance (CRITICAL per Grok)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trade_attributes_dimensions
            ON trade_attributes (
                strategy_type, entry_time_bucket, market_regime,
                conviction_level, exit_type, period
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trade_attributes_symbol_period
            ON trade_attributes (symbol, period, strategy_type)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attribution_metrics_period
            ON attribution_metrics (period, period_type)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attribution_metrics_dimensions
            ON attribution_metrics (
                strategy_type, entry_time_bucket, market_regime, period_type
            )
        """)

        self.conn.commit()
        logger.info("Performance attribution database initialized")

    # ------------------------------------------------------------------
    # Phase 1: Core Attribution Logic
    # ------------------------------------------------------------------

    def attribute_trade(self, trade: Dict[str, Any], context: Dict[str, Any] = None) -> TradeAttribution:
        """
        Extract attribution tags from a trade (synchronous at trade time).

        Args:
            trade: Trade dict from performance_tracker
            context: Additional context from trading agent (optional)

        Returns:
            TradeAttribution object with all tags
        """
        # Extract core dimensions
        strategy_type = self._classify_strategy(trade, context)
        entry_time_bucket = self._classify_entry_time(trade)
        market_regime = self._classify_market_regime(trade, context)
        conviction_level = self._extract_conviction(trade, context)
        exit_type = self._classify_exit_type(trade)
        symbol = trade.get('symbol', 'UNKNOWN')

        # Calculate execution quality
        entry_expected = context.get('expected_price') if context else None
        entry_filled = trade.get('entry_price')
        slippage_bps = self._calculate_slippage_bps(entry_expected, entry_filled)
        commission = trade.get('commission', 0.0)
        gross_pnl = trade.get('realized_pnl', 0.0)
        net_pnl = gross_pnl - commission

        # Adjust net P&L for slippage cost (if we have expected price)
        if slippage_bps is not None and entry_expected is not None:
            quantity = trade.get('quantity', 0)
            slippage_cost = (slippage_bps / 10000.0) * entry_expected * quantity
            net_pnl -= slippage_cost

        exit_time = trade.get('exit_time')
        if isinstance(exit_time, str):
            exit_time = datetime.fromisoformat(exit_time)
        period = exit_time.date() if exit_time else date.today()

        attribution = TradeAttribution(
            trade_id=trade['trade_id'],
            strategy_type=strategy_type,
            entry_time_bucket=entry_time_bucket,
            market_regime=market_regime,
            conviction_level=conviction_level,
            exit_type=exit_type,
            symbol=symbol,
            entry_expected_price=entry_expected,
            entry_filled_price=entry_filled,
            slippage_bps=slippage_bps,
            commission=commission,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            period=period,
            attributed_at=datetime.now()
        )

        # Save to database (synchronous)
        self._save_attribution(attribution)

        logger.info(
            f"Attributed trade {trade['trade_id']} ({symbol}): "
            f"strategy={strategy_type}, time={entry_time_bucket}, "
            f"regime={market_regime}, conviction={conviction_level}, "
            f"net_pnl=${net_pnl:.2f}"
        )

        return attribution

    def _classify_strategy(self, trade: Dict, context: Dict = None) -> str:
        """Classify trade strategy type."""
        # Use existing strategy field if available
        strategy_raw = trade.get('strategy', '').lower()

        if 'news' in strategy_raw or 'catalyst' in strategy_raw:
            return 'news_catalyst'
        elif 'momentum' in strategy_raw:
            return 'momentum'
        elif 'breakout' in strategy_raw:
            return 'breakout'
        elif 'gap' in strategy_raw or 'fade' in strategy_raw:
            return 'gap_fade'
        elif 'scalp' in strategy_raw:
            return 'scalp'
        else:
            return 'other'

    def _classify_entry_time(self, trade: Dict) -> str:
        """Classify entry time bucket."""
        entry_time = trade.get('entry_time')
        if isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)

        if not entry_time:
            return 'unknown'

        hour = entry_time.hour
        minute = entry_time.minute
        time_val = hour + minute / 60.0

        # Market hours: 6:30 AM - 1:00 PM PST (day trading schedule)
        if time_val < 6.5:  # Before 6:30 AM
            return 'pre_market'
        elif time_val < 7.0:  # 6:30-7:00 AM (market open)
            return 'open'
        elif time_val < 10.0:  # 7:00-10:00 AM
            return 'morning'
        elif time_val < 12.0:  # 10:00 AM - 12:00 PM
            return 'midday'
        elif time_val < 13.0:  # 12:00-1:00 PM
            return 'afternoon'
        else:  # After 1:00 PM
            return 'post_market'

    def _classify_market_regime(self, trade: Dict, context: Dict = None) -> str:
        """Classify market regime at trade time."""
        # Use context if available (from agent's market assessment)
        if context and 'market_regime' in context:
            return context['market_regime']

        # Otherwise, default to "unknown" (will be enhanced in Phase 2)
        return 'unknown'

    def _extract_conviction(self, trade: Dict, context: Dict = None) -> str:
        """Extract conviction level from context or trade data."""
        # Use context if available (from agent's decision)
        if context and 'conviction' in context:
            conviction = context['conviction'].lower()
            if conviction in ['high', 'medium', 'low']:
                return conviction

        # Otherwise infer from position size or default to medium
        # (will be enhanced when agent provides explicit conviction)
        return 'medium'

    def _classify_exit_type(self, trade: Dict) -> str:
        """Classify exit type."""
        exit_reason = trade.get('exit_reason', 'unknown').lower()

        if 'target' in exit_reason:
            return 'target'
        elif 'stop' in exit_reason:
            return 'stop'
        elif 'manual' in exit_reason:
            return 'manual'
        elif 'eod' in exit_reason or 'close' in exit_reason:
            return 'eod'
        else:
            return 'other'

    def _calculate_slippage_bps(self, expected: Optional[float], filled: Optional[float]) -> Optional[float]:
        """Calculate slippage in basis points."""
        if expected is None or filled is None or expected == 0:
            return None

        # For long entries: positive slippage = paid more than expected (bad)
        # For short entries: would need side info (defer to Phase 2)
        slippage_bps = ((filled - expected) / expected) * 10000
        return round(slippage_bps, 2)

    def _save_attribution(self, attr: TradeAttribution):
        """Save trade attribution to database."""
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trade_attributes (
                    trade_id, strategy_type, entry_time_bucket, market_regime,
                    conviction_level, exit_type, symbol,
                    entry_expected_price, entry_filled_price, slippage_bps,
                    commission, gross_pnl, net_pnl,
                    period, attributed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                attr.trade_id, attr.strategy_type, attr.entry_time_bucket,
                attr.market_regime, attr.conviction_level, attr.exit_type,
                attr.symbol, attr.entry_expected_price, attr.entry_filled_price,
                attr.slippage_bps, attr.commission, attr.gross_pnl, attr.net_pnl,
                attr.period.isoformat(), attr.attributed_at.isoformat()
            ))
            self.conn.commit()

    # ------------------------------------------------------------------
    # Phase 1: Aggregation (EOD batch)
    # ------------------------------------------------------------------

    def aggregate_daily_metrics(self, target_date: date):
        """
        Aggregate attribution metrics for a specific day (run at EOD).

        This is the async part of the hybrid approach - aggregate metrics
        in batch rather than at every trade.
        """
        logger.info(f"Aggregating attribution metrics for {target_date}")

        with self._db_lock:
            cursor = self.conn.cursor()

            # Get all trades for this day
            cursor.execute("""
                SELECT * FROM trade_attributes WHERE period = ?
            """, (target_date.isoformat(),))

            rows = cursor.fetchall()
            if not rows:
                logger.info(f"No trades to aggregate for {target_date}")
                return

            # Aggregate by each dimension separately
            dimensions = [
                ('strategy_type', 2),
                ('entry_time_bucket', 3),
                ('market_regime', 4),
                ('conviction_level', 5),
                ('exit_type', 6),
                ('symbol', 7)
            ]

            for dim_name, dim_col_idx in dimensions:
                self._aggregate_by_dimension(target_date, dim_name, dim_col_idx, rows)

            # Also aggregate overall (no dimension filter)
            self._aggregate_overall(target_date, rows)

            logger.info(f"Aggregation complete for {target_date}")

    def _aggregate_by_dimension(self, target_date: date, dim_name: str, dim_col_idx: int, rows: List):
        """Aggregate metrics by a single dimension."""
        from collections import defaultdict

        # Group trades by dimension value
        groups = defaultdict(list)
        for row in rows:
            dim_value = row[dim_col_idx]
            groups[dim_value].append(row)

        # Calculate metrics for each group
        for dim_value, trade_rows in groups.items():
            metrics = self._calculate_metrics(trade_rows)

            # Build dimension dict
            dim_dict = {
                'strategy_type': None,
                'entry_time_bucket': None,
                'market_regime': None,
                'conviction_level': None,
                'exit_type': None,
                'symbol': None
            }
            dim_dict[dim_name] = dim_value

            self._save_metrics(target_date, 'daily', dim_dict, metrics)

    def _aggregate_overall(self, target_date: date, rows: List):
        """Aggregate overall metrics (no dimension filter)."""
        metrics = self._calculate_metrics(rows)
        dim_dict = {
            'strategy_type': None,
            'entry_time_bucket': None,
            'market_regime': None,
            'conviction_level': None,
            'exit_type': None,
            'symbol': None
        }
        self._save_metrics(target_date, 'daily', dim_dict, metrics)

    def _calculate_metrics(self, trade_rows: List) -> Dict:
        """Calculate aggregated metrics from trade rows."""
        if not trade_rows:
            return {}

        # Column indexes (from trade_attributes table)
        # 0=id, 1=trade_id, 2=strategy_type, 3=entry_time_bucket, 4=market_regime,
        # 5=conviction_level, 6=exit_type, 7=symbol, 8=entry_expected_price,
        # 9=entry_filled_price, 10=slippage_bps, 11=commission, 12=gross_pnl,
        # 13=net_pnl, 14=period, 15=attributed_at

        trade_count = len(trade_rows)
        win_count = sum(1 for row in trade_rows if row[13] > 0)  # net_pnl > 0
        loss_count = trade_count - win_count
        win_rate = win_count / trade_count if trade_count > 0 else 0.0

        gross_pnl = sum(row[12] for row in trade_rows if row[12] is not None)
        net_pnl = sum(row[13] for row in trade_rows if row[13] is not None)
        avg_gross_pnl = gross_pnl / trade_count if trade_count > 0 else 0.0
        avg_net_pnl = net_pnl / trade_count if trade_count > 0 else 0.0

        slippages = [row[10] for row in trade_rows if row[10] is not None]
        total_slippage_bps = sum(slippages) if slippages else 0.0
        avg_slippage_bps = total_slippage_bps / len(slippages) if slippages else 0.0

        commissions = [row[11] for row in trade_rows if row[11] is not None]
        total_commission = sum(commissions) if commissions else 0.0

        net_pnls = [row[13] for row in trade_rows if row[13] is not None]
        largest_win = max(net_pnls) if net_pnls else 0.0
        largest_loss = min(net_pnls) if net_pnls else 0.0

        # Calculate confidence interval for win rate (Wilson score method)
        ci_low, ci_high = self._wilson_confidence_interval(win_count, trade_count)

        return {
            'trade_count': trade_count,
            'win_count': win_count,
            'loss_count': loss_count,
            'win_rate': win_rate,
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'avg_gross_pnl': avg_gross_pnl,
            'avg_net_pnl': avg_net_pnl,
            'total_slippage_bps': total_slippage_bps,
            'total_commission': total_commission,
            'avg_slippage_bps': avg_slippage_bps,
            'largest_win': largest_win,
            'largest_loss': largest_loss,
            'win_rate_ci_low': ci_low,
            'win_rate_ci_high': ci_high
        }

    def _wilson_confidence_interval(self, successes: int, total: int, confidence: float = 0.95) -> tuple:
        """
        Calculate Wilson score confidence interval for win rate.

        More accurate than normal approximation for small sample sizes.
        Falls back to simple binomial confidence if scipy not available.
        """
        if total == 0:
            return (0.0, 0.0)

        import math

        # Z-score for 95% confidence
        z = 1.96 if confidence == 0.95 else 1.645

        p = successes / total
        denominator = 1 + z**2 / total
        centre = (p + z**2 / (2 * total)) / denominator

        # Safe sqrt - avoid negative numbers from floating point errors
        sqrt_arg = (p * (1 - p) / total + z**2 / (4 * total**2))
        if sqrt_arg < 0:
            sqrt_arg = 0

        margin = z * math.sqrt(sqrt_arg) / denominator

        ci_low = max(0.0, centre - margin)
        ci_high = min(1.0, centre + margin)

        return (round(ci_low, 3), round(ci_high, 3))

    def _save_metrics(self, target_date: date, period_type: str, dimensions: Dict, metrics: Dict):
        """Save aggregated metrics to database."""
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO attribution_metrics (
                    strategy_type, entry_time_bucket, market_regime,
                    conviction_level, exit_type, symbol,
                    period, period_type,
                    trade_count, win_count, loss_count, win_rate,
                    gross_pnl, net_pnl, avg_gross_pnl, avg_net_pnl,
                    total_slippage_bps, total_commission, avg_slippage_bps,
                    largest_win, largest_loss,
                    win_rate_ci_low, win_rate_ci_high,
                    last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dimensions['strategy_type'],
                dimensions['entry_time_bucket'],
                dimensions['market_regime'],
                dimensions['conviction_level'],
                dimensions['exit_type'],
                dimensions['symbol'],
                target_date.isoformat(),
                period_type,
                metrics['trade_count'],
                metrics['win_count'],
                metrics['loss_count'],
                metrics['win_rate'],
                metrics['gross_pnl'],
                metrics['net_pnl'],
                metrics['avg_gross_pnl'],
                metrics['avg_net_pnl'],
                metrics['total_slippage_bps'],
                metrics['total_commission'],
                metrics['avg_slippage_bps'],
                metrics['largest_win'],
                metrics['largest_loss'],
                metrics['win_rate_ci_low'],
                metrics['win_rate_ci_high'],
                datetime.now().isoformat()
            ))
            self.conn.commit()

    # ------------------------------------------------------------------
    # Phase 1: Reporting
    # ------------------------------------------------------------------

    def generate_daily_summary(self, target_date: date = None) -> str:
        """
        Generate daily attribution summary (console/log only for Phase 1).

        Args:
            target_date: Date to summarize (default: today)

        Returns:
            Formatted summary string
        """
        if target_date is None:
            target_date = date.today()

        with self._db_lock:
            cursor = self.conn.cursor()

            # Get overall metrics
            cursor.execute("""
                SELECT trade_count, win_count, win_rate, net_pnl,
                       total_commission, avg_slippage_bps,
                       win_rate_ci_low, win_rate_ci_high
                FROM attribution_metrics
                WHERE period = ? AND period_type = 'daily'
                  AND strategy_type IS NULL AND entry_time_bucket IS NULL
                  AND market_regime IS NULL AND conviction_level IS NULL
                  AND exit_type IS NULL AND symbol IS NULL
            """, (target_date.isoformat(),))

            overall = cursor.fetchone()
            if not overall:
                return f"No attribution data for {target_date}"

            trade_count, win_count, win_rate, net_pnl, commission, slippage, ci_low, ci_high = overall

            # Get best/worst dimensions
            best_strategy = self._get_best_dimension(cursor, target_date, 'strategy_type')
            best_time = self._get_best_dimension(cursor, target_date, 'entry_time_bucket')
            worst_strategy = self._get_worst_dimension(cursor, target_date, 'strategy_type')

            # Format summary
            summary = f"\n{'='*70}\n"
            summary += f"PERFORMANCE ATTRIBUTION SUMMARY - {target_date}\n"
            summary += f"{'='*70}\n\n"
            summary += f"Overall Performance:\n"
            summary += f"  Trades:        {trade_count}\n"
            summary += f"  Win Rate:      {win_rate:.1%} (95% CI: {ci_low:.1%} - {ci_high:.1%})\n"
            summary += f"  Net P&L:       ${net_pnl:,.2f}\n"
            summary += f"  Commissions:   ${commission:,.2f}\n"
            summary += f"  Avg Slippage:  {slippage:.2f} bps\n"
            summary += f"\n"

            if best_strategy:
                summary += f"Best Strategy:   {best_strategy[0]} ({best_strategy[1]} trades, "
                summary += f"{best_strategy[2]:.1%} WR, ${best_strategy[3]:,.2f} net P&L)\n"

            if best_time:
                summary += f"Best Time:       {best_time[0]} ({best_time[1]} trades, "
                summary += f"{best_time[2]:.1%} WR, ${best_time[3]:,.2f} net P&L)\n"

            if worst_strategy:
                summary += f"Worst Strategy:  {worst_strategy[0]} ({worst_strategy[1]} trades, "
                summary += f"{worst_strategy[2]:.1%} WR, ${worst_strategy[3]:,.2f} net P&L)\n"

            summary += f"\n{'='*70}\n"

            return summary

    def _get_best_dimension(self, cursor, target_date: date, dim_name: str) -> Optional[tuple]:
        """Get best performing dimension value."""
        query = f"""
            SELECT {dim_name}, trade_count, win_rate, net_pnl
            FROM attribution_metrics
            WHERE period = ? AND period_type = 'daily'
              AND {dim_name} IS NOT NULL
              AND trade_count >= 2
            ORDER BY net_pnl DESC
            LIMIT 1
        """
        cursor.execute(query, (target_date.isoformat(),))
        return cursor.fetchone()

    def _get_worst_dimension(self, cursor, target_date: date, dim_name: str) -> Optional[tuple]:
        """Get worst performing dimension value."""
        query = f"""
            SELECT {dim_name}, trade_count, win_rate, net_pnl
            FROM attribution_metrics
            WHERE period = ? AND period_type = 'daily'
              AND {dim_name} IS NOT NULL
              AND trade_count >= 2
            ORDER BY net_pnl ASC
            LIMIT 1
        """
        cursor.execute(query, (target_date.isoformat(),))
        return cursor.fetchone()

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


# Example usage
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # Initialize engine
    engine = PerformanceAttributionEngine()

    # Example: attribute a trade
    trade = {
        'trade_id': 'TEST001',
        'symbol': 'CGNX',
        'entry_time': '2026-02-12T06:42:00',
        'exit_time': '2026-02-12T08:15:00',
        'entry_price': 45.20,
        'realized_pnl': 125.50,
        'commission': 1.00,
        'strategy': 'News Catalyst Scalp',
        'exit_reason': 'target'
    }

    context = {
        'expected_price': 45.00,  # Expected vs filled = slippage
        'conviction': 'high',
        'market_regime': 'trending_up'
    }

    attr = engine.attribute_trade(trade, context)
    print(f"\nAttributed trade: {attr.trade_id}")
    print(f"  Strategy: {attr.strategy_type}")
    print(f"  Time: {attr.entry_time_bucket}")
    print(f"  Slippage: {attr.slippage_bps} bps")
    print(f"  Net P&L: ${attr.net_pnl:.2f}")

    # Example: aggregate metrics
    engine.aggregate_daily_metrics(date.today())

    # Example: generate summary
    summary = engine.generate_daily_summary(date.today())
    print(summary)

    engine.close()
