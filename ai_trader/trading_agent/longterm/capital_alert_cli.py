"""CLI helpers for long-term capital-needed alerts."""

from __future__ import annotations

import argparse

from longterm.capital_alert import build_capital_needed_alert, build_capital_needed_email
from longterm.decision_journal import LongTermDecisionJournal
from longterm.email_sender import SmtpEmailSender, load_email_settings
from longterm.portfolio_state import PortfolioState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render or send long-term capital-needed alerts.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--active-sleeve-value", type=float, required=True)
    parser.add_argument("--available-cash", type=float, required=True)
    parser.add_argument("--min-confidence", type=int, default=85)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--recipient-email", default="")
    parser.add_argument("--email-config", default=None)
    parser.add_argument("--portfolio-state", default=None)
    parser.add_argument("--send", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, sender: SmtpEmailSender | None = None) -> int:
    journal = LongTermDecisionJournal(args.journal_db)
    portfolio_state = PortfolioState.from_file(args.portfolio_state) if args.portfolio_state else None
    if not args.send:
        alert = build_capital_needed_alert(
            journal,
            active_sleeve_value=args.active_sleeve_value,
            available_cash=args.available_cash,
            portfolio_state=portfolio_state,
            min_confidence=args.min_confidence,
            limit=args.limit,
        )
        if not alert.should_alert:
            print(f"No capital-needed alert. {alert.reason}".strip())
            return 0
        print(alert.markdown, end="")
        return 0

    settings = load_email_settings(args.email_config)
    recipient = args.recipient_email or settings.email_to
    email = build_capital_needed_email(
        journal,
        active_sleeve_value=args.active_sleeve_value,
        available_cash=args.available_cash,
        recipient_email=recipient,
        portfolio_state=portfolio_state,
        min_confidence=args.min_confidence,
        limit=args.limit,
    )
    result = (sender or SmtpEmailSender()).send(email, settings)
    print(result.reason)
    return 0 if result.sent or not email.should_send else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
