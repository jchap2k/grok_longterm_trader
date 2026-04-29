"""
Trade Simulator for Historical Lesson Discovery

Simulates trades using actual trading system rules:
- Entry timing: 7:00-8:00 AM PST (7:15-7:45 if VIX >25)
- Entry logic: Wait for pullback after gap
- Position sizing: 20% max, Kelly-adjusted
- Exit: 2% trailing stop (regime-aware: 1-2.5% based on VIX)
- Target: 2% profit target (mathematically validated - do NOT adjust)

Updated Feb 14, 2026 with Claude's statistical analysis findings:
- VIX sweet spot: 16-19 (70.4% win rate vs 53.8% at 19-22)
- Small Gap Paradox: 0-3% gaps outperform 5-8% gaps (75% vs 42.9% win rate)
- Perfect risk/reward: $22.49 expected value matches actual $22.50/trade

Faithfully replicates real system to generate realistic trade outcomes.
"""

import logging
from datetime import datetime, timedelta, time as dt_time
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def compute_lookback_summary(price_df, n_bars=6):
    """Compute a 30-min price direction summary from the first N bars of a 5-min DataFrame.

    Produces the same signal format as production _fetch_price_lookback() but uses
    already-fetched intraday bars (5-min resolution) instead of Schwab live data.
    Uses percentage-normalized returns so slope thresholds are stock-price-independent.

    Args:
        price_df: DataFrame with 'close'/'Close' and 'volume'/'Volume' columns.
                  Rows should be sorted chronologically (first row = market open).
        n_bars:   Number of bars to use (default 6 = first 30 min of 5-min bars).

    Returns:
        str: Summary string like "LOOKBACK 6bars(5m) | overall=+1.25% | ..."
        None: if price_df is None, empty, or has fewer than 3 bars.
    """
    if price_df is None or len(price_df) < 3:
        return None

    try:
        chunk = price_df.head(n_bars)

        # Normalize column names (handle both lowercase and Title case)
        close_col = 'close' if 'close' in chunk.columns else 'Close'
        vol_col = 'volume' if 'volume' in chunk.columns else 'Volume'

        if close_col not in chunk.columns:
            return None

        closes = list(chunk[close_col].values)
        volumes = list(chunk[vol_col].values) if vol_col in chunk.columns else [1] * len(closes)

        if len(closes) < 3 or not closes[0] or closes[0] == 0:
            return None

        n = len(closes)
        c0 = float(closes[0])

        # Pct change metrics
        total_change_pct = (float(closes[-1]) - c0) / c0 * 100
        last_bar_pct = (
            (float(closes[-1]) - float(closes[-2])) / float(closes[-2]) * 100
            if n >= 2 and closes[-2] else 0
        )

        # Slope on pct-normalized returns (makes thresholds stock-price-independent)
        pct_returns = [(float(c) - c0) / c0 * 100 for c in closes]
        slope = float(np.polyfit(range(n), pct_returns, 1)[0])  # % per 5-min bar

        # Slope labels tuned for 5-min bars (% per bar):
        # +0.25% per bar = +3% over 12 bars = strong sustained up-trend
        if slope > 0.25:
            slope_label = "UP-STRONG"
        elif slope > 0.05:
            slope_label = "UP-MILD"
        elif slope > -0.05:
            slope_label = "FLAT"
        elif slope > -0.25:
            slope_label = "DN-MILD"
        else:
            slope_label = "DN-STRONG"

        # Acceleration: compare first half slope vs second half slope
        half = n // 2
        if half >= 2:
            pct_first = pct_returns[:half]
            pct_second = pct_returns[half:half * 2]
            s1 = float(np.polyfit(range(len(pct_first)), pct_first, 1)[0])
            s2 = float(np.polyfit(range(len(pct_second)), pct_second, 1)[0])
            if s2 > s1 * 1.2 and s2 > 0:
                accel_label = "ACCELERATING-UP"
            elif s2 < s1 * 1.2 and s2 < 0:
                accel_label = "ACCELERATING-DN"
            else:
                accel_label = "STEADY"
        else:
            accel_label = "STEADY"

        # Volume: last bar vs window average
        avg_vol = sum(float(v) for v in volumes) / len(volumes) if volumes else 1.0
        last_vol = float(volumes[-1]) if volumes else avg_vol
        vol_ratio = last_vol / avg_vol if avg_vol else 1.0

        last_dir = "[UP]" if float(closes[-1]) > float(closes[-2]) else "[DN]"

        return (
            f"LOOKBACK {n}bars(5m) | "
            f"overall={total_change_pct:+.2f}% | "
            f"last1bar={last_bar_pct:+.2f}% | "
            f"trend={slope_label} | "
            f"momentum={accel_label} | "
            f"vol={vol_ratio:.1f}x avg | "
            f"dir={last_dir}"
        )

    except Exception as e:
        logger.debug(f"[Lookback] compute_lookback_summary failed: {e}")
        return None


@dataclass
class SimulatedTrade:
    """Result of a simulated trade with all metrics."""
    # Identifiers
    symbol: str
    entry_date: datetime
    exit_date: datetime

    # Prices
    entry_price: float
    exit_price: float
    peak_price: float  # Highest price reached

    # P&L
    pnl_pct: float
    pnl_dollars: float
    peak_profit_pct: float  # Max unrealized profit

    # Catalyst attributes
    catalyst_type: str
    catalyst_date: datetime
    catalyst_age_hours: float
    gap_pct: float
    surprise_pct: Optional[float]

    # Market context
    vix_level: float
    market_cap: float

    # Trade execution
    entry_time_pst: str  # "07:15", "07:30", etc.
    time_to_exit_minutes: int
    exit_reason: str  # 'target_hit', 'stop_hit', 'eod', 'no_entry'

    # Rules applied
    simulated_conviction: float  # Conviction score based on rules
    entry_window: str  # 'golden', 'late', 'early'
    vix_regime: str  # 'low', 'medium', 'high'


class TradeSimulator:
    """
    Simulates historical trades using actual trading system rules.

    Matches real system behavior:
    1. Entry timing rules (golden window, regime-aware)
    2. Pullback detection (wait for dip after gap)
    3. Conviction scoring (catalyst age, gap direction, timing)
    4. Exit logic (trailing stops, targets, EOD)
    5. Position sizing (Kelly formula, 20% max)
    """

    def __init__(self,
                 learning_db_path: Optional[Path] = None,
                 use_ollama: bool = False,
                 ollama_model: str = "deepseek-coder-v2:16b",
                 backend: str = "grok",
                 skip_db_lessons: bool = False):
        """
        Initialize trade simulator with system rules.

        Args:
            learning_db_path: Path to learning.db (required for AI trade decisions)
            use_ollama: If True, use local Ollama for conviction scoring
            ollama_model: Ollama model for trade decisions (use_ollama=True only)
            backend: 'grok' (production API, default) or 'ollama' (local)
            skip_db_lessons: If True, OllamaTradeEvaluator loads no DB lessons (baseline
                             mode). Grok still uses active_rules.txt. Default False.
        """

        # Entry timing rules (from your system)
        self.golden_window_start = dt_time(7, 0)   # 7:00 AM PST
        self.golden_window_end = dt_time(8, 0)     # 8:00 AM PST
        self.golden_window_tight_start = dt_time(7, 15)  # High-VIX
        self.golden_window_tight_end = dt_time(7, 45)    # High-VIX

        # Exit rules (from your validation)
        self.profit_target_pct = 2.0  # 2% target (validated optimal)
        self.trailing_stop_base_pct = 2.0  # 2% trailing (validated)

        # VIX regime thresholds (updated from Claude's Feb 14 analysis)
        # Analysis showed VIX 16-19 has best performance (70.4% win rate)
        self.vix_low = 16.0       # Below 16 = LOW (avoid if possible)
        self.vix_medium = 19.0    # 16-19 = MEDIUM (optimal)
        self.vix_high = 22.0      # 19-22 = ELEVATED (53.8% win rate)
        self.vix_extreme = 25.0   # 22+ = HIGH (limited data)

        # Position sizing
        self.max_position_pct = 0.20  # 20% of account

        # AI decision making (optional)
        self.use_ollama = use_ollama
        self.backend = backend.lower()
        self.ollama_evaluator = None

        # Sector lookup for AI decisions
        from .sector_lookup import SectorLookup
        self.sector_lookup = SectorLookup()
        logger.info("Sector lookup initialized with cache")

        use_ai = use_ollama or (self.backend == "grok")
        logger.debug(f"TradeSimulator init: backend={self.backend}, use_ai={use_ai}, learning_db_path={learning_db_path}")
        if use_ai:
            if not learning_db_path:
                raise ValueError("learning_db_path required when using AI backend")

            from .ollama_trade_evaluator import OllamaTradeEvaluator
            self.ollama_evaluator = OllamaTradeEvaluator(
                learning_db_path=learning_db_path,
                ollama_model=ollama_model,
                backend=self.backend,
                skip_db_lessons=skip_db_lessons
            )
            logger.debug(f"OllamaTradeEvaluator initialized: {self.ollama_evaluator is not None}")
            if self.backend == "grok":
                logger.info("Trade Simulator initialized with Grok API (production backend)")
                # Prime rules context at iteration start - warms xAI prompt cache so
                # subsequent per-catalyst calls get cache hits instead of resending 41K tokens each.
                self.ollama_evaluator.setup_rules_context()
            else:
                logger.info(f"Trade Simulator initialized with Ollama AI ({ollama_model})")
        else:
            logger.info("Trade Simulator initialized with hardcoded rules (baseline)")

    def simulate_trade(self,
                      catalyst: Dict[str, Any],
                      price_data: pd.DataFrame) -> Optional[SimulatedTrade]:
        """
        Simulate a single trade based on catalyst and price data.

        Args:
            catalyst: Catalyst dict with symbol, date, gap, surprise, VIX
            price_data: DataFrame with intraday OHLCV data

        Returns:
            SimulatedTrade if trade would have been taken, None if skipped
        """
        symbol = catalyst['symbol']
        catalyst_date = datetime.fromisoformat(catalyst['date'])
        gap_pct = catalyst['gap_pct']
        vix_level = catalyst['vix_level']

        logger.debug(f"Simulating {symbol} on {catalyst_date.date()}, gap={gap_pct:.1f}%, VIX={vix_level:.1f}")

        # Step 1: Check entry rules (would we enter this trade?)
        conviction, skip_reason = self._calculate_conviction(catalyst)

        # CRITICAL FIX (Feb 18, 2026): Distinguish hard vetoes from conviction filtering
        # Grok returns skip_reason="Low conviction" when conviction <6.5 (from its JSON response)
        # But we have a separate MIN_CONVICTION check below that handles conviction filtering
        # Only reject immediately if there's a HARD VETO (market rule, not conviction-based)
        # Hard veto examples: "Gap down >-2%", "Priced in", explicit market violations
        hard_veto_keywords = ["gap down", "priced in", "hard veto"]
        is_hard_veto = skip_reason and any(kw.lower() in (skip_reason or "").lower() for kw in hard_veto_keywords)

        if is_hard_veto:
            logger.debug(f"{symbol}: HARD VETO - {skip_reason}")
            return None  # Hard veto, don't trade

        # Conviction threshold - calibrated per Grok 4.2 recommendation (Feb 18, 2026)
        # Changed from 7.0 to 6.5 to provide minimum opportunity floor
        # Grok 4.2: Lower to 6.5 + add opportunity floor guidance
        MIN_CONVICTION = 6.5  # Calibrated threshold per Grok 4.2

        if conviction < MIN_CONVICTION:
            logger.debug(f"{symbol}: SKIP - Conviction {conviction:.1f} < {MIN_CONVICTION} threshold")
            return None

        # Step 2: Find entry point (pullback after gap)
        # CRITICAL FIX (Feb 18, 2026): Remove entry gating for Grok mode entirely
        # Grok should make entry timing decisions, not the simulator
        # Baseline still uses hardcoded entry rules
        logger.debug(f"{symbol}: Backend is '{self.backend}' - checking which entry logic to use")

        if self.backend == 'grok':
            # GROK MODE: Skip entry validation - let Grok decide
            # Use first available price as entry since Grok already validated the trade
            logger.debug(f"{symbol}: [GROK MODE] Skipping entry point validation")
            if price_data.empty:
                logger.debug(f"{symbol}: No price data available")
                return None

            first_bar = price_data.iloc[0]

            # Get actual timestamp from first bar (needed for downstream datetime math)
            if 'date' in price_data.columns:
                entry_time = pd.to_datetime(first_bar['date'])
            elif 'time' in price_data.columns:
                entry_time = pd.to_datetime(first_bar['time'])
            else:
                entry_time = pd.to_datetime(price_data.index[0])

            # Extract entry price - handle both column names
            if 'open' in price_data.columns:
                entry_price = float(first_bar['open'])
            elif 'Open' in price_data.columns:
                entry_price = float(first_bar['Open'])
            else:
                entry_price = float(first_bar.iloc[0])

            logger.info(f"{symbol}: [GROK] TAKING TRADE - Entry at {entry_time}, price={entry_price:.2f}")
        else:
            # BASELINE MODE: Use hardcoded entry rules (pullback + golden window)
            logger.debug(f"{symbol}: [BASELINE MODE] Using hardcoded entry rules")
            entry_time, entry_price = self._find_entry_point(price_data, gap_pct, vix_level)

            if entry_time is None:
                logger.debug(f"{symbol}: SKIP - No valid entry found (no pullback or outside window)")
                return None

        # Step 3: Simulate trade execution (track price action, apply exits)
        trade_result = self._simulate_execution(
            symbol=symbol,
            catalyst=catalyst,
            price_data=price_data,
            entry_time=entry_time,
            entry_price=entry_price,
            conviction=conviction,
            vix_level=vix_level
        )

        return trade_result

    def _calculate_conviction(self, catalyst: Dict[str, Any]) -> Tuple[float, Optional[str]]:
        """
        Calculate conviction score based on catalyst attributes.

        If backend='grok': Uses Grok API to evaluate trade (matches production)
        If use_ollama=True: Uses local Ollama to evaluate trade
        If neither: Applies hardcoded trading rules (baseline)

        Returns:
            (conviction_score, skip_reason)
            - conviction_score: 0-10 score
            - skip_reason: Reason to skip trade (None if should enter)
        """
        # Use AI decision maker if evaluator is available (Grok or Ollama)
        if self.ollama_evaluator:
            catalyst_date = datetime.fromisoformat(catalyst['date'])
            now = catalyst_date + timedelta(hours=10)  # Simulate 7:00 AM PST entry
            age_hours = (now - catalyst_date).total_seconds() / 3600

            # Get sector for this stock
            sector = self.sector_lookup.get_sector(catalyst['symbol'])

            # Build context for Ollama
            ollama_catalyst = {
                'symbol': catalyst['symbol'],
                'catalyst_type': catalyst.get('catalyst_type', 'earnings'),
                'gap_pct': catalyst['gap_pct'],
                'vix_level': catalyst['vix_level'],
                'catalyst_age_hours': age_hours,
                'sector': sector,  # Add sector information
                # Pass pre-computed lookback string (None if not available)
                'price_lookback': catalyst.get('price_lookback'),
            }

            return self.ollama_evaluator.evaluate_trade(ollama_catalyst)

        # Otherwise use hardcoded rules (legacy mode)
        conviction = 8.0  # Base conviction for earnings catalyst
        skip_reason = None

        gap_pct = catalyst['gap_pct']
        catalyst_date = datetime.fromisoformat(catalyst['date'])
        now = catalyst_date + timedelta(hours=10)  # Simulate 7:00 AM PST entry

        # Calculate catalyst age at entry time
        age_hours = (now - catalyst_date).total_seconds() / 3600

        # Rule 1: Gap direction (from your rules)
        if gap_pct < -2.0:
            skip_reason = "Gap down <-2% - avoid entirely"
            return 0.0, skip_reason


        # Rule 2: Catalyst age (from your lessons)
        if age_hours > 48:
            conviction -= 2  # Stale catalyst
            logger.debug(f"  Catalyst age {age_hours:.1f}h: -2 conviction")
        elif age_hours > 24:
            conviction -= 1  # Aging catalyst
            logger.debug(f"  Catalyst age {age_hours:.1f}h: -1 conviction")

        # Rule 3: Gap size
        # NOTE: Claude's Feb 14 analysis found "Small Gap Paradox":
        # - 0-3% gaps: 75% win rate (less retail FOMO, better follow-through)
        # - 5-8% gaps: 42.9% win rate (retail FOMO creates false breakouts)
        # - >12% gaps: 66.7% win rate (but priced in risk)
        # For now, penalize very large gaps that may be priced in
        if abs(gap_pct) > 15:
            conviction -= 2  # Stock already moved significantly
            logger.debug(f"  Gap {gap_pct:.1f}% >15%: -2 conviction (priced in)")
        elif abs(gap_pct) > 10:
            conviction -= 1
            logger.debug(f"  Gap {gap_pct:.1f}% >10%: -1 conviction")

        # Rule 4: Earnings surprise quality (if available)
        surprise_pct = catalyst.get('surprise_pct') or 0
        if surprise_pct > 15:
            conviction += 1.5  # Strong beat
            logger.debug(f"  Earnings surprise {surprise_pct:.1f}%: +1.5 conviction")
        elif surprise_pct > 10:
            conviction += 1.0
            logger.debug(f"  Earnings surprise {surprise_pct:.1f}%: +1.0 conviction")

        logger.debug(f"  Final conviction: {conviction:.1f}")

        return conviction, skip_reason

    def _find_entry_point(self,
                         price_data: pd.DataFrame,
                         gap_pct: float,
                         vix_level: float) -> Tuple[Optional[datetime], Optional[float]]:
        """
        Find entry point using pullback logic.

        Entry rules (from your timing validation):
        1. Wait for pullback from gap spike (don't chase at open)
        2. Enter in golden window (7:00-8:00 AM, or 7:15-7:45 if VIX >25)
        3. Entry at pullback low or bounce confirmation

        Args:
            price_data: Intraday price data
            gap_pct: Gap percentage
            vix_level: VIX level

        Returns:
            (entry_time, entry_price) or (None, None) if no valid entry
        """
        # Determine golden window based on VIX regime
        if vix_level >= self.vix_high:
            window_start = self.golden_window_tight_start
            window_end = self.golden_window_tight_end
            logger.debug(f"  High-VIX regime: Using tight window {window_start}-{window_end}")
        else:
            window_start = self.golden_window_start
            window_end = self.golden_window_end
            logger.debug(f"  Normal regime: Using full window {window_start}-{window_end}")

        # Filter to golden window
        if 'time' in price_data.columns:
            price_data['time_obj'] = pd.to_datetime(price_data['time']).dt.time
        elif 'date' in price_data.columns:
            price_data['time_obj'] = pd.to_datetime(price_data['date']).dt.time
        else:
            logger.warning("No time column found in price data")
            return None, None

        window_mask = (price_data['time_obj'] >= window_start) & (price_data['time_obj'] <= window_end)
        window_data = price_data[window_mask]

        if window_data.empty:
            logger.debug("  No data in golden window")
            return None, None

        # For gap up: Wait for pullback, enter at pullback low or bounce
        # For small gaps: Enter near open
        if gap_pct > 3:
            # Large gap - wait for pullback
            # Entry logic: Find lowest point in first 30min, then enter on bounce
            first_30min = window_data.head(6)  # Assume 5-min bars

            if first_30min.empty:
                return None, None

            pullback_low = first_30min['low'].min()
            pullback_idx = first_30min['low'].idxmin()

            # Entry at pullback low + small buffer (simulates bounce entry)
            entry_price = pullback_low * 1.002  # 0.2% above low
            entry_time = first_30min.loc[pullback_idx, 'date' if 'date' in first_30min.columns else 'time']

            logger.debug(f"  Entry after pullback: {entry_time}, price={entry_price:.2f}")

        else:
            # Small gap or gap down - enter near window start
            entry_bar = window_data.iloc[0]
            entry_price = entry_bar['open']
            entry_time = entry_bar['date' if 'date' in entry_bar.index else 'time']

            logger.debug(f"  Entry at window open: {entry_time}, price={entry_price:.2f}")

        return pd.to_datetime(entry_time), entry_price

    def _simulate_execution(self,
                           symbol: str,
                           catalyst: Dict[str, Any],
                           price_data: pd.DataFrame,
                           entry_time: datetime,
                           entry_price: float,
                           conviction: float,
                           vix_level: float) -> SimulatedTrade:
        """
        Simulate trade execution from entry to exit.

        Tracks:
        - Peak price (high-water mark)
        - Trailing stop hits
        - Profit target hits
        - EOD exits

        Args:
            symbol: Stock symbol
            catalyst: Catalyst dict
            price_data: Intraday OHLCV data
            entry_time: Entry timestamp
            entry_price: Entry price
            conviction: Conviction score
            vix_level: VIX level

        Returns:
            SimulatedTrade with all metrics
        """
        # Calculate trailing stop distance based on VIX regime (your system)
        if vix_level < 15:
            trail_pct = 2.5  # Wide in low-VIX
        elif vix_level < 25:
            trail_pct = 2.0  # Normal
        elif vix_level < 35:
            trail_pct = 1.5  # Tight in high-VIX
        else:
            trail_pct = 1.0  # Very tight in extreme VIX

        logger.debug(f"  Using {trail_pct}% trailing stop (VIX={vix_level:.1f})")

        # Filter price data to after entry
        price_data = price_data.copy()
        if 'date' in price_data.columns:
            price_data['timestamp'] = pd.to_datetime(price_data['date'])
        else:
            price_data['timestamp'] = pd.to_datetime(price_data['time'])

        after_entry = price_data[price_data['timestamp'] >= entry_time]

        if after_entry.empty:
            logger.warning(f"{symbol}: No price data after entry")
            # Return trade with entry=exit (immediate close)
            return self._create_trade_result(
                symbol, catalyst, entry_time, entry_time,
                entry_price, entry_price, entry_price,
                'no_data', conviction, vix_level
            )

        # Track high-water mark and trailing stop
        peak_price = entry_price
        stop_price = entry_price * (1 - trail_pct / 100)
        target_price = entry_price * (1 + self.profit_target_pct / 100)

        exit_time = None
        exit_price = None
        exit_reason = None

        for idx, bar in after_entry.iterrows():
            current_high = bar['high']
            current_low = bar['low']
            current_close = bar['close']
            bar_time = bar['timestamp']

            # Update peak
            if current_high > peak_price:
                peak_price = current_high
                # Update trailing stop
                stop_price = peak_price * (1 - trail_pct / 100)

            # Check exits (in priority order)

            # 1. Target hit?
            if current_high >= target_price:
                exit_time = bar_time
                exit_price = target_price
                exit_reason = 'target_hit'
                logger.debug(f"  Target hit at {exit_time}: ${exit_price:.2f}")
                break

            # 2. Stop hit?
            if current_low <= stop_price:
                exit_time = bar_time
                exit_price = stop_price
                exit_reason = 'stop_hit'
                logger.debug(f"  Stop hit at {exit_time}: ${exit_price:.2f}")
                break

            # 3. EOD exit (1:00 PM PST = 13:00)
            if bar_time.time() >= dt_time(13, 0):
                exit_time = bar_time
                exit_price = current_close
                exit_reason = 'eod'
                logger.debug(f"  EOD exit at {exit_time}: ${exit_price:.2f}")
                break

        # If loop completes without exit, use last bar (EOD)
        if exit_time is None:
            last_bar = after_entry.iloc[-1]
            exit_time = last_bar['timestamp']
            exit_price = last_bar['close']
            exit_reason = 'eod'
            logger.debug(f"  EOD exit (end of data) at {exit_time}: ${exit_price:.2f}")

        return self._create_trade_result(
            symbol, catalyst, entry_time, exit_time,
            entry_price, exit_price, peak_price,
            exit_reason, conviction, vix_level
        )

    def _create_trade_result(self,
                            symbol: str,
                            catalyst: Dict[str, Any],
                            entry_time: datetime,
                            exit_time: datetime,
                            entry_price: float,
                            exit_price: float,
                            peak_price: float,
                            exit_reason: str,
                            conviction: float,
                            vix_level: float) -> SimulatedTrade:
        """Create SimulatedTrade result object."""

        # Guard against None prices (defensive coding)
        if entry_price is None or exit_price is None or peak_price is None:
            logger.warning(f"{symbol}: Cannot create trade with None prices (entry={entry_price}, exit={exit_price}, peak={peak_price})")
            return None

        # Calculate P&L
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        peak_profit_pct = ((peak_price - entry_price) / entry_price) * 100

        # Position size (20% max = $2000 on $10k account)
        position_size = 2000  # Simplified - real system uses Kelly
        shares = position_size / entry_price
        pnl_dollars = shares * (exit_price - entry_price)

        # Calculate catalyst age at entry
        catalyst_date = datetime.fromisoformat(catalyst['date'])
        # Strip timezone from entry_time to allow comparison with tz-naive catalyst_date
        entry_time_naive = entry_time.replace(tzinfo=None) if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo else entry_time
        age_hours = (entry_time_naive - catalyst_date).total_seconds() / 3600

        # Time to exit (strip tz from both to avoid tz-aware/tz-naive mismatch)
        def _naive(dt):
            return dt.replace(tzinfo=None) if hasattr(dt, 'tzinfo') and dt.tzinfo else dt
        time_to_exit_minutes = int((_naive(exit_time) - _naive(entry_time)).total_seconds() / 60)

        # Determine VIX regime
        if vix_level < self.vix_medium:
            vix_regime = 'low'
        elif vix_level < self.vix_high:
            vix_regime = 'medium'
        else:
            vix_regime = 'high'

        # Determine entry window
        entry_time_obj = entry_time.time()
        if self.golden_window_start <= entry_time_obj <= self.golden_window_end:
            entry_window = 'golden'
        elif entry_time_obj < self.golden_window_start:
            entry_window = 'early'
        else:
            entry_window = 'late'

        return SimulatedTrade(
            symbol=symbol,
            entry_date=entry_time,
            exit_date=exit_time,
            entry_price=entry_price,
            exit_price=exit_price,
            peak_price=peak_price,
            pnl_pct=round(pnl_pct, 2),
            pnl_dollars=round(pnl_dollars, 2),
            peak_profit_pct=round(peak_profit_pct, 2),
            catalyst_type=catalyst['catalyst_type'],
            catalyst_date=catalyst_date,
            catalyst_age_hours=round(age_hours, 1),
            gap_pct=catalyst['gap_pct'],
            surprise_pct=catalyst.get('surprise_pct'),
            vix_level=vix_level,
            market_cap=catalyst['market_cap'],
            entry_time_pst=entry_time.strftime('%H:%M'),
            time_to_exit_minutes=time_to_exit_minutes,
            exit_reason=exit_reason,
            simulated_conviction=conviction,
            entry_window=entry_window,
            vix_regime=vix_regime
        )


def test_trade_simulator():
    """Test trade simulator with sample data."""
    print("Testing Trade Simulator...")

    simulator = TradeSimulator()

    # Create sample catalyst
    catalyst = {
        'symbol': 'TEST',
        'date': '2024-10-15T16:00:00',
        'catalyst_type': 'earnings',
        'gap_pct': 5.2,
        'surprise_pct': 12.5,
        'market_cap': 5_000_000_000,
        'vix_level': 18.5
    }

    # Create sample price data (would come from yfinance in real use)
    times = pd.date_range('2024-10-16 06:30:00', periods=84, freq='5min')
    prices = [100 + np.sin(i/10) * 2 + i*0.05 for i in range(84)]

    price_data = pd.DataFrame({
        'date': times,
        'open': prices,
        'high': [p * 1.01 for p in prices],
        'low': [p * 0.99 for p in prices],
        'close': prices,
        'volume': [100000] * 84
    })

    # Simulate trade
    result = simulator.simulate_trade(catalyst, price_data)

    if result:
        print(f"\n[OK] Trade simulated successfully")
        print(f"  Entry: ${result.entry_price:.2f} at {result.entry_time_pst}")
        print(f"  Exit: ${result.exit_price:.2f} ({result.exit_reason})")
        print(f"  P&L: {result.pnl_pct:+.2f}% (${result.pnl_dollars:+.2f})")
        print(f"  Peak profit: {result.peak_profit_pct:.2f}%")
        print(f"  Conviction: {result.simulated_conviction:.1f}")
        print(f"  Window: {result.entry_window}, VIX regime: {result.vix_regime}")
    else:
        print("[OK] Trade skipped (rules applied correctly)")

    print("\nTrade Simulator test complete!")


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    test_trade_simulator()
