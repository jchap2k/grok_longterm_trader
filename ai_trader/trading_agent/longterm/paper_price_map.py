"""Build explicit price maps for whole-share paper previews."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol


class QuoteProvider(Protocol):
    def get_quote(self, symbol: str) -> Any: ...


@dataclass(frozen=True)
class PriceMapResult:
    mode: str
    symbols_requested: list[str]
    price_map: dict[str, float]
    missing_symbols: list[str]
    errors: dict[str, str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_price_map_from_action_plan(
    action_plan: Mapping[str, Any],
    *,
    quote_provider: QuoteProvider,
    protected_symbols: set[str] | None = None,
) -> PriceMapResult:
    """Fetch explicit current prices for orderable symbols in an action plan."""
    protected = {symbol.upper() for symbol in (protected_symbols or set())}
    symbols = _symbols_for_price_map(action_plan, protected_symbols=protected)
    prices: dict[str, float] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            price = _quote_price(quote_provider.get_quote(symbol))
            if price <= 0:
                errors[symbol] = "quote_price_not_positive"
                continue
            prices[symbol] = round(price, 4)
        except Exception as exc:
            errors[symbol] = str(exc)
    return PriceMapResult(
        mode="paper_price_map",
        symbols_requested=symbols,
        price_map=prices,
        missing_symbols=[symbol for symbol in symbols if symbol not in prices],
        errors=errors,
        notes=[
            "Read-only quote-to-price-map helper. No broker orders were submitted.",
            "Use this output as --price-map for whole-share paper previews.",
        ],
    )


def build_price_map_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Preview Price Map",
        "",
        "Read-only quote helper. No orders were submitted.",
        "",
        f"- Symbols requested: {len(result.get('symbols_requested') or [])}",
        f"- Prices found: {len(result.get('price_map') or {})}",
        f"- Missing symbols: {', '.join(result.get('missing_symbols') or []) or 'none'}",
        "",
        "| Symbol | Price |",
        "| --- | ---: |",
    ]
    for symbol, price in sorted((result.get("price_map") or {}).items()):
        lines.append(f"| {symbol} | ${float(price):,.2f} |")
    return "\n".join(lines) + "\n"


def _symbols_for_price_map(
    action_plan: Mapping[str, Any],
    *,
    protected_symbols: set[str],
) -> list[str]:
    symbols: list[str] = []
    for intent in action_plan.get("intents") or []:
        intent_type = str(intent.get("intent_type") or "").upper()
        if intent_type not in {"BUY", "REBALANCE"}:
            continue
        for key in ("symbol", "source_symbol"):
            symbol = str(intent.get(key) or "").upper().strip()
            if symbol and symbol not in protected_symbols and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _quote_price(quote: Any) -> float:
    if isinstance(quote, Mapping):
        raw = quote.get("price") or quote.get("current_price") or quote.get("last") or quote.get("close")
    else:
        raw = getattr(quote, "price", None) or getattr(quote, "current_price", None)
    return float(raw or 0.0)


__all__ = ["PriceMapResult", "build_price_map_from_action_plan", "build_price_map_markdown"]
