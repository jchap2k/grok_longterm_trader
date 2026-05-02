"""CLI for the read-only long-term operator status bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.decision_journal import LongTermDecisionJournal
from longterm.operator_status_bundle import build_operator_status_bundle, build_operator_status_markdown
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only long-term operator status bundle.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--portfolio-state", default=None)
    parser.add_argument("--paper-ledger-db", default=None)
    parser.add_argument("--action-plan", default=None)
    parser.add_argument("--price-map", default=None)
    parser.add_argument("--feedback-summary", default=None)
    parser.add_argument("--monday-operator-check", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    payload = build_operator_status_bundle(
        LongTermDecisionJournal(args.journal_db),
        portfolio_state=PortfolioState.from_file(args.portfolio_state) if args.portfolio_state else None,
        paper_ledger=PaperTradeLedger(args.paper_ledger_db) if args.paper_ledger_db else None,
        action_plan=_load_json(args.action_plan) if args.action_plan else None,
        price_map=_load_json(args.price_map) if args.price_map else None,
        feedback_summary=_load_json(args.feedback_summary) if args.feedback_summary else None,
        monday_operator_check=_load_json(args.monday_operator_check) if args.monday_operator_check else None,
    )
    if args.report_output:
        Path(args.report_output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_operator_status_markdown(payload), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
