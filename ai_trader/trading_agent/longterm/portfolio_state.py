"""Read-only portfolio state for long-term action planning."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from portfolio.portfolio_profile import PortfolioProfile


@dataclass(frozen=True)
class Holding:
    symbol: str
    market_value: float = 0.0
    quantity: float = 0.0
    current_price: float = 0.0
    avg_entry_price: float = 0.0
    original_purchase_total_cost: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_percent: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "market_value", float(self.market_value or 0.0))
        object.__setattr__(self, "quantity", float(self.quantity or 0.0))
        object.__setattr__(self, "current_price", float(self.current_price or 0.0))
        object.__setattr__(self, "avg_entry_price", float(self.avg_entry_price or 0.0))
        object.__setattr__(
            self,
            "original_purchase_total_cost",
            float(self.original_purchase_total_cost or 0.0),
        )
        object.__setattr__(self, "unrealized_pnl", float(self.unrealized_pnl or 0.0))
        object.__setattr__(self, "unrealized_pnl_percent", float(self.unrealized_pnl_percent or 0.0))


@dataclass
class PortfolioState:
    cash: float = 0.0
    holdings: list[Holding | Mapping[str, Any]] = field(default_factory=list)
    protected_symbols: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.cash or 0.0)
        self.holdings = [
            holding if isinstance(holding, Holding) else Holding(**dict(holding))
            for holding in (self.holdings or [])
        ]
        self.protected_symbols = [symbol.upper() for symbol in (self.protected_symbols or [])]

    @property
    def active_market_value(self) -> float:
        return sum(
            holding.market_value
            for holding in self.holdings
            if holding.symbol not in self.protected_symbols
        )

    @property
    def protected_market_value(self) -> float:
        return sum(
            holding.market_value
            for holding in self.holdings
            if holding.symbol in self.protected_symbols
        )

    def holding_value(self, symbol: str) -> float:
        normalized = symbol.upper()
        return sum(
            holding.market_value
            for holding in self.holdings
            if holding.symbol == normalized
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        profile: PortfolioProfile | None = None,
    ) -> "PortfolioState":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Portfolio state file must contain a JSON object.")
        if profile is not None:
            payload.setdefault("protected_symbols", profile.protected_symbols)
        return cls(**payload)
