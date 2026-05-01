"""CLI for long-term rebalance outcome analysis."""

from __future__ import annotations

import argparse
import json

from longterm.decision_journal import LongTermDecisionJournal
from longterm.rebalance_outcome_analysis import (
    RebalanceOutcomeAnalyzer,
    build_rebalance_outcome_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze evaluated long-term outcomes by thesis/review-risk bucket."
    )
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = RebalanceOutcomeAnalyzer(LongTermDecisionJournal(args.journal_db)).build(
        limit=args.limit
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(build_rebalance_outcome_markdown(report), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
