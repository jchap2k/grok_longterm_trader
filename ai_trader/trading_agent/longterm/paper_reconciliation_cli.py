"""CLI for dry-run paper account reconciliation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.paper_reconciliation import (
    build_paper_reconciliation_markdown,
    reconcile_paper_account,
)
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile paper account state against dry-run plans.")
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--expected-cash", type=float, default=None)
    parser.add_argument("--paper-ledger-db", default=None)
    parser.add_argument("--protected-symbol", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    portfolio_state = PortfolioState.from_file(args.portfolio_state)
    action_plan = _load_json(args.action_plan) if args.action_plan else {}
    report = reconcile_paper_account(
        portfolio_state,
        action_plan=action_plan,
        expected_cash=args.expected_cash,
        protected_symbols=args.protected_symbol or portfolio_state.protected_symbols,
        paper_ledger=PaperTradeLedger(args.paper_ledger_db) if args.paper_ledger_db else None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(build_paper_reconciliation_markdown(report), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Action plan file must contain a JSON object.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
