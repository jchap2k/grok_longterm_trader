"""
Position Manager - Tracks and monitors trading positions

Extracted from ClaudeTradingAgent monolith (Phase 1 refactor)
Manages: Position tracking, monitoring, watermarks, validated quotes, momentum reversals
"""

import json
import logging
import sys
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

# Add project root to path for relative imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from analytics.data_validator import DataValidator, validate_market_data
from utils.position_quantity import get_position_quantity

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages position tracking and monitoring for ClaudeTradingAgent.

    Responsibilities:
    - Track agent-opened positions (with entry prices, TP/SL targets)
    - Monitor positions against watermarks
    - Detect momentum reversals (dead cat bounce)
    - Validate quotes from multiple sources
    - Trigger position update recommendations
    - Track partial profit opportunities
    """

    def __init__(
        self,
        broker=None,
        data_provider=None,
        forbidden_symbols: Optional[set] = None,
        state_file=None
    ):
        """
        Initialize position manager.

        Args:
            broker: Trading broker instance
            data_provider: Market data provider
            forbidden_symbols: Set of symbols that cannot be traded
            state_file: Optional path override for JSON persistence (used in tests)
        """
        self.broker = broker
        self.data_provider = data_provider
        self.forbidden_symbols = forbidden_symbols or set()

        # Position tracking dictionaries
        self.agent_opened_positions = {}  # {symbol: quantity}
        self.agent_position_entry_prices = {}  # {symbol: entry_price}
        self.agent_position_convictions = {}  # {symbol: conviction_score (1-10)}
        self.agent_position_tp_targets = {}  # {symbol: take_profit_price}
        self.agent_position_sl_targets = {}  # {symbol: stop_loss_price}
        self.agent_position_partial_profits = {}  # {symbol: {'taken': bool, 'qty_sold': int, 'price': float, 'timestamp': str}}
        self.agent_position_high_water_marks = {}  # {symbol: highest_price_seen}

        # Momentum reversal tracking for "dead cat bounce" detection
        self.momentum_reversals = {}  # {symbol: {'low_watermark': float, 'recovery_high': float, ...}}

        # Bracket order update cooldowns
        self.agent_bracket_order_updates = {}  # {symbol: {'timestamp': datetime, 'price': float}}

        # Swing trading metadata (populated for hold_mode='swing' positions only)
        self.swing_metadata: Dict[str, Dict] = {}

        # Lock protecting swing_metadata dict (multi-threaded scheduler access)
        self._swing_lock = threading.RLock()

        # Persistence path - absolute so it works regardless of CWD at startup.
        # positions/ -> agent/ -> trading_agent/ -> ai_trader/ then ai_trader_data/
        if state_file is not None:
            self.state_file = Path(state_file)
        else:
            self.state_file = Path(__file__).resolve().parent.parent.parent.parent / "ai_trader_data" / "position_state.json"

    def save_to_json(self):
        """Persist all position state to JSON for restart safety."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "saved_at": datetime.now().isoformat(),
                "agent_opened_positions": self.agent_opened_positions,
                "agent_position_entry_prices": self.agent_position_entry_prices,
                "agent_position_convictions": self.agent_position_convictions,
                "agent_position_tp_targets": self.agent_position_tp_targets,
                "agent_position_sl_targets": self.agent_position_sl_targets,
                "agent_position_partial_profits": self.agent_position_partial_profits,
                "agent_position_high_water_marks": self.agent_position_high_water_marks,
                "momentum_reversals": {
                    sym: {
                        k: v.isoformat() if isinstance(v, datetime) else v
                        for k, v in data.items()
                    }
                    for sym, data in self.momentum_reversals.items()
                },
                "agent_bracket_order_updates": {
                    sym: {
                        k: v.isoformat() if isinstance(v, datetime) else v
                        for k, v in data.items()
                    }
                    for sym, data in self.agent_bracket_order_updates.items()
                },
                "swing_metadata": {
                    k: {**v, "next_earnings_date": v["next_earnings_date"].isoformat()
                        if v.get("next_earnings_date") and hasattr(v["next_earnings_date"], "isoformat")
                        else v.get("next_earnings_date")}
                    for k, v in self.swing_metadata.items()
                },
            }
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
            logger.debug(f"Position state saved: {len(self.agent_opened_positions)} positions")
        except Exception as e:
            logger.error(f"Failed to save position state: {e}")

    def load_from_json(self) -> bool:
        """
        Load position state from JSON on startup.

        Returns:
            True if state was loaded, False if no saved state found
        """
        if not self.state_file.exists():
            logger.info("No saved position state found - starting fresh")
            return False

        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)

            self.agent_opened_positions = state.get("agent_opened_positions", {})
            self.agent_position_entry_prices = state.get("agent_position_entry_prices", {})
            self.agent_position_convictions = state.get("agent_position_convictions", {})
            self.agent_position_tp_targets = state.get("agent_position_tp_targets", {})
            self.agent_position_sl_targets = state.get("agent_position_sl_targets", {})
            self.agent_position_partial_profits = state.get("agent_position_partial_profits", {})
            self.agent_position_high_water_marks = state.get("agent_position_high_water_marks", {})

            # Restore momentum_reversals (datetime fields stored as ISO strings)
            raw_reversals = state.get("momentum_reversals", {})
            for sym, data in raw_reversals.items():
                self.momentum_reversals[sym] = {}
                for k, v in data.items():
                    if k == "last_update" and isinstance(v, str):
                        try:
                            self.momentum_reversals[sym][k] = datetime.fromisoformat(v)
                        except (ValueError, TypeError):
                            self.momentum_reversals[sym][k] = v
                    else:
                        self.momentum_reversals[sym][k] = v

            # Restore bracket order cooldowns (datetime fields stored as ISO strings)
            raw_cooldowns = state.get("agent_bracket_order_updates", {})
            for sym, data in raw_cooldowns.items():
                self.agent_bracket_order_updates[sym] = {}
                for k, v in data.items():
                    if k == "timestamp" and isinstance(v, str):
                        try:
                            self.agent_bracket_order_updates[sym][k] = datetime.fromisoformat(v)
                        except (ValueError, TypeError):
                            self.agent_bracket_order_updates[sym][k] = v
                    else:
                        self.agent_bracket_order_updates[sym][k] = v

            # Restore swing_metadata (next_earnings_date stored as ISO string)
            raw_swing = state.get("swing_metadata", {})
            self.swing_metadata = {}
            for sym, meta in raw_swing.items():
                restored = dict(meta)
                if restored.get("next_earnings_date"):
                    from datetime import date as date_type
                    try:
                        restored["next_earnings_date"] = date_type.fromisoformat(restored["next_earnings_date"])
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            "swing_metadata load: could not parse next_earnings_date=%r for %s: %s",
                            restored["next_earnings_date"], sym, e
                        )
                        restored["next_earnings_date"] = None
                self.swing_metadata[sym] = restored

            saved_at = state.get("saved_at", "unknown")
            logger.info(
                f"Position state restored from {saved_at}: "
                f"{len(self.agent_opened_positions)} positions - "
                f"{list(self.agent_opened_positions.keys())}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to load position state: {e} - starting fresh")
            return False

    def add_position(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        conviction: int = 5,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None
    ):
        """
        Add a new position to tracking.

        Args:
            symbol: Stock symbol
            quantity: Number of shares
            entry_price: Entry price
            conviction: Conviction score 1-10
            take_profit: Take profit target price
            stop_loss: Stop loss price
        """
        symbol = symbol.upper()

        self.agent_opened_positions[symbol] = quantity
        self.agent_position_entry_prices[symbol] = entry_price
        self.agent_position_convictions[symbol] = conviction

        if take_profit:
            self.agent_position_tp_targets[symbol] = take_profit
        if stop_loss:
            self.agent_position_sl_targets[symbol] = stop_loss

        logger.info(
            f"Position added: {symbol} ({quantity} @ ${entry_price:.2f}, "
            f"conviction={conviction}, TP=${take_profit or 'None'}, SL=${stop_loss or 'None'})"
        )
        self.save_to_json()

    def set_swing_metadata(self, symbol: str, hold_mode: str,
                           entry_catalyst: str, hold_type: str,
                           next_earnings_date, profit_target,
                           initial_stop: float, current_stop: float,
                           shares_remaining: int,
                           entry_price: float = 0.0) -> None:
        """Attach swing-specific metadata to a tracked position."""
        with self._swing_lock:
            self.swing_metadata[symbol] = {
                "hold_mode": hold_mode,
                "entry_catalyst": entry_catalyst,
                "hold_type": hold_type,
                "next_earnings_date": next_earnings_date,
                "profit_target": profit_target,
                "initial_stop": initial_stop,
                "current_stop": current_stop,
                "shares_remaining": shares_remaining,
                "partial_exit_executed": False,
                "entry_date": datetime.now().date().isoformat(),
                "entry_price": float(entry_price),
                "progress_state": "UNKNOWN",
                "progress_score": 0.0,
                "progress_reason_codes": [],
                "progress_summary": "UNKNOWN not yet evaluated",
            }
        self.save_to_json()

    def get_swing_metadata(self, symbol: str) -> Optional[Dict]:
        """Return swing metadata for a symbol, or None if not set."""
        with self._swing_lock:
            return self.swing_metadata.get(symbol)

    def update_swing_trailing_stop(self, symbol: str, new_stop: float) -> None:
        """Trail stop up only - never moves down."""
        with self._swing_lock:
            if symbol not in self.swing_metadata:
                return
            current = self.swing_metadata[symbol]["current_stop"]
            if new_stop > current:
                self.swing_metadata[symbol]["current_stop"] = new_stop

    def update_swing_metadata_after_partial(self, symbol: str, sold_qty: int) -> None:
        """
        Record that a partial exit (50%) was executed.
        Reduces shares_remaining by sold_qty. Idempotent - second call is a no-op
        (guards against double-counting if broker confirms fill twice).
        """
        with self._swing_lock:
            if symbol not in self.swing_metadata:
                return
            if self.swing_metadata[symbol].get("partial_exit_executed"):
                return  # idempotent
            current_remaining = self.swing_metadata[symbol].get("shares_remaining", 0)
            self.swing_metadata[symbol]["shares_remaining"] = max(0, current_remaining - sold_qty)
            self.swing_metadata[symbol]["partial_exit_executed"] = True
            self.swing_metadata[symbol]["partial_exit_date"] = datetime.now().date().isoformat()
            self.swing_metadata[symbol]["progress_summary"] = "LATE_STAGE_RISK partial taken"

    def update_swing_progress_metadata(self, symbol: str, progress: Dict[str, Any]) -> None:
        """Persist latest progress-state snapshot for an open swing position."""
        with self._swing_lock:
            if symbol not in self.swing_metadata:
                return
            self.swing_metadata[symbol]["progress_state"] = progress.get("progress_state", "UNKNOWN")
            self.swing_metadata[symbol]["progress_score"] = float(progress.get("progress_score", 0.0) or 0.0)
            self.swing_metadata[symbol]["progress_reason_codes"] = list(progress.get("progress_reason_codes") or [])
            self.swing_metadata[symbol]["progress_summary"] = progress.get("progress_summary", "UNKNOWN")
        self.save_to_json()

    def remove_position(self, symbol: str):
        """
        Remove a position from tracking.

        Args:
            symbol: Stock symbol
        """
        symbol = symbol.upper()

        # Remove from all tracking dicts
        self.agent_opened_positions.pop(symbol, None)
        self.agent_position_entry_prices.pop(symbol, None)
        self.agent_position_convictions.pop(symbol, None)
        self.agent_position_tp_targets.pop(symbol, None)
        self.agent_position_sl_targets.pop(symbol, None)
        self.agent_position_partial_profits.pop(symbol, None)
        self.agent_position_high_water_marks.pop(symbol, None)
        self.momentum_reversals.pop(symbol, None)
        with self._swing_lock:
            self.swing_metadata.pop(symbol, None)

        logger.info(f"Position removed: {symbol}")
        self.save_to_json()

    def update_position_quantity(self, symbol: str, new_quantity: int):
        """
        Update position quantity.

        Args:
            symbol: Stock symbol
            new_quantity: New quantity (0 removes position)
        """
        symbol = symbol.upper()

        if new_quantity <= 0:
            self.remove_position(symbol)
        else:
            if symbol in self.agent_opened_positions:
                self.agent_opened_positions[symbol] = new_quantity
                logger.info(f"Position quantity updated: {symbol} -> {new_quantity} shares")
                self.save_to_json()

    def is_position_tracked(self, symbol: str) -> bool:
        """
        Check if a symbol is tracked by the agent.

        Args:
            symbol: Stock symbol

        Returns:
            True if position is tracked
        """
        return symbol.upper() in self.agent_opened_positions

    def get_position_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get all info about a tracked position.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with position info, or None if not tracked
        """
        symbol = symbol.upper()

        if symbol not in self.agent_opened_positions:
            return None

        return {
            "symbol": symbol,
            "quantity": self.agent_opened_positions.get(symbol, 0),
            "entry_price": self.agent_position_entry_prices.get(symbol),
            "conviction": self.agent_position_convictions.get(symbol),
            "take_profit": self.agent_position_tp_targets.get(symbol),
            "stop_loss": self.agent_position_sl_targets.get(symbol),
            "partial_profits": self.agent_position_partial_profits.get(symbol),
            "high_water_mark": self.agent_position_high_water_marks.get(symbol)
        }

    def record_partial_profit(
        self,
        symbol: str,
        quantity_sold: int,
        price: float
    ):
        """
        Record that partial profits were taken.

        Args:
            symbol: Stock symbol
            quantity_sold: Number of shares sold
            price: Price at which shares were sold
        """
        symbol = symbol.upper()

        self.agent_position_partial_profits[symbol] = {
            'taken': True,
            'qty_sold': quantity_sold,
            'price': price,
            'timestamp': datetime.now().isoformat()
        }

        logger.info(
            f"Partial profit recorded: {symbol} - sold {quantity_sold} @ ${price:.2f}"
        )

    def get_validated_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get validated quote from multiple data sources using DataValidator.

        Args:
            symbol: Stock symbol to validate

        Returns:
            Dict with consensus_price, confidence, and validation metadata
        """
        sources = {}

        # Get quote from data_provider if available
        if self.data_provider:
            try:
                provider_quote = self.data_provider.get_quote(symbol)
                if provider_quote:
                    sources['data_provider'] = provider_quote
            except Exception as e:
                logger.warning(f"Failed to get quote from data_provider for {symbol}: {e}")

        # Get quote from broker if available
        if self.broker:
            try:
                broker_quote = self.broker.get_quote(symbol)
                if broker_quote:
                    sources['broker'] = broker_quote
            except Exception as e:
                logger.warning(f"Failed to get quote from broker for {symbol}: {e}")

        # Special handling for VIX
        if symbol.upper() == 'VIX':
            try:
                validator = DataValidator()
                result = validator.validate_vix(sources)
                logger.info(
                    f"VIX validation: ${result['consensus_price']:.2f} "
                    f"(confidence: {result['confidence']:.2f}, "
                    f"sources: {result['sources_used']}/{result['total_sources']})"
                )
                return result
            except Exception as e:
                logger.error(f"VIX validation failed for {symbol}: {e}")
                # Fallback to any available source
                if sources:
                    fallback_source = next(iter(sources.values()))
                    return {
                        'symbol': symbol,
                        'consensus_price': fallback_source.get('price', fallback_source.get('last', 0)),
                        'confidence': 0.1,
                        'sources_used': 1,
                        'total_sources': len(sources),
                        'validation_status': 'fallback_single_source',
                        'warnings': ['VIX validation failed - using fallback']
                    }
                else:
                    raise Exception(f"No VIX data available from any source")

        # Validate regular symbols
        try:
            result = validate_market_data(symbol, sources)
            return result
        except Exception as e:
            logger.warning(f"Quote validation failed for {symbol}: {e}")
            # Fallback: return data from best available source
            if sources:
                best_source = None
                best_price = None

                for source_name, quote_data in sources.items():
                    price = quote_data.get('price') or quote_data.get('last') or quote_data.get('lastPrice')
                    if price and price > 0:
                        if best_price is None or source_name == 'broker':
                            best_price = price
                            best_source = source_name

                if best_price:
                    logger.warning(f"Using fallback price from {best_source} for {symbol}: ${best_price:.2f}")
                    return {
                        'symbol': symbol,
                        'consensus_price': best_price,
                        'confidence': 0.3,
                        'sources_used': 1,
                        'total_sources': len(sources),
                        'validation_status': 'fallback_single_source'
                    }

            # No data available
            raise Exception(f"No quote data available for {symbol}")

    def position_monitor_check(
        self,
        get_atr_stop_percent_callback
    ) -> Dict[str, Any]:
        """
        Lightweight position monitoring check (no LLM call).

        Checks positions against watermarks to determine if action is needed.

        Watermarks (aligned with action points):
        - Position down > 0.75% from entry -> triggers before -1% stop loss rule
        - Position at partial profit threshold -> triggers AT action point (not before)
        - Position up > 3% from entry -> triggers SL/TP adjustment review
        - Account value dropped near $25k PDT threshold

        Args:
            get_atr_stop_percent_callback: Callback function to get ATR stop percent
                                          signature: (symbol, current_price, multiplier) -> float

        Returns:
            Dict with keys:
            - trigger_update: bool - whether to call update_trades()
            - reasons: list - why update is triggered
            - positions_checked: int - number of positions monitored
        """
        result = {
            "trigger_update": False,
            "reasons": [],
            "positions_checked": 0,
            "timestamp": datetime.now().isoformat()
        }

        try:
            account_info = self.broker.get_account_info()
            if not account_info:
                logger.warning("Position monitor: Could not get account info")
                return result

            # Check 1: Account value near PDT threshold
            MINIMUM_ACCOUNT = 25000.0
            ACCOUNT_WARNING_BUFFER = 500.0
            if account_info.portfolio_value < (MINIMUM_ACCOUNT + ACCOUNT_WARNING_BUFFER):
                result["trigger_update"] = True
                result["reasons"].append(
                    f"ACCOUNT WARNING: ${account_info.portfolio_value:.2f} near $25k PDT threshold"
                )

            # Check 2: Idle portfolio detection
            if not account_info.positions or len(account_info.positions) == 0:
                MINIMUM_CASH_FOR_SCAN = 1000.0
                if account_info.cash >= MINIMUM_CASH_FOR_SCAN:
                    result["trigger_update"] = True
                    result["reasons"].append(
                        f"PORTFOLIO IDLE: 0 positions, ${account_info.cash:.2f} cash available - "
                        "scanning for opportunities"
                    )
                    logger.info(
                        f"Portfolio idle (0 positions) with ${account_info.cash:.2f} cash - "
                        "triggering opportunity scan"
                    )
                else:
                    logger.info(
                        f"Position monitor: 0 positions but only ${account_info.cash:.2f} cash - "
                        "skipping scan"
                    )
                return result

            # Get positions to check
            positions_to_check = []
            for pos in account_info.positions:
                symbol = pos.symbol.upper()
                # Only monitor positions we opened
                if symbol not in self.forbidden_symbols and symbol in self.agent_opened_positions:
                    positions_to_check.append(pos)

            result["positions_checked"] = len(positions_to_check)

            for pos in positions_to_check:
                symbol = pos.symbol.upper()
                quantity = get_position_quantity(pos)

                if quantity <= 0:
                    continue

                # Get entry price
                entry_price = self.agent_position_entry_prices.get(symbol)
                if not entry_price:
                    # Try to use position's avg_entry_price
                    if hasattr(pos, 'avg_entry_price') and pos.avg_entry_price:
                        entry_price = pos.avg_entry_price
                    else:
                        continue

                # Get validated current quote
                try:
                    validated_quote = self.get_validated_quote(symbol)
                    current_price = validated_quote["consensus_price"]
                    confidence = validated_quote["confidence"]

                    # Skip if confidence is too low
                    if confidence < 0.5:
                        logger.warning(
                            f"Position monitor: Skipping {symbol} - "
                            f"low confidence quote ({confidence:.1f})"
                        )
                        continue
                except Exception as e:
                    logger.warning(f"Position monitor: Could not get validated quote for {symbol}: {e}")
                    continue

                # Calculate P&L percentage
                pnl_percent = ((current_price - entry_price) / entry_price) * 100

                # SWING GUARD: Intraday P&L thresholds do not apply to swing positions.
                # Swing positions breathe 3-4% intraday; exits come from swing_exit_engine.
                is_swing = (
                    hasattr(self, 'swing_metadata') and
                    symbol in self.swing_metadata
                )

                # Watermark 1: Position down > 0.75% (day trades only)
                if not is_swing and pnl_percent < -0.75:
                    result["trigger_update"] = True
                    result["reasons"].append(
                        f"{symbol} DOWN {pnl_percent:.1f}% - approaching -1% stop loss"
                    )

                # EMERGENCY: Position down > 2% (day trades only)
                if not is_swing and pnl_percent < -2.0:
                    result["trigger_update"] = True
                    result["reasons"].append(
                        f"{symbol} EMERGENCY: DOWN {pnl_percent:.1f}% - exceeds 2% safety threshold"
                    )

                # Watermark 2: Position rocketing up > 3%
                if pnl_percent > 3.0:
                    conviction = self.agent_position_convictions.get(symbol, 5)
                    if conviction >= 7:
                        result["trigger_update"] = True
                        result["reasons"].append(
                            f"{symbol} ROCKETING +{pnl_percent:.1f}% - consider SL/TP adjustment"
                        )

                # Watermark 3: Partial profit threshold
                tp_target = self.agent_position_tp_targets.get(symbol)
                if tp_target and tp_target > entry_price:
                    # Check cooldown
                    PARTIAL_PROFIT_COOLDOWN_MINUTES = 15
                    if symbol in self.agent_position_partial_profits:
                        pp_info = self.agent_position_partial_profits[symbol]
                        last_taken_str = pp_info.get('timestamp')
                        if last_taken_str:
                            try:
                                last_taken = datetime.fromisoformat(last_taken_str)
                                minutes_since = (datetime.now() - last_taken).total_seconds() / 60
                                if minutes_since < PARTIAL_PROFIT_COOLDOWN_MINUTES:
                                    continue
                            except (ValueError, TypeError):
                                pass

                    # Calculate progress toward TP
                    price_range = tp_target - entry_price
                    current_profit = current_price - entry_price
                    progress_percent = (current_profit / price_range) * 100

                    # Determine threshold based on conviction
                    conviction = self.agent_position_convictions.get(symbol, 5)
                    if conviction >= 8:
                        trigger_threshold = 75
                    elif conviction >= 5:
                        trigger_threshold = 50
                    else:
                        trigger_threshold = 40

                    if progress_percent >= trigger_threshold:
                        result["trigger_update"] = True
                        result["reasons"].append(
                            f"{symbol} PARTIAL PROFIT +{pnl_percent:.1f}% - "
                            f"at {progress_percent:.0f}% to TP (threshold: {trigger_threshold}%)"
                        )

                # Watermark 4: ATR EMERGENCY STOP
                emergency_atr_percent = get_atr_stop_percent_callback(symbol, entry_price, multiplier=2.0)
                emergency_stop_price = round(entry_price * (1 - emergency_atr_percent / 100), 2)

                if current_price < emergency_stop_price:
                    result["trigger_update"] = True
                    result["reasons"].append(
                        f"{symbol} ATR EMERGENCY STOP: Current ${current_price:.2f} < "
                        f"Emergency ${emergency_stop_price:.2f} ({emergency_atr_percent:.1f}% ATR) - "
                        "cutting losses early"
                    )

                # Watermark 5: STOP LOSS ORDER VERIFICATION
                if entry_price and entry_price > 0:
                    atr_sl_percent = get_atr_stop_percent_callback(symbol, entry_price, multiplier=1.5)
                    calculated_stop_price = round(entry_price * (1 - atr_sl_percent / 100), 2)

                    if current_price < calculated_stop_price:
                        result["trigger_update"] = True
                        result["reasons"].append(
                            f"{symbol} STOP LOSS FAILURE: Current ${current_price:.2f} < "
                            f"Stop ${calculated_stop_price:.2f} - broker stop order failed to execute"
                        )

                # Watermark 6: MOMENTUM REVERSAL TRACKER ("Dead cat bounce")
                DECLINE_THRESHOLD = -1.5
                RECOVERY_ZONE_LOW = -0.5
                RECOVERY_ZONE_HIGH = 0.5
                FAILED_RECOVERY_DROP = 0.3
                COOLDOWN_SECONDS = 300

                # Initialize or get existing state
                if symbol not in self.momentum_reversals:
                    self.momentum_reversals[symbol] = {
                        'low_watermark': pnl_percent,
                        'recovery_high': pnl_percent,
                        'in_recovery': False,
                        'failed_recovery_triggered': False,
                        'last_update': datetime.now()
                    }

                reversal_state = self.momentum_reversals[symbol]

                # Check cooldown
                time_since_update = (datetime.now() - reversal_state['last_update']).total_seconds()
                if time_since_update >= COOLDOWN_SECONDS:
                    # Update state based on current P&L

                    # 1. Update low watermark
                    if pnl_percent < reversal_state['low_watermark']:
                        reversal_state['low_watermark'] = pnl_percent
                        reversal_state['in_recovery'] = False
                        reversal_state['last_update'] = datetime.now()
                        logger.debug(
                            f"{symbol}: New low watermark {pnl_percent:.1f}% "
                            f"(was {reversal_state['low_watermark']:.1f}%)"
                        )

                    # 2. Detect recovery attempt
                    elif (reversal_state['low_watermark'] < DECLINE_THRESHOLD and
                          not reversal_state['in_recovery'] and
                          RECOVERY_ZONE_LOW <= pnl_percent <= RECOVERY_ZONE_HIGH):
                        reversal_state['in_recovery'] = True
                        reversal_state['recovery_high'] = pnl_percent
                        reversal_state['last_update'] = datetime.now()
                        logger.info(
                            f"{symbol}: RECOVERY DETECTED - bounced from "
                            f"{reversal_state['low_watermark']:.1f}% to {pnl_percent:.1f}%"
                        )

                    # 3. Track recovery high
                    elif reversal_state['in_recovery'] and pnl_percent > reversal_state['recovery_high']:
                        reversal_state['recovery_high'] = pnl_percent
                        reversal_state['last_update'] = datetime.now()
                        logger.debug(f"{symbol}: New recovery high {pnl_percent:.1f}%")

                    # 4. FAILED RECOVERY
                    elif (reversal_state['in_recovery'] and
                          not reversal_state['failed_recovery_triggered'] and
                          (reversal_state['recovery_high'] - pnl_percent) >= FAILED_RECOVERY_DROP):

                        result["trigger_update"] = True
                        result["reasons"].append(
                            f"{symbol} FAILED RECOVERY: Peaked at {reversal_state['recovery_high']:+.1f}%, "
                            f"now {pnl_percent:+.1f}% (dropped {reversal_state['recovery_high'] - pnl_percent:.1f}% "
                            f"from recovery high) - SELL before returning to low of "
                            f"{reversal_state['low_watermark']:.1f}%"
                        )

                        reversal_state['failed_recovery_triggered'] = True
                        reversal_state['last_update'] = datetime.now()

                        logger.warning(f"MOMENTUM REVERSAL TRIGGERED: {symbol} - Dead cat bounce detected!")

            # Log result
            if result["trigger_update"]:
                logger.info(f"POSITION MONITOR: Triggering update - {', '.join(result['reasons'])}")
            else:
                logger.debug(f"Position monitor: {result['positions_checked']} positions OK, no triggers")

            return result

        except Exception as e:
            logger.error(f"Position monitor check failed: {e}", exc_info=True)
            return result
