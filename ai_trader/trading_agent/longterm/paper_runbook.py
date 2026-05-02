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
) -> dict[str, Any]:
    """Build ordered operator commands for a supervised paper-trading smoke."""
    artifacts = {
        "paper_snapshot": _artifact(output_dir, "paper_snapshot.json"),
        "workflow_smoke": _artifact(output_dir, "paper_workflow_smoke.json"),
        "paper_smoke_readiness": _artifact(output_dir, "paper_smoke_readiness.json"),
        "paper_execution_audit": _artifact(output_dir, "paper_execution_audit.json"),
        "paper_trading_observed": _artifact(output_dir, "paper_trading_observed.json"),
        "live_readiness_bundle": _artifact(output_dir, "live_readiness_bundle.json"),
    }
    expected_cash_arg = f" --expected-cash {_format_number(expected_cash)}" if expected_cash is not None else ""
    steps = [
        {
            "step_id": "snapshot",
            "title": "Export fresh Alpaca paper portfolio state",
            "command": (
                "python scripts/longterm_alpaca_paper_snapshot.py "
                f"--portfolio-state-output {portfolio_state}"
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
                f"--report-output {artifacts['workflow_smoke']} --json"
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
            "step_id": "supervised_submit",
            "title": "Submit explicitly confirmed simple BUY paper orders",
            "command": (
                "python scripts/longterm_paper_execution.py "
                f"--journal-db {journal_db} --ledger-db {ledger_db} "
                f"--portfolio-state {portfolio_state} --action-plan {action_plan} "
                "--submit-paper-orders --confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY "
                f"--audit-output {artifacts['paper_execution_audit']} --json"
            ),
        },
        {
            "step_id": "status_refresh",
            "title": "Refresh submitted paper order statuses",
            "command": f"python scripts/longterm_paper_order_status_refresh.py --ledger-db {ledger_db} --json",
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
    ]
    return {
        "schema_version": 1,
        "mode": "paper_runbook",
        "order_submission_enabled": False,
        "expected_cash": expected_cash,
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
