"""CLI for running Kronos advisory on a bounded batch of symbols."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from longterm.kronos_advisory_batch import (
    build_kronos_batch_payload,
    build_symbol_error_advisory,
    load_symbols_from_args,
)
from longterm.kronos_advisory_cli import (
    DEFAULT_KRONOS_PYTHON,
    DEFAULT_KRONOS_ROOT,
    run_cli as run_single_kronos_cli,
)


AdvisoryRunner = Callable[[str, argparse.Namespace], dict[str, Any]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run optional Kronos advisory on a symbol batch.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols.")
    parser.add_argument("--idea-batch", default="", help="Optional JSON list containing symbol/ticker fields.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--kronos-root", default=DEFAULT_KRONOS_ROOT)
    parser.add_argument("--kronos-python", default=DEFAULT_KRONOS_PYTHON)
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--lookback", type=int, default=256)
    parser.add_argument("--pred-len", type=int, default=5)
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--limit", type=int, default=None)
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    advisory_runner: AdvisoryRunner | None = None,
) -> int:
    symbols = load_symbols_from_args(args)
    if args.limit is not None:
        symbols = symbols[: max(0, int(args.limit))]
    runner = advisory_runner or _run_single_symbol_advisory
    advisories = []
    for symbol in symbols:
        try:
            advisories.append(runner(symbol, args))
        except Exception as exc:
            advisories.append(build_symbol_error_advisory(symbol, exc))

    payload = build_kronos_batch_payload(advisories)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"mode": "kronos_batch", "output": str(output_path), **payload}, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _run_single_symbol_advisory(symbol: str, args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="longterm_kronos_batch_") as temp_dir:
        symbol_output = Path(temp_dir) / f"{symbol}.json"
        single_args = argparse.Namespace(
            symbol=symbol,
            output=str(symbol_output),
            kronos_root=args.kronos_root,
            kronos_python=args.kronos_python,
            provider="yfinance",
            period=args.period,
            interval=args.interval,
            lookback=args.lookback,
            pred_len=args.pred_len,
            model=args.model,
            tokenizer=args.tokenizer,
            device=args.device,
            timeout_seconds=args.timeout_seconds,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            run_single_kronos_cli(single_args)
        return json.loads(symbol_output.read_text(encoding="utf-8"))


__all__ = ["build_parser", "main", "run_cli"]
