"""CLI for read-only paper account cleanliness checks."""

from __future__ import annotations

import argparse
import json

from longterm.paper_account_cleanliness import (
    build_paper_account_cleanliness_markdown,
    evaluate_paper_account_cleanliness,
)
from longterm.portfolio_state import PortfolioState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check whether a paper account is clean for the next smoke.")
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--expected-cash", type=float, default=None)
    parser.add_argument("--cash-tolerance", type=float, default=1.0)
    parser.add_argument("--protected-symbol", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    state = PortfolioState.from_file(args.portfolio_state)
    report = evaluate_paper_account_cleanliness(
        state,
        expected_cash=args.expected_cash,
        cash_tolerance=args.cash_tolerance,
        protected_symbols=args.protected_symbol or None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(build_paper_account_cleanliness_markdown(report), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
