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
SUBMIT_CAPABLE_BUNDLE_FRAGMENTS = (
    "--submit-paper-orders",
    "--confirm-paper-submit",
    "longterm_paper_execution.py",
    "paper_execution.py",
    "supervised_paper",
    PAPER_SUBMIT_CONFIRMATION_TOKEN.lower(),
)


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
    parser.add_argument("--scheduler-review-bundle", default="")
    parser.add_argument("--max-scheduler-review-bundle-age-hours", type=int, default=24)
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
        scheduler_review_bundle = _validate_scheduler_review_bundle(
            args.scheduler_review_bundle,
            max_age_hours=args.max_scheduler_review_bundle_age_hours,
        )
        precheck_blockers = [*runbook_check["blockers"], *scheduler_review_bundle["blockers"]]
        if precheck_blockers:
            result = _precheck_required_result(
                action_plan,
                blockers=precheck_blockers,
                runbook_check=runbook_check,
                scheduler_review_bundle=scheduler_review_bundle,
            )
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


def _precheck_required_result(
    action_plan: dict,
    *,
    blockers: list[str],
    runbook_check: dict,
    scheduler_review_bundle: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "mode": "paper_execution_submit_precheck",
        "paper_mode": True,
        "live_mode": False,
        "submit_requested": True,
        "order_submission_enabled": False,
        "plan_id": str(action_plan.get("plan_id") or ""),
        "blockers": blockers,
        "runbook_check": runbook_check,
        "scheduler_review_bundle": scheduler_review_bundle or {"path": "", "blockers": [], "payload": {}},
        "notes": [
            "Paper submission was requested but pre-submit evidence was missing or invalid.",
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
        "Paper submission was requested, but pre-submit evidence was missing or invalid.",
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
        promotion_summary = payload.get("promotion_summary")
        if not isinstance(promotion_summary, dict):
            blockers.append("runbook_check_missing_promotion_summary")
        elif _promotion_summary_blocked_count(promotion_summary) > 0:
            blockers.append("runbook_check_buy_promotion_blockers")
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


def _validate_scheduler_review_bundle(path: str, *, max_age_hours: int) -> dict:
    blockers: list[str] = []
    payload: dict = {}
    submit_plan_payload: dict = {}
    if not path:
        return {"path": "", "blockers": [], "payload": {}, "paper_submit_mode_plan": {}}
    target = Path(path)
    if not target.exists():
        blockers.append("scheduler_review_bundle_missing")
    else:
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
        if not isinstance(loaded, dict):
            blockers.append("scheduler_review_bundle_malformed")
        else:
            payload = loaded
    if payload:
        if payload.get("status") != "ready_for_manual_review":
            blockers.append("scheduler_review_bundle_not_ready")
        for blocker in _string_list(payload.get("blockers")):
            blockers.append(f"scheduler_review_bundle:{blocker}")
        for key in (
            "order_submission_enabled",
            "submit_profile_enabled",
            "broker_calls_enabled",
            "llm_calls_enabled",
            "runnable_submit_command_emitted",
        ):
            if bool(payload.get(key)):
                blockers.append(f"scheduler_review_bundle_{key}")
        checks = payload.get("checks")
        required_checks = (
            "paper_submit_mode_plan",
            "post_run_verification",
            "scheduler_summary",
            "position_review_queue",
            "order_submission_boundary",
        )
        if not isinstance(checks, dict):
            blockers.append("scheduler_review_bundle_checks_missing")
        else:
            for check in required_checks:
                if checks.get(check) != "ready":
                    blockers.append(f"scheduler_review_bundle_check_{check}_not_ready")
        policy_summary = payload.get("scheduler_policy_summary")
        if isinstance(policy_summary, dict):
            if _int_value(policy_summary.get("blocker_count")) != 0:
                blockers.append("scheduler_review_bundle_scheduler_policy_blockers_present")
            if bool(policy_summary.get("benchmark_paused")):
                blockers.append("scheduler_review_bundle_benchmark_paused")
        else:
            blockers.append("scheduler_review_bundle_scheduler_policy_summary_missing")
        position_summary = payload.get("position_review_summary")
        if isinstance(position_summary, dict):
            if position_summary.get("status") != "completed":
                blockers.append("scheduler_review_bundle_position_review_not_completed")
            if _int_value(position_summary.get("high_priority_count")) > 0:
                blockers.append("scheduler_review_bundle_high_priority_position_reviews_present")
        else:
            blockers.append("scheduler_review_bundle_position_review_summary_missing")
        generated_at = str(payload.get("generated_at") or "")
        if not generated_at:
            blockers.append("scheduler_review_bundle_missing_generated_at")
        elif _is_stale(generated_at, max_age_hours=max_age_hours):
            blockers.append("scheduler_review_bundle_stale")

        submit_plan_payload = _load_scheduler_bundle_submit_plan(
            target,
            payload.get("paper_submit_mode_plan"),
            blockers=blockers,
        )
        _check_scheduler_bundle_submit_plan(submit_plan_payload, blockers=blockers)
        serialized = json.dumps([payload, submit_plan_payload], sort_keys=True).lower()
        if any(fragment in serialized for fragment in SUBMIT_CAPABLE_BUNDLE_FRAGMENTS):
            blockers.append("scheduler_review_bundle_submit_capable_fragment_present")
    return {
        "path": path,
        "status": str(payload.get("status") or "") if payload else "",
        "blockers": sorted(set(blockers)),
        "payload": payload,
        "paper_submit_mode_plan": submit_plan_payload,
    }


def _load_scheduler_bundle_submit_plan(bundle_path: Path, value: object, *, blockers: list[str]) -> dict:
    if not value:
        blockers.append("scheduler_review_bundle_paper_submit_mode_plan_missing")
        return {}
    target = Path(str(value))
    if not target.is_absolute():
        target = bundle_path.parent / target
    if not target.exists():
        blockers.append("scheduler_review_bundle_paper_submit_mode_plan_missing")
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        blockers.append("scheduler_review_bundle_paper_submit_mode_plan_malformed")
        return {}
    if not isinstance(payload, dict):
        blockers.append("scheduler_review_bundle_paper_submit_mode_plan_malformed")
        return {}
    return payload


def _check_scheduler_bundle_submit_plan(plan: dict, *, blockers: list[str]) -> None:
    if not plan:
        return
    if plan.get("status") != "ready_for_manual_review":
        blockers.append("scheduler_review_bundle_paper_submit_mode_plan_not_ready")
    for key in (
        "order_submission_enabled",
        "submit_profile_enabled",
        "broker_calls_enabled",
        "llm_calls_enabled",
        "runnable_submit_command_emitted",
    ):
        if bool(plan.get(key)):
            blockers.append(f"scheduler_review_bundle_paper_submit_mode_plan_{key}")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _schema_version(payload: dict) -> int:
    try:
        return int(payload.get("schema_version") or 1)
    except (TypeError, ValueError):
        return 1


def _promotion_summary_blocked_count(summary: dict) -> int:
    total = 0
    for key in ("workflow_blocked_count", "readiness_blocked_count"):
        try:
            total += int(summary.get(key) or 0)
        except (TypeError, ValueError):
            total += 1
    return total


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
