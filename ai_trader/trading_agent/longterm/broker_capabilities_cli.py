"""CLI for advisory broker capability compatibility checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.broker_capabilities import (
    BROKER_CAPABILITIES,
    build_broker_capability_markdown,
    evaluate_broker_capability_match,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare paper and live broker capabilities.")
    parser.add_argument("--paper-broker", default="alpaca_paper", choices=sorted(BROKER_CAPABILITIES))
    parser.add_argument("--live-broker", default="schwab_api", choices=sorted(BROKER_CAPABILITIES))
    parser.add_argument(
        "--required-order-model",
        default="notional_fractional",
        choices=["notional_fractional", "whole_share"],
    )
    parser.add_argument(
        "--observed-output",
        default="",
        help="Optional path for a live-readiness observed JSON fragment.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = evaluate_broker_capability_match(
        paper_broker=args.paper_broker,
        live_broker=args.live_broker,
        required_order_model=args.required_order_model,
    )
    if args.observed_output:
        _write_observed(args.observed_output, report["live_readiness_observed"])
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(build_broker_capability_markdown(report), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _write_observed(path: str | Path, observed: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(observed, indent=2, sort_keys=True), encoding="utf-8")


__all__ = ["build_parser", "main", "run_cli"]
