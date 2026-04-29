"""
SwingTradeSimulator - Daily-bar trade simulator for swing positions.

Simulates FORCESWING mechanics:
  - Entry: buy stop = prior day high + $0.06
  - Initial stop: max(-4% from entry, prior day low - $0.06)
  - Partial exit: sell 50% at +7% profit target (LIMIT order)
  - Trailing stop: prev_low - ATR x regime_multiplier (never moves down)
  - Max hold: 7 days (standard swing) or 15 days (PEAD)
  - Exit signals: STOP_HIT, PROFIT_TARGET_PARTIAL, TIME_KILL_SWITCH,
                  BELOW_21EMA, OVERBOUGHT_RSI, MAX_HOLD

Hypothesis testing:
  Use HypothesisParams to vary key parameters and compare outcomes via
  BatchSimulator.compare_hypotheses().
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import logging
from datetime import date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class HypothesisParams:
    """
    All tunable simulation parameters in one place.
    Defaults match the live system (system_parameters.json / active_rules.txt).
    """
    # Entry / exit targets
    partial_exit_pct:    float = 0.07   # +7% triggers partial sell of 50%
    max_stop_pct:        float = 0.04   # initial stop max -4% from entry
    entry_buffer_cents:  float = 0.06   # buy stop: prior high + $0.06

    # Hold duration limits
    max_hold_days:       int   = 7      # hard cap for standard swing
    pead_max_hold_days:  int   = 15     # PEAD positions may hold up to 15 days

    # Time kill switch
    time_kill_days:      int   = 4      # day index (1-based) when kill switch arms
    time_kill_threshold: float = 0.02   # < 2% move from entry -> exit

    # Indicator-based exits
    rsi_overbought:      float = 75.0   # RSI(14) > threshold -> exit signal
    ema_period:          int   = 21     # EMA period for BELOW_EMA exit check

    # ATR trailing stop multipliers by VIX mode (match system defaults)
    atr_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "aggressive": 3.0,
        "standard":   2.5,
        "defensive":  1.5,
        "cash":       1.5,
    })

    # Cost model: Schwab charges $0 commissions for equities; slippage covers bid-ask spread
    slippage_pct:    float = 0.0005  # 0.05% per side = 0.10% round-trip drag on pnl_pct_net


# ---------------------------------------------------------------------------
# Result structures
# ---------------------------------------------------------------------------

@dataclass
class SwingSimulatedTrade:
    symbol:             str
    entry_price:        float
    exit_price:         float
    partial_exit_price: Optional[float]   # where 50% was sold; None if not triggered
    pnl_pct:            float             # PnL on full position (unblended)
    pnl_pct_net:        float             # blended PnL (partial 50% + remainder)
    hold_days:          int
    exit_reason:        str               # exit signal name
    entry_catalyst:     str
    hold_type:          str
    peak_price:         float
    max_drawdown_pct:   float
    vix_mode:           str
    # Optional enrichment fields
    conviction_score:   Optional[float]   = None
    regime_mode:        Optional[str]     = None


@dataclass
class SimulationSummary:
    """Aggregate statistics across a batch simulation run."""
    total_trades:      int
    win_count:         int
    win_rate:          float
    avg_pnl_net:       float
    avg_hold_days:     float
    exit_type_counts:  Dict[str, int]
    partial_exit_rate: float           # % of trades that hit +7% before final exit
    avg_max_drawdown:  float
    by_catalyst:       Dict[str, Dict[str, float]]  # {catalyst: {win_rate, avg_pnl}}
    by_vix_mode:       Dict[str, Dict[str, float]]  # {vix_mode: {win_rate, avg_pnl}}

    @classmethod
    def from_trades(cls, trades: List[SwingSimulatedTrade]) -> "SimulationSummary":
        if not trades:
            return cls(
                total_trades=0, win_count=0, win_rate=0.0, avg_pnl_net=0.0,
                avg_hold_days=0.0, exit_type_counts={}, partial_exit_rate=0.0,
                avg_max_drawdown=0.0, by_catalyst={}, by_vix_mode={},
            )
        n = len(trades)
        wins = [t for t in trades if t.pnl_pct_net > 0]
        exit_counts: Dict[str, int] = {}
        for t in trades:
            exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

        def _group_stats(group: List[SwingSimulatedTrade]) -> Dict[str, float]:
            if not group:
                return {"win_rate": 0.0, "avg_pnl": 0.0, "count": 0}
            g_wins = sum(1 for t in group if t.pnl_pct_net > 0)
            return {
                "win_rate": g_wins / len(group),
                "avg_pnl":  sum(t.pnl_pct_net for t in group) / len(group),
                "count":    len(group),
            }

        catalysts = {}
        vix_modes = {}
        for t in trades:
            catalysts.setdefault(t.entry_catalyst, []).append(t)
            vix_modes.setdefault(t.vix_mode, []).append(t)

        return cls(
            total_trades=n,
            win_count=len(wins),
            win_rate=len(wins) / n,
            avg_pnl_net=sum(t.pnl_pct_net for t in trades) / n,
            avg_hold_days=sum(t.hold_days for t in trades) / n,
            exit_type_counts=exit_counts,
            partial_exit_rate=sum(1 for t in trades if t.partial_exit_price is not None) / n,
            avg_max_drawdown=sum(t.max_drawdown_pct for t in trades) / n,
            by_catalyst={cat: _group_stats(group) for cat, group in catalysts.items()},
            by_vix_mode={mode: _group_stats(group) for mode, group in vix_modes.items()},
        )


# ---------------------------------------------------------------------------
# Main simulator
# ---------------------------------------------------------------------------

class SwingTradeSimulator:
    """
    Single-trade simulator. For batch runs and hypothesis testing, use BatchSimulator.
    """

    DEFAULT_PARAMS = HypothesisParams()

    @staticmethod
    def evaluate_progress_state(
        *,
        entry_price: float,
        current_stop: float,
        current_price: float,
        close: float,
        ema21: float,
        sector_rs_ok: bool,
        days_in_trade: int,
        partial_done: bool = False,
        hold_type: str = "swing",
        time_kill_days: int | None = None,
        meaningful_move_pct: float | None = None,
    ) -> dict:
        from scheduler.swing_exit_engine import SwingExitEngine

        return SwingExitEngine.evaluate_progress_snapshot(
            entry_price=entry_price,
            current_stop=current_stop,
            current_price=current_price,
            close=close,
            ema21=ema21,
            sector_rs_ok=sector_rs_ok,
            days_in_trade=days_in_trade,
            partial_done=partial_done,
            hold_type=hold_type,
            time_kill_days=time_kill_days,
            meaningful_move_pct=meaningful_move_pct,
        )

    def simulate_trade(
        self,
        candidate: dict,
        signal_bars: Optional[list] = None,
        forward_bars: Optional[list] = None,
        daily_bars: Optional[list] = None,   # backward-compat: replaces signal_bars
        params: Optional[HypothesisParams] = None,
        vix_mode: str = "standard",
    ) -> Optional[SwingSimulatedTrade]:
        """
        Simulate a single swing trade.

        Args:
            candidate:    Dict with symbol, price, entry_catalyst, hold_type,
                          optionally conviction_score and regime_mode.
            signal_bars:  Historical daily bars up to (and including) the entry day.
                          Used to calculate entry price, initial stop, and seed indicators.
            forward_bars: Bars AFTER entry (the true forward window). Simulation runs on these.
            daily_bars:   Backward-compat: if provided and signal_bars is None, used for both
                          signal (last bar) and forward (the subsequent max_hold bars).
            params:       HypothesisParams to use. Defaults to system defaults.
            vix_mode:     VIX regime string ("aggressive"/"standard"/"defensive"/"cash").

        Returns:
            SwingSimulatedTrade or None if insufficient data.
        """
        p = params or self.DEFAULT_PARAMS

        # Backward-compat: daily_bars acts as combined history
        if signal_bars is None and daily_bars is not None:
            signal_bars = daily_bars
        if signal_bars is None or len(signal_bars) < 5:
            return None

        symbol         = candidate.get("symbol", "UNKNOWN")
        entry_catalyst = candidate.get("entry_catalyst", "FORCESWING")
        hold_type      = candidate.get("hold_type", "swing")
        next_earnings_date = candidate.get("next_earnings_date")
        conviction     = candidate.get("conviction_score")
        regime_mode    = candidate.get("regime_mode")
        max_hold       = p.pead_max_hold_days if hold_type == "pead" else p.max_hold_days

        # Entry price: buy stop = prior day high + buffer
        entry_price = float(signal_bars[-1]["high"]) + p.entry_buffer_cents

        # ATR for trailing stop
        try:
            from agent.components.technical_indicators import calculate_atr
            atr14 = calculate_atr(
                signal_bars[-20:] if len(signal_bars) >= 20 else signal_bars,
                period=min(14, len(signal_bars) - 1),
            )
        except Exception:
            atr14 = entry_price * 0.015  # 1.5% fallback

        # Initial stop: max(-4% from entry, prev_low - buffer)
        initial_stop = max(
            entry_price * (1.0 - p.max_stop_pct),
            float(signal_bars[-1]["low"]) - p.entry_buffer_cents,
        )
        profit_target = entry_price * (1.0 + p.partial_exit_pct)

        # Forward bars: true future bars if provided, otherwise slice from signal
        if forward_bars is not None:
            fwd = forward_bars[:max_hold]
        else:
            fwd = signal_bars[-(max_hold + 1):][:max_hold]

        # Seed EMA and RSI state from signal bars history
        ema_seed   = self._seed_ema(signal_bars, p.ema_period)
        rsi_seed   = self._seed_rsi(signal_bars, period=14)

        return self._run_forward_simulation(
            symbol=symbol,
            entry_price=entry_price,
            bars_forward=fwd,
            initial_stop=initial_stop,
            profit_target=profit_target,
            atr14=atr14,
            vix_mode=vix_mode,
            entry_catalyst=entry_catalyst,
            hold_type=hold_type,
            next_earnings_date=next_earnings_date,
            max_hold=max_hold,
            params=p,
            conviction_score=conviction,
            regime_mode=regime_mode,
            ema_seed=ema_seed,
            rsi_seed=rsi_seed,
        )

    # ------------------------------------------------------------------
    # Forward simulation engine
    # ------------------------------------------------------------------

    def _run_forward_simulation(
        self,
        symbol: str,
        entry_price: float,
        bars_forward: list,
        initial_stop: float,
        profit_target: float,
        atr14: float,
        vix_mode: str = "standard",
        entry_catalyst: str = "FORCESWING",
        hold_type: str = "swing",
        next_earnings_date: Optional[str] = None,
        max_hold: int = 7,
        params: Optional[HypothesisParams] = None,
        conviction_score: Optional[float] = None,
        regime_mode: Optional[str] = None,
        ema_seed: Optional[float] = None,
        rsi_seed: Optional[tuple] = None,   # (avg_gain, avg_loss)
    ) -> SwingSimulatedTrade:
        p    = params or self.DEFAULT_PARAMS
        mult = p.atr_multipliers.get(vix_mode, 2.5)

        current_stop  = initial_stop
        partial_done  = False
        partial_day: Optional[int] = None
        partial_price: Optional[float] = None
        peak_price    = entry_price
        min_price     = entry_price
        prev_low: Optional[float] = None   # updated at end of each bar; used for trailing stop

        # Rolling indicator state (seeded from signal history)
        ema_val       = ema_seed
        rsi_avg_gain, rsi_avg_loss = rsi_seed if rsi_seed else (None, None)
        rsi_prev_close = entry_price
        earnings_dt: Optional[date] = None
        if next_earnings_date:
            try:
                earnings_dt = (
                    next_earnings_date if isinstance(next_earnings_date, date)
                    else date.fromisoformat(str(next_earnings_date))
                )
            except Exception:
                earnings_dt = None

        effective_bars = (bars_forward or [])[:max_hold]

        for day_idx, bar in enumerate(effective_bars):
            high  = float(bar["high"])
            low   = float(bar["low"])
            close = float(bar["close"])
            day   = day_idx + 1   # 1-based hold day counter
            bar_date = None
            raw_bar_date = bar.get("date")
            if raw_bar_date:
                try:
                    bar_date = (
                        raw_bar_date if isinstance(raw_bar_date, date)
                        else date.fromisoformat(str(raw_bar_date))
                    )
                except Exception:
                    bar_date = None

            peak_price = max(peak_price, high)
            min_price  = min(min_price, low)

            # --- Update rolling indicators first (end-of-day values) ---
            ema_val = self._update_ema(ema_val, close, p.ema_period)
            rsi_avg_gain, rsi_avg_loss = self._update_rsi_state(
                rsi_avg_gain, rsi_avg_loss, rsi_prev_close, close, period=14
            )
            rsi_val = self._rsi_from_state(rsi_avg_gain, rsi_avg_loss)
            rsi_prev_close = close

            # 1. Earnings proximity/immediate exit (when dated metadata is available).
            if earnings_dt is not None and bar_date is not None:
                days_to_earnings = (earnings_dt - bar_date).days
                if hold_type == "pead":
                    if days_to_earnings >= 0:
                        return self._make_result(
                            symbol=symbol, entry_price=entry_price,
                            exit_price=close, partial_price=partial_price,
                            partial_done=partial_done, hold_days=day,
                            exit_reason="EARNINGS_PROXIMITY",
                            entry_catalyst=entry_catalyst, hold_type=hold_type,
                            peak_price=peak_price, min_price=min_price,
                            vix_mode=vix_mode, conviction_score=conviction_score,
                            regime_mode=regime_mode, params=p,
                        )
                else:
                    if days_to_earnings <= 1:
                        return self._make_result(
                            symbol=symbol, entry_price=entry_price,
                            exit_price=close, partial_price=partial_price,
                            partial_done=partial_done, hold_days=day,
                            exit_reason="EARNINGS_IMMEDIATE",
                            entry_catalyst=entry_catalyst, hold_type=hold_type,
                            peak_price=peak_price, min_price=min_price,
                            vix_mode=vix_mode, conviction_score=conviction_score,
                            regime_mode=regime_mode, params=p,
                        )
                    if days_to_earnings <= 3:
                        return self._make_result(
                            symbol=symbol, entry_price=entry_price,
                            exit_price=close, partial_price=partial_price,
                            partial_done=partial_done, hold_days=day,
                            exit_reason="EARNINGS_PROXIMITY",
                            entry_catalyst=entry_catalyst, hold_type=hold_type,
                            peak_price=peak_price, min_price=min_price,
                            vix_mode=vix_mode, conviction_score=conviction_score,
                            regime_mode=regime_mode, params=p,
                        )

            # 2. STOP_HIT: intraday low touches or breaches current stop
            if low <= current_stop:
                return self._make_result(
                    symbol=symbol, entry_price=entry_price,
                    exit_price=current_stop, partial_price=partial_price,
                    partial_done=partial_done, hold_days=day,
                    exit_reason="STOP_HIT",
                    entry_catalyst=entry_catalyst, hold_type=hold_type,
                    peak_price=peak_price, min_price=min_price,
                    vix_mode=vix_mode, conviction_score=conviction_score,
                    regime_mode=regime_mode, params=p,
                )

            # 3. PROFIT_TARGET_PARTIAL: intraday high reaches +7%
            if not partial_done and high >= profit_target:
                partial_done  = True
                partial_day   = day
                partial_price = profit_target

            # Update trailing stop (only moves up; uses PREVIOUS day's low to match
            # live SwingExitEngine.calculate_trailing_stop() which takes prev_low).
            # Day 1: prev_low is None (no prior forward bar), fall back to current low.
            _stop_low    = prev_low if prev_low is not None else low
            atr_trail    = _stop_low - (atr14 * mult)
            fixed_trail  = _stop_low - p.entry_buffer_cents
            new_stop     = max(atr_trail, fixed_trail)
            current_stop = max(current_stop, new_stop)

            # --- EOD exits (checked using close) ---

            # 4. BELOW_21EMA: daily close falls below EMA(21)
            if ema_val is not None and close < ema_val:
                return self._make_result(
                    symbol=symbol, entry_price=entry_price,
                    exit_price=close, partial_price=partial_price,
                    partial_done=partial_done, hold_days=day,
                    exit_reason="BELOW_21EMA",
                    entry_catalyst=entry_catalyst, hold_type=hold_type,
                    peak_price=peak_price, min_price=min_price,
                    vix_mode=vix_mode, conviction_score=conviction_score,
                    regime_mode=regime_mode, params=p,
                )

            # 5. OVERBOUGHT_RSI: RSI(14) exceeds threshold
            if rsi_val is not None and rsi_val > p.rsi_overbought:
                return self._make_result(
                    symbol=symbol, entry_price=entry_price,
                    exit_price=close, partial_price=partial_price,
                    partial_done=partial_done, hold_days=day,
                    exit_reason="OVERBOUGHT_RSI",
                    entry_catalyst=entry_catalyst, hold_type=hold_type,
                    peak_price=peak_price, min_price=min_price,
                    vix_mode=vix_mode, conviction_score=conviction_score,
                    regime_mode=regime_mode, params=p,
                )

            # 6. TIME_KILL_SWITCH: day >= time_kill_days AND < threshold move from entry
            time_kill_day = day - partial_day if partial_day is not None else day
            if time_kill_day >= p.time_kill_days:
                progress = self.evaluate_progress_state(
                    entry_price=entry_price,
                    current_stop=current_stop,
                    current_price=close,
                    close=close,
                    ema21=ema_val if ema_val is not None else close,
                    sector_rs_ok=True,
                    days_in_trade=time_kill_day,
                    partial_done=partial_done,
                    hold_type=hold_type,
                    time_kill_days=p.time_kill_days,
                    meaningful_move_pct=p.time_kill_threshold,
                )
                if progress.get("progress_state") == "STALLED":
                    return self._make_result(
                        symbol=symbol, entry_price=entry_price,
                        exit_price=close, partial_price=partial_price,
                        partial_done=partial_done, hold_days=day,
                        exit_reason="TIME_KILL_SWITCH",
                        entry_catalyst=entry_catalyst, hold_type=hold_type,
                        peak_price=peak_price, min_price=min_price,
                        vix_mode=vix_mode, conviction_score=conviction_score,
                        regime_mode=regime_mode, params=p,
                    )

            # Save this bar's low so next iteration can use it as prev_low
            prev_low = low

        # 7. MAX_HOLD: exhausted all forward bars
        last_close  = float(effective_bars[-1]["close"]) if effective_bars else entry_price
        exit_reason = "PROFIT_TARGET_PARTIAL" if partial_done else "MAX_HOLD"
        return self._make_result(
            symbol=symbol, entry_price=entry_price,
            exit_price=last_close, partial_price=partial_price,
            partial_done=partial_done, hold_days=len(effective_bars),
            exit_reason=exit_reason,
            entry_catalyst=entry_catalyst, hold_type=hold_type,
            peak_price=peak_price, min_price=min_price,
            vix_mode=vix_mode, conviction_score=conviction_score,
            regime_mode=regime_mode, params=p,
        )

    # ------------------------------------------------------------------
    # Indicator helpers
    # ------------------------------------------------------------------

    def _seed_ema(self, bars: list, period: int) -> Optional[float]:
        """Compute EMA from the tail of signal bars to seed the forward simulation."""
        if not bars or len(bars) < period:
            return None
        closes = [float(b["close"]) for b in bars]
        k = 2.0 / (period + 1)
        ema = sum(closes[:period]) / period   # SMA as seed
        for c in closes[period:]:
            ema = c * k + ema * (1.0 - k)
        return ema

    def _update_ema(self, current: Optional[float], new_val: float, period: int) -> Optional[float]:
        """Single-step EMA update."""
        if current is None:
            return new_val   # no history yet, use current value
        k = 2.0 / (period + 1)
        return new_val * k + current * (1.0 - k)

    def _seed_rsi(self, bars: list, period: int = 14) -> Optional[tuple]:
        """
        Compute Wilder-smoothed RSI state (avg_gain, avg_loss) from signal bar history.
        Returns (avg_gain, avg_loss) or None if insufficient data.
        """
        if len(bars) < period + 1:
            return None
        closes = [float(b["close"]) for b in bars]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(0.0, d) for d in deltas]
        losses = [max(0.0, -d) for d in deltas]
        # Initial SMA seed
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        # Wilder smoothing for remaining bars
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        return (avg_gain, avg_loss)

    def _update_rsi_state(
        self,
        avg_gain: Optional[float],
        avg_loss: Optional[float],
        prev_close: float,
        close: float,
        period: int = 14,
    ) -> tuple:
        """Single-step Wilder RSI state update. Returns (avg_gain, avg_loss)."""
        if avg_gain is None or avg_loss is None:
            return (avg_gain, avg_loss)
        delta = close - prev_close
        gain  = max(0.0, delta)
        loss  = max(0.0, -delta)
        new_avg_gain = (avg_gain * (period - 1) + gain) / period
        new_avg_loss = (avg_loss * (period - 1) + loss) / period
        return (new_avg_gain, new_avg_loss)

    def _rsi_from_state(
        self, avg_gain: Optional[float], avg_loss: Optional[float]
    ) -> Optional[float]:
        """Compute RSI value from smoothed state."""
        if avg_gain is None or avg_loss is None:
            return None
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _calculate_entry_stop(self, bar: dict, buffer: float = 0.06) -> float:
        """Buy stop order price: prior day high + buffer."""
        return float(bar["high"]) + buffer

    def _make_result(
        self,
        symbol: str, entry_price: float, exit_price: float,
        partial_price: Optional[float], partial_done: bool,
        hold_days: int, exit_reason: str,
        entry_catalyst: str, hold_type: str,
        peak_price: float, min_price: float,
        vix_mode: str,
        conviction_score: Optional[float] = None,
        regime_mode: Optional[str] = None,
        params: Optional[HypothesisParams] = None,
    ) -> SwingSimulatedTrade:
        p_cost  = params.slippage_pct if params is not None else 0.0
        pnl_pct = (exit_price - entry_price) / entry_price
        return SwingSimulatedTrade(
            symbol=symbol,
            entry_price=entry_price,
            exit_price=exit_price,
            partial_exit_price=partial_price,
            pnl_pct=pnl_pct,
            pnl_pct_net=self._blended_pnl(entry_price, partial_price, exit_price, partial_done) - 2.0 * p_cost,
            hold_days=hold_days,
            exit_reason=exit_reason,
            entry_catalyst=entry_catalyst,
            hold_type=hold_type,
            peak_price=peak_price,
            max_drawdown_pct=(entry_price - min_price) / entry_price if min_price < entry_price else 0.0,
            vix_mode=vix_mode,
            conviction_score=conviction_score,
            regime_mode=regime_mode,
        )

    @staticmethod
    def _blended_pnl(
        entry: float,
        partial_exit: Optional[float],
        final_exit: float,
        partial_done: bool,
    ) -> float:
        """Blended PnL: 50% at partial_exit + 50% at final_exit (if partial triggered)."""
        if partial_done and partial_exit is not None:
            return (
                0.5 * (partial_exit - entry) / entry
                + 0.5 * (final_exit  - entry) / entry
            )
        return (final_exit - entry) / entry


# ---------------------------------------------------------------------------
# Batch simulator (hypothesis testing)
# ---------------------------------------------------------------------------

class BatchSimulator:
    """
    Runs simulate_trade() over a list of setups and aggregates results.
    Use compare_hypotheses() to test parameter variations.

    Each setup dict in trade_setups:
        {
            "candidate":    dict with symbol, entry_catalyst, hold_type, etc.
            "signal_bars":  list of daily bars up to entry day
            "forward_bars": list of daily bars after entry  (or omit; uses daily_bars)
            "daily_bars":   backward-compat combined bar list (optional)
            "vix_mode":     str (optional, default "standard")
        }
    """

    def __init__(self):
        self._sim = SwingTradeSimulator()

    def run(
        self,
        trade_setups: List[Dict[str, Any]],
        params: Optional[HypothesisParams] = None,
        default_vix_mode: str = "standard",
    ) -> SimulationSummary:
        """
        Run simulation over all setups with given params.
        Returns aggregate SimulationSummary.
        """
        trades = []
        for setup in trade_setups:
            vix_mode = setup.get("vix_mode", default_vix_mode)
            result = self._sim.simulate_trade(
                candidate=setup["candidate"],
                signal_bars=setup.get("signal_bars"),
                forward_bars=setup.get("forward_bars"),
                daily_bars=setup.get("daily_bars"),
                params=params,
                vix_mode=vix_mode,
            )
            if result is not None:
                trades.append(result)
        return SimulationSummary.from_trades(trades)

    def compare_hypotheses(
        self,
        trade_setups: List[Dict[str, Any]],
        base_params: Optional[HypothesisParams] = None,
        test_params: Optional[HypothesisParams] = None,
        default_vix_mode: str = "standard",
    ) -> Dict[str, Any]:
        """
        Compare base vs test parameter set on the same setups.
        Returns {base: SimulationSummary, test: SimulationSummary, delta: dict}.

        delta keys: win_rate_delta, avg_pnl_delta, avg_hold_days_delta
        """
        base_summary = self.run(trade_setups, params=base_params, default_vix_mode=default_vix_mode)
        test_summary = self.run(trade_setups, params=test_params, default_vix_mode=default_vix_mode)
        delta = {
            "win_rate_delta":     test_summary.win_rate - base_summary.win_rate,
            "avg_pnl_delta":      test_summary.avg_pnl_net - base_summary.avg_pnl_net,
            "avg_hold_days_delta":test_summary.avg_hold_days - base_summary.avg_hold_days,
            "partial_exit_rate_delta": (
                test_summary.partial_exit_rate - base_summary.partial_exit_rate
            ),
        }
        return {"base": base_summary, "test": test_summary, "delta": delta}
