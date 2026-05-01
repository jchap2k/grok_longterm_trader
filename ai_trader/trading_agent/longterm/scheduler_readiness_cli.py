"""CLI for advisory long-term scheduler readiness reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from longterm.scheduler_readiness import (
    build_scheduler_readiness_markdown,
    build_scheduler_readiness_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render advisory scheduler readiness checks.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--portfolio-state", default=None)
    parser.add_argument("--action-plan", default=None)
    parser.add_argument("--feedback-summary", default=None)
    parser.add_argument("--paper-lifecycle-summary", default=None)
    parser.add_argument("--active-rules", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    payload = build_scheduler_readiness_report(
        LongTermDecisionJournal(args.journal_db),
        portfolio_state=PortfolioState.from_file(args.portfolio_state) if args.portfolio_state else None,
        action_plan=_load_json(args.action_plan) if args.action_plan else None,
        feedback_summary=_load_json(args.feedback_summary) if args.feedback_summary else None,
        paper_lifecycle_summary=_load_json(args.paper_lifecycle_summary) if args.paper_lifecycle_summary else None,
        active_rules_path=args.active_rules if args.active_rules else None,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_scheduler_readiness_markdown(payload), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
