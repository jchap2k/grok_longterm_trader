"""
Order Manager - Handles order execution, tracking, and cooldowns

Extracted from ClaudeTradingAgent monolith (Phase 1 refactor)
Manages: Order cooldowns, pending order tracking, order execution recording
"""

import logging
import time
from typing import Dict, Any, Optional

from utils.position_quantity import get_position_quantity

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order execution and tracking for ClaudeTradingAgent.

    Responsibilities:
    - Track pending (unfilled) LIMIT orders
    - Enforce order cooldowns to prevent duplicate orders
    - Verify position state before order execution
    - Record order executions for cooldown tracking
    - Check pending orders for fills/timeouts
    """

    def __init__(
        self,
        broker=None,
        learning_db=None,
        pending_order_timeout_sec: int = 300
    ):
        """
        Initialize order manager.

        Args:
            broker: Trading broker instance
            learning_db: Learning database for trade recording
            pending_order_timeout_sec: Timeout for unfilled LIMIT orders (default 5 min)
        """
        self.broker = broker
        self.learning_db = learning_db
        self.PENDING_ORDER_TIMEOUT_SEC = pending_order_timeout_sec

        # Order tracking
        self.pending_orders = {}  # {order_id: {...order_details...}}
        self.recently_sold_symbols = {}  # {symbol: timestamp}
        self.recently_bought_symbols = {}  # {symbol: timestamp}

    def check_order_cooldown(
        self,
        symbol: str,
        side: str,
        agent_positions: Dict[str, int],
        cooldown_seconds: int = 1800
    ) -> Dict[str, Any]:
        """
        Check if an order was recently executed for this symbol/side.
        Includes position confirmation to catch hallucinations.

        Args:
            symbol: Stock symbol
            side: "buy" or "sell"
            agent_positions: Dict of agent-tracked positions {symbol: qty}
            cooldown_seconds: Minimum seconds between orders (default: 30 minutes)

        Returns:
            Dict with 'allowed': True/False and 'reason' if blocked
        """
        current_time = time.time()
        symbol = symbol.upper()
        side = side.lower()

        # Use appropriate tracking dict
        if side == "sell":
            tracking_dict = self.recently_sold_symbols
        else:
            tracking_dict = self.recently_bought_symbols

        # Cleanup old entries
        for sym in list(tracking_dict.keys()):
            if current_time - tracking_dict[sym] > cooldown_seconds + 60:
                del tracking_dict[sym]

        # Check cooldown
        if symbol in tracking_dict:
            elapsed = current_time - tracking_dict[symbol]
            if elapsed < cooldown_seconds:
                mins_remaining = (cooldown_seconds - elapsed) / 60
                logger.warning(
                    f"COOLDOWN BLOCK: {side.upper()} {symbol} blocked - "
                    f"last {side} was {elapsed:.0f}s ago ({mins_remaining:.1f} min remaining)"
                )
                return {
                    'allowed': False,
                    'reason': 'cooldown_active',
                    'elapsed_seconds': elapsed,
                    'cooldown_seconds': cooldown_seconds,
                    'minutes_remaining': mins_remaining
                }

        # POSITION CONFIRMATION: Verify the action makes sense
        if side == "sell":
            # Confirm we actually have a position to sell
            agent_qty = agent_positions.get(symbol, 0)
            if agent_qty <= 0:
                logger.warning(
                    f"CONFIRMATION BLOCK: SELL {symbol} rejected - "
                    f"no position in agent tracking (qty={agent_qty})"
                )
                return {
                    'allowed': False,
                    'reason': 'no_position_to_sell',
                    'agent_qty': agent_qty
                }

            # Double-check with broker
            if self.broker:
                try:
                    broker_qty = 0
                    account_info = self.broker.get_account_info()
                    if account_info and account_info.positions:
                        for pos in account_info.positions:
                            if pos.symbol.upper() == symbol:
                                broker_qty = get_position_quantity(pos)
                                break
                    if broker_qty <= 0:
                        logger.warning(
                            f"CONFIRMATION BLOCK: SELL {symbol} rejected - "
                            f"broker shows no position (broker_qty={broker_qty})"
                        )
                        return {
                            'allowed': False,
                            'reason': 'no_broker_position',
                            'broker_qty': broker_qty
                        }
                except Exception as e:
                    logger.warning(f"Could not confirm broker position for {symbol}: {e}")

        elif side == "buy":
            # Confirm we don't already have this position
            agent_qty = agent_positions.get(symbol, 0)
            if agent_qty > 0:
                logger.warning(
                    f"CONFIRMATION BLOCK: BUY {symbol} rejected - "
                    f"already holding {agent_qty} shares"
                )
                return {
                    'allowed': False,
                    'reason': 'already_holding_position',
                    'agent_qty': agent_qty
                }

        return {'allowed': True}

    def check_and_record_sell(
        self,
        symbol: str,
        cooldown_seconds: int = 15
    ) -> bool:
        """
        Check if a sell is allowed (not on cooldown) and record it if allowed.

        This prevents double-selling the same position within the cooldown period.

        Args:
            symbol: Stock symbol to check
            cooldown_seconds: Cooldown period in seconds (default: 15)

        Returns:
            True if sell is allowed (not on cooldown), False if blocked
        """
        symbol = symbol.upper()
        current_time = time.time()

        # Check if symbol was recently sold
        if symbol in self.recently_sold_symbols:
            last_sell_time = self.recently_sold_symbols[symbol]
            elapsed = current_time - last_sell_time

            if elapsed < cooldown_seconds:
                # Still on cooldown
                return False

        # Allowed - record this sell
        self.recently_sold_symbols[symbol] = current_time
        return True

    def record_order_executed(self, symbol: str, side: str):
        """
        Record that an order was successfully executed (for cooldown tracking).

        Args:
            symbol: Stock symbol
            side: "buy" or "sell"
        """
        symbol = symbol.upper()
        side = side.lower()

        if side == "sell":
            self.recently_sold_symbols[symbol] = time.time()
            logger.info(f"Recorded SELL for {symbol} - 30-min cooldown started")
        else:
            self.recently_bought_symbols[symbol] = time.time()
            logger.info(f"Recorded BUY for {symbol} - 30-min cooldown started")

    def add_pending_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        requested_qty: int,
        entry_price: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        conviction_score: Optional[int] = None
    ):
        """
        Add an order to pending tracking.

        Args:
            order_id: Broker order ID
            symbol: Stock symbol
            side: "buy" or "sell"
            requested_qty: Number of shares requested
            entry_price: Entry price
            take_profit: Take profit target price
            stop_loss: Stop loss price
            conviction_score: Conviction score 1-10
        """
        self.pending_orders[order_id] = {
            'symbol': symbol.upper(),
            'side': side.lower(),
            'requested_qty': requested_qty,
            'original_requested_qty': requested_qty,
            'reported_filled_qty': 0,
            'entry_price': entry_price,
            'take_profit': take_profit,
            'stop_loss': stop_loss,
            'conviction_score': conviction_score,
            'placed_at': time.time()
        }

        logger.info(
            f"Pending order added: {order_id} - {side.upper()} {requested_qty} {symbol} @ ${entry_price:.2f}"
        )

    def check_pending_orders(
        self,
        agent_positions: Dict[str, int],
        agent_entry_prices: Dict[str, float],
        agent_tp_targets: Dict[str, float],
        agent_convictions: Dict[str, int]
    ) -> Dict[str, Any]:
        """
        Check all pending LIMIT orders for fills.

        - If filled: updates tracking dicts, returns filled orders
        - If expired (>timeout): cancels the order
        - If cancelled/rejected by broker: removes from pending

        Args:
            agent_positions: Dict to update with filled positions
            agent_entry_prices: Dict to update with entry prices
            agent_tp_targets: Dict to update with TP targets
            agent_convictions: Dict to update with convictions

        Returns:
            Dict with 'filled_orders': list of filled order details
        """
        if not self.pending_orders:
            return {'filled_orders': []}

        now = time.time()
        to_remove = []
        filled_orders = []

        for order_id, data in list(self.pending_orders.items()):
            symbol = data['symbol']
            try:
                updated = self.broker.get_order_status(order_id)
                reported_filled_qty = updated.filled_quantity if hasattr(updated, 'filled_quantity') else 0
                try:
                    reported_filled_qty = int(reported_filled_qty or 0)
                except (TypeError, ValueError):
                    reported_filled_qty = 0
                last_reported_qty = int(data.get('reported_filled_qty', 0) or 0)
                filled_qty = max(0, reported_filled_qty - last_reported_qty)
                filled_price = updated.filled_price if hasattr(updated, 'filled_price') else None
                status = updated.status if hasattr(updated, 'status') else None
                status_str = status.name.lower() if hasattr(status, 'name') else str(status).lower()

                if filled_qty > 0:
                    # Order filled
                    logger.info(
                        f"Pending LIMIT order filled: {symbol} {filled_qty} shares" +
                        (f" at ${filled_price:.2f}" if filled_price else "")
                    )

                    # Update position tracking
                    current_qty = agent_positions.get(symbol, 0)
                    agent_positions[symbol] = current_qty + filled_qty

                    # Update entry price
                    actual_entry = filled_price if filled_price else data.get('entry_price')
                    if actual_entry:
                        agent_entry_prices[symbol] = actual_entry

                    # Update TP target
                    take_profit = data.get('take_profit')
                    if take_profit:
                        agent_tp_targets[symbol] = take_profit

                    # Update conviction
                    conviction = data.get('conviction_score')
                    if conviction:
                        agent_convictions[symbol] = conviction

                    self.pending_orders[order_id]['reported_filled_qty'] = reported_filled_qty

                    # Record filled order details
                    filled_orders.append({
                        'order_id': order_id,
                        'symbol': symbol,
                        'quantity': filled_qty,
                        'price': filled_price or actual_entry,
                        'side': data.get('side', 'buy')
                    })

                    # Remove from pending if fully filled
                    remaining = max(0, data['requested_qty'] - filled_qty)
                    self.pending_orders[order_id]['requested_qty'] = remaining

                    if remaining <= 0 or reported_filled_qty >= data.get('original_requested_qty', reported_filled_qty):
                        to_remove.append(order_id)
                    else:
                        # Partial fill
                        logger.info(
                            f"Partial fill on pending order {order_id}: "
                            f"{reported_filled_qty}/{data.get('original_requested_qty', data['requested_qty'])} "
                            f"{symbol} filled, {remaining} remaining"
                        )

                if 'cancelled' in status_str or 'canceled' in status_str or 'rejected' in status_str:
                    logger.info(f"Pending order {order_id} for {symbol} was {status_str} - removing")
                    to_remove.append(order_id)
                    continue

                # Check for timeout
                age_sec = now - data.get('placed_at', now)
                if age_sec > self.PENDING_ORDER_TIMEOUT_SEC:
                    logger.warning(
                        f"Pending LIMIT order {order_id} for {symbol} timed out after {age_sec:.0f}s "
                        f"(limit={self.PENDING_ORDER_TIMEOUT_SEC}s) - cancelling"
                    )
                    try:
                        self.broker.cancel_order(order_id)
                    except Exception as e:
                        logger.warning(f"Could not cancel timed-out pending order {order_id}: {e}")
                    to_remove.append(order_id)

            except Exception as e:
                logger.warning(f"Could not check pending order {order_id} for {symbol}: {e}")

        # Clean up removed orders
        for order_id in to_remove:
            self.pending_orders.pop(order_id, None)

        if to_remove:
            logger.debug(
                f"Pending order cleanup: removed {len(to_remove)} order(s), "
                f"{len(self.pending_orders)} still pending"
            )

        return {'filled_orders': filled_orders}

    def get_pending_count(self) -> int:
        """Get count of pending orders."""
        return len(self.pending_orders)

    def get_pending_symbols(self) -> set:
        """Get set of symbols with pending orders."""
        return {data['symbol'] for data in self.pending_orders.values()}

    def is_pending(self, symbol: str) -> bool:
        """
        Check if symbol has a pending order.

        Args:
            symbol: Stock symbol

        Returns:
            True if symbol has pending order
        """
        symbol = symbol.upper()
        return symbol in {data['symbol'] for data in self.pending_orders.values()}

    def clear_cooldowns(self):
        """Clear all cooldown trackers."""
        self.recently_sold_symbols.clear()
        self.recently_bought_symbols.clear()
        logger.info("Order cooldowns cleared")

    def clear_pending_orders(self):
        """Clear all pending order tracking."""
        self.pending_orders.clear()
        logger.info("Pending orders cleared")
