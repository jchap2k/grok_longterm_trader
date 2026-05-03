"""CLI for generating market-regime snapshots used by idle-cash parking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.market_regime_snapshot import (
    DEFAULT_SPY_SYMBOL,
    DEFAULT_TEN_YEAR_YIELD_SYMBOL,
    DEFAULT_VIX_SYMBOL,
    build_market_regime_snapshot,
    build_market_regime_snapshot_from_histories,
    fetch_yfinance_history,
    market_regime_to_dict,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a market-regime JSON snapshot for long-term parking policy.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot-file", default="", help="Offline JSON with vix, spy, and ten_year_yield histories.")
    source.add_argument("--provider", choices=["yfinance"], default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--period", default="1y")
    parser.add_argument("--vix-symbol", default=DEFAULT_VIX_SYMBOL)
    parser.add_argument("--spy-symbol", default=DEFAULT_SPY_SYMBOL)
    parser.add_argument("--ten-year-yield-symbol", default=DEFAULT_TEN_YEAR_YIELD_SYMBOL)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    if args.snapshot_file:
        payload = _load_snapshot_file(args.snapshot_file)
        snapshot = build_market_regime_snapshot_from_histories(
            vix_history=payload.get("vix") or [],
            spy_history=payload.get("spy") or [],
            ten_year_yield_history=payload.get("ten_year_yield") or [],
        )
        mode = "snapshot_file"
    else:
        snapshot = build_market_regime_snapshot(
            fetch_history=fetch_yfinance_history,
            vix_symbol=args.vix_symbol,
            spy_symbol=args.spy_symbol,
            ten_year_yield_symbol=args.ten_year_yield_symbol,
            period=args.period,
        )
        mode = args.provider

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_json = market_regime_to_dict(snapshot)
    output_path.write_text(json.dumps(snapshot_json, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"mode": mode, "output": str(output_path), **snapshot_json}, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _load_snapshot_file(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Market history snapshot file must contain a JSON object.")
    return {
        str(key): [dict(item) for item in value if isinstance(item, Mapping)]
        for key, value in payload.items()
        if isinstance(value, list)
    }


__all__ = ["build_parser", "main", "run_cli"]
