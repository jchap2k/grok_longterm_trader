"""CLI for read-only long-term position intelligence reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.decision_journal import LongTermDecisionJournal
from longterm.email_sender import SmtpEmailSender, load_email_settings
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.position_report import (
    build_position_intelligence_email,
    build_position_intelligence_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render or send a position intelligence report.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--paper-ledger-db", default=None)
    parser.add_argument("--paper-outcome-price-map", default=None)
    parser.add_argument("--period", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--recipient-email", default="")
    parser.add_argument("--email-config", default=None)
    parser.add_argument("--send", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, sender: SmtpEmailSender | None = None) -> int:
    journal = LongTermDecisionJournal(args.journal_db)
    portfolio_state = PortfolioState.from_file(args.portfolio_state)
    paper_ledger = PaperTradeLedger(args.paper_ledger_db) if args.paper_ledger_db else None
    paper_outcome_price_map = _load_json(args.paper_outcome_price_map) if args.paper_outcome_price_map else None
    if not args.send:
        print(
            build_position_intelligence_report(
                journal,
                portfolio_state=portfolio_state,
                paper_ledger=paper_ledger,
                paper_outcome_price_map=paper_outcome_price_map,
            ),
            end="",
        )
        return 0

    settings = load_email_settings(args.email_config)
    recipient = args.recipient_email or settings.email_to
    email = build_position_intelligence_email(
        journal,
        portfolio_state=portfolio_state,
        paper_ledger=paper_ledger,
        paper_outcome_price_map=paper_outcome_price_map,
        recipient_email=recipient,
        period=args.period,
    )
    result = (sender or SmtpEmailSender()).send(email, settings)
    print(result.reason)
    return 0 if result.sent or not email.should_send else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = ["build_parser", "main", "run_cli"]
