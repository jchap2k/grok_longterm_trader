"""CLI helpers for long-term next-actions reports."""

from __future__ import annotations

import argparse

from longterm.cli import DEFAULT_PROFILE_PATH
from longterm.decision_journal import LongTermDecisionJournal
from longterm.next_actions import build_next_actions_markdown
from longterm.paper_preview_status import PaperPreviewStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render long-term next actions.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--evidence-file", default=None)
    parser.add_argument("--paper-ledger-db", default=None)
    parser.add_argument("--limit", type=int, default=10)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    journal = LongTermDecisionJournal(args.journal_db)
    paper_status = (
        PaperPreviewStatusBuilder(PaperTradeLedger(args.paper_ledger_db)).build()
        if args.paper_ledger_db
        else None
    )
    print(
        build_next_actions_markdown(
            journal,
            profile=profile,
            portfolio_state=state,
            evidence_file=args.evidence_file,
            deferred_research_queue=journal.list_deferred_research_items(limit=args.limit),
            paper_preview_status_by_decision=paper_status.by_decision_id if paper_status else None,
            paper_preview_status_by_symbol=paper_status.by_symbol if paper_status else None,
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
