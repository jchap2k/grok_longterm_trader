"""CLI for pre-submit paper execution eligibility checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.paper_execution_eligibility import (
    PaperExecutionEligibilityBuilder,
    build_paper_execution_eligibility_markdown,
)
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate non-submitting paper execution eligibility.")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--max-preview-age-hours", type=int, default=24)
    parser.add_argument("--paper-execution-enabled", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    action_plan = _load_json(args.action_plan)
    payload = PaperExecutionEligibilityBuilder(
        max_preview_age_hours=args.max_preview_age_hours,
        paper_execution_enabled=args.paper_execution_enabled,
    ).build(
        action_plan,
        ledger=PaperTradeLedger(args.ledger_db),
        profile=profile,
        portfolio_state=state,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_paper_execution_eligibility_markdown(payload), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Action plan file must contain a JSON object.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
