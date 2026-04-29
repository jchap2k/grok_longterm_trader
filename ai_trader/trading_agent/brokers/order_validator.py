"""
Pre-Order Validation Module

Comprehensive validation checks before placing trading orders.
Prevents common order placement errors and ensures all orders meet safety criteria.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class OrderValidationError(Exception):
    """Raised when an order fails validation."""
    pass


class ValidationSeverity(Enum):
    """Severity levels for validation issues."""
    ERROR = "error"  # Blocks order
    WARNING = "warning"  # Logs but allows order
    INFO = "info"  # Informational only


class OrderValidator:
    """
    Comprehensive pre-order validation.
    
    Checks all aspects of an order before submission to prevent:
    - Duplicate orders
    - Invalid prices
    - Insufficient funds
    - Position conflicts
    - Market hours violations
    - And more...
    """
    
    def __init__(self, broker=None, data_provider=None):
        """
        Initialize order validator.
        
        Args:
            broker: Trading broker instance (for balance checks)
            data_provider: Market data provider (for price validation)
        """
        self.broker = broker
        self.data_provider = data_provider
        self.recent_orders = []  # Track recent orders for duplicate detection
        
    def validate_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        current_positions: Optional[Dict[str, int]] = None,
        account_value: Optional[float] = None,
        available_cash: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Validate an order before placement.
        
        Args:
            symbol: Stock symbol
            side: 'buy' or 'sell'
            quantity: Number of shares
            order_type: 'market', 'limit', 'stop', 'moc'
            limit_price: Limit price (for limit orders)
            stop_price: Stop price (for stop orders)
            current_positions: Dict of current positions {symbol: quantity}
            account_value: Current account value
            available_cash: Available cash for trading
            
        Returns:
            Dict with validation results:
            {
                'valid': bool,
                'errors': List[str],
                'warnings': List[str],
                'info': List[str]
            }
        """
        result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': []
        }
        
        try:
            # 1. Basic parameter validation
            self._validate_parameters(
                symbol, side, quantity, order_type, 
                limit_price, stop_price, result
            )
            
            # 2. Symbol validation
            self._validate_symbol(symbol, result)
            
            # 3. Price validation
            self._validate_prices(
                symbol, order_type, limit_price, stop_price, result, side=side
            )
            
            # 4. Position validation (for sells)
            if side.lower() == 'sell':
                self._validate_sell_position(
                    symbol, quantity, current_positions, result
                )
            
            # 5. Buying power validation (for buys)
            if side.lower() == 'buy':
                self._validate_buying_power(
                    symbol, quantity, limit_price, 
                    available_cash, account_value, result
                )
            
            # 6. Market hours validation
            self._validate_market_hours(order_type, result)
            
            # 7. Duplicate order detection
            self._check_duplicate_order(
                symbol, side, quantity, result, order_type
            )
            
            # 8. Order size validation
            self._validate_order_size(quantity, result)
            
            # Set overall validity
            result['valid'] = len(result['errors']) == 0
            
            # Log validation result
            if not result['valid']:
                logger.warning(
                    f"Order validation FAILED for {side} {quantity} {symbol}: "
                    f"{', '.join(result['errors'])}"
                )
            elif result['warnings']:
                logger.warning(
                    f"Order validation WARNINGS for {side} {quantity} {symbol}: "
                    f"{', '.join(result['warnings'])}"
                )
            else:
                logger.info(f"Order validation PASSED for {side} {quantity} {symbol}")
                
        except Exception as e:
            logger.error(f"Order validation error: {e}", exc_info=True)
            result['valid'] = False
            result['errors'].append(f"Validation error: {str(e)}")
            
        return result
    
    def _validate_parameters(
        self, symbol: str, side: str, quantity: int, order_type: str,
        limit_price: Optional[float], stop_price: Optional[float],
        result: Dict[str, Any]
    ):
        """Validate basic order parameters."""
        # Symbol
        if not symbol or not isinstance(symbol, str):
            result['errors'].append("Invalid symbol")
            
        # Side
        if side.lower() not in ['buy', 'sell']:
            result['errors'].append(f"Invalid side: {side} (must be 'buy' or 'sell')")
            
        # Quantity
        if not isinstance(quantity, int) or quantity <= 0:
            result['errors'].append(f"Invalid quantity: {quantity} (must be positive integer)")
            
        # Order type
        valid_types = ['market', 'limit', 'stop', 'stop_limit', 'moc']
        if order_type.lower() not in valid_types:
            result['errors'].append(f"Invalid order type: {order_type}")
            
        # Limit price (required for limit orders)
        if order_type.lower() == 'limit':
            if limit_price is None or limit_price <= 0:
                result['errors'].append("Limit price required for limit orders")
                
        # Stop price (required for stop orders)
        if order_type.lower() in ['stop', 'stop_limit']:
            if stop_price is None or stop_price <= 0:
                result['errors'].append("Stop price required for stop orders")
    
    def _validate_symbol(self, symbol: str, result: Dict[str, Any]):
        """Validate symbol format and tradability."""
        # Basic format check
        symbol = symbol.upper().strip()
        
        if len(symbol) < 1 or len(symbol) > 5:
            result['errors'].append(f"Invalid symbol format: {symbol}")
            
        if not symbol.isalpha():
            result['warnings'].append(f"Symbol contains non-alpha characters: {symbol}")
    
    def _validate_prices(
        self, symbol: str, order_type: str,
        limit_price: Optional[float], stop_price: Optional[float],
        result: Dict[str, Any], side: str = None
    ):
        """Validate price reasonableness against current market price and quote freshness."""
        try:
            # Get current market price if data provider available
            if self.data_provider:
                quote_data = self._get_quote_with_timestamp(symbol)

                if not quote_data:
                    result['warnings'].append("Cannot validate prices - no quote data available")
                    return

                current_price = quote_data.get('price')
                quote_age_seconds = quote_data.get('age_seconds', 0)

                if current_price:
                    # CRITICAL: Check quote freshness - REJECT stale data for market BUY orders only.
                    # STOP and LIMIT orders use fixed prices so staleness doesn't affect fill quality.
                    # SELL orders should ALWAYS be allowed through - blocking a protective sell is
                    # far more dangerous than a slightly stale fill price.
                    MAX_QUOTE_AGE_SECONDS = 30
                    order_type_lower = (order_type or '').lower()
                    side_lower = (side or '').lower()
                    is_market_order = order_type_lower in ('market', 'moc')
                    is_sell_order = side_lower == 'sell'

                    if is_market_order and not is_sell_order and quote_age_seconds > MAX_QUOTE_AGE_SECONDS:
                        result['errors'].append(
                            f"Quote data is too old ({quote_age_seconds:.0f} seconds) - "
                            f"refusing order to prevent bad fills from stale prices. "
                            f"Max allowed: {MAX_QUOTE_AGE_SECONDS} seconds"
                        )
                        return
                    elif quote_age_seconds > MAX_QUOTE_AGE_SECONDS:
                        result['warnings'].append(
                            f"Quote data is {quote_age_seconds:.0f}s old (price sanity check may be imprecise)"
                        )

                    # Only run price sanity checks when quote is fresh enough to be meaningful
                    if quote_age_seconds <= MAX_QUOTE_AGE_SECONDS:
                        # Validate limit price (tightened from 50% to 5% for safety)
                        if limit_price:
                            price_diff_pct = abs(limit_price - current_price) / current_price * 100

                            if price_diff_pct > 5:  # More than 5% away from market (was 50%)
                                result['errors'].append(
                                    f"Limit price ${limit_price:.2f} is {price_diff_pct:.1f}% "
                                    f"away from market price ${current_price:.2f} - "
                                    f"possible data error or extreme volatility"
                                )
                            elif price_diff_pct > 2:  # Warning for 2-5%
                                result['warnings'].append(
                                    f"Limit price ${limit_price:.2f} is {price_diff_pct:.1f}% "
                                    f"away from market price ${current_price:.2f}"
                                )

                        # Validate stop price
                        if stop_price:
                            price_diff_pct = abs(stop_price - current_price) / current_price * 100

                            if price_diff_pct > 10:  # Stops can be further away
                                result['warnings'].append(
                                    f"Stop price ${stop_price:.2f} is {price_diff_pct:.1f}% "
                                    f"away from market price ${current_price:.2f}"
                                )

        except Exception as e:
            logger.debug(f"Could not validate prices against market: {e}")
            result['info'].append("Price validation skipped (no market data)")
    
    def _validate_sell_position(
        self, symbol: str, quantity: int,
        current_positions: Optional[Dict[str, int]],
        result: Dict[str, Any]
    ):
        """Validate that we have sufficient position to sell."""
        if current_positions is None:
            result['warnings'].append("Cannot verify position (no position data provided)")
            return
            
        position_qty = current_positions.get(symbol.upper(), 0)
        
        if position_qty <= 0:
            result['errors'].append(
                f"Cannot sell {symbol} - no position found "
                f"(current position: {position_qty})"
            )
        elif quantity > position_qty:
            result['errors'].append(
                f"Cannot sell {quantity} shares of {symbol} - "
                f"only own {position_qty} shares"
            )
            result['info'].append(f"Consider selling {position_qty} shares instead")
    
    def _validate_buying_power(
        self, symbol: str, quantity: int, limit_price: Optional[float],
        available_cash: Optional[float], account_value: Optional[float],
        result: Dict[str, Any]
    ):
        """Validate sufficient buying power for purchase."""
        if available_cash is None:
            result['warnings'].append("Cannot verify buying power (no cash data provided)")
            return
            
        # Estimate order cost
        estimated_price = limit_price if limit_price else self._get_current_price(symbol)
        
        if not estimated_price:
            result['warnings'].append("Cannot estimate order cost (no price available)")
            return
            
        order_cost = quantity * estimated_price
        
        # Check if sufficient cash
        if order_cost > available_cash:
            result['errors'].append(
                f"Insufficient buying power: Order cost ${order_cost:,.2f} "
                f"exceeds available cash ${available_cash:,.2f}"
            )
            
            # Suggest reduced quantity
            max_qty = int(available_cash / estimated_price)
            if max_qty > 0:
                result['info'].append(
                    f"Maximum affordable quantity at ${estimated_price:.2f}: "
                    f"{max_qty} shares (${max_qty * estimated_price:,.2f})"
                )
        
        # Check account value protection (if provided)
        if account_value:
            MINIMUM_ACCOUNT = 25000.0
            projected_value = account_value - order_cost
            
            if projected_value < MINIMUM_ACCOUNT:
                result['errors'].append(
                    f"Order would violate $25k minimum: "
                    f"Projected value ${projected_value:,.2f} < ${MINIMUM_ACCOUNT:,.2f}"
                )
    
    def _validate_market_hours(self, order_type: str, result: Dict[str, Any]):
        """Validate order type is appropriate for current market hours."""
        try:
            if self.data_provider and hasattr(self.data_provider, 'is_market_open'):
                is_open = self.data_provider.is_market_open()
                
                # Market orders require market to be open
                if order_type.lower() == 'market' and not is_open:
                    result['warnings'].append(
                        "Market is closed - market order will execute at next open"
                    )
                    
                # MOC orders only valid during market hours
                if order_type.lower() == 'moc' and not is_open:
                    result['errors'].append(
                        "Market on close (MOC) orders only valid during market hours"
                    )
        except Exception as e:
            logger.debug(f"Could not validate market hours: {e}")
    
    def _check_duplicate_order(
        self, symbol: str, side: str, quantity: int,
        result: Dict[str, Any], order_type: str = None
    ):
        """Check for duplicate orders in last 60 seconds."""
        import time
        current_time = time.time()
        
        # Clean up old orders
        self.recent_orders = [
            order for order in self.recent_orders
            if current_time - order['timestamp'] < 60
        ]
        
        # Check for duplicates
        for order in self.recent_orders:
            if (order['symbol'] == symbol.upper() and
                order['side'] == side.lower() and
                order['quantity'] == quantity):

                time_ago = current_time - order['timestamp']

                # Ignore very recent duplicates for SELL LIMIT orders (likely TP updates after partial fills)
                # These are legitimate bracket order updates, not true duplicates
                if side.lower() == 'sell' and order_type and order_type.lower() == 'limit' and time_ago < 2:
                    # Skip this - it's a TP order update after partial sell
                    continue

                result['errors'].append(
                    f"Duplicate order detected: Same order placed {time_ago:.1f}s ago"
                )
                break
        
        # Record this order
        self.recent_orders.append({
            'symbol': symbol.upper(),
            'side': side.lower(),
            'quantity': quantity,
            'timestamp': current_time
        })
    
    def _validate_order_size(self, quantity: int, result: Dict[str, Any]):
        """Validate order size is reasonable."""
        # Minimum quantity
        if quantity < 1:
            result['errors'].append(f"Order quantity {quantity} below minimum (1)")
            
        # Warn on very large orders (may be input error)
        if quantity > 10000:
            result['warnings'].append(
                f"Large order size: {quantity} shares - verify this is intentional"
            )
    
    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current market price for a symbol."""
        try:
            if self.data_provider:
                quote = self.data_provider.get_quote(symbol)
                if quote:
                    return quote.price if hasattr(quote, 'price') else quote.get('price')
        except Exception as e:
            logger.debug(f"Could not get current price for {symbol}: {e}")

        return None

    def _get_quote_with_timestamp(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get current market quote with timestamp for staleness checking.

        Returns:
            Dict with 'price', 'timestamp', 'age_seconds' or None
        """
        try:
            if self.data_provider:
                quote = self.data_provider.get_quote(symbol)
                if not quote:
                    return None

                # Extract price
                if hasattr(quote, 'price'):
                    price = quote.price
                elif isinstance(quote, dict):
                    price = quote.get('price')
                else:
                    return None

                # Extract timestamp
                timestamp = None
                if hasattr(quote, 'timestamp'):
                    timestamp = quote.timestamp
                elif isinstance(quote, dict) and 'timestamp' in quote:
                    timestamp = quote.get('timestamp')

                # Calculate age in seconds
                age_seconds = 0
                if timestamp:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc) if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo else datetime.now()

                    # Remove timezone info for comparison if timestamp is naive
                    if hasattr(timestamp, 'tzinfo'):
                        if timestamp.tzinfo is None:
                            # Naive timestamp - assume local time
                            age_seconds = (datetime.now() - timestamp).total_seconds()
                        else:
                            # Timezone-aware - use UTC
                            age_seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - timestamp.replace(tzinfo=None)).total_seconds()
                    else:
                        age_seconds = (now - timestamp).total_seconds()
                else:
                    # No timestamp - assume stale (force staleness check to warn)
                    age_seconds = 999

                return {
                    'price': price,
                    'timestamp': timestamp,
                    'age_seconds': abs(age_seconds)  # Use absolute value in case of clock skew
                }

        except Exception as e:
            logger.debug(f"Could not get quote with timestamp for {symbol}: {e}")

        return None


def validate_order(
    symbol: str,
    side: str,
    quantity: int,
    order_type: str,
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    broker=None,
    data_provider=None,
    current_positions: Optional[Dict[str, int]] = None,
    account_value: Optional[float] = None,
    available_cash: Optional[float] = None
) -> Dict[str, Any]:
    """
    Convenience function for order validation.
    
    Returns validation result dict with 'valid', 'errors', 'warnings', 'info' keys.
    """
    validator = OrderValidator(broker=broker, data_provider=data_provider)
    
    return validator.validate_order(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        current_positions=current_positions,
        account_value=account_value,
        available_cash=available_cash
    )