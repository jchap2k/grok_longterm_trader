"""CLI for audit-only whole-share paper workflow smokes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from longterm.decision_journal import LongTermDecisionJournal
from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.paper_price_map_cli import _default_quote_provider
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.paper_workflow_smoke import (
    build_paper_workflow_smoke_markdown,
    build_paper_workflow_smoke_report,
)
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an audit-only whole-share paper workflow smoke.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--max-preview-age-hours", type=int, default=24)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    quote_provider_factory: Callable[[], object] | None = None,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    action_plan = _load_json(args.action_plan)
    provider = quote_provider_factory() if quote_provider_factory else _default_quote_provider()
    report = build_paper_workflow_smoke_report(
        action_plan,
        journal=LongTermDecisionJournal(args.journal_db),
        ledger=PaperTradeLedger(args.ledger_db),
        profile=profile,
        portfolio_state=state,
        quote_provider=provider,
        max_preview_age_hours=args.max_preview_age_hours,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(build_paper_workflow_smoke_markdown(report), end="")
    return 0 if report.get("ready_for_supervised_submit") else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
