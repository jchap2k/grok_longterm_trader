"""
Automated Lesson Discovery System

Simulates historical trades using actual trading rules to discover new lessons.
Runs backtests on 200-500 historical catalyst plays and finds statistical patterns.

Budget: $10-15 for comprehensive PoC (500+ simulated trades)
Expected output: 5-10 high-confidence lessons auto-added to learning.db

Architecture:
1. Data Fetcher - Historical catalysts + price data (yfinance + Perplexity)
2. Trade Simulator - Entry/exit logic matching real system
3. Pattern Analyzer - Statistical discovery (chi-square, t-tests)
4. Lesson Generator - Convert patterns → actionable lessons
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass
import json
import statistics
from scipy import stats
import numpy as np

# Local imports
from .historical_data_fetcher import HistoricalDataFetcher
from .learning_database import LearningDatabase
from .trade_simulator import TradeSimulator, SimulatedTrade
from .pattern_analyzer import PatternAnalyzer, LessonCandidate

try:
    from .perplexity_provider import PerplexityProvider
    PERPLEXITY_AVAILABLE = True
except ImportError:
    PERPLEXITY_AVAILABLE = False
    logger.warning("Perplexity provider not available - using free sources only")

logger = logging.getLogger(__name__)


# Note: SimulatedTrade and LessonCandidate now imported from their respective modules


class AutomatedLessonDiscovery:
    """
    Automated lesson discovery through historical simulation.

    Process:
    1. Fetch historical catalysts (earnings, FDA, M&A)
    2. Simulate trades using your actual entry/exit rules
    3. Segment by attributes (catalyst age, gap, VIX, etc.)
    4. Find statistical patterns (t-tests, chi-square)
    5. Generate high-confidence lessons
    6. Auto-add to learning.db if confidence >0.85
    """

    def __init__(self,
                 learning_db_path: Path,
                 cache_dir: Optional[Path] = None,
                 perplexity_api_key: Optional[str] = None,
                 schwab_broker=None,
                 alpaca_broker=None,
                 use_ollama: bool = False,
                 ollama_model: str = "deepseek-coder-v2:16b",
                 backend: str = "grok",
                 simulation_phase: str = "baseline",
                 no_new_lessons: bool = False,
                 use_price_lookback: bool = True):
        """
        Initialize lesson discovery system.

        Args:
            learning_db_path: Path to learning.db
            cache_dir: Cache directory for fetched data
            perplexity_api_key: Perplexity API key (optional, for catalyst context)
            schwab_broker: Schwab broker instance (optional, for intraday historical data)
            alpaca_broker: Alpaca broker instance (optional, for earnings catalyst discovery)
            use_ollama: If True, use local Ollama for trade decisions (only if backend='ollama')
            ollama_model: Ollama model for trade decisions (backend='ollama' only)
            backend: 'grok' (production API, default) or 'ollama' (local for trade decisions)
            simulation_phase: Label for this simulation run ('baseline', 'with_lessons', etc.)
            no_new_lessons: If True, skip writing new lessons - stocks/bars/sim still run normally
            use_price_lookback: If True (default), compute 30-min price direction summary from
                first 6 bars of intraday data and inject into Grok prompt. Mirrors production
                _fetch_price_lookback(). Pass False to simulate without lookback context.
        """
        self.learning_db = LearningDatabase(learning_db_path)
        self.learning_db_path = learning_db_path
        self.simulation_phase = simulation_phase
        self.backend = backend.lower()
        self.no_new_lessons = no_new_lessons
        self.use_price_lookback = use_price_lookback
        self.data_fetcher = HistoricalDataFetcher(
            schwab_broker=schwab_broker,
            alpaca_broker=alpaca_broker,
            use_weekly_rotation=True  # sim needs dynamic stock rotation
        )
        # use_ollama=True only when explicitly using local Ollama for trade decisions
        # Grok backend uses TradeSimulator with backend='grok' for AI decisions
        # baseline (use_ollama=False AND backend='baseline') uses hardcoded rules
        # When no_new_lessons=True (baseline), skip loading DB lessons so Grok evaluates
        # trades using only active_rules.txt - no learned conviction adjustments.
        # This ensures a true "raw Grok" baseline for the pruning cycle comparison.
        self.trade_simulator = TradeSimulator(
            learning_db_path=learning_db_path if use_ollama else (learning_db_path if self.backend == "grok" else None),
            use_ollama=use_ollama,
            ollama_model=ollama_model,
            backend=self.backend,
            skip_db_lessons=no_new_lessons  # Baseline mode: no learned lesson adjustments
        )
        self.pattern_analyzer = PatternAnalyzer(min_sample_size=10, min_confidence=0.75)

        if PERPLEXITY_AVAILABLE and perplexity_api_key:
            self.perplexity = PerplexityProvider(perplexity_api_key)
        else:
            self.perplexity = None

        self.simulated_trades: List[SimulatedTrade] = []
        self.lesson_candidates: List[LessonCandidate] = []

        # Initialize simulated trade journal table
        self._init_simulated_journal()

        logger.info("Automated Lesson Discovery initialized")
        if use_ollama:
            logger.info(f'  Decision maker: Ollama AI ({ollama_model})')
        elif self.backend == 'grok':
            logger.info('  Decision maker: Grok API (production backend)')
        else:
            logger.info('  Decision maker: Hardcoded rules')
        logger.info(f"  Data source: yfinance (FREE)")
        logger.info(f"  Perplexity: {'Available' if self.perplexity else 'Not available (FREE mode)'}")

    def _init_simulated_journal(self):
        """Create simulated_trade_journal table (same schema as trade_journal)."""
        import sqlite3
        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        # Create table with same schema as trade_journal + phase column
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulated_trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL,
                shares INTEGER,
                why_entered TEXT NOT NULL,
                expected_target REAL,
                expected_stop REAL,
                setup_type TEXT,
                catalyst TEXT,
                market_context TEXT,
                confidence_level TEXT,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,
                actual_pnl REAL,
                order_id TEXT,
                created_at TEXT NOT NULL,
                partial_exits TEXT,
                simulation_phase TEXT DEFAULT 'baseline'
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sim_journal_date ON simulated_trade_journal(trade_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sim_journal_symbol ON simulated_trade_journal(symbol)")

        conn.commit()
        conn.close()

    def _clear_simulated_journal(self):
        """Clear existing simulated trades for current phase only."""
        import sqlite3
        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        # Only clear trades from the current simulation phase
        cursor.execute("DELETE FROM simulated_trade_journal WHERE simulation_phase = ?", (self.simulation_phase,))
        deleted_count = cursor.rowcount

        conn.commit()
        conn.close()
        logger.info(f"Cleared {deleted_count} existing trades from phase '{self.simulation_phase}'")

    def _save_trade_to_journal(self, trade):
        """Save a simulated trade to the database."""
        import sqlite3
        from datetime import datetime as dt

        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        # Build comprehensive why_entered from simulation context
        why_entered = f"SIMULATED TRADE: {trade.symbol} on {trade.entry_date.strftime('%Y-%m-%d')}.\n\n"
        why_entered += f"CATALYST: {trade.catalyst_type} announcement\n"
        why_entered += f"  - Gap: {trade.gap_pct:+.1f}%\n"
        why_entered += f"  - Catalyst age: {trade.catalyst_age_hours:.1f} hours\n\n"
        why_entered += f"ENTRY DECISION:\n"
        why_entered += f"  - Conviction: {trade.simulated_conviction:.1f}/10\n"
        why_entered += f"  - Entry window: {trade.entry_window}\n"
        why_entered += f"  - Entry time: {trade.entry_time_pst}\n"
        why_entered += f"  - Entry price: ${trade.entry_price:.2f}\n\n"
        # Calculate initial stop price (2% trailing from active_rules.txt)
        initial_stop = trade.entry_price * 0.98  # 2% below entry

        why_entered += f"MARKET CONTEXT:\n"
        why_entered += f"  - VIX: {trade.vix_level:.1f} ({trade.vix_regime})\n"
        why_entered += f"  - Expected target: ${trade.entry_price * 1.02:.2f} (+2%)\n"
        why_entered += f"  - Initial stop: ${initial_stop:.2f} (-2%)"

        # Market context
        market_context = f"VIX: {trade.vix_level:.1f} ({trade.vix_regime})"

        # Catalyst info
        catalyst_info = f"{trade.catalyst_type} (gap: {trade.gap_pct:+.1f}%)"

        cursor.execute("""
            INSERT INTO simulated_trade_journal (
                trade_date, symbol, entry_time, entry_price, shares,
                why_entered, expected_target, expected_stop,
                setup_type, catalyst, market_context, confidence_level,
                exit_time, exit_price, exit_reason, actual_pnl,
                order_id, created_at, simulation_phase, gap_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.entry_date.strftime('%Y-%m-%d'),
            trade.symbol,
            trade.entry_date.strftime('%Y-%m-%d %H:%M:%S'),
            trade.entry_price,
            100,  # Fixed share count for simulation
            why_entered,
            trade.entry_price * 1.02,  # 2% target
            initial_stop,  # 2% below entry
            'gap_and_go' if trade.gap_pct > 3 else 'earnings_play',
            catalyst_info,
            market_context,
            f"{trade.simulated_conviction:.1f}/10",
            trade.exit_date.strftime('%Y-%m-%d %H:%M:%S'),
            trade.exit_price,
            trade.exit_reason,
            trade.pnl_dollars,
            f"SIM_{trade.symbol}_{trade.entry_date.strftime('%Y%m%d')}",
            dt.now().isoformat(),
            self.simulation_phase,
            trade.gap_pct
        ))

        conn.commit()
        conn.close()

    def run_discovery(self,
                     start_date: datetime,
                     end_date: datetime,
                     max_trades: int = 200,
                     min_confidence: float = 0.80,
                     fixed_universe: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run full lesson discovery pipeline.

        Args:
            start_date: Start of historical range
            end_date: End of historical range
            max_trades: Maximum trades to simulate
            min_confidence: Minimum confidence to generate lessons (0.80 = 80%)
            fixed_universe: Optional list of symbols to test instead of dynamic scanner.
                When provided, Finnhub/Alpaca still look up real catalyst events for
                these symbols across the date range - the universe is fixed but the
                catalysts are real. Useful for testing yesterday's snapshot candidates
                over a 2-week window to see how lessons hold up as stocks cool off.
                Example: ['IRON', 'NESR', 'ZIM', 'AG', 'EXK'] from Feb 17 snapshot.

        Returns:
            Summary dict with:
                - trades_simulated: Number of trades simulated
                - lessons_discovered: Number of high-confidence lessons
                - cost_summary: API usage and costs
                - top_lessons: Top 5 lessons by confidence
        """
        logger.info(f"=== Starting Automated Lesson Discovery ===")
        logger.info(f"Date range: {start_date} to {end_date}")
        logger.info(f"Max trades: {max_trades}, Min confidence: {min_confidence}")
        if fixed_universe:
            logger.info(f"Fixed universe mode: {len(fixed_universe)} symbols - {fixed_universe}")

        # Clear previous simulated trades
        self._clear_simulated_journal()

        # Step 1: Fetch historical catalysts
        logger.info("\n[1/5] Fetching historical catalysts...")
        catalysts = self._fetch_catalysts(start_date, end_date, max_trades,
                                          fixed_universe=fixed_universe)
        logger.info(f"Found {len(catalysts)} catalyst events")

        if len(catalysts) < 10:
            logger.warning(f"Only {len(catalysts)} catalysts found - need 10+ (WeeklyRotator universe yields ~15 per 60 days)")
            return {'error': 'Insufficient data', 'catalysts_found': len(catalysts)}

        if len(catalysts) < 15:
            logger.warning(f"Low-sample run: {len(catalysts)} catalysts (< 15) - results have higher variance, "
                           f"any graduating lessons tagged low_sample for aggressive re-test next cycle")

        # Step 2: Simulate trades
        logger.info("\n[2/5] Simulating trades...")
        self.simulated_trades = self._simulate_trades(catalysts)
        logger.info(f"Simulated {len(self.simulated_trades)} trades")

        # Step 3-5: Pattern discovery and lesson generation (skip if no_new_lessons=True)
        if self.no_new_lessons:
            logger.info("\n[3/5] Skipping pattern discovery (evaluation mode - using existing lessons only)")
            high_confidence = []
            added_count = 0
        else:
            # Step 3: Discover patterns
            logger.info("\n[3/5] Analyzing patterns...")
            self.lesson_candidates = self._discover_patterns()
            logger.info(f"Found {len(self.lesson_candidates)} pattern candidates")

            # Step 4: Filter by confidence
            high_confidence = [l for l in self.lesson_candidates if l.confidence >= min_confidence]
            logger.info(f"{len(high_confidence)} lessons meet {min_confidence:.0%} confidence threshold")

            # Step 5: Add to learning database
            logger.info("\n[4/5] Adding lessons to database...")
            added_count = self._add_lessons_to_db(high_confidence)
            logger.info(f"Added {added_count} new lessons to learning.db")

        # Generate summary
        cost_summary = {
            'perplexity_calls': 0,
            'perplexity_cost': 0.00,
            'free_source_hits': len(catalysts),
            'total_catalysts': len(catalysts),
            'cost_per_catalyst': 0.00
        }

        summary = {
            'trades_simulated': len(self.simulated_trades),
            'lessons_discovered': len(high_confidence),
            'lessons_added_to_db': added_count,
            'cost_summary': cost_summary,
            'top_lessons': self._format_top_lessons(high_confidence[:5]),
            'all_candidates': len(self.lesson_candidates)
        }

        logger.info("\n=== Discovery Complete ===")
        logger.info(f"Trades simulated: {summary['trades_simulated']}")
        logger.info(f"Lessons discovered: {summary['lessons_discovered']}")
        logger.info(f"Total cost: ${cost_summary['perplexity_cost']}")

        return summary

    def _fetch_catalysts(self,
                        start_date: datetime,
                        end_date: datetime,
                        max_trades: int,
                        fixed_universe: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Fetch historical catalyst events.

        Args:
            fixed_universe: If provided, only fetch catalysts for these symbols.
                The weekly rotator is bypassed - Finnhub looks up real earnings
                events for this exact list across the date range.
        """

        # For PoC, focus on earnings (most data available)
        earnings = self.data_fetcher.fetch_earnings_catalysts(
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
            min_market_cap=500_000_000,  # $500M minimum
            max_catalysts=max_trades,
            universe=fixed_universe  # None = use weekly rotator; list = fixed set
        )

        # TODO: Add FDA catalysts in Phase 2
        # fda = self.data_fetcher.fetch_fda_catalysts(start_date, end_date, max_stocks=50)

        return earnings

    def _simulate_trades(self, catalysts: List[Dict[str, Any]]) -> List[SimulatedTrade]:
        """
        Simulate trades using your actual trading rules.

        Matches real system:
        - Entry: 7:00-8:00 AM window (7:15-7:45 if VIX >25)
        - Entry logic: Wait for pullback from gap spike
        - Exit: 2% trailing stop (regime-aware)
        - Position sizing: 20% max ($2000 on $10k account)
        """
        simulated = []

        for i, catalyst in enumerate(catalysts):
            symbol = catalyst['symbol']
            logger.info(f"[{i+1}/{len(catalysts)}] Simulating {symbol}...")

            try:
                # Fetch intraday price data for catalyst date
                catalyst_date = datetime.fromisoformat(catalyst['date'])
                trade_date = catalyst_date + timedelta(days=1)  # Day after earnings

                # Fetch 1-day intraday data at 5-minute intervals
                # Note: Schwab API provides 6 months of intraday data
                price_data = self.data_fetcher.fetch_historical_ohlcv(
                    symbol=symbol,
                    start_date=trade_date.strftime('%Y-%m-%d'),
                    end_date=(trade_date + timedelta(days=1)).strftime('%Y-%m-%d'),
                    with_indicators=False,
                    frequency='5min'  # Request 5-minute intraday bars
                )

                if price_data is None or price_data.empty:
                    logger.debug(f"  No price data for {symbol} on {trade_date.date()}")
                    continue

                # Add 30-min price direction lookback if enabled (mirrors production _fetch_price_lookback)
                if self.use_price_lookback:
                    from .trade_simulator import compute_lookback_summary
                    lookback = compute_lookback_summary(price_data, n_bars=6)
                    if lookback:
                        catalyst = dict(catalyst)  # Don't mutate the original
                        catalyst['price_lookback'] = lookback
                        logger.debug(f"  [Lookback] {lookback}")

                # Simulate trade
                trade = self.trade_simulator.simulate_trade(catalyst, price_data)

                if trade:
                    simulated.append(trade)
                    # Save trade to database immediately
                    try:
                        self._save_trade_to_journal(trade)
                    except Exception as save_err:
                        logger.error(f"  Failed to save trade to DB: {save_err}")
                        import traceback
                        logger.debug(traceback.format_exc())
                    logger.info(f"  ✓ Trade: {trade.pnl_pct:+.2f}% ({trade.exit_reason})")
                else:
                    logger.debug(f"  ✗ Skipped (rules filtered)")

            except Exception as e:
                import traceback as _tb
                logger.warning(f"  Error simulating {symbol}: {e}\n{_tb.format_exc()}")
                continue

        logger.info(f"\n Simulated {len(simulated)} trades from {len(catalysts)} catalysts")

        return simulated

    def _discover_patterns(self) -> List[LessonCandidate]:
        """
        Discover statistical patterns in simulated trades.

        Uses PatternAnalyzer to segment and test for significant differences.
        """
        if not self.simulated_trades:
            logger.warning("No simulated trades to analyze")
            return []

        # Use pattern analyzer to discover all patterns
        candidates = self.pattern_analyzer.discover_patterns(self.simulated_trades)

        return candidates

    def _add_lessons_to_db(self, lessons: List[LessonCandidate]) -> int:
        """
        Add high-confidence lessons to learning database.

        Args:
            lessons: List of lesson candidates to add

        Returns:
            Number of lessons actually added
        """
        added = 0

        for lesson in lessons:
            try:
                # Check if similar lesson already exists
                existing = self.learning_db.get_lessons()
                if self._is_duplicate_lesson(lesson, existing):
                    logger.info(f"Skipping duplicate lesson: {lesson.title}")
                    continue

                # Add to database
                self.learning_db.add_lesson(
                    title=lesson.title,
                    description=lesson.description,
                    confidence=lesson.confidence,
                    source='automated_discovery',
                    validations=lesson.supporting_trades,
                    violations=lesson.opposing_trades
                )

                added += 1
                logger.info(f"✓ Added lesson: {lesson.title} (confidence: {lesson.confidence:.2%})")

            except Exception as e:
                logger.error(f"Failed to add lesson '{lesson.title}': {e}")
                continue

        return added

    def _is_duplicate_lesson(self, new_lesson: LessonCandidate, existing_lessons: List[Dict]) -> bool:
        """
        Check if a new lesson duplicates an existing active lesson.

        Uses keyword + percentage overlap: if a new lesson shares >=60% of
        its key terms (numeric claims + domain keywords) with an existing
        lesson, it is considered a duplicate and will be skipped.

        This prevents the lesson pool from filling up with N variations of
        the same insight (e.g. 'gap < 1% has 33% WR' written 5 different ways).
        """
        import re

        def extract_key_terms(text):
            text = text.lower()
            terms = set()
            # All percentage values - the main signal (33%, 75%, -4%, +2%, etc.)
            terms.update(re.findall(r'[+-]?\d+\.?\d*%', text))
            # Numeric ranges like "1.5" or "10" near % signs
            terms.update(re.findall(r'\b\d+\.?\d*\b', text))
            # Domain keywords that distinguish lesson categories
            for kw in [
                'gap', 'negative', 'positive', 'semiconductor', 'win rate',
                'risk-reward', 'risk reward', 'small', 'large', 'moderate',
                'earnings', 'intraday', 'premarket', 'pre-market', 'volume',
                'momentum', 'reversal', 'bounce', 'catalyst', 'absolute',
                'loss', 'profit', 'expectancy', 're-enter', 're-entry',
            ]:
                if kw in text:
                    terms.add(kw)
            return terms

        new_text = (new_lesson.title or '') + ' ' + (new_lesson.description or '')
        new_terms = extract_key_terms(new_text)
        if not new_terms:
            return False

        for existing in existing_lessons:
            ex_text = (existing.get('lesson_text') or
                       existing.get('title') or
                       existing.get('description') or '')
            if not ex_text:
                continue
            ex_terms = extract_key_terms(ex_text)
            if not ex_terms:
                continue
            overlap = len(new_terms & ex_terms) / max(len(new_terms | ex_terms), 1)
            if overlap >= 0.60:
                logger.info(
                    f"Dedup gate: skipping '{new_lesson.title[:70]}' "
                    f"({overlap:.0%} overlap with existing id={existing.get('id', '?')})"
                )
                return True

        return False

    def _format_top_lessons(self, lessons: List[LessonCandidate]) -> List[Dict[str, Any]]:
        """Format lessons for summary output."""
        return [
            {
                'title': l.title,
                'confidence': round(l.confidence, 3),
                'evidence': f"{l.supporting_trades} trades",
                'win_rate_improvement': round(l.supporting_win_rate - l.opposing_win_rate, 3),
                'pnl_improvement': round(l.supporting_avg_pnl - l.opposing_avg_pnl, 3),
                'rule': l.rule
            }
            for l in lessons
        ]


def run_proof_of_concept():
    """
    Run proof of concept lesson discovery.

    Settings:
    - Date range: Oct 1 - Dec 31, 2024 (Q4 earnings season)
    - Max trades: 200
    - Min confidence: 0.80 (80%)
    - Budget: ~$2-3 (mostly free yfinance data)
    """

    # Setup
    learning_db_path = Path("ai_trader/ai_trader_data/learning.db")
    cache_dir = Path("ai_trader/ai_trader_data/simulation_cache")

    discovery = AutomatedLessonDiscovery(
        learning_db_path=learning_db_path,
        cache_dir=cache_dir
    )

    # Run discovery
    start_date = datetime(2024, 10, 1)
    end_date = datetime(2024, 12, 31)

    results = discovery.run_discovery(
        start_date=start_date,
        end_date=end_date,
        max_trades=200,
        min_confidence=0.80
    )

    # Print summary
    print("\n" + "=" * 60)
    print("AUTOMATED LESSON DISCOVERY - PROOF OF CONCEPT")
    print("=" * 60)
    print(f"\nTrades Simulated: {results['trades_simulated']}")
    print(f"Lessons Discovered: {results['lessons_discovered']}")
    print(f"Total Cost: ${results['cost_summary']['perplexity_cost']}")
    print(f"\nTop Lessons:")

    for i, lesson in enumerate(results['top_lessons'], 1):
        print(f"\n{i}. {lesson['title']}")
        print(f"   Confidence: {lesson['confidence']:.1%}")
        print(f"   Evidence: {lesson['evidence']}")
        print(f"   Win Rate Improvement: {lesson['win_rate_improvement']:+.1%}")
        print(f"   P&L Improvement: {lesson['pnl_improvement']:+.2%}")
        print(f"   Rule: {lesson['rule']}")

    print("\n" + "=" * 60)

    return results


if __name__ == '__main__':
    # Run PoC
    logging.basicConfig(level=logging.INFO)
    results = run_proof_of_concept()
