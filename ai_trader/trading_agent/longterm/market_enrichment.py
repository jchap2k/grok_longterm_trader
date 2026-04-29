"""Read-only market enrichment helpers for long-term research runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from research.research_packet import ResearchPacket


class QuoteProvider(Protocol):
    """Minimal quote provider interface for price enrichment."""

    def get_price(self, symbol: str) -> float:
        ...


@dataclass
class PriceEnrichment:
    candidate_symbol: str
    candidate_price: float
    benchmark_symbol: str
    benchmark_price: float


def enrich_prices(
    packet: ResearchPacket,
    *,
    quote_provider: QuoteProvider,
) -> PriceEnrichment:
    """Fetch candidate and benchmark prices without placing any orders."""
    candidate_symbol = packet.symbol.upper()
    benchmark_symbol = (packet.benchmark_symbol or "FXAIX").upper()
    return PriceEnrichment(
        candidate_symbol=candidate_symbol,
        candidate_price=float(quote_provider.get_price(candidate_symbol)),
        benchmark_symbol=benchmark_symbol,
        benchmark_price=float(quote_provider.get_price(benchmark_symbol)),
    )
