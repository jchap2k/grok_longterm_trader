"""
Daily Re-Simulation Engine - Phase 2 of Post-Market Analysis

After market close each day, this engine:
1. Loads the EOD snapshot (ALL stocks/catalysts seen that day, not just traded ones)
2. Fetches 5-minute intraday data for EVERY candidate stock
3. Re-simulates each candidate using the trade simulator
4. Compares results vs actual live trading decisions
5. Identifies missed opportunities and poor decisions

Purpose:
- Learn from the full universe of candidates, not just what we traded
- Validate that our filters are working correctly
- Identify if we skipped good setups or took bad ones
- Feed general lessons back to the learning system (NO overfitting)

Key principle: We simulate ALL candidates (including those the agent skipped)
to see the "full picture" of what was available that day.
"""

import logging
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import time

logger = logging.getLogger(__name__)

# Try to import pandas
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    logger.error("pandas required for daily re-simulation")


class DailyResimulator:
    """
    Re-simulates a full trading day using saved EOD snapshot data.

    Fetches intraday 5-min bars for ALL candidates seen that day,
    then runs the trade simulator on each to see what results were possible.
    """

    def __init__(
        self,
        schwab_broker=None,
        learning_db_path: str = None,
        snapshot_dir: str = None,
        resim_output_dir: str = None,
        use_price_lookback: bool = True
    ):
        """
        Initialize the daily re-simulator.

        Args:
            schwab_broker: Connected SchwabBroker instance (for intraday data)
            learning_db_path: Path to learning database. Defaults to absolute path
                              derived from this file's location (immune to CWD).
            snapshot_dir: Directory containing EOD snapshot JSON files
            resim_output_dir: Directory to write re-simulation results
            use_price_lookback: If True (default), compute 30-min price direction summary
                from first 6 bars of intraday data and inject into Grok prompt. Mirrors
                production _fetch_price_lookback(). Pass False to simulate without lookback.
        """
        # Resolve all paths to absolute using this file's location so the
        # resimulator works correctly regardless of the caller's working directory.
        # analytics/ -> trading_agent/ -> ai_trader/ -> project_root/
        _root = Path(__file__).parent.parent.parent.parent
        if learning_db_path is None:
            learning_db_path = str(_root / 'ai_trader' / 'ai_trader_data' / 'learning.db')
        if snapshot_dir is None:
            snapshot_dir = str(_root / 'ai_trader' / 'ai_trader_data' / 'daily_snapshots')
        if resim_output_dir is None:
            resim_output_dir = str(_root / 'ai_trader' / 'ai_trader_data' / 'resim_results')

        self.schwab_broker = schwab_broker
        self.learning_db_path = Path(learning_db_path)
        self.snapshot_dir = Path(snapshot_dir)
        self.resim_output_dir = Path(resim_output_dir)
        self.resim_output_dir.mkdir(parents=True, exist_ok=True)
        self.use_price_lookback = use_price_lookback

        from ai_trader.trading_agent.analytics.trade_simulator import TradeSimulator

        # Pass 1: baseline - hardcoded rules only, no lessons
        self.sim_baseline = TradeSimulator(
            use_ollama=False,
            backend="baseline",
            learning_db_path=learning_db_path
        )

        # Pass 2: with lessons - Ollama reads from learning.db
        # Falls back gracefully if Ollama is unavailable
        self.ollama_available = self._check_ollama_reachable()
        if self.ollama_available:
            try:
                self.sim_with_lessons = TradeSimulator(
                    use_ollama=True,
                    learning_db_path=learning_db_path
                )
            except Exception as e:
                logger.warning(f"Ollama init failed, lessons pass will mirror baseline: {e}")
                self.sim_with_lessons = self.sim_baseline
                self.ollama_available = False
        else:
            self.sim_with_lessons = self.sim_baseline

        # Pass 3: with lessons (Grok) - for multi-iteration consistency testing
        # Uses Grok API for conviction scoring with lessons from learning.db
        self.sim_with_lessons_grok = TradeSimulator(
            backend="grok",
            learning_db_path=learning_db_path
        )

        # Keep a reference for callers that used the old .simulator attribute
        self.simulator = self.sim_baseline

        logger.info("DailyResimulator initialized")
        logger.info(f"  Snapshot dir: {self.snapshot_dir}")
        logger.info(f"  Output dir: {self.resim_output_dir}")
        logger.info(f"  Ollama (lessons pass): {'available' if self.ollama_available else 'unavailable'}")
        logger.info(f"  Grok (multi-iteration lessons pass): available")

    def _check_ollama_reachable(self, timeout: float = 2.0) -> bool:
        """
        Quick check if Ollama is running and reachable.

        Uses a fast 2-second timeout so we don't hang at startup.
        Returns True if Ollama responds, False if not running.
        """
        try:
            import requests
            resp = requests.get("http://localhost:11434/api/tags", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            logger.info("Ollama not reachable - lessons pass will mirror baseline")
            return False

    def _init_schwab(self):
        """Initialize Schwab broker from config file if not provided."""
        if self.schwab_broker and getattr(self.schwab_broker, 'connected', False):
            return True

        try:
            import os
            token_file = Path(__file__).parent.parent / "config" / "schwab_refresh_token.txt"
            with open(token_file, 'r') as f:
                refresh_token = f.read().strip()
            os.environ['SCHWAB_REFRESH_TOKEN'] = refresh_token

            from ai_trader.trading_agent.brokers.schwab_broker import SchwabBroker
            self.schwab_broker = SchwabBroker()
            if self.schwab_broker.connect():
                logger.info("Schwab broker connected for intraday data")
                return True
            else:
                logger.warning("Schwab connection failed - will try Alpaca fallback")
                return False
        except Exception as e:
            logger.warning(f"Could not initialize Schwab: {e}")
            return False

    def fetch_intraday_data(
        self,
        symbol: str,
        trade_date: date,
        timeframe: str = "5Min"
    ) -> Optional["pd.DataFrame"]:
        """
        Fetch 5-minute intraday data for a symbol on a specific trading day.

        Uses Schwab as primary source, Alpaca as fallback.
        No yfinance - Schwab/Alpaca only.

        Args:
            symbol: Stock ticker
            trade_date: The trading day to fetch
            timeframe: Bar size - "5Min", "1Min", "15Min"

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume
            Index: datetime timestamps for the requested trading day
            Returns None if data unavailable
        """
        if not PANDAS_AVAILABLE:
            return None

        # Schwab is the only supported source for intraday data
        if not (self.schwab_broker and getattr(self.schwab_broker, 'connected', False)):
            logger.warning(f"  {symbol}: Schwab not connected - cannot fetch intraday data")
            return None

        try:
            df = self._fetch_from_schwab(symbol, trade_date, timeframe)
            if df is not None and len(df) > 0:
                logger.debug(f"  {symbol}: {len(df)} bars from Schwab ({timeframe})")
                return df
        except Exception as e:
            logger.debug(f"  {symbol}: Schwab error: {e}")

        logger.warning(f"  {symbol}: No intraday data available for {trade_date}")
        return None

    def _fetch_from_schwab(
        self,
        symbol: str,
        trade_date: date,
        timeframe: str = "5Min"
    ) -> Optional["pd.DataFrame"]:
        """
        Fetch intraday data from Schwab for a specific date.

        Schwab's API returns a fixed lookback window (e.g., last 10 days of 5-min data).
        We call get_historical_data() to get raw bars, then filter by date.

        Args:
            symbol: Stock ticker
            trade_date: The specific trading day we want
            timeframe: Bar size ("5Min", "1Min", "15Min")

        Returns:
            DataFrame filtered to just trade_date bars, or None
        """
        import pandas as pd

        # Schwab's intraday API ignores start_date/end_date - it always returns
        # a fixed lookback window (period=10 days). We filter to trade_date below.
        dummy_start = datetime.combine(trade_date, datetime.min.time())
        dummy_end = datetime.combine(trade_date, datetime.max.time())

        raw_bars = self.schwab_broker.get_historical_data(
            symbol=symbol,
            start_date=dummy_start,
            end_date=dummy_end,
            timeframe=timeframe
        )

        if not raw_bars:
            return None

        # Build DataFrame
        df = pd.DataFrame(raw_bars)
        if df.empty:
            return None

        # Filter to the requested trading date BEFORE renaming
        day_mask = df['timestamp'].dt.date == trade_date
        df = df[day_mask].copy()

        if df.empty:
            logger.debug(f"  {symbol}: Schwab returned data but none for {trade_date}")
            return None

        # Rename timestamp -> 'date' so TradeSimulator._find_entry_point() can find it.
        # The simulator checks for a 'date' or 'time' column (not the index).
        # Schwab timestamps are in PST: 06:30 = market open, 12:55 = market close.
        df.rename(columns={
            'timestamp': 'date',
            'open': 'open', 'high': 'high',
            'low': 'low', 'close': 'close', 'volume': 'volume'
        }, inplace=True)

        # Also set as index for any callers that use df.index for time lookups
        df.set_index('date', inplace=True)
        df.index.name = 'date'

        # Restore 'date' as a column too (simulator reads it as a column, not index)
        df['date'] = df.index

        return df

    def _snapshot_candidate_to_catalyst(
        self,
        candidate: Dict[str, Any],
        trade_date: date,
        vix: float
    ) -> Dict[str, Any]:
        """
        Convert a snapshot candidate to the catalyst format expected by TradeSimulator.

        Args:
            candidate: Candidate dict from daily snapshot
            trade_date: Trading date
            vix: VIX level for the day

        Returns:
            Catalyst dict compatible with TradeSimulator.simulate_trade()
        """
        # TradeSimulator expects: symbol, date, gap_pct, surprise_pct, vix_level, market_cap, catalyst_type
        gap_pct = candidate.get('gap_pct') or 0.0
        catalyst_type = candidate.get('catalyst_type') or 'gap'

        # Map snapshot source to catalyst type
        if candidate.get('source') == 'earnings_catalyst':
            catalyst_type = candidate.get('catalyst_type', 'earnings')

        # Use 9:30 AM EST as the catalyst time (market open)
        catalyst_datetime = datetime.combine(
            trade_date,
            datetime.strptime("09:30", "%H:%M").time()
        )

        return {
            'symbol': candidate['symbol'],
            'date': catalyst_datetime.isoformat(),
            'gap_pct': gap_pct,
            'surprise_pct': None,  # Will use earnings surprise if available
            'vix_level': vix,
            'market_cap': 1e9,  # Default 1B - simulator uses this for sizing
            'catalyst_type': catalyst_type,
            'catalyst_headline': candidate.get('catalyst_headline', ''),
            'catalyst_sentiment': candidate.get('catalyst_sentiment', 'neutral'),
            'source': candidate.get('source', 'unknown'),
            'opening_price': candidate.get('opening_price')
        }

    def _run_sim_pass(
        self,
        simulator,
        candidates: List[Dict],
        bars_by_symbol: Dict[str, Any],
        trade_date: "date",
        vix: float
    ) -> List[Dict]:
        """
        Run one simulation pass over all candidates using pre-fetched bars.

        Args:
            simulator: TradeSimulator instance (baseline or with-lessons)
            candidates: List of candidate dicts from the snapshot
            bars_by_symbol: Dict of symbol -> DataFrame (pre-fetched, reused across passes)
            trade_date: The trading date
            vix: VIX level for the day

        Returns:
            List of per-symbol result dicts
        """
        results = []
        for candidate in candidates:
            symbol = candidate['symbol']
            source = candidate.get('source', 'unknown')
            intraday_data = bars_by_symbol.get(symbol)

            if intraday_data is None:
                results.append({
                    'symbol': symbol, 'source': source,
                    'status': 'no_data', 'pnl_pct': None, 'pnl_dollars': None,
                    'exit_reason': None, 'conviction': None, 'entry_time': None
                })
                continue

            catalyst = self._snapshot_candidate_to_catalyst(candidate, trade_date, vix)
            # Inject 30-min price direction lookback (mirrors production _fetch_price_lookback)
            if self.use_price_lookback:
                from ai_trader.trading_agent.analytics.trade_simulator import compute_lookback_summary
                lookback = compute_lookback_summary(intraday_data, n_bars=6)
                if lookback:
                    catalyst['price_lookback'] = lookback
            try:
                sim = simulator.simulate_trade(catalyst, intraday_data)
                # Capture which lessons the evaluator referenced (for loss tracking)
                lessons_applied = []
                lessons_used = []
                evaluator = getattr(simulator, 'ollama_evaluator', None)
                if evaluator is not None:
                    lessons_applied = list(getattr(evaluator, 'last_adjustments', []))
                    lessons_used = list(getattr(evaluator, 'last_lessons_used', []))
                if sim is not None:
                    results.append({
                        'symbol': symbol, 'source': source, 'status': 'traded',
                        'entry_price': sim.entry_price, 'exit_price': sim.exit_price,
                        'pnl_pct': sim.pnl_pct, 'pnl_dollars': sim.pnl_dollars,
                        'exit_reason': sim.exit_reason, 'conviction': sim.simulated_conviction,
                        'entry_time': sim.entry_time_pst, 'gap_pct': catalyst['gap_pct'],
                        'lessons_applied': lessons_applied,
                        'lessons_used': lessons_used  # [{id, text_excerpt, adjustment}]
                    })
                else:
                    results.append({
                        'symbol': symbol, 'source': source,
                        'status': 'skipped', 'pnl_pct': None, 'pnl_dollars': None,
                        'exit_reason': None, 'conviction': None, 'entry_time': None,
                        'gap_pct': catalyst['gap_pct'],
                        'lessons_applied': lessons_applied,
                        'lessons_used': lessons_used
                    })
            except Exception as e:
                logger.error(f"Sim error {symbol}: {e}")
                results.append({
                    'symbol': symbol, 'source': source,
                    'status': 'error', 'error': str(e),
                    'pnl_pct': None, 'pnl_dollars': None,
                    'exit_reason': None, 'conviction': None,
                    'lessons_applied': [],
                    'lessons_used': []
                })
        return results

    def _pass_summary(self, results: List[Dict]) -> Dict:
        """Compute aggregate stats for one simulation pass."""
        traded = [r for r in results if r['status'] == 'traded']
        winners = [r for r in traded if (r.get('pnl_pct') or 0) > 0]
        total_pnl = sum(r.get('pnl_dollars') or 0 for r in traded)
        win_rate = len(winners) / len(traded) * 100 if traded else 0
        return {
            'trades': len(traded),
            'winners': len(winners),
            'win_rate': round(win_rate, 1),
            'total_pnl': round(total_pnl, 2),
            'skipped': sum(1 for r in results if r['status'] == 'skipped'),
            'no_data': sum(1 for r in results if r['status'] == 'no_data'),
        }

    def tally_lesson_losses(self, all_pass_results: List[List[Dict]], loss_threshold: int = 2) -> Dict[str, Dict]:
        """
        Tally wins/losses per lesson across all simulation passes.

        Uses lessons_used (with DB IDs) when available; falls back to lessons_applied
        (generic rule names) for backward compatibility.

        Args:
            all_pass_results: List of pass result lists (each from _run_sim_pass)
            loss_threshold: Flag lesson for re-evaluation if losses >= this count

        Returns:
            Dict keyed by "L{id}: {text_excerpt}" -> {'id': int, 'losses': int, 'wins': int, 'flag': bool}
            or keyed by rule_name for fallback entries
        """
        lesson_stats = {}  # key -> {'id': int or None, 'losses': 0, 'wins': 0, 'text': str}

        for pass_results in all_pass_results:
            for trade in pass_results:
                if trade.get('status') != 'traded':
                    continue
                pnl = trade.get('pnl_pct') or 0
                is_loss = pnl < 0

                # Prefer structured lessons_used (has DB id)
                lessons_used = trade.get('lessons_used', [])
                if lessons_used:
                    for lu in lessons_used:
                        if not isinstance(lu, dict):
                            continue
                        lesson_id = lu.get('id')
                        text = lu.get('text_excerpt', '')
                        key = f"L{lesson_id}: {text[:50]}" if lesson_id else text[:50]
                        if key not in lesson_stats:
                            lesson_stats[key] = {'id': lesson_id, 'losses': 0, 'wins': 0}
                        if is_loss:
                            lesson_stats[key]['losses'] += 1
                        else:
                            lesson_stats[key]['wins'] += 1
                else:
                    # Fallback: generic rule names from adjustments_applied
                    for adj in trade.get('lessons_applied', []):
                        rule_name = adj.get('rule', '') if isinstance(adj, dict) else str(adj)
                        if not rule_name or 'Lesson' not in rule_name:
                            continue  # Skip non-lesson adjustments (VIX, Catalyst Age, etc.)
                        if rule_name not in lesson_stats:
                            lesson_stats[rule_name] = {'id': None, 'losses': 0, 'wins': 0}
                        if is_loss:
                            lesson_stats[rule_name]['losses'] += 1
                        else:
                            lesson_stats[rule_name]['wins'] += 1

        # Flag lessons that hit the threshold
        for key, stats in lesson_stats.items():
            stats['flag'] = stats['losses'] >= loss_threshold

        return lesson_stats

    def resimulate_day(
        self,
        snapshot_path: str,
        verbose: bool = True
    ) -> Dict[str, Any]:
        """
        Re-simulate a trading day using a saved EOD snapshot - two passes:

        Pass 1 (baseline): Hardcoded rules only - no lessons from learning.db.
                           Shows what the raw system would have done.

        Pass 2 (with lessons): Ollama reads current lessons from learning.db.
                               Shows what the system does with today's lessons applied.

        The delta between passes shows whether your lessons are helping or hurting.
        Same Schwab bars used for both passes - only conviction scoring differs.

        Args:
            snapshot_path: Path to EOD snapshot JSON
            verbose: Print comparison to stdout

        Returns:
            Dict with both passes and comparison delta
        """
        if not PANDAS_AVAILABLE:
            raise RuntimeError("pandas required for re-simulation")

        snapshot_path = Path(snapshot_path)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

        with open(snapshot_path, 'r') as f:
            snapshot = json.load(f)

        trade_date_str = snapshot['trade_date']
        trade_date = date.fromisoformat(trade_date_str)
        conditions = snapshot.get('market_conditions', {})
        candidates = snapshot.get('candidates', [])
        vix = conditions.get('vix_open', 18.0) if conditions else 18.0

        if verbose:
            print(f"\n{'='*60}")
            print(f"DAILY RE-SIMULATION: {trade_date_str}")
            print(f"{'='*60}")
            print(f"Candidates: {len(candidates)}  |  VIX: {vix}  |  "
                  f"Sentiment: {conditions.get('market_sentiment', '?') if conditions else '?'}")
            print()

        self._init_schwab()

        # --- Fetch bars once, reuse for both passes ---
        if verbose:
            print("Fetching intraday bars (used for both passes)...")

        bars_by_symbol = {}
        no_data_symbols = []
        for candidate in candidates:
            symbol = candidate['symbol']
            df = self.fetch_intraday_data(symbol, trade_date)
            if df is not None and len(df) > 0:
                bars_by_symbol[symbol] = df
                if verbose:
                    print(f"  {symbol}: {len(df)} bars")
            else:
                no_data_symbols.append(symbol)
                if verbose:
                    print(f"  {symbol}: no data")
            time.sleep(0.1)

        if not bars_by_symbol:
            if verbose:
                print("\nNo intraday data available - cannot simulate.")
            return {
                'trade_date': trade_date_str,
                'error': 'no_data',
                'baseline': None,
                'with_lessons': None
            }

        # --- Pass 1: Baseline (hardcoded rules, no lessons) ---
        if verbose:
            print(f"\n--- PASS 1: BASELINE (rules only, no lessons) ---")
        baseline_results = self._run_sim_pass(
            self.sim_baseline, candidates, bars_by_symbol, trade_date, vix
        )
        baseline = self._pass_summary(baseline_results)

        if verbose:
            traded = [r for r in baseline_results if r['status'] == 'traded']
            for r in sorted(traded, key=lambda x: x.get('pnl_pct') or 0, reverse=True):
                sign = '+' if (r['pnl_pct'] or 0) >= 0 else ''
                print(f"  {r['symbol']:6} {sign}{r['pnl_pct']:.2f}% | {r['exit_reason']}")
            if not traded:
                print("  (no trades)")
            print(f"  => {baseline['trades']} trades, "
                  f"{baseline['win_rate']:.0f}% WR, "
                  f"${baseline['total_pnl']:+.2f}")

        # --- Pass 2: With lessons (Ollama reads learning.db) ---
        if verbose:
            if self.ollama_available:
                print(f"\n--- PASS 2: WITH LESSONS (Ollama + learning.db) ---")
            else:
                print(f"\n--- PASS 2: LESSONS UNAVAILABLE (Ollama not running) ---")

        lessons_results = self._run_sim_pass(
            self.sim_with_lessons, candidates, bars_by_symbol, trade_date, vix
        )
        lessons = self._pass_summary(lessons_results)

        if verbose:
            traded = [r for r in lessons_results if r['status'] == 'traded']
            for r in sorted(traded, key=lambda x: x.get('pnl_pct') or 0, reverse=True):
                sign = '+' if (r['pnl_pct'] or 0) >= 0 else ''
                print(f"  {r['symbol']:6} {sign}{r['pnl_pct']:.2f}% | {r['exit_reason']}")
            if not traded:
                print("  (no trades)")
            print(f"  => {lessons['trades']} trades, "
                  f"{lessons['win_rate']:.0f}% WR, "
                  f"${lessons['total_pnl']:+.2f}")

        # --- Comparison ---
        pnl_delta = lessons['total_pnl'] - baseline['total_pnl']
        wr_delta = lessons['win_rate'] - baseline['win_rate']
        trade_delta = lessons['trades'] - baseline['trades']

        if verbose:
            print(f"\n{'='*60}")
            print(f"COMPARISON: lessons vs baseline")
            print(f"{'='*60}")
            print(f"  Trades:   {baseline['trades']} -> {lessons['trades']}  "
                  f"({'+' if trade_delta >= 0 else ''}{trade_delta})")
            print(f"  Win rate: {baseline['win_rate']:.0f}% -> {lessons['win_rate']:.0f}%  "
                  f"({'+' if wr_delta >= 0 else ''}{wr_delta:.0f}pp)")
            print(f"  P&L:      ${baseline['total_pnl']:+.2f} -> ${lessons['total_pnl']:+.2f}  "
                  f"(delta: ${pnl_delta:+.2f})")
            if pnl_delta > 0:
                print(f"  Lessons are HELPING (+${pnl_delta:.2f})")
            elif pnl_delta < 0:
                print(f"  Lessons are HURTING (-${abs(pnl_delta):.2f})")
            else:
                print(f"  Lessons have no effect today")
            if no_data_symbols:
                print(f"\n  No data: {', '.join(no_data_symbols)}")

        # Build and save full output
        output = {
            'trade_date': trade_date_str,
            'market_conditions': conditions,
            'total_candidates': len(candidates),
            'no_data_symbols': no_data_symbols,
            'ollama_available': self.ollama_available,
            'baseline': {**baseline, 'per_symbol': baseline_results},
            'with_lessons': {**lessons, 'per_symbol': lessons_results},
            'delta': {
                'pnl': round(pnl_delta, 2),
                'win_rate_pp': round(wr_delta, 1),
                'trades': trade_delta,
                'verdict': 'helping' if pnl_delta > 0 else ('hurting' if pnl_delta < 0 else 'neutral')
            }
        }

        output_file = self.resim_output_dir / f"{trade_date_str}_resim.json"
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2, default=str)

        logger.info(f"Re-simulation saved to {output_file}")
        return output

    def list_available_snapshots(self) -> List[Dict[str, str]]:
        """
        List all EOD snapshots available for re-simulation.

        Returns:
            List of dicts with date and path
        """
        if not self.snapshot_dir.exists():
            return []

        snapshots = []
        for f in sorted(self.snapshot_dir.glob("*_snapshot.json")):
            date_str = f.stem.replace("_snapshot", "")
            # Check if resim already done
            resim_file = self.resim_output_dir / f"{date_str}_resim.json"
            snapshots.append({
                'date': date_str,
                'snapshot_path': str(f),
                'resim_done': resim_file.exists(),
                'resim_path': str(resim_file) if resim_file.exists() else None,
                'size_kb': round(f.stat().st_size / 1024, 1)
            })

        return snapshots

    def load_resim_results(self, trade_date_str: str) -> Optional[Dict[str, Any]]:
        """
        Load previously saved re-simulation results.

        Args:
            trade_date_str: Date string YYYY-MM-DD

        Returns:
            Results dict or None if not found
        """
        result_file = self.resim_output_dir / f"{trade_date_str}_resim.json"
        if not result_file.exists():
            return None

        with open(result_file, 'r') as f:
            return json.load(f)
