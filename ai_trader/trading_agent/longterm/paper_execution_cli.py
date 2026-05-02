"""CLI for supervised long-term Alpaca paper execution."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
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
from longterm.paper_runbook_check import hash_action_plan
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
    parser.add_argument("--runbook-check", default="")
    parser.add_argument("--max-runbook-check-age-hours", type=int, default=24)
    parser.add_argument("--audit-output", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    broker_factory: Callable[[], PaperSubmitBroker] | None = None,
    market_clock_factory: Callable[[], bool] | None = None,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    action_plan = _load_json(args.action_plan)
    broker = None
    if args.submit_paper_orders:
        if args.confirm_paper_submit != PAPER_SUBMIT_CONFIRMATION_TOKEN:
            result = _confirmation_required_result(action_plan)
            _emit_result(result, args=args, markdown_builder=_confirmation_required_markdown)
            return 2
        runbook_check = _validate_runbook_check(
            args.runbook_check,
            action_plan=action_plan,
            max_age_hours=args.max_runbook_check_age_hours,
        )
        if runbook_check["blockers"]:
            result = _precheck_required_result(action_plan, runbook_check=runbook_check)
            _emit_result(result, args=args, markdown_builder=_precheck_required_markdown)
            return 2
        market_is_open = market_clock_factory() if market_clock_factory else _alpaca_paper_market_is_open()
        if not market_is_open:
            result = _market_closed_result(action_plan)
            _emit_result(result, args=args, markdown_builder=_market_closed_markdown)
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


def _emit_result(result: dict, *, args: argparse.Namespace, markdown_builder: Callable[[dict], str]) -> None:
    if args.audit_output:
        Path(args.audit_output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(markdown_builder(result), end="")


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


def _precheck_required_result(action_plan: dict, *, runbook_check: dict) -> dict:
    return {
        "schema_version": 1,
        "mode": "paper_execution_submit_precheck",
        "paper_mode": True,
        "live_mode": False,
        "submit_requested": True,
        "order_submission_enabled": False,
        "plan_id": str(action_plan.get("plan_id") or ""),
        "blockers": runbook_check["blockers"],
        "runbook_check": runbook_check,
        "notes": [
            "Paper submission was requested but pre-submit runbook evidence was missing or invalid.",
            "No broker state was refreshed and no broker orders were submitted.",
        ],
    }


def _market_closed_result(action_plan: dict) -> dict:
    return {
        "schema_version": 1,
        "mode": "paper_execution_market_closed",
        "paper_mode": True,
        "live_mode": False,
        "submit_requested": True,
        "order_submission_enabled": False,
        "plan_id": str(action_plan.get("plan_id") or ""),
        "blockers": ["market_closed"],
        "notes": [
            "Paper submission was requested, but the Alpaca paper market clock is closed.",
            "No paper orders were submitted.",
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


def _precheck_required_markdown(result: dict) -> str:
    blockers = result.get("blockers") or []
    lines = [
        "# Paper Execution Pre-Submit Check Required",
        "",
        "Paper submission was requested, but runbook-check evidence was missing or invalid.",
        "",
        f"- Submit requested: `{str(result.get('submit_requested')).lower()}`",
        f"- Order submission enabled: `{str(result.get('order_submission_enabled')).lower()}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {blocker}" for blocker in blockers)
    return "\n".join(lines) + "\n"


def _market_closed_markdown(result: dict) -> str:
    return "\n".join(
        [
            "# Paper Execution Market Closed",
            "",
            "Paper submission was requested, but the market is closed.",
            "",
            f"- Submit requested: `{str(result.get('submit_requested')).lower()}`",
            f"- Order submission enabled: `{str(result.get('order_submission_enabled')).lower()}`",
            "",
        ]
    )


def _validate_runbook_check(
    path: str,
    *,
    action_plan: dict,
    max_age_hours: int,
) -> dict:
    blockers: list[str] = []
    payload: dict = {}
    if not path:
        blockers.append("runbook_check_missing")
    else:
        target = Path(path)
        if not target.exists():
            blockers.append("runbook_check_missing")
        else:
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = None
            if not isinstance(loaded, dict):
                blockers.append("runbook_check_malformed")
            else:
                payload = loaded
    if payload:
        if _schema_version(payload) < 2:
            blockers.append("runbook_check_schema_too_old")
        if not bool(payload.get("ready_for_supervised_submit")):
            blockers.append("runbook_check_not_ready")
        expected_plan_id = str(action_plan.get("plan_id") or "")
        observed_plan_id = str(payload.get("plan_id") or "")
        if expected_plan_id and observed_plan_id and observed_plan_id != expected_plan_id:
            blockers.append("runbook_check_plan_mismatch")
        observed_hash = str(payload.get("action_plan_hash") or "")
        expected_hash = hash_action_plan(action_plan)
        if observed_hash and observed_hash != expected_hash:
            blockers.append("runbook_check_action_plan_hash_mismatch")
        elif not observed_hash:
            blockers.append("runbook_check_missing_action_plan_hash")
        generated_at = str(payload.get("generated_at") or "")
        if not generated_at:
            blockers.append("runbook_check_missing_generated_at")
        elif _is_stale(generated_at, max_age_hours=max_age_hours):
            blockers.append("runbook_check_stale")
    return {
        "path": path,
        "blockers": blockers,
        "payload": payload,
    }


def _schema_version(payload: dict) -> int:
    try:
        return int(payload.get("schema_version") or 1)
    except (TypeError, ValueError):
        return 1


def _is_stale(generated_at: str, *, max_age_hours: int) -> bool:
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return datetime.now(UTC) - parsed.astimezone(UTC) > timedelta(hours=max_age_hours)


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


def _alpaca_paper_market_is_open() -> bool:
    """Read the Alpaca paper clock before allowing market-order submission."""
    from brokers.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker(paper_trading=True)
    if not broker.connect():
        raise RuntimeError("Could not connect to Alpaca paper account for market clock.")
    try:
        return bool(broker.is_market_open())
    finally:
        broker.disconnect()




__all__ = ["PAPER_SUBMIT_CONFIRMATION_TOKEN", "build_parser", "main", "run_cli"]
