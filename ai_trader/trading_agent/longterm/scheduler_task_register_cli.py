"""Guarded Windows Task Scheduler registration from reviewed no-submit handoff."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping


CONFIRM_REGISTER_TOKEN = "NO_SUBMIT_SCHEDULER_REGISTER"
CommandRunner = Callable[[str], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or explicitly register a reviewed no-submit Windows Task Scheduler plan."
    )
    parser.add_argument("--scheduler-handoff", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--confirm-register", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, command_runner: CommandRunner | None = None) -> int:
    handoff_path = Path(args.scheduler_handoff).expanduser().resolve()
    handoff = _load_json(handoff_path)
    _validate_handoff_ready(handoff)
    task_plan_path = Path(str(handoff.get("scheduler_task_plan") or "")).expanduser().resolve()
    task_plan = _load_json(task_plan_path)
    _validate_task_plan_ready(task_plan)
    registration_command = str(task_plan.get("schtasks_command") or "").strip()
    if not registration_command:
        raise ValueError("Scheduler task plan is missing schtasks_command.")
    if args.register and args.confirm_register != CONFIRM_REGISTER_TOKEN:
        raise ValueError(f"--register requires --confirm-register {CONFIRM_REGISTER_TOKEN}.")

    runner = command_runner or _run_registration_command
    exit_code: int | None = None
    if args.register:
        exit_code = runner(registration_command)

    payload = {
        "schema_version": 1,
        "mode": "windows_task_scheduler_registration_review",
        "status": _status(args.register, exit_code),
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "scheduler_handoff": str(handoff_path),
        "scheduler_task_plan": str(task_plan_path),
        "task_name": str(task_plan.get("task_name") or ""),
        "registration_command": registration_command,
        "powershell_command": str(task_plan.get("powershell_command") or ""),
        "registration_requested": bool(args.register),
        "registration_executed": bool(args.register and exit_code == 0),
        "registration_exit_code": exit_code,
        "order_submission_enabled": False,
        "next_safe_action": (
            "monitor_no_submit_scheduler_task"
            if args.register and exit_code == 0
            else "rerun_with_confirm_register_only_if_operator_approves_windows_task"
        ),
        "notes": [
            "Default mode is dry-run; no Windows task is registered unless --register and the confirmation token are supplied.",
            "This registers the no-submit scheduler only; it does not authorize paper or live order submission.",
        ],
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Scheduler task registration {payload['status']}.")
        if not args.register:
            print("Dry run only; no Windows task was registered.")
    return 0 if payload["status"] in {"ready_for_registration_review", "registered"} else 1


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _validate_handoff_ready(handoff: Mapping[str, Any]) -> None:
    if str(handoff.get("status") or "").lower().strip() != "ready" or handoff.get("ready") is not True:
        raise ValueError("Scheduler handoff is not ready for task registration.")
    if bool(handoff.get("order_submission_enabled")):
        raise ValueError("Scheduler handoff unexpectedly enables order submission.")
    checks = handoff.get("checks") if isinstance(handoff.get("checks"), Mapping) else {}
    required_ready_checks = (
        "scheduler_config_validation",
        "recurring_no_submit_readiness",
        "scheduler_task_plan",
        "dashboard_manifest",
        "order_submission_boundary",
    )
    blocked = [key for key in required_ready_checks if checks.get(key) != "ready"]
    if blocked:
        raise ValueError("Scheduler handoff is not ready; blocked checks: " + ", ".join(blocked))


def _validate_task_plan_ready(task_plan: Mapping[str, Any]) -> None:
    if str(task_plan.get("status") or "").lower().strip() != "ready":
        raise ValueError("Scheduler task plan is not ready.")
    if bool(task_plan.get("order_submission_enabled")):
        raise ValueError("Scheduler task plan unexpectedly enables order submission.")
    if str(task_plan.get("profile_run_mode") or "no-submit") != "no-submit":
        raise ValueError("Scheduler task registration requires a no-submit run profile.")


def _status(register: bool, exit_code: int | None) -> str:
    if not register:
        return "ready_for_registration_review"
    return "registered" if exit_code == 0 else "registration_failed"


def _run_registration_command(command: str) -> int:
    completed = subprocess.run(command, shell=True, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "Windows Task Scheduler registration failed: "
            + (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")
        )
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["CONFIRM_REGISTER_TOKEN", "build_parser", "main", "run_cli"]
