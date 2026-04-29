"""
Performance Analytics Module

Tracks, analyzes, and reports trading performance metrics.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from collections import defaultdict
import json
import sqlite3
from pathlib import Path
import numpy as np
import threading


@dataclass
class Trade:
    """Represents a completed trade."""
    trade_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    side: str  # "long" or "short"
    realized_pnl: float
    realized_pnl_percent: float
    strategy: str
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    exit_reason: str = "unknown"  # "target", "stop", "eod", "manual"
    # New metrics (Grok's request - Feb 13, 2026)
    time_to_stopout: Optional[int] = None  # Seconds from entry to stop hit
    peak_profit_pct: Optional[float] = None  # Max unrealized profit %
    give_back_pct: Optional[float] = None  # Peak - exit on stopped trades
    fill_type: Optional[str] = None  # "LIMIT" or "MARKET"
    fill_time_seconds: Optional[int] = None  # Order to fill duration


@dataclass
class DailyStats:
    """Daily performance statistics."""
    date: date
    num_trades: int
    num_wins: int
    num_losses: int
    win_rate: float
    total_pnl: float
    total_pnl_percent: float
    starting_capital: float
    ending_capital: float
    largest_win: float
    largest_loss: float
    avg_win: float
    avg_loss: float
    strategies_used: List[str]


class PerformanceTracker:
    """
    Tracks and analyzes trading performance.

    Features:
    - Real-time P&L tracking
    - Win rate and statistics
    - Strategy performance comparison
    - Risk metrics (Sharpe, max drawdown)
    - Daily/weekly/monthly reports
    """

    def __init__(self, db_path: str = "trading_performance.db"):
        """
        Initialize performance tracker.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()  # Thread-safe database access
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database."""
        # Enable multi-threaded access to SQLite connection
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()

        # WAL mode: allows concurrent reads while writing (dashboard + scheduler)
        # synchronous=NORMAL: safe with WAL, significantly faster than FULL
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")

        # Trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                side TEXT NOT NULL,
                realized_pnl REAL NOT NULL,
                realized_pnl_percent REAL NOT NULL,
                strategy TEXT NOT NULL,
                stop_price REAL,
                target_price REAL,
                exit_reason TEXT,
                time_to_stopout INTEGER,
                peak_profit_pct REAL,
                give_back_pct REAL,
                fill_type TEXT,
                fill_time_seconds INTEGER,
                slippage_bps REAL
            )
        """)

        # Add new columns to existing tables (migration)
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN time_to_stopout INTEGER")
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN peak_profit_pct REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN give_back_pct REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN fill_type TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN fill_time_seconds INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN slippage_bps REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Daily stats table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                num_trades INTEGER,
                num_wins INTEGER,
                num_losses INTEGER,
                win_rate REAL,
                total_pnl REAL,
                total_pnl_percent REAL,
                starting_capital REAL,
                ending_capital REAL,
                largest_win REAL,
                largest_loss REAL,
                avg_win REAL,
                avg_loss REAL,
                strategies_used TEXT
            )
        """)

        # Account snapshots table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshots (
                timestamp TEXT PRIMARY KEY,
                account_value REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL
            )
        """)

        # Partial exits table - tracks each partial sell within a trade
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS partial_exits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_trade_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                qty_sold INTEGER NOT NULL,
                exit_price REAL NOT NULL,
                partial_pnl REAL NOT NULL,
                remaining_qty INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_partial_exits_trade_id
            ON partial_exits(parent_trade_id)
        """)
        # Indexes for date-range and symbol queries used by performance reports
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")

        self.conn.commit()

    def record_trade(self, trade: Trade):
        """
        Record a completed trade.

        Args:
            trade: Trade object to record
        """
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id,
                trade.symbol,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat(),
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.side,
                trade.realized_pnl,
                trade.realized_pnl_percent,
                trade.strategy,
                trade.stop_price,
                trade.target_price,
                trade.exit_reason,
                trade.time_to_stopout,
                trade.peak_profit_pct,
                trade.give_back_pct,
                trade.fill_type,
                trade.fill_time_seconds
            ))
            self.conn.commit()

    def record_partial_exit(self, parent_trade_id: str, symbol: str,
                            qty_sold: int, exit_price: float,
                            partial_pnl: float, remaining_qty: int,
                            reason: str = None) -> int:
        """
        Record a partial exit from an open position.

        Args:
            parent_trade_id: trade_id of the parent trade in trades table
            symbol: Stock symbol
            qty_sold: Number of shares sold in this partial exit
            exit_price: Price at which shares were sold
            partial_pnl: P&L from this partial exit (dollars)
            remaining_qty: Shares remaining after this exit
            reason: Why the partial exit was triggered

        Returns:
            ID of the partial exit record
        """
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO partial_exits
                (parent_trade_id, symbol, exit_time, qty_sold, exit_price,
                 partial_pnl, remaining_qty, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parent_trade_id,
                symbol,
                datetime.now().isoformat(),
                qty_sold,
                exit_price,
                partial_pnl,
                remaining_qty,
                reason,
                datetime.now().isoformat()
            ))
            self.conn.commit()
            return cursor.lastrowid

    def record_daily_stats(self, stats: DailyStats):
        """Record daily statistics."""
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO daily_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stats.date.isoformat(),
                stats.num_trades,
                stats.num_wins,
                stats.num_losses,
                stats.win_rate,
                stats.total_pnl,
                stats.total_pnl_percent,
                stats.starting_capital,
                stats.ending_capital,
                stats.largest_win,
                stats.largest_loss,
                stats.avg_win,
                stats.avg_loss,
                json.dumps(stats.strategies_used)
            ))
            self.conn.commit()

    def record_account_snapshot(self, account_value: float, cash: float, positions_value: float):
        """Record account snapshot for tracking equity curve."""
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO account_snapshots VALUES (?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                account_value,
                cash,
                positions_value
            ))
            self.conn.commit()

    def get_trades(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None
    ) -> List[Trade]:
        """
        Get trades with optional filters.

        Args:
            start_date: Filter by start date
            end_date: Filter by end date
            symbol: Filter by symbol
            strategy: Filter by strategy

        Returns:
            List of Trade objects
        """
        with self._db_lock:
            cursor = self.conn.cursor()
            query = "SELECT * FROM trades WHERE 1=1"
            params = []

            if start_date:
                query += " AND date(exit_time) >= ?"
                params.append(start_date.isoformat())

            if end_date:
                query += " AND date(exit_time) <= ?"
                params.append(end_date.isoformat())

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            if strategy:
                query += " AND strategy = ?"
                params.append(strategy)

            query += " ORDER BY exit_time DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            trades = []
            for row in rows:
                trades.append(Trade(
                    trade_id=row[0],
                    symbol=row[1],
                    entry_time=datetime.fromisoformat(row[2]),
                    exit_time=datetime.fromisoformat(row[3]),
                    entry_price=row[4],
                    exit_price=row[5],
                    quantity=row[6],
                    side=row[7],
                    realized_pnl=row[8],
                    realized_pnl_percent=row[9],
                    strategy=row[10],
                    stop_price=row[11],
                    target_price=row[12],
                    exit_reason=row[13],
                    time_to_stopout=row[14] if len(row) > 14 else None,
                    peak_profit_pct=row[15] if len(row) > 15 else None,
                    give_back_pct=row[16] if len(row) > 16 else None,
                    fill_type=row[17] if len(row) > 17 else None,
                    fill_time_seconds=row[18] if len(row) > 18 else None
                ))

            return trades

    def calculate_daily_stats(self, target_date: date, starting_capital: float) -> DailyStats:
        """
        Calculate statistics for a specific day.

        Args:
            target_date: Date to calculate stats for
            starting_capital: Starting capital for the day

        Returns:
            DailyStats object
        """
        trades = self.get_trades(start_date=target_date, end_date=target_date)

        if not trades:
            return DailyStats(
                date=target_date,
                num_trades=0,
                num_wins=0,
                num_losses=0,
                win_rate=0.0,
                total_pnl=0.0,
                total_pnl_percent=0.0,
                starting_capital=starting_capital,
                ending_capital=starting_capital,
                largest_win=0.0,
                largest_loss=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                strategies_used=[]
            )

        wins = [t for t in trades if t.realized_pnl > 0]
        losses = [t for t in trades if t.realized_pnl <= 0]

        total_pnl = sum(t.realized_pnl for t in trades)
        ending_capital = starting_capital + total_pnl

        strategies = list(set(t.strategy for t in trades))

        return DailyStats(
            date=target_date,
            num_trades=len(trades),
            num_wins=len(wins),
            num_losses=len(losses),
            win_rate=len(wins) / len(trades) if trades else 0.0,
            total_pnl=total_pnl,
            total_pnl_percent=(total_pnl / starting_capital * 100) if starting_capital > 0 else 0.0,
            starting_capital=starting_capital,
            ending_capital=ending_capital,
            largest_win=max((t.realized_pnl for t in wins), default=0.0),
            largest_loss=min((t.realized_pnl for t in losses), default=0.0),
            avg_win=np.mean([t.realized_pnl for t in wins]) if wins else 0.0,
            avg_loss=np.mean([t.realized_pnl for t in losses]) if losses else 0.0,
            strategies_used=strategies
        )

    def calculate_sharpe_ratio(self, start_date: date, end_date: date, risk_free_rate: float = 0.04) -> float:
        """
        Calculate Sharpe ratio for a date range.

        Args:
            start_date: Start date
            end_date: End date
            risk_free_rate: Annual risk-free rate (default 4%)

        Returns:
            Sharpe ratio
        """
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT total_pnl_percent FROM daily_stats
                WHERE date >= ? AND date <= ?
                ORDER BY date
            """, (start_date.isoformat(), end_date.isoformat()))

            daily_returns = [row[0] / 100 for row in cursor.fetchall()]

            if not daily_returns or len(daily_returns) < 2:
                return 0.0

            # Convert annual risk-free rate to daily
            daily_rf = (1 + risk_free_rate) ** (1/252) - 1

            excess_returns = [r - daily_rf for r in daily_returns]
            avg_excess = np.mean(excess_returns)
            std_excess = np.std(excess_returns)

            if std_excess == 0:
                return 0.0

            # Annualize
            sharpe = (avg_excess / std_excess) * np.sqrt(252)
            return sharpe

    def calculate_max_drawdown(self, start_date: date, end_date: date) -> Dict[str, Any]:
        """
        Calculate maximum drawdown for a date range.

        Args:
            start_date: Start date
            end_date: End date

        Returns:
            Dict with max_drawdown_percent, max_drawdown_value, peak_date, trough_date
        """
        with self._db_lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT date, ending_capital FROM daily_stats
                WHERE date >= ? AND date <= ?
                ORDER BY date
            """, (start_date.isoformat(), end_date.isoformat()))

            data = cursor.fetchall()
            if not data:
                return {"max_drawdown_percent": 0.0, "max_drawdown_value": 0.0}

            peak = data[0][1]
            peak_date = data[0][0]
            max_dd = 0.0
            max_dd_value = 0.0
            trough_date = None

            for date_str, value in data:
                if value > peak:
                    peak = value
                    peak_date = date_str

                dd = (peak - value) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                    max_dd_value = peak - value
                    trough_date = date_str

            return {
                "max_drawdown_percent": max_dd * 100,
                "max_drawdown_value": max_dd_value,
                "peak_date": peak_date,
                "trough_date": trough_date
            }

    def get_strategy_performance(self, start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get performance breakdown by strategy.

        Returns:
            Dict mapping strategy name to performance metrics
        """
        trades = self.get_trades(start_date=start_date, end_date=end_date)

        strategy_stats = defaultdict(lambda: {
            "num_trades": 0,
            "num_wins": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0
        })

        for trade in trades:
            stats = strategy_stats[trade.strategy]
            stats["num_trades"] += 1
            if trade.realized_pnl > 0:
                stats["num_wins"] += 1
            stats["total_pnl"] += trade.realized_pnl

        # Calculate averages and win rates
        for strategy, stats in strategy_stats.items():
            stats["avg_pnl"] = stats["total_pnl"] / stats["num_trades"] if stats["num_trades"] > 0 else 0.0
            stats["win_rate"] = stats["num_wins"] / stats["num_trades"] if stats["num_trades"] > 0 else 0.0

        return dict(strategy_stats)

    def generate_report(self, start_date: date, end_date: date, output_format: str = "text") -> str:
        """
        Generate performance report.

        Args:
            start_date: Start date
            end_date: End date
            output_format: "text", "json", or "markdown"

        Returns:
            Formatted report
        """
        trades = self.get_trades(start_date=start_date, end_date=end_date)

        if not trades:
            return "No trades in this period."

        # Calculate metrics
        wins = [t for t in trades if t.realized_pnl > 0]
        losses = [t for t in trades if t.realized_pnl <= 0]

        total_pnl = sum(t.realized_pnl for t in trades)
        win_rate = len(wins) / len(trades) if trades else 0.0
        avg_win = np.mean([t.realized_pnl for t in wins]) if wins else 0.0
        avg_loss = np.mean([t.realized_pnl for t in losses]) if losses else 0.0
        profit_factor = abs(sum(t.realized_pnl for t in wins) / sum(t.realized_pnl for t in losses)) if losses and sum(t.realized_pnl for t in losses) != 0 else 0.0

        sharpe = self.calculate_sharpe_ratio(start_date, end_date)
        drawdown = self.calculate_max_drawdown(start_date, end_date)
        strategy_perf = self.get_strategy_performance(start_date, end_date)

        # New metrics (Grok's request - Feb 13, 2026)
        stopped_trades = [t for t in trades if t.exit_reason == "stop" and t.time_to_stopout is not None]
        avg_time_to_stopout = np.mean([t.time_to_stopout for t in stopped_trades]) if stopped_trades else 0
        avg_time_to_stopout_min = avg_time_to_stopout / 60 if avg_time_to_stopout > 0 else 0

        trades_with_giveback = [t for t in trades if t.give_back_pct is not None and t.give_back_pct > 0]
        avg_give_back = np.mean([t.give_back_pct for t in trades_with_giveback]) if trades_with_giveback else 0

        limit_orders = [t for t in trades if t.fill_type == "LIMIT"]
        market_orders = [t for t in trades if t.fill_type == "MARKET"]
        fill_rate_limit = len(limit_orders) / len(trades) * 100 if trades else 0
        fill_rate_market = len(market_orders) / len(trades) * 100 if trades else 0

        trades_with_fill_time = [t for t in trades if t.fill_time_seconds is not None]
        avg_fill_time = np.mean([t.fill_time_seconds for t in trades_with_fill_time]) if trades_with_fill_time else 0

        if output_format == "json":
            return json.dumps({
                "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
                "total_trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
                "sharpe_ratio": sharpe,
                "max_drawdown": drawdown,
                "strategy_performance": strategy_perf
            }, indent=2)

        elif output_format == "markdown":
            md = f"# Trading Performance Report\n\n"
            md += f"**Period**: {start_date} to {end_date}\n\n"
            md += f"## Summary\n\n"
            md += f"- **Total Trades**: {len(trades)}\n"
            md += f"- **Win Rate**: {win_rate:.1%}\n"
            md += f"- **Total P&L**: ${total_pnl:.2f}\n"
            md += f"- **Sharpe Ratio**: {sharpe:.2f}\n"
            md += f"- **Max Drawdown**: {drawdown['max_drawdown_percent']:.2f}%\n\n"
            md += f"## Strategy Performance\n\n"
            for strategy, stats in strategy_perf.items():
                md += f"### {strategy}\n"
                md += f"- Trades: {stats['num_trades']}\n"
                md += f"- Win Rate: {stats['win_rate']:.1%}\n"
                md += f"- Total P&L: ${stats['total_pnl']:.2f}\n\n"
            return md

        else:  # text format
            report = f"\n{'='*60}\n"
            report += f"TRADING PERFORMANCE REPORT\n"
            report += f"Period: {start_date} to {end_date}\n"
            report += f"{'='*60}\n\n"
            report += f"Total Trades:     {len(trades)}\n"
            report += f"Wins:             {len(wins)} ({win_rate:.1%})\n"
            report += f"Losses:           {len(losses)}\n"
            report += f"Total P&L:        ${total_pnl:,.2f}\n"
            report += f"Avg Win:          ${avg_win:,.2f}\n"
            report += f"Avg Loss:         ${avg_loss:,.2f}\n"
            report += f"Profit Factor:    {profit_factor:.2f}\n"
            report += f"Sharpe Ratio:     {sharpe:.2f}\n"
            report += f"Max Drawdown:     {drawdown['max_drawdown_percent']:.2f}%\n"
            report += f"\n--- Execution Metrics (Grok's Request - Feb 13) ---\n"
            report += f"Avg Time to Stop: {avg_time_to_stopout_min:.1f} min ({len(stopped_trades)} stopped trades)\n"
            report += f"Avg Give-Back:    {avg_give_back:.2f}% ({len(trades_with_giveback)} trades)\n"
            report += f"Fill Type:        LIMIT {fill_rate_limit:.0f}% | MARKET {fill_rate_market:.0f}%\n"
            report += f"Avg Fill Time:    {avg_fill_time:.1f} sec ({len(trades_with_fill_time)} trades)\n"
            report += f"\n{'='*60}\n"
            return report

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


# Example usage
if __name__ == "__main__":
    tracker = PerformanceTracker("test_performance.db")

    # Record a sample trade
    trade = Trade(
        trade_id="TRADE001",
        symbol="AAPL",
        entry_time=datetime(2026, 1, 16, 9, 35),
        exit_time=datetime(2026, 1, 16, 15, 55),
        entry_price=150.00,
        exit_price=152.50,
        quantity=100,
        side="long",
        realized_pnl=250.00,
        realized_pnl_percent=1.67,
        strategy="Momentum Breakout",
        target_price=153.00,
        stop_price=148.50,
        exit_reason="target"
    )

    tracker.record_trade(trade)

    # Generate report
    report = tracker.generate_report(date.today(), date.today())
    print(report)

    tracker.close()
