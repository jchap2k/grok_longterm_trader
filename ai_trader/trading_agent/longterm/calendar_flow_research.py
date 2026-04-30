"""Research helpers for calendar-based flow strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class CalendarFlowTrade:
    side: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    raw_return_pct: float
    net_return_pct: float
    holding_days: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry_date"] = self.entry_date.strftime("%Y-%m-%d")
        payload["exit_date"] = self.exit_date.strftime("%Y-%m-%d")
        return payload


@dataclass(frozen=True)
class CalendarFlowBacktestResult:
    symbol: str
    start_date: str
    end_date: str
    trade_count: int
    win_rate_pct: float
    total_return_pct: float
    buy_and_hold_return_pct: float
    average_trade_return_pct: float
    average_winning_trade_pct: float
    average_losing_trade_pct: float
    max_drawdown_pct: float
    warnings: list[str]
    trades: list[CalendarFlowTrade]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trades"] = [trade.to_dict() for trade in self.trades]
        return payload


def download_close_series(
    symbol: str = "TLT",
    *,
    start: str = "2004-01-01",
    end: str | None = None,
) -> pd.Series:
    """Download adjusted close data for research."""
    history = yf.download(symbol, start=start, end=end, auto_adjust=False, progress=False)
    if history.empty:
        raise ValueError(f"No data downloaded for {symbol}.")
    close_column = "Adj Close" if "Adj Close" in history.columns else "Close"
    close = history[close_column]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = close.dropna()
    if close.empty:
        raise ValueError(f"No close series available for {symbol}.")
    close.index = pd.DatetimeIndex(close.index)
    return close.astype(float)


def generate_calendar_flow_trades(
    close: pd.Series,
    *,
    short_holding_days: int = 5,
    long_entry_days_before_next_month: int = 7,
    round_trip_cost_bps: float = 0.0,
) -> list[CalendarFlowTrade]:
    """Generate the monthly short/long trades implied by the posted code."""
    series = _normalize_close(close)
    trades: list[CalendarFlowTrade] = []

    grouped = series.groupby(series.index.to_period("M"))
    for _, monthly_close in grouped:
        if len(monthly_close) <= short_holding_days:
            continue

        month_index = list(monthly_close.index)

        short_entry_date = month_index[0]
        short_exit_date = month_index[short_holding_days]
        trades.append(
            _build_trade(
                side="SHORT",
                close=series,
                entry_date=short_entry_date,
                exit_date=short_exit_date,
                round_trip_cost_bps=round_trip_cost_bps,
            )
        )

        if len(month_index) < long_entry_days_before_next_month:
            continue

        long_entry_pos = len(month_index) - long_entry_days_before_next_month
        long_exit_pos = len(month_index) - 1
        if long_entry_pos >= long_exit_pos:
            continue
        trades.append(
            _build_trade(
                side="LONG",
                close=series,
                entry_date=month_index[long_entry_pos],
                exit_date=month_index[long_exit_pos],
                round_trip_cost_bps=round_trip_cost_bps,
            )
        )

    return trades


def backtest_calendar_flow_strategy(
    close: pd.Series,
    *,
    symbol: str = "TLT",
    round_trip_cost_bps: float = 0.0,
) -> CalendarFlowBacktestResult:
    """Run a transparent fixed-notional backtest for the calendar flow idea."""
    series = _normalize_close(close)
    trades = generate_calendar_flow_trades(
        series,
        round_trip_cost_bps=round_trip_cost_bps,
    )
    if not trades:
        return CalendarFlowBacktestResult(
            symbol=symbol,
            start_date=series.index[0].strftime("%Y-%m-%d"),
            end_date=series.index[-1].strftime("%Y-%m-%d"),
            trade_count=0,
            win_rate_pct=0.0,
            total_return_pct=0.0,
            buy_and_hold_return_pct=_compute_buy_and_hold_return_pct(series),
            average_trade_return_pct=0.0,
            average_winning_trade_pct=0.0,
            average_losing_trade_pct=0.0,
            max_drawdown_pct=0.0,
            warnings=["No trades generated from the supplied series."],
            trades=[],
        )

    equity_curve = [1.0]
    for trade in trades:
        equity_curve.append(equity_curve[-1] * (1.0 + trade.net_return_pct / 100.0))

    winning_trades = [trade.net_return_pct for trade in trades if trade.net_return_pct > 0]
    losing_trades = [trade.net_return_pct for trade in trades if trade.net_return_pct <= 0]
    total_return_pct = (equity_curve[-1] - 1.0) * 100.0
    buy_and_hold_return_pct = _compute_buy_and_hold_return_pct(series)
    warnings = _build_warnings(
        total_return_pct=total_return_pct,
        buy_and_hold_return_pct=buy_and_hold_return_pct,
        max_drawdown_pct=_compute_max_drawdown_pct(equity_curve),
    )

    return CalendarFlowBacktestResult(
        symbol=symbol,
        start_date=series.index[0].strftime("%Y-%m-%d"),
        end_date=series.index[-1].strftime("%Y-%m-%d"),
        trade_count=len(trades),
        win_rate_pct=len(winning_trades) / len(trades) * 100.0,
        total_return_pct=total_return_pct,
        buy_and_hold_return_pct=buy_and_hold_return_pct,
        average_trade_return_pct=sum(trade.net_return_pct for trade in trades) / len(trades),
        average_winning_trade_pct=(sum(winning_trades) / len(winning_trades)) if winning_trades else 0.0,
        average_losing_trade_pct=(sum(losing_trades) / len(losing_trades)) if losing_trades else 0.0,
        max_drawdown_pct=_compute_max_drawdown_pct(equity_curve),
        warnings=warnings,
        trades=trades,
    )


def _normalize_close(close: pd.Series) -> pd.Series:
    if isinstance(close, pd.DataFrame):
        if close.shape[1] != 1:
            raise ValueError("Close data must be a Series or single-column DataFrame.")
        close = close.iloc[:, 0]
    if not isinstance(close.index, pd.DatetimeIndex):
        raise ValueError("Close series must use a DatetimeIndex.")
    series = close.dropna().sort_index()
    if series.empty:
        raise ValueError("Close series is empty after dropping missing values.")
    return series.astype(float)


def _build_trade(
    *,
    side: str,
    close: pd.Series,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    round_trip_cost_bps: float,
) -> CalendarFlowTrade:
    entry_price = float(close.loc[entry_date])
    exit_price = float(close.loc[exit_date])
    if side == "LONG":
        raw_return_pct = (exit_price - entry_price) / entry_price * 100.0
    elif side == "SHORT":
        raw_return_pct = (entry_price - exit_price) / entry_price * 100.0
    else:
        raise ValueError(f"Unsupported trade side: {side}")

    net_return_pct = raw_return_pct - (round_trip_cost_bps / 100.0)
    holding_days = close.index.get_loc(exit_date) - close.index.get_loc(entry_date)
    return CalendarFlowTrade(
        side=side,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        raw_return_pct=raw_return_pct,
        net_return_pct=net_return_pct,
        holding_days=int(holding_days),
    )


def _compute_buy_and_hold_return_pct(close: pd.Series) -> float:
    return (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100.0


def _compute_max_drawdown_pct(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = (value / peak - 1.0) * 100.0
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def _build_warnings(
    *,
    total_return_pct: float,
    buy_and_hold_return_pct: float,
    max_drawdown_pct: float,
) -> list[str]:
    warnings: list[str] = []
    if abs(buy_and_hold_return_pct) > 1000:
        warnings.append(
            "Buy-and-hold return exceeds 1000%; verify price column, compounding, and benchmark interpretation."
        )
    if abs(total_return_pct) > 10000:
        warnings.append(
            "Strategy total return exceeds 10000%; verify position sizing and compounding assumptions."
        )
    if max_drawdown_pct < -100:
        warnings.append(
            "Max drawdown below -100% suggests unrealistic leverage or broken accounting."
        )
    return warnings
