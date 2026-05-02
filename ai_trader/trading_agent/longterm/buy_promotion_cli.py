"""CLI for dry-run buy-promotion review reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.buy_promotion import (
    build_buy_promotion_markdown,
    build_buy_promotion_reviews,
)
from longterm.decision_journal import LongTermDecisionJournal
from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render dry-run promotion reviews for first-pass BUY/ADD rows."
    )
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default=None)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    portfolio_state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    reviews = build_buy_promotion_reviews(
        LongTermDecisionJournal(args.journal_db),
        profile=profile,
        portfolio_state=portfolio_state,
        limit=args.limit,
    )
    if args.json:
        output = json.dumps([review.to_dict() for review in reviews], indent=2, sort_keys=True)
    else:
        output = build_buy_promotion_markdown(reviews)

    if args.output:
        Path(args.output).write_text(output + ("" if output.endswith("\n") else "\n"), encoding="utf-8")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
