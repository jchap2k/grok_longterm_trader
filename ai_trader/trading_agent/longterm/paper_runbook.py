"""Read-only Monday paper-trading runbook generator."""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any, Mapping


def build_paper_runbook(
    *,
    journal_db: str,
    ledger_db: str,
    portfolio_state: str,
    action_plan: str,
    output_dir: str,
    expected_cash: float | None = None,
    profile_config: str = "",
    include_submit_command: bool = False,
) -> dict[str, Any]:
    """Build ordered operator commands for a supervised paper-trading smoke."""
    artifacts = {
        "paper_runbook": _artifact(output_dir, "paper_runbook.json"),
        "paper_snapshot": _artifact(output_dir, "paper_snapshot.json"),
        "workflow_smoke": _artifact(output_dir, "paper_workflow_smoke.json"),
        "paper_smoke_readiness": _artifact(output_dir, "paper_smoke_readiness.json"),
        "runbook_check": _artifact(output_dir, "paper_runbook_check.json"),
        "paper_monday_operator_check": _artifact(output_dir, "paper_monday_operator_check.json"),
        "paper_execution_audit": _artifact(output_dir, "paper_execution_audit.json"),
        "paper_order_status_refresh": _artifact(output_dir, "paper_order_status_refresh.json"),
        "paper_lifecycle": _artifact(output_dir, "paper_lifecycle.json"),
        "paper_trading_observed": _artifact(output_dir, "paper_trading_observed.json"),
        "live_readiness_bundle": _artifact(output_dir, "live_readiness_bundle.json"),
        "operator_status_bundle": _artifact(output_dir, "operator_status_bundle.json"),
    }
    expected_cash_arg = f" --expected-cash {_format_number(expected_cash)}" if expected_cash is not None else ""
    profile_arg = f" --profile-config {profile_config}" if profile_config else ""
    submit_command = (
        "python scripts/longterm_paper_execution.py "
        f"--journal-db {journal_db} --ledger-db {ledger_db} "
        f"--portfolio-state {portfolio_state} --action-plan {action_plan} "
        "--submit-paper-orders --confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY "
        f"--runbook-check {artifacts['runbook_check']} "
        f"--audit-output {artifacts['paper_execution_audit']}{profile_arg} --json"
    )
    submit_step = {
        "step_id": "supervised_submit",
        "title": "Submit explicitly confirmed simple BUY paper orders",
        "command": submit_command
        if include_submit_command
        else (
            "Submit command redacted. Re-run this runbook with "
            "--include-submit-command only after saved workflow-smoke, "
            "paper-smoke-readiness, and runbook-check artifacts are reviewed."
        ),
        "requires_explicit_reveal": not include_submit_command,
    }
    steps = [
        {
            "step_id": "snapshot",
            "title": "Export fresh Alpaca paper portfolio state",
            "command": (
                "python scripts/longterm_alpaca_paper_snapshot.py "
                f"--portfolio-state-output {portfolio_state}{profile_arg}"
            ),
            "save_stdout_to": artifacts["paper_snapshot"],
        },
        {
            "step_id": "workflow_smoke",
            "title": "Run audit-only workflow smoke",
            "command": (
                "python scripts/longterm_paper_workflow_smoke.py "
                f"--journal-db {journal_db} --ledger-db {ledger_db} "
                f"--portfolio-state {portfolio_state} --action-plan {action_plan} "
                f"--report-output {artifacts['workflow_smoke']}{profile_arg} --json"
            ),
        },
        {
            "step_id": "paper_smoke_readiness",
            "title": "Build paper-smoke readiness report",
            "command": (
                "python scripts/longterm_paper_smoke_readiness.py "
                f"--portfolio-state {portfolio_state}{expected_cash_arg} "
                "--required-order-model whole_share "
                f"--workflow-smoke {artifacts['workflow_smoke']} "
                f"--report-output {artifacts['paper_smoke_readiness']} --json"
            ),
        },
        {
            "step_id": "runbook_check",
            "title": "Check saved pre-submit artifacts",
            "command": (
                "python scripts/longterm_paper_runbook_check.py "
                f"--workflow-smoke {artifacts['workflow_smoke']} "
                f"--paper-smoke-readiness {artifacts['paper_smoke_readiness']} "
                f"--action-plan {action_plan} "
                f"--report-output {artifacts['runbook_check']} --json"
            ),
        },
        {
            "step_id": "monday_operator_check",
            "title": "Summarize saved Monday paper artifacts",
            "command": (
                "python scripts/longterm_paper_monday_check.py "
                f"--runbook {artifacts['paper_runbook']} "
                f"--workflow-smoke {artifacts['workflow_smoke']} "
                f"--paper-smoke-readiness {artifacts['paper_smoke_readiness']} "
                f"--runbook-check {artifacts['runbook_check']} "
                f"--report-output {artifacts['paper_monday_operator_check']} --json"
            ),
        },
        submit_step,
        {
            "step_id": "status_refresh",
            "title": "Refresh submitted paper order statuses",
            "command": (
                "python scripts/longterm_paper_order_status_refresh.py "
                f"--ledger-db {ledger_db} "
                f"--report-output {artifacts['paper_order_status_refresh']} --json"
            ),
        },
        {
            "step_id": "paper_cleanup_reminder",
            "title": "Sell or cancel the temporary paper position manually",
            "command": (
                "Manual cleanup required: after the paper smoke is observed, "
                "sell/cancel any temporary paper position in Alpaca paper before the next run."
            ),
            "manual_step": True,
        },
        {
            "step_id": "paper_lifecycle",
            "title": "Build paper lifecycle summary",
            "command": (
                "python scripts/longterm_paper_lifecycle.py "
                f"--ledger-db {ledger_db} --report-output {artifacts['paper_lifecycle']} --json"
            ),
        },
        {
            "step_id": "paper_trading_verification",
            "title": "Generate paper-trading verification evidence",
            "command": (
                "python scripts/longterm_paper_trading_verification.py "
                f"--ledger-db {ledger_db} --observed-output {artifacts['paper_trading_observed']} --json"
            ),
        },
        {
            "step_id": "live_readiness_bundle",
            "title": "Build evidence-only live-readiness bundle",
            "command": (
                "python scripts/longterm_live_readiness_bundle.py "
                f"--paper-ledger-db {ledger_db} "
                f"--paper-smoke-readiness {artifacts['paper_smoke_readiness']} "
                "--required-order-model whole_share "
                f"--report-output {artifacts['live_readiness_bundle']} --json"
            ),
        },
        {
            "step_id": "operator_status_bundle",
            "title": "Build final read-only operator status bundle",
            "command": (
                "python scripts/longterm_operator_status_bundle.py "
                f"--journal-db {journal_db} "
                f"--portfolio-state {portfolio_state} "
                f"--paper-ledger-db {ledger_db} "
                f"--action-plan {action_plan} "
                f"--monday-operator-check {artifacts['paper_monday_operator_check']} "
                f"--live-readiness-bundle {artifacts['live_readiness_bundle']} "
                f"--status-refresh {artifacts['paper_order_status_refresh']} "
                f"--report-output {artifacts['operator_status_bundle']} --json"
            ),
        },
    ]
    return {
        "schema_version": 1,
        "mode": "paper_runbook",
        "order_submission_enabled": False,
        "expected_cash": expected_cash,
        "profile_config": profile_config,
        "include_submit_command": include_submit_command,
        "artifacts": artifacts,
        "steps": steps,
        "notes": [
            "Runbook only. It does not call brokers or submit orders.",
            "The supervised submit step still requires the explicit confirmation token.",
        ],
    }


def build_paper_runbook_markdown(runbook: Mapping[str, Any]) -> str:
    lines = [
        "# Monday Paper Trading Runbook",
        "",
        "Generated checklist only. No broker calls were made.",
        "",
        "## Steps",
        "",
    ]
    for index, step in enumerate(runbook.get("steps") or [], start=1):
        lines.extend(
            [
                f"{index}. {step.get('title')}",
                "",
                "```powershell",
                str(step.get("command") or ""),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _artifact(output_dir: str, filename: str) -> str:
    return str(PureWindowsPath(output_dir) / filename)


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


__all__ = ["build_paper_runbook", "build_paper_runbook_markdown"]
