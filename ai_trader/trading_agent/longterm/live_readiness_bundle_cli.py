"""CLI for read-only live-readiness evidence bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.live_readiness_bundle import (
    build_live_readiness_bundle,
    build_live_readiness_bundle_markdown,
)
from longterm.paper_trade_ledger import PaperTradeLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a live-readiness evidence bundle.")
    parser.add_argument("--observed-file", default="")
    parser.add_argument("--paper-ledger-db", default="")
    parser.add_argument("--paper-broker", default="alpaca_paper")
    parser.add_argument("--live-broker", default="schwab_api")
    parser.add_argument(
        "--required-order-model",
        default="notional_fractional",
        choices=["notional_fractional", "whole_share"],
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    bundle = build_live_readiness_bundle(
        base_observed=_load_json(args.observed_file) if args.observed_file else {},
        paper_ledger=PaperTradeLedger(args.paper_ledger_db) if args.paper_ledger_db else None,
        paper_broker=args.paper_broker,
        live_broker=args.live_broker,
        required_order_model=args.required_order_model,
    )
    if args.json:
        print(json.dumps(bundle, indent=2, sort_keys=True))
    else:
        print(build_live_readiness_bundle_markdown(bundle), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
