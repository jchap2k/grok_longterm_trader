"""CLI for generating market-regime snapshots used by idle-cash parking."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from longterm.idle_cash_policy import MarketRegimeSnapshot
from longterm.market_regime_snapshot import (
    DEFAULT_FRED_CPI_SERIES,
    DEFAULT_FRED_CREDIT_SPREAD_SERIES,
    DEFAULT_FRED_SP500_SERIES,
    DEFAULT_FRED_TEN_YEAR_SERIES,
    DEFAULT_FRED_VIX_SERIES,
    DEFAULT_FRED_YIELD_CURVE_SERIES,
    DEFAULT_SPY_SYMBOL,
    DEFAULT_TEN_YEAR_YIELD_SYMBOL,
    DEFAULT_VIX_SYMBOL,
    build_fred_market_regime_snapshot,
    build_market_regime_snapshot,
    build_market_regime_snapshot_from_histories,
    fetch_yfinance_history,
    market_regime_to_dict,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a market-regime JSON snapshot for long-term parking policy.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot-file", default="", help="Offline JSON with vix, spy, and ten_year_yield histories.")
    source.add_argument("--provider", choices=["yfinance", "fredapi"], default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--period", default="1y")
    parser.add_argument("--vix-symbol", default=DEFAULT_VIX_SYMBOL)
    parser.add_argument("--spy-symbol", default=DEFAULT_SPY_SYMBOL)
    parser.add_argument("--ten-year-yield-symbol", default=DEFAULT_TEN_YEAR_YIELD_SYMBOL)
    parser.add_argument("--fred-api-key-env", default="FRED_API_KEY")
    parser.add_argument("--fred-vix-series", default=DEFAULT_FRED_VIX_SERIES)
    parser.add_argument("--fred-sp500-series", default=DEFAULT_FRED_SP500_SERIES)
    parser.add_argument("--fred-ten-year-series", default=DEFAULT_FRED_TEN_YEAR_SERIES)
    parser.add_argument("--fred-cpi-series", default=DEFAULT_FRED_CPI_SERIES)
    parser.add_argument("--fred-yield-curve-series", default=DEFAULT_FRED_YIELD_CURVE_SERIES)
    parser.add_argument("--fred-credit-spread-series", default=DEFAULT_FRED_CREDIT_SPREAD_SERIES)
    return parser


def run_cli(args: argparse.Namespace, *, fred_fetcher=None) -> int:
    if args.snapshot_file:
        payload = _load_snapshot_file(args.snapshot_file)
        snapshot = build_market_regime_snapshot_from_histories(
            vix_history=payload.get("vix") or [],
            spy_history=payload.get("spy") or [],
            ten_year_yield_history=payload.get("ten_year_yield") or [],
        )
        mode = "snapshot_file"
    elif args.provider == "fredapi":
        import os

        try:
            snapshot = build_fred_market_regime_snapshot(
                fetch_fred_history=fred_fetcher,
                api_key=os.environ.get(args.fred_api_key_env),
                vix_series=args.fred_vix_series,
                sp500_series=args.fred_sp500_series,
                ten_year_series=args.fred_ten_year_series,
                cpi_series=args.fred_cpi_series,
                yield_curve_series=args.fred_yield_curve_series,
                credit_spread_series=args.fred_credit_spread_series,
            )
            mode = args.provider
        except Exception as fred_exc:
            print(f"FRED market-regime provider failed: {_safe_error(fred_exc)}", file=sys.stderr)
            try:
                snapshot = build_market_regime_snapshot(
                    fetch_history=fetch_yfinance_history,
                    vix_symbol=args.vix_symbol,
                    spy_symbol=args.spy_symbol,
                    ten_year_yield_symbol=args.ten_year_yield_symbol,
                    period=args.period,
                )
                snapshot = replace(
                    snapshot,
                    reason=f"FRED provider unavailable; fell back to yfinance. {snapshot.reason}",
                    provider_status="degraded_fallback",
                    provider_mode="fredapi_fallback_yfinance",
                    provider_warning=f"FRED provider unavailable: {_safe_error(fred_exc)}",
                )
                mode = "fredapi_fallback_yfinance"
            except Exception as fallback_exc:
                print(f"Fallback market-regime provider failed: {_safe_error(fallback_exc)}", file=sys.stderr)
                snapshot = MarketRegimeSnapshot(
                    risk_regime="market_data_unavailable",
                    reason=(
                        "Market-regime providers unavailable; no-submit scheduler continues with "
                        "parking/rebalance decisions constrained by missing regime data."
                    ),
                    macro_regime_label="market_data_unavailable",
                    provider_status="unavailable",
                    provider_mode="fredapi_unavailable",
                    provider_warning=(
                        f"FRED provider unavailable: {_safe_error(fred_exc)}; "
                        f"fallback provider unavailable: {_safe_error(fallback_exc)}"
                    ),
                )
                mode = "fredapi_unavailable"
    else:
        snapshot = build_market_regime_snapshot(
            fetch_history=fetch_yfinance_history,
            vix_symbol=args.vix_symbol,
            spy_symbol=args.spy_symbol,
            ten_year_yield_symbol=args.ten_year_yield_symbol,
            period=args.period,
        )
        snapshot = replace(snapshot, provider_mode=args.provider or "yfinance")
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


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message[:240]}"


__all__ = ["build_parser", "main", "run_cli"]
