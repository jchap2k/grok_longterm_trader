"""CLI helpers for calendar-flow concept research."""

from __future__ import annotations

import argparse
import json

from longterm.calendar_flow_research import (
    backtest_calendar_flow_strategy,
    download_close_series,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research a calendar-flow strategy on daily price data.")
    parser.add_argument("--symbol", default="TLT")
    parser.add_argument("--start", default="2004-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--round-trip-cost-bps", type=float, default=0.0)
    parser.add_argument("--include-trades", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, download_func=download_close_series) -> int:
    close = download_func(
        args.symbol,
        start=args.start,
        end=args.end,
    )
    result = backtest_calendar_flow_strategy(
        close,
        symbol=args.symbol,
        round_trip_cost_bps=args.round_trip_cost_bps,
    )
    payload = result.to_dict()
    if not args.include_trades:
        payload.pop("trades", None)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))
