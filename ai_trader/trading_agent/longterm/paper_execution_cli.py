"""CLI for supervised long-term Alpaca paper execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.paper_execution import (
    AlpacaPaperSubmitAdapter,
    PaperExecutionBoundary,
    PaperSubmitBroker,
    build_paper_execution_markdown,
)
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.decision_journal import LongTermDecisionJournal
from portfolio.portfolio_profile import PortfolioProfile

PAPER_SUBMIT_CONFIRMATION_TOKEN = "SUPERVISED_PAPER_BUY_ONLY"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the supervised long-term paper execution boundary.")
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--max-preview-age-hours", type=int, default=24)
    parser.add_argument("--submit-paper-orders", action="store_true")
    parser.add_argument("--confirm-paper-submit", default="")
    parser.add_argument("--audit-output", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    broker_factory: Callable[[], PaperSubmitBroker] | None = None,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    action_plan = _load_json(args.action_plan)
    broker = None
    if args.submit_paper_orders:
        if args.confirm_paper_submit != PAPER_SUBMIT_CONFIRMATION_TOKEN:
            result = _confirmation_required_result(action_plan)
            if args.audit_output:
                Path(args.audit_output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_confirmation_required_markdown(result), end="")
            return 2
        if broker_factory:
            broker = broker_factory()
        else:
            state = _fresh_alpaca_paper_state(profile)
            broker = AlpacaPaperSubmitAdapter.from_env()
    result = PaperExecutionBoundary(max_preview_age_hours=args.max_preview_age_hours).run(
        action_plan,
        journal=LongTermDecisionJournal(args.journal_db),
        ledger=PaperTradeLedger(args.ledger_db),
        profile=profile,
        portfolio_state=state,
        broker=broker,
        submit=args.submit_paper_orders,
        audit_output=args.audit_output,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(build_paper_execution_markdown(result), end="")
    return 0 if result.get("rejected_count", 0) == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Action plan file must contain a JSON object.")
    return payload


def _confirmation_required_result(action_plan: dict) -> dict:
    return {
        "schema_version": 1,
        "mode": "paper_execution_submit_confirmation",
        "paper_mode": True,
        "live_mode": False,
        "submit_requested": True,
        "order_submission_enabled": False,
        "plan_id": str(action_plan.get("plan_id") or ""),
        "blockers": ["missing_or_invalid_confirm_paper_submit"],
        "required_confirmation": PAPER_SUBMIT_CONFIRMATION_TOKEN,
        "notes": [
            "Paper submission was requested but the confirmation token was missing or invalid.",
            "No broker state was refreshed and no broker orders were submitted.",
        ],
    }


def _confirmation_required_markdown(result: dict) -> str:
    return "\n".join(
        [
            "# Paper Execution Confirmation Required",
            "",
            "Paper submission was requested, but the confirmation token was missing or invalid.",
            "",
            f"- Submit requested: `{str(result.get('submit_requested')).lower()}`",
            f"- Order submission enabled: `{str(result.get('order_submission_enabled')).lower()}`",
            f"- Required confirmation: `{result.get('required_confirmation')}`",
            "",
        ]
    )


def _fresh_alpaca_paper_state(profile: PortfolioProfile) -> PortfolioState:
    """Read fresh Alpaca paper state before the real submit adapter is used."""
    from brokers.alpaca_broker import AlpacaBroker
    from longterm.alpaca_paper_account import (
        AlpacaPaperAccountReader,
        paper_account_snapshot_to_portfolio_state,
    )

    snapshot = AlpacaPaperAccountReader(
        broker=AlpacaBroker(paper_trading=True),
        paper_trading=True,
    ).read_snapshot(profile=profile)
    return paper_account_snapshot_to_portfolio_state(snapshot)




__all__ = ["PAPER_SUBMIT_CONFIRMATION_TOKEN", "build_parser", "main", "run_cli"]
