"""CLI helpers for long-term next-actions reports."""

from __future__ import annotations

import argparse

from longterm.cli import DEFAULT_PROFILE_PATH
from longterm.decision_journal import LongTermDecisionJournal
from longterm.next_actions import build_next_actions_markdown
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render long-term next actions.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--limit", type=int, default=10)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    journal = LongTermDecisionJournal(args.journal_db)
    print(
        build_next_actions_markdown(
            journal,
            profile=profile,
            portfolio_state=state,
            limit=args.limit,
        ),
        end="",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
