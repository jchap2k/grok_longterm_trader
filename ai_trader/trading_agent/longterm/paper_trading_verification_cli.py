"""CLI for read-only paper trading verification evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.paper_trading_verification import (
    build_paper_trading_verification_markdown,
    build_paper_trading_verification_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build paper trading verification evidence.")
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--observed-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = build_paper_trading_verification_report(PaperTradeLedger(args.ledger_db))
    if args.observed_output:
        _write_observed(args.observed_output, report["live_readiness_observed"])
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(build_paper_trading_verification_markdown(report), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _write_observed(path: str | Path, observed: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(observed, indent=2, sort_keys=True), encoding="utf-8")


__all__ = ["build_parser", "main", "run_cli"]
