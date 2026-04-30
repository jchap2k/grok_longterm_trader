import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.calendar_flow_cli import build_parser, run_cli
from longterm.calendar_flow_research import (
    backtest_calendar_flow_strategy,
    generate_calendar_flow_trades,
)


def test_generate_calendar_flow_trades_matches_posted_window_logic():
    dates = pd.date_range("2024-01-02", "2024-02-29", freq="B")
    close = pd.Series(range(100, 100 + len(dates)), index=dates, dtype=float)

    trades = generate_calendar_flow_trades(close)

    january_days = [ts for ts in dates if ts.month == 1]
    february_days = [ts for ts in dates if ts.month == 2]

    assert len(trades) == 4

    jan_short, jan_long, feb_short, feb_long = trades

    assert jan_short.side == "SHORT"
    assert jan_short.entry_date == january_days[0]
    assert jan_short.exit_date == january_days[5]

    assert jan_long.side == "LONG"
    assert jan_long.entry_date == january_days[len(january_days) - 7]
    assert jan_long.exit_date == january_days[-1]

    assert feb_short.side == "SHORT"
    assert feb_short.entry_date == february_days[0]
    assert feb_short.exit_date == february_days[5]

    assert feb_long.side == "LONG"
    assert feb_long.entry_date == february_days[len(february_days) - 7]
    assert feb_long.exit_date == february_days[-1]

    assert jan_short.exit_date < jan_long.entry_date
    assert feb_short.exit_date < feb_long.entry_date


def test_backtest_calendar_flow_strategy_uses_simple_long_short_return_accounting():
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    close = pd.Series(
        [100.0, 101.0, 102.0, 103.0, 104.0, 95.0, 96.0, 97.0, 98.0, 99.0],
        index=dates,
    )

    result = backtest_calendar_flow_strategy(close)

    assert len(result.trades) == 2

    short_trade = result.trades[0]
    long_trade = result.trades[1]

    assert short_trade.side == "SHORT"
    assert short_trade.entry_price == 100.0
    assert short_trade.exit_price == 95.0
    assert short_trade.raw_return_pct == pytest.approx(5.0)

    assert long_trade.side == "LONG"
    assert long_trade.entry_price == 103.0
    assert long_trade.exit_price == 99.0
    assert long_trade.raw_return_pct == pytest.approx((99.0 - 103.0) / 103.0 * 100.0)

    expected_total_return = (
        (1.0 + short_trade.net_return_pct / 100.0)
        * (1.0 + long_trade.net_return_pct / 100.0)
        - 1.0
    ) * 100.0
    assert result.total_return_pct == pytest.approx(expected_total_return)


def test_backtest_calendar_flow_strategy_applies_round_trip_costs():
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    close = pd.Series(
        [100.0, 101.0, 102.0, 103.0, 104.0, 95.0, 96.0, 97.0, 98.0, 99.0],
        index=dates,
    )

    result = backtest_calendar_flow_strategy(close, round_trip_cost_bps=20.0)

    short_trade = result.trades[0]
    long_trade = result.trades[1]

    assert short_trade.net_return_pct == pytest.approx(short_trade.raw_return_pct - 0.20)
    assert long_trade.net_return_pct == pytest.approx(long_trade.raw_return_pct - 0.20)


def test_calendar_flow_cli_prints_json_summary(capsys):
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    close = pd.Series(
        [100.0, 101.0, 102.0, 103.0, 104.0, 95.0, 96.0, 97.0, 98.0, 99.0],
        index=dates,
    )

    def fake_download(symbol, *, start, end):
        assert symbol == "TLT"
        assert start == "2024-01-01"
        assert end == "2024-02-01"
        return close

    parser = build_parser()
    args = parser.parse_args(
        [
            "--symbol",
            "TLT",
            "--start",
            "2024-01-01",
            "--end",
            "2024-02-01",
        ]
    )

    exit_code = run_cli(args, download_func=fake_download)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"symbol": "TLT"' in captured.out
    assert '"trade_count": 2' in captured.out
    assert '"trades"' not in captured.out


def test_calendar_flow_cli_can_include_trade_details(capsys):
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    close = pd.Series(
        [100.0, 101.0, 102.0, 103.0, 104.0, 95.0, 96.0, 97.0, 98.0, 99.0],
        index=dates,
    )

    def fake_download(symbol, *, start, end):
        return close

    parser = build_parser()
    args = parser.parse_args(
        [
            "--include-trades",
        ]
    )

    exit_code = run_cli(args, download_func=fake_download)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"trades": [' in captured.out
