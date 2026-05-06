"""Read-only Alpaca paper account snapshots for the long-term trader."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from brokers.base_broker import AccountInfo
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


class ReadOnlyAccountBroker(Protocol):
    """Narrow broker protocol: account reads only, no order methods."""

    def connect(self) -> bool: ...

    def disconnect(self) -> None: ...

    def get_account_info(self) -> AccountInfo: ...


@dataclass(frozen=True)
class PaperAccountPosition:
    symbol: str
    quantity: float
    current_price: float
    market_value: float
    avg_entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_percent: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "quantity", float(self.quantity or 0.0))
        object.__setattr__(self, "current_price", float(self.current_price or 0.0))
        object.__setattr__(self, "market_value", float(self.market_value or 0.0))


@dataclass(frozen=True)
class PaperAccountSnapshot:
    mode: str
    cash: float
    portfolio_value: float
    buying_power: float
    positions: list[PaperAccountPosition]
    protected_symbols: list[str]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "cash": self.cash,
            "portfolio_value": self.portfolio_value,
            "buying_power": self.buying_power,
            "positions": [asdict(position) for position in self.positions],
            "protected_symbols": self.protected_symbols,
        }


class AlpacaPaperAccountReader:
    """Read Alpaca paper account state and normalize it for long-term planning."""

    def __init__(self, *, broker: ReadOnlyAccountBroker, paper_trading: bool = True):
        self.broker = broker
        self.paper_trading = paper_trading

    def read_snapshot(self, *, profile: PortfolioProfile | None = None) -> PaperAccountSnapshot:
        if not self.paper_trading:
            raise ValueError("Long-term Alpaca account reader only supports paper mode.")
        if not self.broker.connect():
            raise RuntimeError("Could not connect to Alpaca paper account.")
        try:
            account = self.broker.get_account_info()
        finally:
            self.broker.disconnect()
        protected_symbols = list(profile.protected_symbols if profile else [])
        return PaperAccountSnapshot(
            mode="paper",
            cash=float(account.cash or 0.0),
            portfolio_value=float(account.portfolio_value or 0.0),
            buying_power=float(account.buying_power or 0.0),
            positions=[
                PaperAccountPosition(
                    symbol=position.symbol,
                    quantity=float(position.quantity or 0.0),
                    current_price=float(position.current_price or 0.0),
                    market_value=round(
                        float(position.quantity or 0.0) * float(position.current_price or 0.0),
                        2,
                    ),
                    avg_entry_price=float(position.avg_entry_price or 0.0),
                    unrealized_pnl=float(position.unrealized_pnl or 0.0),
                    unrealized_pnl_percent=float(position.unrealized_pnl_percent or 0.0),
                )
                for position in account.positions
            ],
            protected_symbols=[symbol.upper() for symbol in protected_symbols],
        )


def paper_account_snapshot_to_portfolio_state(snapshot: PaperAccountSnapshot) -> PortfolioState:
    """Convert a paper snapshot into the existing read-only PortfolioState contract."""
    return PortfolioState(
        cash=snapshot.cash,
        protected_symbols=snapshot.protected_symbols,
        holdings=[
            {
                "symbol": position.symbol,
                "market_value": position.market_value,
                "quantity": position.quantity,
                "current_price": position.current_price,
                "avg_entry_price": position.avg_entry_price,
                "original_purchase_total_cost": round(position.quantity * position.avg_entry_price, 2),
                "unrealized_pnl": position.unrealized_pnl,
                "unrealized_pnl_percent": position.unrealized_pnl_percent,
            }
            for position in snapshot.positions
        ],
    )
