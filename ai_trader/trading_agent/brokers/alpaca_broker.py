"""
Alpaca Broker Implementation

Connects to Alpaca Markets API for live and paper trading.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import os
import logging

logger = logging.getLogger(__name__)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest, StopOrderRequest, TrailingStopOrderRequest
    )
    from alpaca.trading.enums import OrderSide as AlpacaOrderSide, OrderType as AlpacaOrderType, TimeInForce
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False

# News client is in alpaca.data directly, not alpaca.data.news
try:
    from alpaca.data import NewsClient, NewsRequest
    ALPACA_NEWS_AVAILABLE = True
except ImportError:
    ALPACA_NEWS_AVAILABLE = False

from .base_broker import (
    BaseBroker, Order, Position, Quote, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from .order_validator import OrderValidator


class AlpacaBroker(BaseBroker):
    """
    Alpaca broker implementation.

    Supports both paper and live trading via Alpaca Markets API.
    Free tier available with paper trading.
    """

    def __init__(self, api_key: str = None, secret_key: str = None, paper_trading: bool = True):
        """
        Initialize Alpaca broker.

        Args:
            api_key: Alpaca API key (or set ALPACA_API_KEY env var)
            secret_key: Alpaca secret key (or set ALPACA_SECRET_KEY env var)
            paper_trading: Use paper trading (default True)
        """
        if not ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py package not installed. Install with: pip install alpaca-py"
            )

        super().__init__(api_key, secret_key, paper_trading)

        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")

        if not self.api_key or not self.secret_key:
            raise ValueError("Alpaca API credentials not found")

        self.trading_client: Optional[TradingClient] = None
        self.data_client: Optional[StockHistoricalDataClient] = None
        self.news_client: Optional[NewsClient] = None
        self.order_validator: Optional[OrderValidator] = None

    def connect(self) -> bool:
        """Connect to Alpaca API."""
        try:
            self.trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper_trading
            )

            self.data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key
            )

            # Initialize news client if available
            if ALPACA_NEWS_AVAILABLE:
                self.news_client = NewsClient(
                    api_key=self.api_key,
                    secret_key=self.secret_key
                )

            # Test connection
            self.trading_client.get_account()

            # Initialize order validator
            self.order_validator = OrderValidator(broker=self, data_provider=self)

            self.connected = True
            print(f"[OK] Connected to Alpaca ({'PAPER' if self.paper_trading else 'LIVE'} mode)")
            return True

        except Exception as e:
            print(f"[X] Failed to connect to Alpaca: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from Alpaca."""
        self.trading_client = None
        self.data_client = None
        self.news_client = None
        self.connected = False
        print("Disconnected from Alpaca")

    def get_account_info(self) -> AccountInfo:
        """Get account information."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        account = self.trading_client.get_account()
        positions = self.get_positions()

        return AccountInfo(
            cash=float(account.cash),
            portfolio_value=float(account.portfolio_value),
            buying_power=float(account.buying_power),
            positions=positions
        )

    def get_quote(self, symbol: str) -> Quote:
        """Get current quote for a symbol."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote_data = quotes[symbol]

        return Quote(
            symbol=symbol,
            price=(quote_data.bid_price + quote_data.ask_price) / 2,
            bid=float(quote_data.bid_price),
            ask=float(quote_data.ask_price),
            bid_size=int(quote_data.bid_size),
            ask_size=int(quote_data.ask_size),
            timestamp=quote_data.timestamp,
            source="Alpaca"
        )

    def get_positions(self) -> List[Position]:
        """Get all positions."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        alpaca_positions = self.trading_client.get_all_positions()
        positions = []

        for pos in alpaca_positions:
            positions.append(Position(
                symbol=pos.symbol,
                quantity=int(pos.qty),
                avg_entry_price=float(pos.avg_entry_price),
                current_price=float(pos.current_price),
                unrealized_pnl=float(pos.unrealized_pl),
                unrealized_pnl_percent=float(pos.unrealized_plpc) * 100
            ))

        return positions

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Order:
        """Place a trading order."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # PRE-ORDER VALIDATION
        if self.order_validator:
            try:
                # Get account info for validation
                account = self.trading_client.get_account()
                positions = self.get_positions()
                
                # Build current positions dict
                current_positions = {pos.symbol: pos.quantity for pos in positions}
                
                # Convert order type to string for validator
                order_type_str = order_type.value if hasattr(order_type, 'value') else str(order_type).split('.')[-1].lower()
                
                # Run validation
                validation_result = self.order_validator.validate_order(
                    symbol=symbol,
                    side=side.value if hasattr(side, 'value') else ('buy' if side == OrderSide.BUY else 'sell'),
                    quantity=quantity,
                    order_type=order_type_str,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    current_positions=current_positions,
                    account_value=float(account.portfolio_value),
                    available_cash=float(account.cash)
                )
                
                # Log warnings
                for warning in validation_result.get('warnings', []):
                    logger.warning(f"Order validation warning: {warning}")
                
                # Block order if validation failed
                if not validation_result['valid']:
                    error_msg = f"Order validation failed: {', '.join(validation_result['errors'])}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                    
                logger.info(f"Order validation PASSED for {side.value if hasattr(side, 'value') else side} {quantity} {symbol}")
                
            except Exception as e:
                # If validation itself fails, log but don't block (fail-open for safety)
                logger.error(f"Order validation error (allowing order): {e}", exc_info=True)

        # Convert OrderSide
        alpaca_side = AlpacaOrderSide.BUY if side == OrderSide.BUY else AlpacaOrderSide.SELL

        # Create appropriate order request
        if order_type == OrderType.MARKET or order_type == OrderType.MOC:
            time_in_force = TimeInForce.CLS if order_type == OrderType.MOC else TimeInForce.DAY
            order_data = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                time_in_force=time_in_force
            )
        elif order_type == OrderType.LIMIT:
            if limit_price is None:
                raise ValueError("Limit price required for limit orders")
            order_data = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY
            )
        elif order_type == OrderType.STOP:
            if stop_price is None:
                raise ValueError("Stop price required for stop orders")
            order_data = StopOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                stop_price=stop_price,
                time_in_force=TimeInForce.DAY
            )
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        # Submit order
        alpaca_order = self.trading_client.submit_order(order_data)

        # Convert to our Order format
        return self._convert_alpaca_order(alpaca_order)

    def place_bracket_order(
        self,
        symbol: str,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        quantity: int
    ) -> Order:
        """
        Place a bracket order with entry, take profit, and stop loss.

        BRACKET order = Entry + TP + SL submitted together.
        When entry fills, both TP and SL become active and are OCO-linked.
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Validate price relationships for long bracket order
        if take_profit_price <= entry_price:
            raise ValueError(f"Take profit ({take_profit_price}) must be above entry price ({entry_price})")
        if stop_loss_price >= entry_price:
            raise ValueError(f"Stop loss ({stop_loss_price}) must be below entry price ({entry_price})")
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")

        try:
            from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
            from alpaca.trading.enums import OrderSide, OrderClass

            # Create bracket order per Alpaca docs:
            # - Use LimitOrderRequest for entry with price protection (NOT MarketOrderRequest)
            # - Set order_class=OrderClass.BRACKET
            # - Attach TakeProfitRequest and StopLossRequest
            # IMPORTANT: LimitOrderRequest prevents bad fills from stale/incorrect quote data
            bracket_request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.BUY,
                limit_price=entry_price,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=take_profit_price),
                stop_loss=StopLossRequest(stop_price=stop_loss_price)
            )

            # Submit bracket order
            alpaca_order = self.trading_client.submit_order(bracket_request)

            logger.info(f"[OK] Placed BRACKET order for {symbol}: Entry@${entry_price:.2f}, TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f}")
            return self._convert_alpaca_order(alpaca_order)

        except Exception as e:
            logger.error(f"Bracket order failed for {symbol}: {e}")
            raise

    def place_oco_order(
        self,
        symbol: str,
        quantity: int,
        take_profit_price: float,
        stop_loss_price: float
    ) -> Order:
        """
        Place an OCO (One Cancels Other) order for an existing position.

        OCO order = Two linked exit orders for an existing position.
        - Primary order: Limit sell at take_profit_price (the TP)
        - OCO leg: Stop sell at stop_loss_price (the SL)
        When one fills, the other is automatically canceled.

        Args:
            symbol: Stock symbol
            quantity: Number of shares to sell
            take_profit_price: Limit price for take profit
            stop_loss_price: Stop price for stop loss

        Returns:
            Order object for the OCO parent order
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        try:
            from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
            from alpaca.trading.enums import OrderSide, OrderClass

            # OCO order per Alpaca docs:
            # - order_class=OCO requires BOTH take_profit.limit_price AND stop_loss.stop_price
            # - The primary order limit_price is also used as take_profit.limit_price
            oco_request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.SELL,
                limit_price=take_profit_price,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=take_profit_price),
                stop_loss=StopLossRequest(stop_price=stop_loss_price)
            )

            # Submit OCO order
            alpaca_order = self.trading_client.submit_order(oco_request)

            print(f"[OK] Placed OCO order for {symbol}: TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f}")
            return self._convert_alpaca_order(alpaca_order)

        except Exception as e:
            print(f"OCO order failed for {symbol}: {e}")
            raise

    def place_trailing_stop(
        self,
        symbol: str,
        quantity: int,
        trail_percent: float,
        time_in_force: str = "day"
    ) -> Order:
        """
        Place a trailing stop order.

        Args:
            symbol: Stock symbol
            quantity: Number of shares to sell
            trail_percent: Percentage to trail (e.g., 1.0 for 1%)
            time_in_force: "day" or "gtc" (default: "day")

        Returns:
            Order object

        Example:
            # Sell 100 shares with 1% trailing stop
            order = broker.place_trailing_stop("AAPL", 100, 1.0)
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Convert time_in_force to Alpaca enum
        tif = TimeInForce.GTC if time_in_force.lower() == "gtc" else TimeInForce.DAY

        # Create trailing stop order request
        order_data = TrailingStopOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=AlpacaOrderSide.SELL,
            trail_percent=trail_percent,
            time_in_force=tif
        )

        try:
            alpaca_order = self.trading_client.submit_order(order_data)
            order = self._convert_alpaca_order(alpaca_order)
            logger.info(f"Trailing stop placed: {symbol} {quantity} shares @ {trail_percent}% trail")
            return order
        except Exception as e:
            logger.error(f"Failed to place trailing stop for {symbol}: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        try:
            self.trading_client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            print(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns count of orders canceled."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        try:
            # Alpaca has a built-in cancel_all method
            canceled = self.trading_client.cancel_orders()
            count = len(canceled) if canceled else 0
            print(f"Canceled {count} open orders")
            return count
        except Exception as e:
            print(f"Failed to cancel all orders: {e}")
            # Fallback: cancel one by one
            try:
                orders = self.get_open_orders()
                count = 0
                for order in orders:
                    if self.cancel_order(order.order_id):
                        count += 1
                return count
            except Exception as e2:
                print(f"Fallback cancel also failed: {e2}")
                return 0

    def get_order_status(self, order_id: str) -> Order:
        """Get order status."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        alpaca_order = self.trading_client.get_order_by_id(order_id)
        return self._convert_alpaca_order(alpaca_order)

    def get_open_orders(self) -> Optional[List[Order]]:
        """Get all open (pending) orders."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Alpaca API get_orders() doesn't take a status parameter
        # It returns all orders by default, we need to filter for open ones
        # Add timeout and limit to prevent hanging
        try:
            # Use a more targeted approach - get recent orders only
            # This prevents loading thousands of historical orders
            from datetime import datetime, timedelta
            import threading
            import concurrent.futures

            # Wrap API call in timeout to prevent hanging
            def get_orders_with_timeout():
                # Alpaca API get_orders() doesn't support 'after' or 'limit' parameters
                all_orders = self.trading_client.get_orders()  # Get all orders (no pagination in Alpaca)
                # Filter to last 7 days manually to avoid processing too many old orders
                # Handle timezone-aware vs timezone-naive datetime comparison
                from datetime import timezone
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)  # Make timezone-aware
                recent_orders = [
                    order for order in all_orders
                    if order.created_at and order.created_at > cutoff_date
                ]
                return recent_orders

            # Use ThreadPoolExecutor for Windows-compatible timeout
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(get_orders_with_timeout)
                try:
                    alpaca_orders = future.result(timeout=30.0)  # 30-second timeout
                except concurrent.futures.TimeoutError:
                    logger.warning("get_open_orders() timed out after 30 seconds - returning None (callers must guard with 'is not None')")
                    return None

            # Filter for open orders (not filled, cancelled, etc.)
            # Be more permissive to catch all possible open order statuses
            open_statuses = ['new', 'pending_new', 'accepted', 'pending_cancel', 'stopped', 'suspended', 'calculated', 'partially_filled', 'held']
            open_orders = [order for order in alpaca_orders
                          if order.status in open_statuses]

            print(f"DEBUG: Alpaca returned {len(alpaca_orders)} total orders, filtered to {len(open_orders)} open orders")
            for order in alpaca_orders[:10]:  # Log first 10 orders for debugging
                print(f"DEBUG: Order {order.id}: status={order.status}, symbol={order.symbol}, side={order.side}, qty={order.qty}")

            return [self._convert_alpaca_order(order) for order in open_orders]

        except Exception as e:
            print(f"Failed to get open orders from Alpaca: {e}")
            print("WARNING: Returning empty list - bracket order scanning may be limited")
            return []

    def get_filled_sell_orders(self, since_days: int = 7) -> List[Dict[str, Any]]:
        """
        Get filled sell orders from Alpaca for the past N days.

        Returns list of dicts for use by FillReconciler:
            {symbol, filled_at, filled_avg_price, qty, order_id}
        """
        if not self.connected:
            return []

        try:
            import concurrent.futures
            from datetime import timezone

            cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

            def fetch_orders():
                # Try GetOrdersRequest first (alpaca-py >= 0.8)
                try:
                    from alpaca.trading.requests import GetOrdersRequest
                    from alpaca.trading.enums import QueryOrderStatus
                    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=cutoff, limit=200)
                    return self.trading_client.get_orders(req)
                except (ImportError, TypeError, Exception):
                    # Fallback: get all and filter client-side (same pattern as get_open_orders)
                    all_orders = self.trading_client.get_orders()
                    return [o for o in all_orders if o.created_at and o.created_at >= cutoff]

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(fetch_orders)
                try:
                    orders = future.result(timeout=30.0)
                except concurrent.futures.TimeoutError:
                    logger.warning("get_filled_sell_orders: Alpaca API timed out after 30s")
                    return []

            result = []
            for o in orders:
                status_str = str(o.status).lower()
                side_str = str(o.side).lower()
                is_filled = 'filled' in status_str and 'partially' not in status_str
                is_sell = 'sell' in side_str
                if is_filled and is_sell and o.filled_at:
                    result.append({
                        'symbol': o.symbol,
                        'filled_at': o.filled_at,
                        'filled_avg_price': float(o.filled_avg_price or 0),
                        'qty': float(o.filled_qty or o.qty or 0),
                        'order_id': str(o.id),
                    })

            logger.info(f"get_filled_sell_orders: {len(result)} filled sell(s) in last {since_days}d")
            return result

        except Exception as e:
            logger.error(f"get_filled_sell_orders failed: {e}")
            return []

    def get_historical_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "1D"
    ) -> List[Dict[str, Any]]:
        """Get historical bars."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Convert timeframe
        tf_map = {
            "1Min": TimeFrame.Minute,
            "5Min": TimeFrame(5, "Min"),
            "15Min": TimeFrame(15, "Min"),
            "1H": TimeFrame.Hour,
            "1D": TimeFrame.Day
        }
        alpaca_tf = tf_map.get(timeframe, TimeFrame.Day)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=alpaca_tf,
            start=start_date,
            end=end_date
        )

        bars = self.data_client.get_stock_bars(request)
        result = []

        for bar in bars[symbol]:
            result.append({
                "timestamp": bar.timestamp,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume)
            })

        return result

    def is_market_open(self) -> bool:
        """Check if market is open."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        clock = self.trading_client.get_clock()
        return clock.is_open

    def get_news(
        self,
        symbols: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        hours_back: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent news for symbols or by search query.

        Args:
            symbols: List of stock symbols to get news for (e.g., ["AAPL", "MSFT"])
            query: Search query for general market news (e.g., "tech sector", "earnings")
            limit: Maximum number of news articles to return
            hours_back: How many hours back to search (default 24)

        Returns:
            List of news articles with title, summary, source, timestamp, symbols, and url
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        if not self.news_client:
            return [{
                "error": "News client not available",
                "message": "Alpaca news API not initialized"
            }]

        try:
            # Calculate time range
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours_back)

            # Build news request
            request_params = {
                "start": start_time,
                "end": end_time,
                "limit": limit,
                "sort": "desc"  # Most recent first
            }

            # Add symbols filter if provided (SDK expects comma-separated string)
            if symbols:
                if isinstance(symbols, list):
                    request_params["symbols"] = ",".join(symbols)
                else:
                    request_params["symbols"] = symbols

            request = NewsRequest(**request_params)
            news_response = self.news_client.get_news(request)

            # Convert to list of dicts
            # Response structure: news_response.data['news'] contains list of articles
            articles = []
            raw_articles = news_response.data.get('news', []) if hasattr(news_response, 'data') else []
            for article in raw_articles:
                # Check if article matches query (if query provided)
                if query:
                    query_lower = query.lower()
                    title_lower = article.headline.lower() if article.headline else ""
                    summary_lower = article.summary.lower() if article.summary else ""

                    # Skip if query not found in title or summary
                    if query_lower not in title_lower and query_lower not in summary_lower:
                        continue

                articles.append({
                    "title": article.headline,
                    "summary": article.summary[:500] if article.summary else None,  # Truncate long summaries
                    "source": article.source,
                    "timestamp": article.created_at.isoformat() if article.created_at else None,
                    "symbols": list(article.symbols) if article.symbols else [],
                    "url": article.url,
                    "images": [img.url for img in article.images] if article.images else []
                })

            return articles

        except Exception as e:
            return [{
                "error": f"Failed to fetch news: {str(e)}",
                "symbols": symbols,
                "query": query
            }]

    def _convert_alpaca_order(self, alpaca_order) -> Order:
        """Convert Alpaca order to our Order format."""
        # Map order status
        status_map = {
            "new": OrderStatus.PENDING,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "done_for_day": OrderStatus.CANCELLED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "pending_new": OrderStatus.PENDING,
            "accepted": OrderStatus.PENDING,
            "pending_cancel": OrderStatus.PENDING,
            "stopped": OrderStatus.CANCELLED,
            "suspended": OrderStatus.CANCELLED,
            "calculated": OrderStatus.PENDING
        }

        # Map order side
        side = OrderSide.BUY if alpaca_order.side == AlpacaOrderSide.BUY else OrderSide.SELL

        # Map order type
        type_map = {
            "market": OrderType.MARKET,
            "limit": OrderType.LIMIT,
            "stop": OrderType.STOP,
            "stop_limit": OrderType.STOP_LIMIT,
            "trailing_stop": OrderType.TRAILING_STOP
        }
        order_type = type_map.get(alpaca_order.type, OrderType.MARKET)

        return Order(
            order_id=str(alpaca_order.id),
            symbol=alpaca_order.symbol,
            side=side,
            quantity=int(alpaca_order.qty),
            order_type=order_type,
            status=status_map.get(alpaca_order.status, OrderStatus.PENDING),
            limit_price=float(alpaca_order.limit_price) if alpaca_order.limit_price else None,
            stop_price=float(alpaca_order.stop_price) if alpaca_order.stop_price else None,
            filled_price=float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None,
            filled_quantity=int(alpaca_order.filled_qty) if alpaca_order.filled_qty else 0,
            created_at=alpaca_order.created_at,
            filled_at=alpaca_order.filled_at
        )


# Example usage
if __name__ == "__main__":
    # Create broker instance (paper trading)
    broker = AlpacaBroker(paper_trading=True)

    # Connect
    if broker.connect():
        # Get account info
        account = broker.get_account_info()
        print(f"\nAccount Value: ${account.portfolio_value:.2f}")
        print(f"Cash: ${account.cash:.2f}")
        print(f"Buying Power: ${account.buying_power:.2f}")

        # Get a quote
        quote = broker.get_quote("AAPL")
        print(f"\nAAPL Quote:")
        print(f"  Bid: ${quote.bid:.2f} x {quote.bid_size}")
        print(f"  Ask: ${quote.ask:.2f} x {quote.ask_size}")

        broker.disconnect()
