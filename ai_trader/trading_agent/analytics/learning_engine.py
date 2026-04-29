"""
Learning Engine - Adaptive learning system for trading agent

Analyzes historical trades to:
1. Detect patterns (what works, what doesn't)
2. Track symbol/strategy/time-of-day performance
3. Generate lessons for LLM context injection
4. Build an "avoid list" of underperforming symbols

The goal is to help the LLM agent learn from past mistakes
and improve decision-making over time.
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class SymbolStats:
    """Performance stats for a single symbol."""
    symbol: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    last_trade: str
    status: str  # WORKING, CAUTION, AVOID
    reason: Optional[str] = None


@dataclass
class StrategyStats:
    """Performance stats for a trading strategy."""
    strategy: str
    total_trades: int
    wins: int
    win_rate: float
    avg_pnl: float
    status: str
    reason: Optional[str] = None


class LearningEngine:
    """
    Analyzes trade history to generate actionable lessons for the LLM agent.

    Features:
    - Symbol performance tracking (auto-generate avoid list)
    - Strategy performance comparison
    - Time-of-day analysis
    - Market regime analysis (VIX-based)
    - Pattern detection
    - Daily lesson generation
    - Persistent database for long-term learning (via LearningDatabase)
    """

    def __init__(self, data_dir: Path = None, performance_tracker=None, data_provider=None,
                 use_database: bool = True):
        """
        Initialize learning engine.

        Args:
            data_dir: Directory for storing lessons JSON
            performance_tracker: PerformanceTracker instance for trade data
            data_provider: Market data provider for fetching historical prices
            use_database: Whether to sync lessons to persistent database
        """
        if data_dir is None:
            # Default to ai_trader_data directory
            script_dir = Path(__file__).parent
            data_dir = script_dir.parent.parent / "ai_trader_data"

        self.data_dir = Path(data_dir)
        self.lessons_file = self.data_dir / "trading_lessons.json"
        self.performance_tracker = performance_tracker
        self.data_provider = data_provider
        self.learning_window_days = 30  # How far back to analyze
        self.use_database = use_database

        # Initialize persistent database
        self.learning_db = None
        if use_database:
            try:
                from .learning_database import LearningDatabase
                self.learning_db = LearningDatabase(self.data_dir / "learning.db")
                logger.info("Learning database connected for persistent storage")
            except Exception as e:
                logger.warning(f"Failed to initialize learning database: {e}")
                self.learning_db = None

        # Load existing lessons or create new
        self.lessons = self._load_lessons()

    def _load_lessons(self) -> Dict:
        """Load lessons from JSON file or create default structure."""
        if self.lessons_file.exists():
            try:
                with open(self.lessons_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load lessons file: {e}")

        # Return default structure
        return self._create_default_lessons()

    def _create_default_lessons(self) -> Dict:
        """Create default lessons structure."""
        return {
            "version": "1.2",
            "last_updated": datetime.now().isoformat(),
            "learning_window_days": self.learning_window_days,
            "symbol_performance": {},
            "strategy_performance": {},
            "time_of_day_performance": {},
            "market_regime_performance": {},
            "avoid_list": {"symbols": []},
            "recent_lessons": {"lessons": []},
            "aggregate_stats": {},
            "trade_insights": [],  # Deep analysis of individual trades
            # New statistical sections
            "risk_metrics": {},  # Expectancy, profit factor, R:R ratio
            "streak_analysis": {},  # Win/loss streaks, tilt detection
            "hold_time_analysis": {},  # Avg hold time winners vs losers
            "edge_metrics": {}  # Rolling win rates, edge decay detection
        }

    def _save_lessons(self):
        """Save lessons to JSON file."""
        try:
            self.lessons["last_updated"] = datetime.now().isoformat()
            with open(self.lessons_file, 'w') as f:
                json.dump(self.lessons, f, indent=2, default=str)
            logger.info(f"Saved lessons to {self.lessons_file}")
        except Exception as e:
            logger.error(f"Failed to save lessons: {e}")

    def analyze_trades(self, trades: List[Dict] = None) -> Dict:
        """
        Analyze trade history and update all performance metrics.

        Args:
            trades: List of trade dicts. If None, fetches from performance_tracker.

        Returns:
            Updated lessons dict
        """
        if trades is None and self.performance_tracker:
            # Get trades from last N days
            end_date = date.today()
            start_date = end_date - timedelta(days=self.learning_window_days)
            trades = self.performance_tracker.get_trades(start_date, end_date)

        if not trades:
            logger.info("No trades to analyze")
            return self.lessons

        # Normalize to dicts - performance_tracker returns Trade dataclasses,
        # but all analysis methods use .get() dict-style access
        import dataclasses as _dc
        trades = [_dc.asdict(t) if _dc.is_dataclass(t) else t for t in trades]

        logger.info(f"Analyzing {len(trades)} trades for patterns...")

        # Analyze different dimensions
        self._analyze_symbol_performance(trades)
        self._analyze_strategy_performance(trades)
        self._analyze_time_of_day(trades)
        self._update_avoid_list()
        self._calculate_aggregate_stats(trades)

        # Advanced statistical analysis
        self._calculate_risk_metrics(trades)
        self._analyze_streaks(trades)
        self._analyze_hold_times(trades)
        self._detect_edge_decay(trades)

        # Deep analysis if data provider available
        if self.data_provider:
            try:
                logger.info("Running deep trade analysis with price data...")
                insights = self.analyze_trades_deep(trades)
                logger.info(f"Generated {len(insights)} trade insights")
            except Exception as e:
                logger.warning(f"Deep trade analysis failed: {e}")

        # Sync to persistent database
        if self.learning_db:
            try:
                self._sync_to_database(trades)
            except Exception as e:
                logger.warning(f"Failed to sync to learning database: {e}")

        # Save updated lessons
        self._save_lessons()

        return self.lessons

    def _sync_to_database(self, trades: List[Dict]):
        """Sync analysis results to persistent learning database."""
        if not self.learning_db:
            return

        # 1. Update symbol history for each trade
        for trade in trades:
            symbol = trade.get("symbol")
            pnl = trade.get("realized_pnl", 0)
            won = pnl > 0
            if symbol:
                self.learning_db.update_symbol_stats(symbol, pnl, won)

                # 2. Add loss analysis for losing trades ("why" autopsy)
                if not won and pnl < -10:  # Only analyze meaningful losses
                    self._add_loss_analysis(trade)

        # 3. Sync trade insights as STRUCTURED lessons (with evidence)
        insights = self.lessons.get("trade_insights", [])
        losing_insights = [i for i in insights if not i.get("won") and i.get("lesson")]
        for insight in losing_insights:
            lesson = insight.get("lesson", "")
            symbol = insight.get("symbol")
            analysis = insight.get("analysis", {})

            # Skip generic "review" lessons
            if "review" in lesson.lower():
                continue

            # Create structured lesson from analysis
            condition = None
            action = None

            # Parse analysis for structured format
            entry_quality = analysis.get("entry", {}).get("quality")
            extended_status = analysis.get("extended", {}).get("status")

            if entry_quality == "POOR":
                condition = f"{symbol} entry when price near day's high"
                action = "SKIP - likely chasing, high fade risk"
            elif extended_status in ["EXTENDED", "VERY_EXTENDED"]:
                move_pct = analysis.get("extended", {}).get("move_from_open_pct", 0)
                condition = f"{symbol} up >{move_pct:.0f}% from open"
                action = f"CAUTION - extended stocks fade often"

            if condition and action:
                self.learning_db.add_structured_lesson(
                    condition=condition,
                    action=action,
                    category="trade_insight",
                    symbol=symbol,
                    evidence_count=1,
                    evidence_summary=lesson,
                    source="deep_analysis"
                )

        # 4. Sync symbols to avoid as STRUCTURED lessons (requires 5+ trades now)
        for symbol, stats in self.lessons.get("symbol_performance", {}).items():
            total_trades = stats.get("total_trades", 0)
            win_rate = stats.get("win_rate", 0)

            # Use new threshold: 5+ trades for AVOID lessons
            if stats.get("status") == "AVOID" and total_trades >= 5:
                self.learning_db.add_structured_lesson(
                    condition=f"Considering {symbol} for entry",
                    action=f"AVOID - {win_rate*100:.0f}% win rate over {total_trades} trades",
                    category="symbol_avoid",
                    symbol=symbol,
                    evidence_count=total_trades,
                    evidence_summary=f"Lost on {stats.get('losses', 0)} of {total_trades} trades",
                    confidence=0.7,
                    source="symbol_analysis"
                )

        # 5. Sync time-of-day lessons as time-bound structured lessons
        time_perf = self.lessons.get("time_of_day_performance", {})
        for time_key, data in time_perf.items():
            if data.get("status") == "AVOID" and data.get("total_trades", 0) >= 5:
                # Parse time range (e.g., "06:30-08:00")
                times = time_key.split("-")
                if len(times) == 2:
                    self.learning_db.add_structured_lesson(
                        condition=f"Entry opportunity during {data.get('label', time_key)}",
                        action=f"CAUTION - {data.get('win_rate', 0)*100:.0f}% win rate in this window",
                        category="time_avoid",
                        evidence_count=data.get("total_trades", 0),
                        valid_time_start=times[0],
                        valid_time_end=times[1],
                        source="time_analysis"
                    )

        logger.debug("Synced analysis results to learning database")

    def _add_loss_analysis(self, trade: Dict):
        """Add detailed loss analysis ("autopsy") for a losing trade."""
        if not self.learning_db:
            return

        symbol = trade.get("symbol")
        pnl = trade.get("realized_pnl", 0)
        entry_price = trade.get("entry_price")
        exit_price = trade.get("exit_price")
        strategy = trade.get("strategy", "unknown")

        # Calculate loss percent
        loss_percent = None
        if entry_price and entry_price > 0:
            loss_percent = ((entry_price - exit_price) / entry_price) * 100

        # Get analysis from trade_insights if available
        insights = self.lessons.get("trade_insights", [])
        matching_insight = next((i for i in insights if i.get("symbol") == symbol), None)

        why_entered = f"Strategy: {strategy}"
        why_failed = "Unknown"
        entry_quality = None
        loss_category = None
        was_preventable = False

        if matching_insight:
            analysis = matching_insight.get("analysis", {})

            # Entry quality
            entry_data = analysis.get("entry", {})
            entry_quality = entry_data.get("quality")
            if entry_quality == "POOR":
                why_failed = "Chased entry near day's high"
                loss_category = "chased_entry"
                was_preventable = True
            elif entry_quality == "MEDIOCRE":
                why_failed = "Entry timing not ideal"
                loss_category = "poor_timing"

            # Extended stock
            extended = analysis.get("extended", {})
            if extended.get("status") in ["EXTENDED", "VERY_EXTENDED"]:
                move_pct = extended.get("move_from_open_pct", 0)
                why_failed = f"Stock already up {move_pct:.0f}% - entered extended move"
                loss_category = "extended_fade"
                was_preventable = True

            # Post-entry action
            post_entry = analysis.get("post_entry", {})
            if post_entry.get("immediate_action") == "DROPPED":
                why_failed = "Price dropped immediately after entry"
                if loss_category is None:
                    loss_category = "bad_timing"

            # Exit quality
            exit_data = analysis.get("exit", {})
            if exit_data.get("quality") == "VERY_POOR":
                why_failed += "; held too long (exited near day low)"
                loss_category = "held_too_long"
                was_preventable = True

        try:
            self.learning_db.add_loss_analysis(
                symbol=symbol,
                loss_amount=pnl,
                why_entered=why_entered,
                why_failed=why_failed,
                entry_price=entry_price,
                exit_price=exit_price,
                loss_percent=loss_percent,
                entry_quality=entry_quality,
                pattern_attempted=strategy,
                loss_category=loss_category,
                was_preventable=was_preventable
            )
        except Exception as e:
            logger.warning(f"Failed to add loss analysis for {symbol}: {e}")

    def _analyze_symbol_performance(self, trades: List[Dict]):
        """Analyze win rate by symbol."""
        symbol_stats = defaultdict(lambda: {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "last_trade": None
        })

        for trade in trades:
            symbol = trade.get("symbol", "UNKNOWN")
            pnl = trade.get("realized_pnl", 0)
            trade_date = trade.get("exit_time", datetime.now())
            if isinstance(trade_date, str):
                trade_date = datetime.fromisoformat(trade_date)

            stats = symbol_stats[symbol]
            stats["total_trades"] += 1
            stats["total_pnl"] += pnl

            if pnl > 0:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

            if stats["last_trade"] is None or trade_date > datetime.fromisoformat(stats["last_trade"]):
                stats["last_trade"] = trade_date.date().isoformat()

        # Convert to lessons format
        symbol_perf = {}
        for symbol, stats in symbol_stats.items():
            win_rate = stats["wins"] / stats["total_trades"] if stats["total_trades"] > 0 else 0
            avg_pnl = stats["total_pnl"] / stats["total_trades"] if stats["total_trades"] > 0 else 0

            # Determine status
            if stats["total_trades"] >= 3 and win_rate < 0.25:
                status = "AVOID"
                reason = f"{win_rate*100:.0f}% win rate ({stats['wins']}/{stats['total_trades']} trades)"
            elif win_rate < 0.40:
                status = "CAUTION"
                reason = f"Below average win rate: {win_rate*100:.0f}%"
            elif win_rate >= 0.60:
                status = "WORKING"
                reason = None
            else:
                status = "NEUTRAL"
                reason = None

            symbol_perf[symbol] = {
                "total_trades": stats["total_trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": round(win_rate, 2),
                "avg_pnl": round(avg_pnl, 2),
                "last_trade": stats["last_trade"],
                "status": status,
                "reason": reason
            }

        self.lessons["symbol_performance"] = symbol_perf

    def _analyze_strategy_performance(self, trades: List[Dict]):
        """Analyze win rate by strategy."""
        strategy_stats = defaultdict(lambda: {
            "total_trades": 0,
            "wins": 0,
            "total_pnl": 0.0
        })

        for trade in trades:
            strategy = trade.get("strategy", "unknown")
            pnl = trade.get("realized_pnl", 0)

            stats = strategy_stats[strategy]
            stats["total_trades"] += 1
            stats["total_pnl"] += pnl

            if pnl > 0:
                stats["wins"] += 1

        # Convert to lessons format
        strategy_perf = {}
        for strategy, stats in strategy_stats.items():
            win_rate = stats["wins"] / stats["total_trades"] if stats["total_trades"] > 0 else 0
            avg_pnl = stats["total_pnl"] / stats["total_trades"] if stats["total_trades"] > 0 else 0

            if win_rate < 0.30:
                status = "UNDERPERFORMING"
                reason = f"Only {win_rate*100:.0f}% win rate - use sparingly"
            elif win_rate >= 0.55:
                status = "WORKING"
                reason = None
            else:
                status = "NEUTRAL"
                reason = None

            strategy_perf[strategy] = {
                "total_trades": stats["total_trades"],
                "wins": stats["wins"],
                "win_rate": round(win_rate, 2),
                "avg_pnl": round(avg_pnl, 2),
                "status": status,
                "reason": reason
            }

        self.lessons["strategy_performance"] = strategy_perf

    def _analyze_time_of_day(self, trades: List[Dict]):
        """Analyze win rate by time of day."""
        time_windows = {
            "06:30-08:00": {"label": "Market Open (First 90 min)", "start": 6.5, "end": 8.0},
            "08:00-10:00": {"label": "Mid-Morning", "start": 8.0, "end": 10.0},
            "10:00-12:00": {"label": "Late Morning Lull", "start": 10.0, "end": 12.0},
            "12:00-13:00": {"label": "Power Hour", "start": 12.0, "end": 13.0}
        }

        time_stats = {k: {"total_trades": 0, "wins": 0} for k in time_windows}

        for trade in trades:
            entry_time = trade.get("entry_time")
            if entry_time is None:
                continue

            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)

            # Convert to PST hour (approximate)
            hour = entry_time.hour + entry_time.minute / 60
            # Adjust if in ET (subtract 3 hours for PST)
            if hour > 9:  # Likely ET
                hour -= 3

            pnl = trade.get("realized_pnl", 0)

            for window_key, window_info in time_windows.items():
                if window_info["start"] <= hour < window_info["end"]:
                    time_stats[window_key]["total_trades"] += 1
                    if pnl > 0:
                        time_stats[window_key]["wins"] += 1
                    break

        # Convert to lessons format
        time_perf = {}
        for window_key, stats in time_stats.items():
            win_rate = stats["wins"] / stats["total_trades"] if stats["total_trades"] > 0 else 0

            if stats["total_trades"] >= 3 and win_rate < 0.25:
                status = "AVOID"
                reason = f"{win_rate*100:.0f}% win rate - avoid entries in this window"
            elif win_rate >= 0.60:
                status = "OPTIMAL"
                reason = None
            elif win_rate >= 0.45:
                status = "GOOD"
                reason = None
            elif stats["total_trades"] > 0:
                status = "CAUTION"
                reason = None
            else:
                status = "NO_DATA"
                reason = None

            time_perf[window_key] = {
                "label": time_windows[window_key]["label"],
                "total_trades": stats["total_trades"],
                "wins": stats["wins"],
                "win_rate": round(win_rate, 2),
                "status": status,
                "reason": reason
            }

        self.lessons["time_of_day_performance"] = time_perf

    def _update_avoid_list(self):
        """Update the avoid list based on symbol performance."""
        avoid_symbols = []

        for symbol, stats in self.lessons.get("symbol_performance", {}).items():
            if stats.get("status") == "AVOID":
                avoid_symbols.append({
                    "symbol": symbol,
                    "reason": stats.get("reason", "Poor performance"),
                    "expires": (date.today() + timedelta(days=14)).isoformat(),
                    "added": date.today().isoformat()
                })

        self.lessons["avoid_list"]["symbols"] = avoid_symbols

    def _calculate_aggregate_stats(self, trades: List[Dict]):
        """Calculate overall aggregate statistics."""
        if not trades:
            return

        total_pnl = sum(t.get("realized_pnl", 0) for t in trades)
        wins = sum(1 for t in trades if t.get("realized_pnl", 0) > 0)
        win_rate = wins / len(trades) if trades else 0

        # Find best/worst strategy
        strategy_perf = self.lessons.get("strategy_performance", {})
        best_strategy = max(strategy_perf.items(), key=lambda x: x[1].get("win_rate", 0))[0] if strategy_perf else None
        worst_strategy = min(strategy_perf.items(), key=lambda x: x[1].get("win_rate", 1))[0] if strategy_perf else None

        # Find best/worst time window
        time_perf = self.lessons.get("time_of_day_performance", {})
        time_items = [(k, v) for k, v in time_perf.items() if v.get("total_trades", 0) > 0]
        best_time = max(time_items, key=lambda x: x[1].get("win_rate", 0))[0] if time_items else None
        worst_time = min(time_items, key=lambda x: x[1].get("win_rate", 1))[0] if time_items else None

        self.lessons["aggregate_stats"] = {
            "total_trades_30d": len(trades),
            "win_rate_30d": round(win_rate, 2),
            "total_pnl_30d": round(total_pnl, 2),
            "best_strategy": best_strategy,
            "worst_strategy": worst_strategy,
            "best_time_window": best_time,
            "worst_time_window": worst_time
        }

    # =========================================================================
    # RISK METRICS - Expectancy, Profit Factor, Win/Loss Ratio
    # =========================================================================

    def _calculate_risk_metrics(self, trades: List[Dict]):
        """
        Calculate key risk metrics for the trading system.

        Metrics:
        - Expectancy: Expected $ per trade = (Win% * AvgWin) - (Loss% * AvgLoss)
        - Profit Factor: Gross Profits / Gross Losses (should be > 1.5)
        - Win/Loss Ratio: Avg Win / Avg Loss (reward to risk)
        - Max Drawdown: Worst peak-to-trough decline
        - Recovery Factor: Total Profit / Max Drawdown
        """
        if not trades:
            return

        # Separate wins and losses
        wins = [t for t in trades if t.get("realized_pnl", 0) > 0]
        losses = [t for t in trades if t.get("realized_pnl", 0) <= 0]

        # Basic counts
        num_wins = len(wins)
        num_losses = len(losses)
        total_trades = len(trades)

        if total_trades == 0:
            return

        win_rate = num_wins / total_trades
        loss_rate = num_losses / total_trades

        # Calculate averages
        gross_profit = sum(t.get("realized_pnl", 0) for t in wins)
        gross_loss = abs(sum(t.get("realized_pnl", 0) for t in losses))

        avg_win = gross_profit / num_wins if num_wins > 0 else 0
        avg_loss = gross_loss / num_losses if num_losses > 0 else 0

        # Expectancy: Expected $ per trade
        # Formula: (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
        expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

        # Profit Factor: Gross Profit / Gross Loss
        # Should be > 1.5 for a good system
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        # Win/Loss Ratio (Reward:Risk)
        win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf') if avg_win > 0 else 0

        # Calculate drawdown (running P&L curve)
        running_pnl = 0
        peak_pnl = 0
        max_drawdown = 0
        drawdowns = []

        for trade in sorted(trades, key=lambda t: t.get("exit_time", "")):
            running_pnl += trade.get("realized_pnl", 0)
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            drawdown = peak_pnl - running_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            if drawdown > 0:
                drawdowns.append(drawdown)

        # Average drawdown
        avg_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else 0

        # Recovery Factor: Total Profit / Max Drawdown
        total_pnl = sum(t.get("realized_pnl", 0) for t in trades)
        recovery_factor = total_pnl / max_drawdown if max_drawdown > 0 else float('inf') if total_pnl > 0 else 0

        # Determine system health - require minimum trades to avoid false alarms
        MIN_TRADES_FOR_ASSESSMENT = 10
        # If all PnL values are zero it means data quality issue (Trade dataclass .get() bug),
        # not a genuinely unprofitable system - treat as insufficient data
        all_pnl_zero = (gross_profit == 0 and gross_loss == 0)
        if total_trades < MIN_TRADES_FOR_ASSESSMENT or all_pnl_zero:
            system_status = "INSUFFICIENT DATA"
            system_note = f"Need {MIN_TRADES_FOR_ASSESSMENT}+ trades for assessment ({total_trades} so far)"
        elif expectancy > 20 and profit_factor > 1.5:
            system_status = "HEALTHY"
            system_note = f"Positive edge: ${expectancy:.2f}/trade expected"
        elif expectancy > 0 and profit_factor > 1.0:
            system_status = "MARGINAL"
            system_note = f"Small edge: ${expectancy:.2f}/trade - tighten risk management"
        elif expectancy <= 0:
            system_status = "UNPROFITABLE"
            system_note = f"Negative expectancy: ${expectancy:.2f}/trade - review strategy"
        else:
            system_status = "UNKNOWN"
            system_note = "Insufficient data"

        self.lessons["risk_metrics"] = {
            "expectancy": round(expectancy, 2),
            "expectancy_note": f"Expected ${expectancy:.2f} per trade",
            "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "inf",
            "profit_factor_note": "Good" if profit_factor > 1.5 else "Marginal" if profit_factor > 1.0 else "Poor",
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "win_loss_ratio": round(win_loss_ratio, 2) if win_loss_ratio != float('inf') else "inf",
            "win_loss_note": f"Avg win is {win_loss_ratio:.1f}x avg loss" if win_loss_ratio != float('inf') else "No losses",
            "max_drawdown": round(max_drawdown, 2),
            "avg_drawdown": round(avg_drawdown, 2),
            "recovery_factor": round(recovery_factor, 2) if recovery_factor != float('inf') else "inf",
            "system_status": system_status,
            "system_note": system_note
        }

    # =========================================================================
    # STREAK ANALYSIS - Win/Loss streaks, Tilt Detection
    # =========================================================================

    def _analyze_streaks(self, trades: List[Dict]):
        """
        Analyze win/loss streaks and detect potential tilt behavior.

        Tilt = Performance degradation after losses (revenge trading).
        """
        if not trades:
            return

        # Sort trades by time
        sorted_trades = sorted(trades, key=lambda t: t.get("exit_time", ""))

        # Track streaks
        current_streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        streaks = []  # List of (streak_type, length)

        prev_won = None
        streak_length = 0

        for trade in sorted_trades:
            won = trade.get("realized_pnl", 0) > 0

            if prev_won is None:
                streak_length = 1
            elif won == prev_won:
                streak_length += 1
            else:
                # Streak ended
                streaks.append(("win" if prev_won else "loss", streak_length))
                streak_length = 1

            prev_won = won

        # Record final streak
        if prev_won is not None:
            streaks.append(("win" if prev_won else "loss", streak_length))
            current_streak = streak_length if prev_won else -streak_length

        # Find max streaks
        for streak_type, length in streaks:
            if streak_type == "win" and length > max_win_streak:
                max_win_streak = length
            elif streak_type == "loss" and length > max_loss_streak:
                max_loss_streak = length

        # Tilt detection: Compare performance after losses vs normal
        # Look at trades that followed a loss
        post_loss_trades = []
        post_win_trades = []

        for i in range(1, len(sorted_trades)):
            prev_pnl = sorted_trades[i-1].get("realized_pnl", 0)
            current_pnl = sorted_trades[i].get("realized_pnl", 0)

            if prev_pnl < 0:
                post_loss_trades.append(current_pnl)
            else:
                post_win_trades.append(current_pnl)

        # Calculate win rates after loss vs after win
        post_loss_wins = sum(1 for p in post_loss_trades if p > 0)
        post_win_wins = sum(1 for p in post_win_trades if p > 0)

        post_loss_win_rate = post_loss_wins / len(post_loss_trades) if post_loss_trades else 0
        post_win_win_rate = post_win_wins / len(post_win_trades) if post_win_trades else 0

        # Tilt indicator: significant drop in win rate after losses
        tilt_detected = False
        tilt_severity = "NONE"
        tilt_note = None

        if len(post_loss_trades) >= 3 and len(post_win_trades) >= 3:
            win_rate_drop = post_win_win_rate - post_loss_win_rate

            if win_rate_drop > 0.3:  # 30%+ drop in win rate after losses
                tilt_detected = True
                tilt_severity = "SEVERE"
                tilt_note = f"Win rate drops {win_rate_drop*100:.0f}% after losses - possible revenge trading"
            elif win_rate_drop > 0.15:  # 15%+ drop
                tilt_detected = True
                tilt_severity = "MODERATE"
                tilt_note = f"Win rate drops {win_rate_drop*100:.0f}% after losses - consider pausing after losses"
            elif win_rate_drop > 0.05:
                tilt_severity = "MILD"
                tilt_note = f"Slight performance dip ({win_rate_drop*100:.0f}%) after losses"

        # Psychological state based on current streak
        if current_streak >= 3:
            psych_state = "CONFIDENT"
            psych_note = f"On a {current_streak}-trade winning streak - stay disciplined, don't get overconfident"
        elif current_streak <= -3:
            psych_state = "AT_RISK"
            psych_note = f"On a {abs(current_streak)}-trade losing streak - consider reducing size or pausing"
        elif current_streak <= -2:
            psych_state = "CAUTION"
            psych_note = "Back-to-back losses - stick to A+ setups only"
        else:
            psych_state = "NORMAL"
            psych_note = None

        self.lessons["streak_analysis"] = {
            "current_streak": current_streak,
            "current_streak_note": f"{'Winning' if current_streak > 0 else 'Losing'} streak of {abs(current_streak)}" if current_streak != 0 else "No streak",
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "post_loss_win_rate": round(post_loss_win_rate, 2),
            "post_win_win_rate": round(post_win_win_rate, 2),
            "tilt_detected": tilt_detected,
            "tilt_severity": tilt_severity,
            "tilt_note": tilt_note,
            "psychological_state": psych_state,
            "psychological_note": psych_note
        }

    # =========================================================================
    # HOLD TIME ANALYSIS - Winners vs Losers
    # =========================================================================

    def _analyze_hold_times(self, trades: List[Dict]):
        """
        Analyze hold times for winning vs losing trades.

        Common pattern: Holding losers too long (hoping for recovery)
        while cutting winners too early.
        """
        if not trades:
            return

        winner_hold_times = []
        loser_hold_times = []

        for trade in trades:
            entry_time = trade.get("entry_time")
            exit_time = trade.get("exit_time")
            pnl = trade.get("realized_pnl", 0)

            if entry_time and exit_time:
                # Parse times
                if isinstance(entry_time, str):
                    entry_time = datetime.fromisoformat(entry_time)
                if isinstance(exit_time, str):
                    exit_time = datetime.fromisoformat(exit_time)

                # Calculate hold time in minutes
                hold_minutes = (exit_time - entry_time).total_seconds() / 60

                if hold_minutes > 0:  # Valid hold time
                    if pnl > 0:
                        winner_hold_times.append(hold_minutes)
                    else:
                        loser_hold_times.append(hold_minutes)

        # Calculate averages
        avg_winner_hold = sum(winner_hold_times) / len(winner_hold_times) if winner_hold_times else 0
        avg_loser_hold = sum(loser_hold_times) / len(loser_hold_times) if loser_hold_times else 0

        # Hold ratio: loser_time / winner_time
        # Ratio > 1.5 means holding losers significantly longer than winners (bad)
        hold_ratio = avg_loser_hold / avg_winner_hold if avg_winner_hold > 0 else 0

        # Determine issue
        hold_issue = None
        hold_recommendation = None

        if hold_ratio > 2.0:
            hold_issue = "SEVERE"
            hold_recommendation = f"Holding losers {hold_ratio:.1f}x longer than winners - cut losses faster"
        elif hold_ratio > 1.5:
            hold_issue = "MODERATE"
            hold_recommendation = f"Holding losers {hold_ratio:.1f}x longer than winners - tighten stops"
        elif hold_ratio < 0.5 and avg_winner_hold > 0:
            hold_issue = "CUTTING_WINNERS"
            hold_recommendation = "Cutting winners too fast - let profits run longer"
        else:
            hold_issue = "BALANCED"
            hold_recommendation = None

        self.lessons["hold_time_analysis"] = {
            "avg_winner_hold_minutes": round(avg_winner_hold, 1),
            "avg_loser_hold_minutes": round(avg_loser_hold, 1),
            "hold_ratio": round(hold_ratio, 2),
            "hold_ratio_note": f"Losers held {hold_ratio:.1f}x longer than winners" if hold_ratio > 0 else "N/A",
            "num_winners_analyzed": len(winner_hold_times),
            "num_losers_analyzed": len(loser_hold_times),
            "issue": hold_issue,
            "recommendation": hold_recommendation
        }

    # =========================================================================
    # EDGE DECAY DETECTION - Rolling Win Rates
    # =========================================================================

    def _detect_edge_decay(self, trades: List[Dict]):
        """
        Detect if the trading edge is decaying over time.

        Compare recent performance (last 10 trades) vs overall (last 30).
        Declining win rate suggests strategy may be losing effectiveness.
        """
        if len(trades) < 10:
            self.lessons["edge_metrics"] = {
                "note": "Insufficient trades for edge analysis (need 10+)"
            }
            return

        # Sort by time
        sorted_trades = sorted(trades, key=lambda t: t.get("exit_time", ""))

        # Calculate rolling win rates
        def calc_win_rate(trade_list):
            if not trade_list:
                return 0
            wins = sum(1 for t in trade_list if t.get("realized_pnl", 0) > 0)
            return wins / len(trade_list)

        # Last 10 trades (recent)
        recent_10 = sorted_trades[-10:]
        recent_10_wr = calc_win_rate(recent_10)

        # Last 20 trades (if available)
        recent_20_wr = calc_win_rate(sorted_trades[-20:]) if len(sorted_trades) >= 20 else None

        # All trades (baseline)
        overall_wr = calc_win_rate(sorted_trades)

        # Calculate rolling expectancy for recent trades
        recent_wins = [t.get("realized_pnl", 0) for t in recent_10 if t.get("realized_pnl", 0) > 0]
        recent_losses = [abs(t.get("realized_pnl", 0)) for t in recent_10 if t.get("realized_pnl", 0) <= 0]

        recent_avg_win = sum(recent_wins) / len(recent_wins) if recent_wins else 0
        recent_avg_loss = sum(recent_losses) / len(recent_losses) if recent_losses else 0
        recent_expectancy = (recent_10_wr * recent_avg_win) - ((1 - recent_10_wr) * recent_avg_loss)

        # Determine trend
        edge_trend = "STABLE"
        edge_note = None

        wr_change = recent_10_wr - overall_wr

        if wr_change < -0.15:  # 15%+ drop in recent win rate
            edge_trend = "DECLINING"
            edge_note = f"Recent win rate ({recent_10_wr*100:.0f}%) is {abs(wr_change)*100:.0f}% below average - strategy may be losing edge"
        elif wr_change < -0.08:  # 8%+ drop
            edge_trend = "WEAKENING"
            edge_note = f"Recent win rate slightly below average ({wr_change*100:+.0f}%) - monitor closely"
        elif wr_change > 0.15:  # 15%+ improvement
            edge_trend = "IMPROVING"
            edge_note = f"Recent performance strong ({recent_10_wr*100:.0f}% win rate) - stay disciplined"
        elif wr_change > 0.08:
            edge_trend = "STRENGTHENING"
            edge_note = f"Recent performance improving ({wr_change*100:+.0f}%)"

        # Hot/Cold streaks in recent trades
        recent_pnl = sum(t.get("realized_pnl", 0) for t in recent_10)

        self.lessons["edge_metrics"] = {
            "rolling_10_win_rate": round(recent_10_wr, 2),
            "rolling_20_win_rate": round(recent_20_wr, 2) if recent_20_wr is not None else None,
            "overall_win_rate": round(overall_wr, 2),
            "win_rate_change": round(wr_change, 2),
            "recent_10_expectancy": round(recent_expectancy, 2),
            "recent_10_pnl": round(recent_pnl, 2),
            "edge_trend": edge_trend,
            "edge_note": edge_note,
            "total_trades_analyzed": len(sorted_trades)
        }

    # =========================================================================
    # DEEP TRADE ANALYSIS - Analyzes price action around each trade
    # =========================================================================

    def analyze_trades_deep(self, trades: List[Dict], trade_date: date = None) -> List[Dict]:
        """
        Perform deep analysis on trades using intraday price data.

        This analyzes:
        - Entry quality (where in day's range did we enter)
        - Extended stock detection (was it already up big)
        - Post-entry price action (did it immediately drop)
        - Exit quality (did we exit well or leave money on table)

        Args:
            trades: List of trade records from today
            trade_date: Date to analyze (defaults to today)

        Returns:
            List of trade insights with detailed analysis
        """
        if not self.data_provider:
            logger.warning("No data provider - skipping deep trade analysis")
            return []

        if trade_date is None:
            trade_date = date.today()

        insights = []

        for trade in trades:
            try:
                insight = self._analyze_single_trade(trade, trade_date)
                if insight:
                    insights.append(insight)
            except Exception as e:
                logger.warning(f"Failed to analyze trade {trade.get('symbol')}: {e}")

        # Store insights in lessons
        self.lessons["trade_insights"] = insights
        self._save_lessons()

        return insights

    def _analyze_single_trade(self, trade: Dict, trade_date: date) -> Optional[Dict]:
        """Analyze a single trade against intraday price data."""
        symbol = trade.get("symbol")
        if not symbol:
            return None

        # Get intraday price data
        price_data = self._get_intraday_prices(symbol, trade_date)
        if not price_data or len(price_data) < 10:
            logger.debug(f"Insufficient price data for {symbol}")
            return None

        # Extract trade details
        entry_price = trade.get("entry_price", 0)
        exit_price = trade.get("exit_price", 0)
        entry_time = trade.get("entry_time")
        exit_time = trade.get("exit_time")
        pnl = trade.get("realized_pnl", 0)
        strategy = trade.get("strategy", "unknown")

        if not entry_price or entry_price <= 0:
            return None

        # Calculate day's range
        day_high = max(p["price"] for p in price_data)
        day_low = min(p["price"] for p in price_data)
        day_range = day_high - day_low if day_high > day_low else 1
        open_price = price_data[0]["price"]

        # Build insight
        insight = {
            "symbol": symbol,
            "date": trade_date.isoformat(),
            "strategy": strategy,
            "pnl": round(pnl, 2),
            "won": pnl > 0,
            "analysis": {}
        }

        # 1. Entry Quality Analysis
        entry_analysis = self._analyze_entry_quality(
            entry_price, day_high, day_low, day_range, open_price
        )
        insight["analysis"]["entry"] = entry_analysis

        # 2. Extended Stock Detection
        extended_analysis = self._check_if_extended(
            entry_price, open_price, entry_time, price_data
        )
        insight["analysis"]["extended"] = extended_analysis

        # 3. Post-Entry Price Action
        if entry_time:
            post_entry = self._analyze_post_entry(
                entry_time, entry_price, price_data
            )
            insight["analysis"]["post_entry"] = post_entry

        # 4. Exit Quality Analysis
        if exit_price and exit_price > 0:
            exit_analysis = self._analyze_exit_quality(
                exit_price, entry_price, day_high, day_low, pnl
            )
            insight["analysis"]["exit"] = exit_analysis

        # Generate lesson from analysis
        insight["lesson"] = self._generate_trade_lesson(insight)

        return insight

    def _get_intraday_prices(self, symbol: str, trade_date: date) -> List[Dict]:
        """Fetch intraday price data for a symbol."""
        try:
            # Try to get intraday bars from data provider
            if hasattr(self.data_provider, 'get_intraday_bars'):
                bars = self.data_provider.get_intraday_bars(
                    symbol,
                    interval='5min',
                    start_date=trade_date,
                    end_date=trade_date
                )
                if bars:
                    return [{"time": b.timestamp, "price": b.close} for b in bars]

            # Fallback: try get_stock_data with intraday
            if hasattr(self.data_provider, 'get_stock_data'):
                data = self.data_provider.get_stock_data(symbol, period='1d', interval='5m')
                if data and hasattr(data, 'history'):
                    history = data.history
                    return [{"time": idx, "price": row['Close']} for idx, row in history.iterrows()]

            logger.debug(f"Could not fetch intraday data for {symbol}")
            return []

        except Exception as e:
            logger.debug(f"Error fetching intraday data for {symbol}: {e}")
            return []

    def _analyze_entry_quality(self, entry_price: float, day_high: float,
                               day_low: float, day_range: float,
                               open_price: float) -> Dict:
        """
        Analyze how good our entry was relative to the day's range.

        For longs: closer to day low = better entry
        """
        # Entry percentile: 0.0 = bought at day low (perfect), 1.0 = bought at day high (terrible)
        entry_percentile = (entry_price - day_low) / day_range if day_range > 0 else 0.5

        # Distance from open
        pct_from_open = ((entry_price - open_price) / open_price) * 100 if open_price > 0 else 0

        if entry_percentile < 0.25:
            quality = "EXCELLENT"
            note = "Entered near day's low - good entry"
        elif entry_percentile < 0.40:
            quality = "GOOD"
            note = "Entered in lower part of range"
        elif entry_percentile > 0.75:
            quality = "POOR"
            note = "Entered near day's high - chased the move"
        elif entry_percentile > 0.60:
            quality = "MEDIOCRE"
            note = "Entered in upper part of range"
        else:
            quality = "NEUTRAL"
            note = "Entered mid-range"

        return {
            "entry_percentile": round(entry_percentile, 2),
            "pct_from_open": round(pct_from_open, 2),
            "quality": quality,
            "note": note
        }

    def _check_if_extended(self, entry_price: float, open_price: float,
                           entry_time, price_data: List[Dict]) -> Dict:
        """
        Check if stock was already extended (up big) when we entered.

        Extended stocks have higher fade risk.
        """
        # Calculate how much stock moved from open to entry
        move_from_open = ((entry_price - open_price) / open_price) * 100 if open_price > 0 else 0

        # Get price at market open (first data point)
        # and price 30 min before our entry to see trend
        premarket_high = open_price
        for p in price_data[:6]:  # First 30 min (assuming 5-min bars)
            if p["price"] > premarket_high:
                premarket_high = p["price"]

        extended_from_open = move_from_open > 8  # Up 8%+ from open
        chased_move = move_from_open > 5 and entry_price > premarket_high

        if move_from_open > 15:
            status = "VERY_EXTENDED"
            risk = "HIGH"
            note = f"Stock was up {move_from_open:.1f}% from open - very high fade risk"
        elif move_from_open > 10:
            status = "EXTENDED"
            risk = "ELEVATED"
            note = f"Stock was up {move_from_open:.1f}% from open - elevated fade risk"
        elif move_from_open > 5:
            status = "MODERATELY_EXTENDED"
            risk = "MODERATE"
            note = f"Stock was up {move_from_open:.1f}% from open"
        else:
            status = "NOT_EXTENDED"
            risk = "NORMAL"
            note = "Entry was not chasing an extended move"

        return {
            "move_from_open_pct": round(move_from_open, 2),
            "status": status,
            "fade_risk": risk,
            "chased_move": chased_move,
            "note": note
        }

    def _analyze_post_entry(self, entry_time, entry_price: float,
                           price_data: List[Dict]) -> Dict:
        """
        Analyze what happened immediately after entry.

        Did price immediately drop (bad entry) or continue up (good entry)?
        """
        if isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)

        # Find prices after entry
        post_entry_prices = []
        for p in price_data:
            p_time = p["time"]
            if isinstance(p_time, str):
                p_time = datetime.fromisoformat(p_time)
            if p_time > entry_time:
                post_entry_prices.append(p["price"])

        if not post_entry_prices:
            return {"note": "No post-entry data available"}

        # Analyze first 15 minutes (3 bars of 5-min data)
        first_15_min = post_entry_prices[:3]
        if not first_15_min:
            return {"note": "Insufficient post-entry data"}

        min_after_entry = min(first_15_min)
        max_after_entry = max(first_15_min)

        immediate_drawdown = ((entry_price - min_after_entry) / entry_price) * 100 if entry_price > 0 else 0
        immediate_gain = ((max_after_entry - entry_price) / entry_price) * 100 if entry_price > 0 else 0

        if immediate_drawdown > 2:
            action = "DROPPED"
            note = f"Price dropped {immediate_drawdown:.1f}% immediately after entry - poor timing"
        elif immediate_gain > 2:
            action = "RALLIED"
            note = f"Price rallied {immediate_gain:.1f}% after entry - good timing"
        else:
            action = "FLAT"
            note = "Price stayed relatively flat after entry"

        # Check for max adverse excursion (worst drawdown during trade)
        max_drawdown = ((entry_price - min(post_entry_prices)) / entry_price) * 100 if entry_price > 0 else 0

        return {
            "immediate_action": action,
            "immediate_drawdown_pct": round(immediate_drawdown, 2),
            "immediate_gain_pct": round(immediate_gain, 2),
            "max_adverse_excursion_pct": round(max_drawdown, 2),
            "note": note
        }

    def _analyze_exit_quality(self, exit_price: float, entry_price: float,
                              day_high: float, day_low: float, pnl: float) -> Dict:
        """
        Analyze exit quality - did we exit well or leave money on table?
        """
        day_range = day_high - day_low if day_high > day_low else 1

        # For winning trades: how much of the potential gain did we capture?
        if pnl > 0:
            potential_gain = day_high - entry_price
            actual_gain = exit_price - entry_price
            capture_ratio = actual_gain / potential_gain if potential_gain > 0 else 0

            if capture_ratio > 0.7:
                quality = "EXCELLENT"
                note = f"Captured {capture_ratio*100:.0f}% of potential gain"
            elif capture_ratio > 0.5:
                quality = "GOOD"
                note = f"Captured {capture_ratio*100:.0f}% of potential gain"
            elif capture_ratio > 0.3:
                quality = "MEDIOCRE"
                note = f"Only captured {capture_ratio*100:.0f}% - left money on table"
            else:
                quality = "POOR"
                note = f"Only captured {capture_ratio*100:.0f}% - exited way too early"

            return {
                "quality": quality,
                "capture_ratio": round(capture_ratio, 2),
                "exited_near_high": exit_price >= day_high * 0.98,
                "note": note
            }
        else:
            # Losing trade - analyze if we held too long
            potential_loss = entry_price - day_low
            actual_loss = entry_price - exit_price

            if actual_loss > potential_loss * 0.9:
                quality = "VERY_POOR"
                note = "Exited near day's low - held too long"
            elif actual_loss > potential_loss * 0.7:
                quality = "POOR"
                note = "Exit was in lower range - could have cut earlier"
            else:
                quality = "ACCEPTABLE"
                note = "Stop loss worked as designed"

            return {
                "quality": quality,
                "exited_near_low": exit_price <= day_low * 1.02,
                "note": note
            }

    def _generate_trade_lesson(self, insight: Dict) -> str:
        """Generate a concise, actionable lesson from trade analysis."""
        symbol = insight["symbol"]
        analysis = insight["analysis"]
        won = insight["won"]
        pnl = insight["pnl"]

        lessons = []

        # Entry quality lesson
        entry = analysis.get("entry", {})
        if entry.get("quality") == "POOR":
            lessons.append(f"Chased {symbol} - entered near day's high")
        elif entry.get("quality") == "EXCELLENT" and won:
            lessons.append(f"Good entry on {symbol} near day's low")

        # Extended stock lesson
        extended = analysis.get("extended", {})
        if extended.get("status") in ["EXTENDED", "VERY_EXTENDED"] and not won:
            lessons.append(f"{symbol} was already up {extended.get('move_from_open_pct', 0):.0f}% - avoid late entries on extended stocks")

        # Post-entry lesson
        post_entry = analysis.get("post_entry", {})
        if post_entry.get("immediate_action") == "DROPPED" and not won:
            lessons.append(f"{symbol} dropped immediately after entry - wait for confirmation")

        # Exit lesson
        exit_info = analysis.get("exit", {})
        if exit_info.get("quality") == "POOR" and won:
            lessons.append(f"Exited {symbol} too early - captured only {exit_info.get('capture_ratio', 0)*100:.0f}%")
        elif exit_info.get("quality") == "VERY_POOR" and not won:
            lessons.append(f"Held {symbol} too long - exited near day's low")

        # Combine into final lesson
        if lessons:
            return "; ".join(lessons)
        elif won:
            return f"{symbol}: Clean trade, good execution (${pnl:+.2f})"
        else:
            return f"{symbol}: Loss (${pnl:.2f}) - review for improvements"

    def add_daily_lesson(self, date_str: str, trades_taken: int, wins: int,
                         pnl: float, key_insight: str,
                         what_worked: str = None, what_failed: str = None,
                         market_regime: str = "UNKNOWN"):
        """
        Add a daily lesson summary.

        Called at end of trading day to capture key learnings.
        """
        lesson = {
            "date": date_str,
            "market_regime": market_regime,
            "trades_taken": trades_taken,
            "wins": wins,
            "pnl": round(pnl, 2),
            "key_insight": key_insight,
            "what_worked": what_worked,
            "what_failed": what_failed
        }

        # Keep only last 10 lessons
        lessons_list = self.lessons.get("recent_lessons", {}).get("lessons", [])
        lessons_list.insert(0, lesson)
        lessons_list = lessons_list[:10]

        self.lessons["recent_lessons"]["lessons"] = lessons_list
        self._save_lessons()

        # Also sync to persistent database
        if self.learning_db:
            try:
                self.learning_db.add_daily_summary(
                    trade_date=date.fromisoformat(date_str) if isinstance(date_str, str) else date_str,
                    trades_taken=trades_taken,
                    wins=wins,
                    gross_pnl=pnl,
                    market_regime=market_regime,
                    key_insight=key_insight,
                    what_worked=what_worked,
                    what_failed=what_failed
                )

                # Add key insight as a lesson if notable
                if key_insight and pnl < -50:  # Significant loss day
                    self.learning_db.add_lesson(
                        f"[{date_str}] {key_insight}",
                        category="daily_lesson",
                        confidence=0.6,
                        source="daily_summary"
                    )
            except Exception as e:
                logger.warning(f"Failed to sync daily summary to database: {e}")

        logger.info(f"Added daily lesson for {date_str}: {key_insight}")

    def generate_llm_context(self) -> str:
        """
        Generate formatted context string to inject into LLM prompts.

        This is the key output - a concise summary of lessons learned
        that helps the LLM make better decisions.
        """
        context_parts = []

        context_parts.append("=== TRADING LESSONS (Learn from past trades) ===\n")

        # Risk Metrics Summary (most important)
        risk = self.lessons.get("risk_metrics", {})
        if risk and risk.get("expectancy") is not None:
            status = risk.get("system_status", "UNKNOWN")
            expectancy = risk.get("expectancy", 0)
            pf = risk.get("profit_factor", 0)
            wr_ratio = risk.get("win_loss_ratio", 0)

            # Skip the status block entirely if there is no real PnL data to base it on
            # (all-zero means corrupt/missing data, not a genuinely unprofitable system)
            has_real_data = not (expectancy == 0 and (pf == 0 or pf == "inf"))
            if has_real_data or status in ("HEALTHY", "MARGINAL", "INSUFFICIENT DATA"):
                status_emoji = "OK" if status == "HEALTHY" else "CAUTION" if status == "MARGINAL" else "INFO" if status == "INSUFFICIENT DATA" else "WARNING"
                context_parts.append(f"SYSTEM STATUS: {status_emoji} {status}")
                context_parts.append(f"  Expectancy: ${expectancy:.2f}/trade | Profit Factor: {pf} | Avg Win/Loss: {wr_ratio}:1")
                if risk.get("system_note"):
                    context_parts.append(f"  Note: {risk['system_note']}")
                context_parts.append("")

        # Streak & Psychological State (actionable)
        streak = self.lessons.get("streak_analysis", {})
        if streak:
            psych_state = streak.get("psychological_state", "NORMAL")
            if psych_state in ["AT_RISK", "CAUTION"]:
                context_parts.append(f"PSYCHOLOGICAL STATE: {psych_state}")
                if streak.get("psychological_note"):
                    context_parts.append(f"  {streak['psychological_note']}")
                context_parts.append("")

            # Tilt warning
            if streak.get("tilt_detected"):
                context_parts.append(f"TILT WARNING ({streak.get('tilt_severity', 'DETECTED')}):")
                context_parts.append(f"  {streak.get('tilt_note', 'Performance degrades after losses')}")
                context_parts.append("")

        # Hold Time Issue (if significant)
        hold = self.lessons.get("hold_time_analysis", {})
        if hold and hold.get("issue") in ["SEVERE", "MODERATE", "CUTTING_WINNERS"]:
            context_parts.append(f"HOLD TIME ISSUE: {hold.get('issue')}")
            context_parts.append(f"  {hold.get('recommendation')}")
            context_parts.append("")

        # Edge Decay Warning
        edge = self.lessons.get("edge_metrics", {})
        if edge and edge.get("edge_trend") in ["DECLINING", "WEAKENING"]:
            context_parts.append(f"EDGE WARNING: {edge.get('edge_trend')}")
            if edge.get("edge_note"):
                context_parts.append(f"  {edge['edge_note']}")
            context_parts.append("")

        # Aggregate stats
        agg = self.lessons.get("aggregate_stats", {})
        if agg:
            context_parts.append(f"30-Day Stats: {agg.get('win_rate_30d', 0)*100:.0f}% win rate, ${agg.get('total_pnl_30d', 0):.2f} P/L, {agg.get('total_trades_30d', 0)} trades")
            if agg.get("best_strategy"):
                context_parts.append(f"Best Strategy: {agg['best_strategy']}")
            if agg.get("worst_strategy"):
                context_parts.append(f"Worst Strategy: {agg['worst_strategy']} (use sparingly)")
            context_parts.append("")

        # Avoid list
        avoid_list = self.lessons.get("avoid_list", {}).get("symbols", [])
        if avoid_list:
            context_parts.append("SYMBOLS TO AVOID:")
            for item in avoid_list[:5]:  # Max 5
                context_parts.append(f"  - {item['symbol']}: {item['reason']}")
            context_parts.append("")

        # Strategy performance
        strategy_perf = self.lessons.get("strategy_performance", {})
        underperforming = [s for s, data in strategy_perf.items() if data.get("status") == "UNDERPERFORMING"]
        if underperforming:
            context_parts.append(f"UNDERPERFORMING STRATEGIES: {', '.join(underperforming)}")
            context_parts.append("")

        # Time of day
        time_perf = self.lessons.get("time_of_day_performance", {})
        avoid_times = [data.get("label") for k, data in time_perf.items() if data.get("status") == "AVOID"]
        if avoid_times:
            context_parts.append(f"AVOID ENTRIES DURING: {', '.join(avoid_times)}")
            context_parts.append("")

        # Recent lessons (last 3)
        recent = self.lessons.get("recent_lessons", {}).get("lessons", [])[:3]
        if recent:
            context_parts.append("RECENT LESSONS:")
            for lesson in recent:
                if lesson.get("key_insight"):
                    context_parts.append(f"  [{lesson['date']}] {lesson['key_insight']}")
            context_parts.append("")

        # Trade insights from deep analysis (most recent, most impactful)
        insights = self.lessons.get("trade_insights", [])
        # Filter to losing trades with actionable lessons
        actionable_insights = [
            i for i in insights
            if not i.get("won") and i.get("lesson") and "review" not in i.get("lesson", "").lower()
        ][:3]

        if actionable_insights:
            context_parts.append("TRADE ANALYSIS INSIGHTS:")
            for insight in actionable_insights:
                context_parts.append(f"  - {insight['lesson']}")
            context_parts.append("")

        context_parts.append("\n=== END LESSONS ===")

        # Add persistent database context if available
        if self.learning_db:
            try:
                db_context = self.learning_db.generate_llm_context()
                if db_context:
                    context_parts.append("\n")
                    context_parts.append(db_context)
            except Exception as e:
                logger.warning(f"Failed to get database context: {e}")

        return "\n".join(context_parts)

    def get_full_context_for_premarket(self, symbols_considering: List[str] = None) -> str:
        """
        Get comprehensive learning context for pre-market loading.

        Called at 5:00 AM PST to give the agent full historical context
        before the trading day begins.

        Args:
            symbols_considering: Symbols that might be traded today

        Returns:
            Combined context from JSON lessons and persistent database
        """
        context_parts = []

        # 1. Current 30-day stats from JSON
        json_context = self.generate_llm_context()
        context_parts.append(json_context)

        # 2. Database context with symbol-specific history
        if self.learning_db:
            try:
                db_context = self.learning_db.generate_llm_context(
                    symbols_considering=symbols_considering,
                    include_all_lessons=False
                )
                context_parts.append("\n" + db_context)

                # 3. Database stats
                stats = self.learning_db.get_stats()
                context_parts.append(f"\n[Learning DB: {stats['active_lessons']} lessons, "
                                    f"{stats['symbols_tracked']} symbols tracked, "
                                    f"{stats['days_recorded']} days recorded]")
            except Exception as e:
                logger.warning(f"Failed to get database context for premarket: {e}")

        return "\n".join(context_parts)

    def run_monthly_maintenance(self) -> Dict:
        """
        Run monthly database maintenance (compaction).

        Should be called on 1st of each month to compact previous month's data.
        """
        if not self.learning_db:
            return {"status": "skipped", "reason": "no_database"}

        return self.learning_db.run_monthly_maintenance()

    def get_symbol_recommendation(self, symbol: str) -> Dict:
        """
        Get recommendation for a specific symbol based on history.

        Returns dict with 'recommendation' (BUY/CAUTION/AVOID) and 'reason'.
        """
        symbol_perf = self.lessons.get("symbol_performance", {}).get(symbol.upper(), {})

        if not symbol_perf:
            return {"recommendation": "NO_DATA", "reason": "No trading history for this symbol"}

        status = symbol_perf.get("status", "NEUTRAL")

        if status == "AVOID":
            return {
                "recommendation": "AVOID",
                "reason": symbol_perf.get("reason", "Poor historical performance"),
                "win_rate": symbol_perf.get("win_rate", 0),
                "total_trades": symbol_perf.get("total_trades", 0)
            }
        elif status == "CAUTION":
            return {
                "recommendation": "CAUTION",
                "reason": symbol_perf.get("reason", "Below average performance"),
                "win_rate": symbol_perf.get("win_rate", 0)
            }
        else:
            return {
                "recommendation": "OK",
                "reason": None,
                "win_rate": symbol_perf.get("win_rate", 0)
            }


# Convenience function for quick context generation
def get_trading_lessons_context(data_dir: Path = None) -> str:
    """
    Quick function to load lessons and generate LLM context.

    Usage in prompts:
        lessons_context = get_trading_lessons_context()
        prompt = f"{lessons_context}\n\nNow analyze these stocks..."
    """
    engine = LearningEngine(data_dir=data_dir)
    return engine.generate_llm_context()
