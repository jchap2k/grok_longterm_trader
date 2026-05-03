"""Build market-regime snapshots for idle-cash parking decisions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from longterm.idle_cash_policy import MarketRegimeSnapshot


HistoryFetcher = Callable[[str, str], list[Mapping[str, Any]]]

DEFAULT_VIX_SYMBOL = "^VIX"
DEFAULT_SPY_SYMBOL = "SPY"
DEFAULT_TEN_YEAR_YIELD_SYMBOL = "^TNX"


def build_market_regime_snapshot(
    *,
    fetch_history: HistoryFetcher,
    vix_symbol: str = DEFAULT_VIX_SYMBOL,
    spy_symbol: str = DEFAULT_SPY_SYMBOL,
    ten_year_yield_symbol: str = DEFAULT_TEN_YEAR_YIELD_SYMBOL,
    period: str = "1y",
) -> MarketRegimeSnapshot:
    """Fetch market histories and classify the current broad-market regime."""
    return build_market_regime_snapshot_from_histories(
        vix_history=fetch_history(vix_symbol, period),
        spy_history=fetch_history(spy_symbol, period),
        ten_year_yield_history=fetch_history(ten_year_yield_symbol, period),
    )


def build_market_regime_snapshot_from_histories(
    *,
    vix_history: Iterable[Mapping[str, Any]],
    spy_history: Iterable[Mapping[str, Any]],
    ten_year_yield_history: Iterable[Mapping[str, Any]],
) -> MarketRegimeSnapshot:
    """Classify market regime from VIX, SPY trend, and 10Y yield trend histories."""
    vix_rows = list(vix_history or [])
    spy_rows = list(spy_history or [])
    yield_rows = list(ten_year_yield_history or [])
    vix_level = _last_close(vix_rows)
    spy_closes = _closes(spy_rows)
    spy_above_200d = spy_closes[-1] > _simple_average(spy_closes[-200:]) if len(spy_closes) >= 200 else None
    yield_trend = _yield_trend(yield_rows)
    base = MarketRegimeSnapshot.from_signals(
        vix_level=vix_level,
        spy_above_200d=spy_above_200d,
        ten_year_yield_trend=yield_trend,
    )
    return MarketRegimeSnapshot(
        risk_regime=base.risk_regime,
        vix_level=vix_level,
        spy_above_200d=spy_above_200d,
        ten_year_yield_trend=yield_trend,
        reason=_reason(base, vix_level=vix_level, spy_above_200d=spy_above_200d, yield_trend=yield_trend),
    )


def fetch_yfinance_history(symbol: str, period: str = "1y") -> list[dict[str, Any]]:
    """Fetch daily close history from yfinance for a symbol."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install yfinance to build live market-regime snapshots.") from exc

    history = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    rows: list[dict[str, Any]] = []
    if history is None or history.empty:
        return rows
    for index, row in history.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        rows.append({"date": str(index)[:10], "close": float(close)})
    return rows


def market_regime_to_dict(snapshot: MarketRegimeSnapshot) -> dict[str, Any]:
    """Serialize a snapshot to the JSON format consumed by run_longterm_cycle."""
    return {
        "risk_regime": snapshot.risk_regime,
        "vix_level": snapshot.vix_level,
        "spy_above_200d": snapshot.spy_above_200d,
        "ten_year_yield_trend": snapshot.ten_year_yield_trend,
        "reason": snapshot.reason,
        "source_type": "market_regime_snapshot",
        "schema_version": 1,
    }


def _last_close(rows: list[Mapping[str, Any]]) -> float | None:
    closes = _closes(rows)
    return closes[-1] if closes else None


def _closes(rows: Iterable[Mapping[str, Any]]) -> list[float]:
    closes: list[float] = []
    for row in rows:
        value = row.get("close") if isinstance(row, Mapping) else None
        try:
            if value is not None:
                closes.append(float(value))
        except (TypeError, ValueError):
            continue
    return closes


def _simple_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _yield_trend(rows: list[Mapping[str, Any]], *, lookback: int = 20, flat_threshold_pct: float = 1.0) -> str:
    closes = _closes(rows)
    if len(closes) < 2:
        return ""
    start = closes[-min(lookback, len(closes))]
    end = closes[-1]
    if start == 0:
        return "stable"
    change_pct = ((end - start) / abs(start)) * 100
    if change_pct >= flat_threshold_pct:
        return "rising"
    if change_pct <= -flat_threshold_pct:
        return "falling"
    return "stable"


def _reason(
    base: MarketRegimeSnapshot,
    *,
    vix_level: float | None,
    spy_above_200d: bool | None,
    yield_trend: str,
) -> str:
    pieces = [
        base.reason or "Regime classified from market inputs.",
        f"VIX={vix_level if vix_level is not None else 'unknown'}.",
        f"SPY above 200d={spy_above_200d if spy_above_200d is not None else 'unknown'}.",
        f"10Y yield trend={yield_trend or 'unknown'}.",
    ]
    return " ".join(pieces)


__all__ = [
    "DEFAULT_SPY_SYMBOL",
    "DEFAULT_TEN_YEAR_YIELD_SYMBOL",
    "DEFAULT_VIX_SYMBOL",
    "build_market_regime_snapshot",
    "build_market_regime_snapshot_from_histories",
    "fetch_yfinance_history",
    "market_regime_to_dict",
]
