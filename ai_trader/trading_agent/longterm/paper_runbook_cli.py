"""CLI for read-only Monday paper-trading runbooks."""

from __future__ import annotations

import argparse
import json

from longterm.paper_runbook import build_paper_runbook, build_paper_runbook_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only Monday paper-trading runbook.")
    parser.add_argument("--journal-db", required=True)
    parser.add_argument("--ledger-db", required=True)
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--profile-config", default="")
    parser.add_argument("--expected-cash", type=float, default=None)
    parser.add_argument(
        "--include-submit-command",
        action="store_true",
        help="Reveal the supervised paper submit command in the generated runbook.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    runbook = build_paper_runbook(
        journal_db=args.journal_db,
        ledger_db=args.ledger_db,
        portfolio_state=args.portfolio_state,
        action_plan=args.action_plan,
        output_dir=args.output_dir,
        expected_cash=args.expected_cash,
        profile_config=args.profile_config,
        include_submit_command=args.include_submit_command,
    )
    if args.json:
        print(json.dumps(runbook, indent=2, sort_keys=True))
    else:
        print(build_paper_runbook_markdown(runbook), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
