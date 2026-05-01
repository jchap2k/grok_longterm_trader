"""CLI for dry-run feedback refresh maintenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.decision_journal import LongTermDecisionJournal
from longterm.feedback_refresh import build_feedback_markdown, run_feedback_refresh
from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run dry-run long-term feedback refresh.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--paper-ledger-db", default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", default=None)
    parser.add_argument("--action-plan", default=None)
    parser.add_argument("--reconciliation-file", default=None)
    parser.add_argument("--outcome-price-map", default=None)
    parser.add_argument("--lessons-file", default=None)
    parser.add_argument("--record-eligibility-events", action="store_true")
    parser.add_argument("--stale-after-days", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    if args.record_eligibility_events and not args.paper_ledger_db:
        raise ValueError("--record-eligibility-events requires --paper-ledger-db.")
    journal = LongTermDecisionJournal(args.journal_db)
    ledger = PaperTradeLedger(args.paper_ledger_db) if args.paper_ledger_db else None
    profile = PortfolioProfile.from_file(args.profile_config) if args.profile_config else None
    portfolio_state = (
        PortfolioState.from_file(args.portfolio_state, profile=profile)
        if args.portfolio_state
        else None
    )
    warnings: list[str] = []
    lessons = _load_optional_json(args.lessons_file, default=[], warnings=warnings, label="lessons")
    result = run_feedback_refresh(
        journal=journal,
        paper_ledger=ledger,
        profile=profile,
        portfolio_state=portfolio_state,
        action_plan=_load_optional_json(args.action_plan, default=None, warnings=warnings, label="action_plan"),
        reconciliation=_load_optional_json(args.reconciliation_file, default=None, warnings=warnings, label="reconciliation"),
        outcome_price_map=_load_optional_json(args.outcome_price_map, default=None, warnings=warnings, label="outcome_price_map"),
        lessons=lessons if isinstance(lessons, list) else [],
        record_eligibility_events=args.record_eligibility_events,
        stale_after_days=args.stale_after_days,
    )
    result["warnings"].extend(warnings)
    if args.json:
        print(json.dumps(_without_markdown(result), indent=2, sort_keys=True))
    else:
        print(build_feedback_markdown(result), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_optional_json(path: str | None, *, default, warnings: list[str], label: str):
    if not path:
        return default
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not load {label} file {path}: {exc}")
        return default


def _without_markdown(result: dict) -> dict:
    payload = dict(result)
    payload.pop("markdown", None)
    return payload


__all__ = ["build_parser", "main", "run_cli"]
