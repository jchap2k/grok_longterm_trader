"""
Base Broker Interface - Abstract class for broker implementations

All broker connectors must implement this interface to ensure
consistent behavior across different brokers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass
from datetime import datetime


class OrderSide(Enum):
    """Order side enum."""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type enum."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    MOC = "moc"  # Market on Close


class OrderStatus(Enum):
    """Order status enum."""
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Position:
    """Represents a stock position."""
    symbol: str
    quantity: int
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_percent: float


@dataclass
class Order:
    """Represents a trading order."""
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    status: OrderStatus
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    filled_price: Optional[float] = None
    filled_quantity: int = 0
    created_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None


@dataclass
class Quote:
    """Represents a stock quote."""
    symbol: str
    price: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    timestamp: datetime
    source: str
    prev_close: float = 0.0  # Previous day's closing price


@dataclass
class AccountInfo:
    """Represents account information."""
    cash: float
    portfolio_value: float
    buying_power: float
    positions: List[Position]


class BaseBroker(ABC):
    """
    Abstract base class for broker implementations.

    All broker connectors (Alpaca, IBKR, etc.) must inherit from this
    class and implement all abstract methods.
    """

    def __init__(self, api_key: str = None, secret_key: str = None, paper_trading: bool = True):
        """
        Initialize broker connection.

        Args:
            api_key: API key for broker
            secret_key: API secret for broker
            paper_trading: Whether to use paper trading mode
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper_trading = paper_trading
        self.connected = False

    @abstractmethod
    def connect(self) -> bool:
        """
        Connect to broker API.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    def disconnect(self):
        """Disconnect from broker API."""
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """
        Get current account information.

        Returns:
            AccountInfo object with cash, positions, etc.
        """
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """
        Get current quote for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Quote object with current prices
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """
        Get all current positions.

        Returns:
            List of Position objects
        """
        pass

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Order:
        """
        Place a trading order.

        Args:
            symbol: Stock ticker symbol
            side: Buy or sell
            quantity: Number of shares
            order_type: Market, limit, stop, etc.
            limit_price: Limit price (for limit orders)
            stop_price: Stop price (for stop orders)

        Returns:
            Order object with order details
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an existing order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        pass

    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.

        Returns:
            Number of orders cancelled
        """
        # Default implementation - subclasses can override for efficiency
        orders = self.get_open_orders()
        count = 0
        for order in orders:
            if self.cancel_order(order.order_id):
                count += 1
        return count

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        """
        Get status of an order.

        Args:
            order_id: Order ID to check

        Returns:
            Order object with current status
        """
        pass

    @abstractmethod
    def get_historical_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "1D"
    ) -> List[Dict[str, Any]]:
        """
        Get historical price data.

        Args:
            symbol: Stock ticker symbol
            start_date: Start date
            end_date: End date
            timeframe: Timeframe (1D, 1H, 5Min, etc.)

        Returns:
            List of OHLCV bars
        """
        pass

    @abstractmethod
    def is_market_open(self) -> bool:
        """
        Check if market is currently open.

        Returns:
            True if market is open
        """
        pass

    def get_open_orders(self) -> List[Order]:
        """
        Get all open (pending) orders.

        Returns:
            List of open Order objects
        """
        # Default implementation - subclasses should override
        return []

    def validate_order(self, symbol: str, quantity: int, price: float) -> Dict[str, Any]:
        """
        Validate an order before placing.

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            price: Order price

        Returns:
            Validation result with errors if any
        """
        errors = []
        account = self.get_account_info()

        # Calculate order cost
        cost = quantity * price

        # CRITICAL: Use CASH for validation, NOT buying_power (no margin allowed!)
        # This is a CASH ACCOUNT - we can only spend actual cash, not margin
        available_cash = account.cash
        if cost > available_cash:
            errors.append(f"Insufficient cash. Need ${cost:.2f}, have ${available_cash:.2f} (CASH ACCOUNT - no margin)")

        # Check quantity
        if quantity <= 0:
            errors.append("Quantity must be positive")

        # Check price
        if price <= 0:
            errors.append("Price must be positive")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "cost": cost,
            "available": available_cash  # Report cash, not buying_power
        }

    def __repr__(self):
        mode = "PAPER" if self.paper_trading else "LIVE"
        status = "CONNECTED" if self.connected else "DISCONNECTED"
        return f"<{self.__class__.__name__} mode={mode} status={status}>"
