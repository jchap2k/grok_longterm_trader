"""CLI for generating market-regime snapshots used by idle-cash parking."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
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
    fetch_fred_rest_history,
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
    parser.add_argument(
        "--fred-provider-attempts",
        type=int,
        default=3,
        help="Number of attempts before treating the FRED provider as unavailable.",
    )
    parser.add_argument(
        "--fred-provider-retry-delay-seconds",
        type=float,
        default=2.0,
        help="Delay between failed FRED provider attempts.",
    )
    parser.add_argument(
        "--fred-provider-rest-fallback-attempts",
        type=int,
        default=3,
        help="Per-series attempts for direct FRED REST fallback after fredapi fails.",
    )
    parser.add_argument(
        "--fred-provider-rest-retry-delay-seconds",
        type=float,
        default=2.0,
        help="Base delay for direct FRED REST fallback exponential backoff.",
    )
    parser.add_argument(
        "--fred-rest-timeout-seconds",
        type=float,
        default=30.0,
        help="Per-request timeout for direct FRED REST fallback.",
    )
    return parser


def run_cli(args: argparse.Namespace, *, fred_fetcher=None, fred_rest_fetcher=None) -> int:
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
            snapshot = _build_fred_snapshot_with_retries(
                args,
                api_key=os.environ.get(args.fred_api_key_env),
                fred_fetcher=fred_fetcher,
            )
            mode = args.provider
        except Exception as fred_exc:
            print(f"FRED market-regime provider failed: {_safe_error(fred_exc)}", file=sys.stderr)
            try:
                snapshot = _build_fred_rest_snapshot_with_retries(
                    args,
                    api_key=os.environ.get(args.fred_api_key_env),
                    fred_rest_fetcher=fred_rest_fetcher,
                )
                mode = "fredapi_rest_fallback"
            except Exception as rest_exc:
                print(f"FRED REST fallback provider failed: {_safe_error(rest_exc)}", file=sys.stderr)
                snapshot, mode = _build_non_fred_fallback_snapshot(args, fred_exc, rest_exc)
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


def _build_non_fred_fallback_snapshot(
    args: argparse.Namespace,
    fred_exc: Exception,
    rest_exc: Exception | None = None,
) -> tuple[MarketRegimeSnapshot, str]:
    rest_note = f"; FRED REST fallback unavailable: {_safe_error(rest_exc)}" if rest_exc is not None else ""
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
            provider_warning=f"FRED provider unavailable: {_safe_error(fred_exc)}{rest_note}",
        )
        return snapshot, "fredapi_fallback_yfinance"
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
                f"FRED provider unavailable: {_safe_error(fred_exc)}{rest_note}; "
                f"fallback provider unavailable: {_safe_error(fallback_exc)}"
            ),
        )
        return snapshot, "fredapi_unavailable"


def _build_fred_rest_snapshot_with_retries(
    args: argparse.Namespace,
    *,
    api_key: str | None,
    fred_rest_fetcher=None,
) -> MarketRegimeSnapshot:
    attempts = max(1, int(args.fred_provider_rest_fallback_attempts))
    delay_seconds = max(0.0, float(args.fred_provider_rest_retry_delay_seconds))
    fetcher = fred_rest_fetcher or fetch_fred_rest_history
    last_real_rest_call_at = [0.0]

    def retrying_fetcher(series_id: str, key: str | None = None):
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if fetcher is fetch_fred_rest_history:
                    _sleep_until_min_interval(last_real_rest_call_at, 0.55)
                    return fetcher(series_id, key, timeout_seconds=float(args.fred_rest_timeout_seconds))
                return fetcher(series_id, key)
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                print(
                    f"FRED REST provider series {series_id} attempt {attempt}/{attempts} failed: "
                    f"{_safe_error(exc)}; retrying.",
                    file=sys.stderr,
                )
                _sleep_backoff(delay_seconds, attempt)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"FRED REST provider failed for {series_id} without an exception.")

    return build_fred_market_regime_snapshot(
        fetch_fred_history=retrying_fetcher,
        api_key=api_key,
        vix_series=args.fred_vix_series,
        sp500_series=args.fred_sp500_series,
        ten_year_series=args.fred_ten_year_series,
        cpi_series=args.fred_cpi_series,
        yield_curve_series=args.fred_yield_curve_series,
        credit_spread_series=args.fred_credit_spread_series,
        provider_mode="fredapi_rest_fallback",
    )


def _build_fred_snapshot_with_retries(
    args: argparse.Namespace,
    *,
    api_key: str | None,
    fred_fetcher=None,
) -> MarketRegimeSnapshot:
    attempts = max(1, int(args.fred_provider_attempts))
    delay_seconds = max(0.0, float(args.fred_provider_retry_delay_seconds))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return build_fred_market_regime_snapshot(
                fetch_fred_history=fred_fetcher,
                api_key=api_key,
                vix_series=args.fred_vix_series,
                sp500_series=args.fred_sp500_series,
                ten_year_series=args.fred_ten_year_series,
                cpi_series=args.fred_cpi_series,
                yield_curve_series=args.fred_yield_curve_series,
                credit_spread_series=args.fred_credit_spread_series,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            print(
                f"FRED market-regime provider attempt {attempt}/{attempts} failed: {_safe_error(exc)}; retrying.",
                file=sys.stderr,
            )
            _sleep_backoff(delay_seconds, attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("FRED market-regime provider failed without an exception.")


def _sleep_backoff(base_delay_seconds: float, attempt: int) -> None:
    if base_delay_seconds <= 0:
        return
    time.sleep(min(base_delay_seconds * (2 ** max(0, attempt - 1)), 30.0))


def _sleep_until_min_interval(last_call_at: list[float], min_interval_seconds: float) -> None:
    now = time.monotonic()
    elapsed = now - last_call_at[0]
    if last_call_at[0] and elapsed < min_interval_seconds:
        time.sleep(min_interval_seconds - elapsed)
    last_call_at[0] = time.monotonic()


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
