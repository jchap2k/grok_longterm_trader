"""
Market Regime Filter - Research-backed gap fade strategy.

Based on academic research showing:
- Large gap-ups (>1%) tend to fade intraday (avg -0.5% drift)
- Large gap-downs (>1%) tend to bounce intraday (avg +0.21% recovery)
- Monday gaps are least reliable (61% fade rate vs 45% other days)
- Neutral opens (-0.5% to +0.5%) are most predictable

Key Research Findings:
- 90% of gap-ups close above prior close, but only 40% continue rising from open
- 45-47% of 1-2% gaps completely fill intraday
- Gap continuation strategy: ~40% accuracy (worse than coin flip)
- Gap fade/mean reversion: ~55-60% accuracy (research-backed edge)

Strategy: Use CONTRARIAN logic - big gaps suggest mean reversion, not continuation.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class MarketRegimeFilter:
    """
    Analyzes broad market conditions (SPY/QQQ) and provides adaptive
    trading thresholds based on market regime.
    """
    
    def __init__(self, data_provider, market_scanner=None, learning_db=None, oscillation_guard: bool = False):
        """
        Initialize the market regime filter.

        Args:
            data_provider: Data provider instance for fetching quotes
            market_scanner: Optional MarketScanner for SPY bars and sector ETF RS
            learning_db: Optional LearningDatabase for REGIME_BLOCK event logging (Task 12)
            oscillation_guard: Enable oscillation guard (REDUCED->FULL->REDUCED detection).
                Default False. Set to True externally or in scheduler to activate.
                Public attribute - can be toggled without code changes.
        """
        self.data_provider = data_provider
        self.market_scanner = market_scanner    # injected for SPY bars + sector RS
        self.learning_db = learning_db          # optional - for decision_journal logging
        self.current_regime = None
        self.regime_timestamp = None
        self.regime_data = None
        # TTL cache: {key: (value, fetched_at_datetime)}
        self._data_cache: dict = {}
        self._cache_ttl_minutes: int = 30
        self.oscillation_guard = oscillation_guard  # public - easy to flip externally
        # Oscillation guard state (in-memory; resets if process restarts)
        self._osc_mode_history: list = []   # rolling 3-day raw mode window
        self._osc_active: bool = False       # guard currently triggered
        self._osc_consecutive_full: int = 0  # consecutive FULL days while guard active

    @staticmethod
    def _derive_min_conviction_today(mode: str, score: int) -> Optional[float]:
        """Return the effective conviction floor after regime adjustments."""
        normalized_mode = str(mode or "CASH").upper()
        if normalized_mode == "CASH":
            return None
        if normalized_mode == "REDUCED":
            return 8.0

        score_to_min_conviction = {5: 6.75, 4: 7.25, 3: 7.75}
        return score_to_min_conviction.get(score)
        
    def check_market_regime(self, day_of_week: Optional[str] = None) -> Dict:
        """
        Thin wrapper over get_swing_regime_detail() for backward compatibility.
        Deprecated: prefer get_swing_regime_detail() for direct swing regime access.

        The day_of_week parameter is ignored; swing regime uses the 5-gate score
        (SPY 50d/200d MA, VIX, ADX, breadth), not intraday gap-fade logic.

        Returns a dict that includes all get_swing_regime_detail() fields plus
        legacy keys (regime, min_conviction, max_positions, preferred_strategies)
        for callers using the old check_market_regime() interface.
        """
        if day_of_week is not None:
            logger.debug(
                "[RegimeFilter] check_market_regime: day_of_week='%s' ignored "
                "(deprecated param - swing uses 5-gate score, not gap-fade logic)",
                day_of_week,
            )
        detail = self.get_swing_regime_detail()
        mode = str(detail.get("mode", "CASH")).upper()
        score = detail.get("swing_score", 0)

        # Map swing mode to legacy regime string
        _MODE_TO_REGIME = {
            "FULL":    "swing_full",
            "REDUCED": "swing_reduced",
            "CASH":    "swing_cash",
        }
        # Max positions from swing mode (swing spec: FULL=15, REDUCED=8, CASH=0)
        _MODE_TO_MAX_POSITIONS = {"FULL": 15, "REDUCED": 8, "CASH": 0}
        _MODE_TO_STRATEGIES = {
            "FULL":    ["breakout", "momentum", "pead"],
            "REDUCED": ["high_conviction_only", "pead"],
            "CASH":    [],
        }
        regime = _MODE_TO_REGIME.get(mode, "swing_cash")
        min_conviction = detail.get("min_conviction_today")
        if min_conviction is None:
            min_conviction = self._derive_min_conviction_today(mode, score)
        if min_conviction is None:
            min_conviction = 10.0
        max_positions = _MODE_TO_MAX_POSITIONS.get(mode, 0)
        preferred_strategies = _MODE_TO_STRATEGIES.get(mode, [])

        self.regime_timestamp = datetime.now()
        # Build full result: swing detail fields + legacy compatibility keys
        result = {
            **detail,   # includes swing_score, gates, vix_level, min_conviction_today, etc.
            # Legacy keys (backward compatible)
            "regime":               regime,
            "min_conviction":       min_conviction,
            "max_positions":        max_positions,
            "preferred_strategies": preferred_strategies,
            "spy_change":           0.0,   # Not computed by swing regime (gap-fade is day-trade logic)
            "qqq_change":           0.0,
            "market_change":        0.0,
            "description":          detail.get("mode", "UNKNOWN") + (
                " mode (score {}/5) - min_conviction={:.2f}, max_positions={}".format(
                    score, min_conviction, max_positions
                )
            ),
            "is_monday":            False,  # Swing regime does not apply Monday adjustment
            "timestamp":            self.regime_timestamp,
        }

        self.current_regime = regime
        self.regime_data = result
        logger.info(
            "[RegimeFilter] check_market_regime (wrapper): mode=%s score=%d/5 "
            "min_conviction=%.2f max_positions=%d",
            mode, score, min_conviction, max_positions,
        )
        return result
    
    def get_current_regime(self) -> Optional[Dict]:
        """
        Get the current regime data (cached from last check).
        
        Returns:
            dict with regime data or None if not yet checked
        """
        return self.regime_data
    
    def should_trade(self, conviction: int, current_positions: int) -> tuple[bool, str]:
        """
        Determine if a trade should be taken based on regime rules.
        
        Args:
            conviction: Conviction score (1-10) for the trade
            current_positions: Number of currently open positions
            
        Returns:
            tuple: (should_trade: bool, reason: str)
        """
        if not self.regime_data:
            # No regime data yet, use neutral defaults
            return True, "No regime data - using defaults"
        
        min_conviction = self.regime_data['min_conviction']
        max_positions = self.regime_data['max_positions']
        regime = self.regime_data['regime']
        
        # Check conviction threshold
        if conviction < min_conviction:
            return False, f"Conviction {conviction}/10 below {min_conviction}/10 required for {regime} market"
        
        # Check position limit
        if current_positions >= max_positions:
            return False, f"At max positions ({max_positions}) for {regime} market"
        
        return True, f"Trade approved for {regime} market (conviction {conviction}/10, positions {current_positions}/{max_positions})"
    
    def log_regime_summary(self):
        """Log a formatted summary of the current market regime."""
        if not self.regime_data:
            logger.warning("No regime data to summarize")
            return
        
        logger.info("=" * 60)
        logger.info("MARKET REGIME CHECK")
        logger.info("=" * 60)
        logger.info(f"SPY: {self.regime_data['spy_change']:+.2f}%")
        logger.info(f"QQQ: {self.regime_data['qqq_change']:+.2f}%")
        logger.info(f"Market Avg: {self.regime_data['market_change']:+.2f}%")
        logger.info(f"Regime: {self.regime_data['regime'].upper()}")
        logger.info(f"Description: {self.regime_data['description']}")
        logger.info(f"Min Conviction Required: {self.regime_data['min_conviction']}/10")
        logger.info(f"Max Positions Allowed: {self.regime_data['max_positions']}")
        logger.info("=" * 60)

    def _get_cached(self, key: str):
        """Return cached value if within TTL, else None."""
        entry = self._data_cache.get(key)
        if entry is None:
            return None
        value, fetched_at = entry
        if (datetime.now() - fetched_at).total_seconds() < self._cache_ttl_minutes * 60:
            return value
        return None

    def _set_cached(self, key: str, value) -> None:
        """Store value in TTL cache."""
        self._data_cache[key] = (value, datetime.now())

    def clear_cache(self) -> None:
        """Flush all TTL-cached data so the next call fetches fresh values.

        Use before regime re-checks that must reflect current market data
        (e.g. VIX recovery scan after a pre-market spike).
        """
        self._data_cache.clear()
        logger.info("[RegimeFilter] TTL cache cleared - next regime fetch will be fresh.")

    @staticmethod
    def _extract_history_series(hist, column: str):
        """Safely extract a non-empty series-like object from a yfinance history payload."""
        if hist is None:
            return None
        try:
            series = hist[column]
        except Exception:
            return None
        if series is None:
            return None
        try:
            series = series.dropna()
        except Exception:
            return None
        try:
            if len(series) == 0:
                return None
        except Exception:
            return None
        return series

    @staticmethod
    @contextmanager
    def _suppress_yfinance_error_logs():
        """Temporarily suppress yfinance ERROR log spam for partial batch-download failures."""
        logger_names = ("yfinance", "yfinance.multi")
        prior_levels = {}
        for name in logger_names:
            target = logging.getLogger(name)
            prior_levels[name] = target.level
            target.setLevel(logging.CRITICAL)
        try:
            yield
        finally:
            for name in logger_names:
                logging.getLogger(name).setLevel(prior_levels[name])

    # ------------------------------------------------------------------
    # Swing trading regime methods (Task 6)
    # ------------------------------------------------------------------

    SWING_SCORE_THRESHOLDS = {"full": 5, "reduced": 2}  # below 2 = cash
    VIX_THRESHOLDS = {"aggressive": 15, "standard": 20, "defensive": 25}
    SECTOR_ETFS = [
        "XLK", "XLE", "XLF", "XLV", "XLY",
        "XLP", "XLU", "XLI", "XLB", "XLRE", "XLC"
    ]

    def get_vix_mode(self, vix: float = None) -> str:
        """Return VIX regime string: aggressive / standard / defensive / cash."""
        v = vix if vix is not None else self._get_vix()
        if v < self.VIX_THRESHOLDS["aggressive"]:
            return "aggressive"
        if v < self.VIX_THRESHOLDS["standard"]:
            return "standard"
        if v < self.VIX_THRESHOLDS["defensive"]:
            return "defensive"
        return "cash"

    def get_swing_score(self) -> tuple:
        """
        Compute daily swing score (0-5) and mode string.
        Gates: SPY above 50d MA, SPY above 200d MA, VIX < 20,
               SPY ADX(14) > 25, pct of S&P500 stocks above 50d MA > 60%.
        Returns: (score int, mode str) where mode is 'full' / 'reduced' / 'cash'.
        """
        spy = self._get_spy_data()
        vix = self._get_vix()
        # Breadth gate: fail defensively on fetch error (conservative - no free pass).
        try:
            pct = self._get_pct_above_50d()
            breadth_above_60 = pct > 0.60
        except Exception as e:
            logger.warning(
                "[RegimeFilter] get_swing_score: breadth fetch failed: %s. "
                "Breadth gate set to FAIL (conservative).", e
            )
            breadth_above_60 = False
        score = sum([
            bool(spy.get("above_50d", False)),
            bool(spy.get("above_200d", False)),
            vix < 20,
            spy.get("adx14", 0) > 25,
            breadth_above_60,
        ])
        if score >= self.SWING_SCORE_THRESHOLDS["full"]:
            mode = "full"
        elif score >= self.SWING_SCORE_THRESHOLDS["reduced"]:
            mode = "reduced"
        else:
            mode = "cash"
        # VIX override: when VIX >= 25, force CASH regardless of score.
        # Keeps mode label consistent with vix_mode="CASH" in get_swing_regime_detail().
        if vix >= 25.0:
            mode = "cash"
        return score, mode

    def get_swing_regime_detail(self) -> dict:
        """
        Return full swing regime detail dict for agent tool consumption.

        Includes: swing_score, mode, per-gate pass/fail with raw values,
        VIX level + mode, SPY SMA levels, ADX, breadth, and VIX daily delta
        (to detect +5pt spike requiring all-exit).

        Safe to call multiple times per session - all underlying fetches are TTL cached.
        """
        spy = self._get_spy_data()
        qqq = self._get_qqq_data()
        iwm = self._get_iwm_data()
        vix = self._get_vix()

        # Proxy: yfinance ^SPXA50R (S&P 500 stocks above 50-day MA, value = percent 0-100).
        # Breadth gate: pct_above_50d > 60% required for a passing score.
        # Wrapped in try/except: on any fetch failure the gate is set to FAIL (conservative).
        _breadth_fetch_error = False
        try:
            pct = self._get_pct_above_50d()
            breadth_pass = pct > 0.60
        except Exception as e:
            logger.warning(
                "[RegimeFilter] Breadth gate fetch failed: %s. "
                "Gate set to FAIL (conservative).", e
            )
            pct = 0.0
            breadth_pass = False
            _breadth_fetch_error = True

        score, mode = self.get_swing_score()
        mode = str(mode or "CASH").upper()

        # VIX mode classification
        if vix >= 25.0:
            vix_mode = "CASH"
        elif vix >= 20.0:
            vix_mode = "DEFENSIVE"
        elif vix >= 15.0:
            vix_mode = "STANDARD"
        else:
            vix_mode = "AGGRESSIVE"

        # VIX cash override: apply here so guard can use it as a reset signal
        if vix_mode == "CASH":
            mode = "CASH"

        # Oscillation guard: detect REDUCED->FULL->REDUCED and require 2 consecutive
        # FULL days before granting FULL again. Activated by self.oscillation_guard=True.
        if self.oscillation_guard:
            raw_mode = mode
            if raw_mode == "CASH":
                # Low score or VIX spike: reset all guard state
                self._osc_mode_history.clear()
                self._osc_active = False
                self._osc_consecutive_full = 0
            else:
                # Maintain rolling 3-day window of raw modes
                self._osc_mode_history.append(raw_mode)
                if len(self._osc_mode_history) > 3:
                    self._osc_mode_history.pop(0)
                # Detect oscillation: REDUCED->FULL->REDUCED
                if (len(self._osc_mode_history) == 3
                        and self._osc_mode_history[-3] == "REDUCED"
                        and self._osc_mode_history[-2] == "FULL"
                        and self._osc_mode_history[-1] == "REDUCED"):
                    self._osc_active = True
                    self._osc_consecutive_full = 0
                    logger.info("[OscGuard] Oscillation detected. Guard active: require 2 consecutive FULL days.")
                # Apply guard if active
                if self._osc_active:
                    if raw_mode == "FULL":
                        self._osc_consecutive_full += 1
                        if self._osc_consecutive_full >= 2:
                            self._osc_active = False
                            self._osc_consecutive_full = 0
                            logger.info("[OscGuard] Guard cleared: 2 consecutive FULL days achieved.")
                        else:
                            mode = "REDUCED"
                            logger.info(
                                "[OscGuard] Guard active: raw=FULL but consecutive_full=%d/2, holding REDUCED.",
                                self._osc_consecutive_full,
                            )
                    else:
                        # Score dropped - reset consecutive counter, guard stays active
                        self._osc_consecutive_full = 0

        # VIX daily delta (yfinance prev-close comparison for spike detection)
        vix_delta = None
        vix_spike_alert = False
        try:
            import yfinance as yf
            tick = yf.Ticker("^VIX")
            hist = tick.history(period="2d")
            if len(hist) >= 2:
                vix_yesterday = float(hist["Close"].iloc[-2])
                vix_delta = round(vix - vix_yesterday, 2)
                vix_spike_alert = vix_delta >= 5.0
        except Exception:
            pass

        gates = {
            "spy_above_sma50": {
                "pass": bool(spy.get("above_50d", False)),
                "spy_close": round(spy.get("close", 0.0), 2),
                "sma50": round(spy.get("sma50", 0.0), 2),
            },
            "spy_above_sma200": {
                "pass": bool(spy.get("above_200d", False)),
                "spy_close": round(spy.get("close", 0.0), 2),
                "sma200": round(spy.get("sma200", 0.0), 2),
            },
            "vix_below_20": {
                "pass": vix < 20.0,
                "vix": round(vix, 1),
                "threshold": 20.0,
            },
            "spy_adx_above_25": {
                "pass": spy.get("adx14", 0.0) > 25.0,
                "adx14": round(spy.get("adx14", 0.0), 1),
                "threshold": 25.0,
            },
            "pct_above_50d_gt_60": {
                "pass": breadth_pass,
                "pct_above_50d": round(pct * 100, 1) if not _breadth_fetch_error else None,
                "threshold": 60.0,
                "fetch_error": _breadth_fetch_error,
            },
        }

        min_conviction_today = self._derive_min_conviction_today(mode, score)

        # REGIME_BLOCK: log to decision_journal when score < 3 (no trading today)
        if score < 3 and self.learning_db is not None:
            try:
                self.learning_db.log_regime_event(
                    event_type="REGIME_BLOCK",
                    regime_score=score,
                    gates=gates,
                )
            except Exception as e:
                logger.warning("[RegimeFilter] Failed to log REGIME_BLOCK: %s", e)

        # QQQ/SPY sector divergence
        _qqq_fetch_error = qqq.get("qqq_fetch_error", False)
        qqq_vs_spy_20d = round(qqq.get("return_20d", 0.0) - spy.get("return_20d", 0.0), 2)
        if qqq_vs_spy_20d >= 2.0:
            qqq_signal = "TECH_LEADING"
        elif qqq_vs_spy_20d <= -2.0:
            qqq_signal = "TECH_LAGGING"
        else:
            qqq_signal = "NEUTRAL"

        # SPY/IWM breadth divergence (large-cap vs small-cap)
        _iwm_fetch_error = iwm.get("iwm_fetch_error", False)
        spy_vs_iwm_20d = round(spy.get("return_20d", 0.0) - iwm.get("return_20d", 0.0), 2)
        if spy_vs_iwm_20d >= 2.0:
            spy_vs_iwm_signal = "LARGE_LEADING"
        elif spy_vs_iwm_20d <= -2.0:
            spy_vs_iwm_signal = "SMALL_LEADING"
        else:
            spy_vs_iwm_signal = "NEUTRAL"

        # QQQ/IWM growth vs value/small-cap divergence
        qqq_vs_iwm_20d = round(qqq.get("return_20d", 0.0) - iwm.get("return_20d", 0.0), 2)
        if qqq_vs_iwm_20d >= 2.0:
            qqq_vs_iwm_signal = "GROWTH_LEADING"
        elif qqq_vs_iwm_20d <= -2.0:
            qqq_vs_iwm_signal = "VALUE_LEADING"
        else:
            qqq_vs_iwm_signal = "NEUTRAL"

        result = {
            "swing_score": score,
            "gates_passed": score,
            "gates_total": 5,
            "mode": mode.upper(),
            "vix_level": round(vix, 1),
            "vix_mode": vix_mode,
            "spy_close": round(spy.get("close", 0.0), 2),
            "spy_sma50": round(spy.get("sma50", 0.0), 2),
            "spy_sma200": round(spy.get("sma200", 0.0), 2),
            "spy_adx14": round(spy.get("adx14", 0.0), 1),
            "pct_above_50d": round(pct * 100, 1) if not _breadth_fetch_error else None,
            "breadth_fetch_error": _breadth_fetch_error,
            "gates": gates,
            "vix_delta_today": vix_delta,
            "vix_spike_alert": vix_spike_alert,
            "min_conviction_today": min_conviction_today,
            "qqq_close": round(float(qqq.get("close", 0.0)), 2),
            "qqq_above_50d": bool(qqq.get("above_50d", False)),
            "qqq_above_200d": bool(qqq.get("above_200d", False)),
            "qqq_vs_spy_20d": qqq_vs_spy_20d,
            "qqq_signal": qqq_signal,
            "iwm_close": round(float(iwm.get("close", 0.0)), 2),
            "iwm_above_50d": bool(iwm.get("above_50d", False)),
            "iwm_above_200d": bool(iwm.get("above_200d", False)),
            "spy_vs_iwm_20d": spy_vs_iwm_20d,
            "spy_vs_iwm_signal": spy_vs_iwm_signal,
            "qqq_vs_iwm_20d": qqq_vs_iwm_20d,
            "qqq_vs_iwm_signal": qqq_vs_iwm_signal,
        }
        if vix_spike_alert:
            result["vix_spike_warning"] = (
                "VIX up {:.1f} pts today - all-exit rule threshold is +5pts".format(vix_delta)
            )

        # Summary log so the computed regime impact is visible alongside the raw data fetches.
        gate_summary = " | ".join(
            "%s=%s" % (k.replace("spy_above_", "").replace("_gt_60", ">60%").replace("_below_20", "<20").upper(),
                       "PASS" if v.get("pass") else "FAIL")
            for k, v in gates.items()
        )
        logger.info(
            "[RegimeFilter] RESULT: score=%d/5  mode=%s  VIX=%.1f(%s)  min_conviction=%s",
            score, mode.upper(), vix, vix_mode,
            ("%.2f" % min_conviction_today) if min_conviction_today is not None else "BLOCKED",
        )
        logger.info("[RegimeFilter] gates: %s", gate_summary)
        logger.info(
            "[RegimeFilter] 3-ETF signals: QQQ/SPY=%s(%+.2f%%)  SPY/IWM=%s(%+.2f%%)  QQQ/IWM=%s(%+.2f%%)",
            qqq_signal, qqq_vs_spy_20d,
            spy_vs_iwm_signal, spy_vs_iwm_20d,
            qqq_vs_iwm_signal, qqq_vs_iwm_20d,
        )

        return result

    def get_sector_rotation_signals(self) -> dict:
        """
        Flag sector ETFs whose current RS ratio vs SPY is at or above its
        52-week high (rotation entry signal).
        Returns dict: {sector: {rs_current, rs_52w_high, rotation: bool}}
        """
        ratios = self._get_sector_rs_ratios()
        signals = {}
        for sector, data in ratios.items():
            at_high = data["rs_current"] >= data["rs_52w_high"]
            signals[sector] = {
                "rs_current": data["rs_current"],
                "rs_52w_high": data["rs_52w_high"],
                "rotation": at_high,
            }
        return signals

    # ------------------------------------------------------------------
    # Data helpers - yfinance + market_scanner with TTL cache
    # ------------------------------------------------------------------

    def _calc_adx(self, bars: list, period: int = 14) -> float:
        """
        Wilder's ADX from list of OHLCV dicts with keys: high, low, close.
        Returns 0.0 if insufficient bars (< period * 2).
        """
        if len(bars) < period * 2:
            return 0.0
        trs, pdms, mdms = [], [], []
        for i in range(1, len(bars)):
            h, l = bars[i]["high"], bars[i]["low"]
            ph, pl, pc = bars[i - 1]["high"], bars[i - 1]["low"], bars[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            up, dn = h - ph, pl - l
            pdms.append(up if up > dn and up > 0 else 0.0)
            mdms.append(dn if dn > up and dn > 0 else 0.0)
            trs.append(tr)
        s_tr  = sum(trs[:period])
        s_pdm = sum(pdms[:period])
        s_mdm = sum(mdms[:period])
        dx_vals = []
        for i in range(period, len(trs)):
            s_tr  = s_tr  - (s_tr  / period) + trs[i]
            s_pdm = s_pdm - (s_pdm / period) + pdms[i]
            s_mdm = s_mdm - (s_mdm / period) + mdms[i]
            if s_tr == 0:
                continue
            pdi = 100.0 * s_pdm / s_tr
            mdi = 100.0 * s_mdm / s_tr
            di_sum = pdi + mdi
            dx_vals.append(100.0 * abs(pdi - mdi) / di_sum if di_sum else 0.0)
        if not dx_vals:
            return 0.0
        return sum(dx_vals[-period:]) / min(period, len(dx_vals))

    def _get_vix(self) -> float:
        """Return current VIX level. Uses yfinance ^VIX with data_provider fallback."""
        cached = self._get_cached("vix")
        if cached is not None:
            return cached
        # Primary: yfinance ^VIX
        try:
            import yfinance as yf
            info = yf.Ticker("^VIX").fast_info
            price = getattr(info, "last_price", None)
            if price is None:
                price = info.get("lastPrice") if hasattr(info, "get") else None
            if price and float(price) > 0:
                result = float(price)
                self._set_cached("vix", result)
                return result
        except Exception as e:
            logger.debug("_get_vix yfinance: %s", e)
        # Fallback: data_provider
        try:
            q = self.data_provider.get_quote("^VIX")
            result = float(q.get("price", 25.0))
            self._set_cached("vix", result)
            return result
        except Exception:
            pass
        logger.warning("_get_vix: all sources failed; defaulting to 25.0 (defensive)")
        return 25.0

    def _get_spy_data(self) -> dict:
        """
        Return SPY regime data: above_50d, above_200d, adx14, close, sma50, sma200.
        Uses yfinance as the primary source for SPY historical bars.
        market_scanner is not used here - it only returns ~21 bars (broker API
        limitation for daily history) versus the 200+ needed for SMA200/ADX.
        Returns safe defaults (close=0.0) only if yfinance fails.
        """
        _default = {
            "above_50d": False, "above_200d": False, "adx14": 0.0,
            "close": 0.0, "sma50": 0.0, "sma200": 0.0,
        }
        cached = self._get_cached("spy_data")
        if cached is not None:
            return cached

        try:
            import yfinance as yf
            hist = yf.Ticker("SPY").history(period="1y")
            closes = self._extract_history_series(hist, "Close")
            highs = self._extract_history_series(hist, "High")
            lows = self._extract_history_series(hist, "Low")
            if closes is None or highs is None or lows is None:
                logger.warning("_get_spy_data: yfinance returned malformed history payload")
                return _default
            if len(closes) < 50:
                logger.warning("_get_spy_data: yfinance returned only %d bars (<50)", len(closes))
                return _default
            close_values = closes.tolist()
            high_values = highs.tolist()
            low_values = lows.tolist()
            last_close = close_values[-1]
            sma50 = sum(close_values[-50:]) / 50
            sma200 = sum(close_values[-200:]) / 200 if len(close_values) >= 200 else sum(close_values) / len(close_values)
            bars_for_adx = [
                {
                    "high": float(high_values[i]),
                    "low":  float(low_values[i]),
                    "close": float(close_values[i]),
                }
                for i in range(min(len(close_values), len(high_values), len(low_values)))
            ]
            adx14 = self._calc_adx(bars_for_adx, period=14) if len(bars_for_adx) >= 28 else 0.0
            return_20d = 0.0
            if len(close_values) >= 21:
                return_20d = round((close_values[-1] / close_values[-21] - 1) * 100, 4)
            result = {
                "above_50d": last_close > sma50,
                "above_200d": last_close > sma200,
                "adx14": adx14,
                "close": last_close,
                "sma50": sma50,
                "sma200": sma200,
                "return_20d": return_20d,
            }
            logger.info(
                "_get_spy_data: above_50d=%s above_200d=%s adx14=%.1f close=%.2f return_20d=%.2f%%",
                result["above_50d"], result["above_200d"], adx14, last_close, return_20d,
            )
            self._set_cached("spy_data", result)
            return result
        except Exception as e:
            logger.warning("_get_spy_data yfinance failed: %s", e)
            return _default

    def _get_qqq_data(self) -> dict:
        """
        Return QQQ data for sector divergence: close, sma50, sma200, above_50d, above_200d, return_20d.
        Mirrors _get_spy_data() but for QQQ (no ADX needed - QQQ is not a regime gate input).
        Returns safe defaults with qqq_fetch_error=True if yfinance fails.
        """
        _default = {
            "above_50d": False, "above_200d": False,
            "close": 0.0, "sma50": 0.0, "sma200": 0.0,
            "return_20d": 0.0, "qqq_fetch_error": True,
        }
        cached = self._get_cached("qqq_data")
        if cached is not None:
            return cached

        try:
            import yfinance as yf
            hist = yf.Ticker("QQQ").history(period="1y")
            closes = self._extract_history_series(hist, "Close")
            if closes is None:
                logger.warning("_get_qqq_data: yfinance returned malformed history payload")
                return _default
            if len(closes) < 50:
                logger.warning("_get_qqq_data: yfinance returned only %d bars (<50)", len(closes))
                return _default
            close_values = closes.tolist()
            last_close = close_values[-1]
            sma50 = sum(close_values[-50:]) / 50
            sma200 = sum(close_values[-200:]) / 200 if len(close_values) >= 200 else sum(close_values) / len(close_values)
            return_20d = 0.0
            if len(close_values) >= 21:
                return_20d = round((close_values[-1] / close_values[-21] - 1) * 100, 4)
            result = {
                "above_50d": last_close > sma50,
                "above_200d": last_close > sma200,
                "close": last_close,
                "sma50": sma50,
                "sma200": sma200,
                "return_20d": return_20d,
            }
            logger.info(
                "_get_qqq_data: above_50d=%s above_200d=%s close=%.2f return_20d=%.2f%%",
                result["above_50d"], result["above_200d"], last_close, return_20d,
            )
            self._set_cached("qqq_data", result)
            return result
        except Exception as e:
            logger.warning("_get_qqq_data yfinance failed: %s", e)
            return _default

    def _get_iwm_data(self) -> dict:
        """
        Return IWM (Russell 2000) data for breadth/rotation analysis: close, sma50, sma200,
        above_50d, above_200d, return_20d.
        Mirrors _get_qqq_data() but for IWM (Russell 2000 small-cap ETF).
        Returns safe defaults with iwm_fetch_error=True if yfinance fails.
        """
        _default = {
            "above_50d": False, "above_200d": False,
            "close": 0.0, "sma50": 0.0, "sma200": 0.0,
            "return_20d": 0.0, "iwm_fetch_error": True,
        }
        cached = self._get_cached("iwm_data")
        if cached is not None:
            return cached

        try:
            import yfinance as yf
            hist = yf.Ticker("IWM").history(period="1y")
            closes = self._extract_history_series(hist, "Close")
            if closes is None:
                logger.warning("_get_iwm_data: yfinance returned malformed history payload")
                return _default
            if len(closes) < 50:
                logger.warning("_get_iwm_data: yfinance returned only %d bars (<50)", len(closes))
                return _default
            close_values = closes.tolist()
            last_close = close_values[-1]
            sma50 = sum(close_values[-50:]) / 50
            sma200 = sum(close_values[-200:]) / 200 if len(close_values) >= 200 else sum(close_values) / len(close_values)
            return_20d = 0.0
            if len(close_values) >= 21:
                return_20d = round((close_values[-1] / close_values[-21] - 1) * 100, 4)
            result = {
                "above_50d": last_close > sma50,
                "above_200d": last_close > sma200,
                "close": last_close,
                "sma50": sma50,
                "sma200": sma200,
                "return_20d": return_20d,
            }
            logger.info(
                "_get_iwm_data: above_50d=%s above_200d=%s close=%.2f return_20d=%.2f%%",
                result["above_50d"], result["above_200d"], last_close, return_20d,
            )
            self._set_cached("iwm_data", result)
            return result
        except Exception as e:
            logger.warning("_get_iwm_data yfinance failed: %s", e)
            return _default

    def _get_pct_above_50d(self) -> float:
        """
        Fraction of S&P 500 sectors above their 50-day MA.
        Proxy: 11 GICS sector ETFs (XLK/XLF/XLE/XLV/XLY/XLI/XLB/XLRE/XLC/XLU/XLP).
        ^SPXA50R and similar CBOE breadth indices are no longer available via yfinance.
        Raises on any fetch error so callers can apply defensive gate logic.
        """
        cached = self._get_cached("pct_above_50d")
        if cached is not None:
            return cached
        import yfinance as yf
        # 11 GICS sector ETFs - reliable yfinance tickers
        _SECTOR_ETFS = [
            "XLK", "XLF", "XLE", "XLV", "XLY",
            "XLI", "XLB", "XLRE", "XLC", "XLU", "XLP",
        ]
        # Single batch download (1 HTTP request) instead of 11 sequential Ticker calls.
        # Verified identical data quality vs per-ticker calls (test_yf_batch_vs_single.py).
        with self._suppress_yfinance_error_logs():
            hist_all = yf.download(
                " ".join(_SECTOR_ETFS),
                period="90d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=False,
            )
        above = 0
        valid = 0
        for etf in _SECTOR_ETFS:
            closes = None
            try:
                sector_hist = hist_all[etf]
            except Exception:
                sector_hist = None
            if sector_hist is None:
                continue
            closes = self._extract_history_series(sector_hist, "Close")
            if closes is None or len(closes) < 52:
                continue
            sma50 = closes.rolling(50).mean().iloc[-1]
            current = closes.iloc[-1]
            if current > sma50:
                above += 1
            valid += 1
        if valid == 0:
            raise RuntimeError("sector ETF breadth: no valid data returned for any of 11 ETFs")
        result = above / valid
        self._set_cached("pct_above_50d", result)
        return result

    def _get_sector_rs_ratios(self) -> dict:
        """
        Return sector RS data vs SPY.
        Format: {etf: {"rs_current": float, "rs_52w_high": float}}
        rs_52w_high = 1.0 baseline (sector outperforming SPY when rs_current >= 1.0).
        """
        cached = self._get_cached("sector_rs")
        if cached is not None:
            return cached
        if self.market_scanner is None:
            logger.warning("_get_sector_rs_ratios: no market_scanner injected; returning {}")
            return {}
        ratios = {}
        for etf in self.SECTOR_ETFS:
            try:
                rs = self.market_scanner.get_sector_etf_rs(etf, lookback_days=20)
                ratios[etf] = {"rs_current": rs, "rs_52w_high": 1.0}
            except Exception as e:
                logger.debug("_get_sector_rs_ratios %s: %s", etf, e)
        if ratios:
            self._set_cached("sector_rs", ratios)
        return ratios
