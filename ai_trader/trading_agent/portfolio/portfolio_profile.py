"""Portfolio profile models for long-term trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class PortfolioProfile:
    """Account-aware portfolio controls for the long-term trader."""

    account_strategy_mode: str = ""
    total_account_value: float = 0.0
    tradable_capital: float = 0.0
    protected_symbols: List[str] = field(default_factory=list)
    benchmark_symbol: str = ""
    defensive_parking_symbol: str = ""
    cash_symbol: str = "CASH"

    def __post_init__(self) -> None:
        self.account_strategy_mode = self.account_strategy_mode or ""
        self.total_account_value = float(self.total_account_value or 0.0)
        self.tradable_capital = float(self.tradable_capital or 0.0)
        self.protected_symbols = [
            str(symbol).upper() for symbol in (self.protected_symbols or [])
        ]
        self.benchmark_symbol = (self.benchmark_symbol or "").upper()
        self.defensive_parking_symbol = (self.defensive_parking_symbol or "").upper()
        self.cash_symbol = (self.cash_symbol or "CASH").upper()

    @property
    def protected_capital(self) -> float:
        return max(0.0, self.total_account_value - self.tradable_capital)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["protected_capital"] = self.protected_capital
        return payload
