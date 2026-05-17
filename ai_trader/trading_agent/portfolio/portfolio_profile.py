"""Portfolio profile models for long-term trading."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
    low_risk_parking_symbol: str = "SGOV"
    duration_hedge_symbol: str = "TLT"
    cash_symbol: str = "CASH"
    enable_category_risk_sizing: bool = False

    def __post_init__(self) -> None:
        self.account_strategy_mode = self.account_strategy_mode or ""
        self.total_account_value = float(self.total_account_value or 0.0)
        self.tradable_capital = float(self.tradable_capital or 0.0)
        self.protected_symbols = [
            str(symbol).upper() for symbol in (self.protected_symbols or [])
        ]
        self.benchmark_symbol = (self.benchmark_symbol or "").upper()
        self.defensive_parking_symbol = (self.defensive_parking_symbol or "").upper()
        self.low_risk_parking_symbol = (self.low_risk_parking_symbol or "SGOV").upper()
        self.duration_hedge_symbol = (self.duration_hedge_symbol or "TLT").upper()
        self.cash_symbol = (self.cash_symbol or "CASH").upper()
        self.enable_category_risk_sizing = bool(self.enable_category_risk_sizing)

    @property
    def protected_capital(self) -> float:
        return max(0.0, self.total_account_value - self.tradable_capital)

    @property
    def is_non_taxable(self) -> bool:
        mode = self.account_strategy_mode.lower().replace("-", "_").strip()
        return mode in {
            "roth_ira",
            "traditional_ira",
            "ira",
            "paper",
            "paper_non_taxable",
            "non_taxable",
        }

    @property
    def approved_parking_symbols(self) -> List[str]:
        blocked = set(self.protected_symbols)
        blocked.update(symbol for symbol in [self.benchmark_symbol, self.cash_symbol] if symbol)
        symbols: list[str] = []
        for symbol in [
            self.defensive_parking_symbol,
            self.low_risk_parking_symbol,
            self.duration_hedge_symbol,
        ]:
            normalized = str(symbol or "").upper()
            if normalized and normalized not in blocked and normalized not in symbols:
                symbols.append(normalized)
        return symbols

    def is_approved_parking_symbol(self, symbol: str) -> bool:
        return str(symbol or "").upper() in set(self.approved_parking_symbols)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["protected_capital"] = self.protected_capital
        payload["is_non_taxable"] = self.is_non_taxable
        payload["approved_parking_symbols"] = self.approved_parking_symbols
        return payload

    @classmethod
    def from_file(cls, path: str | Path) -> "PortfolioProfile":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**payload)
