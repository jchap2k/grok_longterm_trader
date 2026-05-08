"""Build a read-only Windows Task Scheduler registration plan."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from longterm.pipeline_scheduler_cli import parse_args as parse_scheduler_args
from longterm.pipeline_scheduler_cli import validate_resolved_scheduler_config


SUBMIT_CAPABLE_KEYS = {"submit_paper_orders", "confirm_paper_submit"}
TRADING_AGENT_DIR = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a reviewable Windows Task Scheduler plan for a no-submit scheduler profile."
    )
    parser.add_argument("--profile-file", required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--start-time", required=True, help="Daily start time in HH:MM 24-hour local time.")
    parser.add_argument("--schedule", default="DAILY", choices=["DAILY"])
    parser.add_argument("--working-dir", default=str(TRADING_AGENT_DIR))
    parser.add_argument("--python", default="python")
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_file).expanduser().resolve()
    working_dir = Path(args.working_dir).expanduser().resolve()
    profile_payload = _load_profile(profile_path)
    profile_args = _profile_args(profile_payload)
    _validate_no_submit_run_profile(profile_args)
    _validate_start_time(args.start_time)
    profile_validation = validate_resolved_scheduler_config(parse_scheduler_args(["--config-file", str(profile_path)]))

    scheduler_script = working_dir / "scripts" / "longterm_pipeline_scheduler.py"
    scheduler_command = " ".join(
        [
            subprocess.list2cmdline([str(args.python)]),
            subprocess.list2cmdline([str(scheduler_script)]),
            "--config-file",
            str(profile_path),
        ]
    )
    powershell_command = _powershell_command(
        task_name=args.task_name,
        python_exe=str(args.python),
        scheduler_script=scheduler_script,
        profile_path=profile_path,
        working_dir=working_dir,
        start_time=args.start_time,
    )
    payload = {
        "schema_version": 1,
        "mode": "windows_task_scheduler_plan",
        "status": "ready",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "task_name": str(args.task_name),
        "profile_file": str(profile_path),
        "profile_run_mode": "no-submit",
        "working_dir": str(working_dir),
        "schedule": {"type": str(args.schedule), "start_time": str(args.start_time)},
        "scheduler_command": scheduler_command,
        "profile_validation": profile_validation,
        "schtasks_command": _schtasks_command(
            task_name=args.task_name,
            start_time=args.start_time,
            scheduler_command=scheduler_command,
        ),
        "powershell_command": powershell_command,
        "order_submission_enabled": False,
        "next_safe_action": "review_task_plan_then_register_manually_if_approved",
        "notes": [
            "This artifact does not register or run a Windows task.",
            "Use only with a reviewed no-submit scheduler profile.",
        ],
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Windows Task Scheduler plan ready.")
        print(f"Task: {args.task_name}")
        print("No task was registered and no commands were executed.")
    return 0


def _load_profile(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--profile-file must contain a JSON object.")
    return payload


def _profile_args(profile_payload: dict[str, Any]) -> dict[str, Any]:
    raw_args = profile_payload.get("args", profile_payload)
    if not isinstance(raw_args, dict):
        raise ValueError("Scheduler profile args must be a JSON object.")
    return dict(raw_args)


def _validate_no_submit_run_profile(profile_args: dict[str, Any]) -> None:
    unsafe = sorted(key for key in SUBMIT_CAPABLE_KEYS if profile_args.get(key))
    if unsafe:
        raise ValueError(f"Submit-capable scheduler profile keys are not allowed: {', '.join(unsafe)}")
    if bool(profile_args.get("validate_config_only")):
        raise ValueError("Windows task plans require a reviewed no-submit run profile, not a validation-only profile.")
    if str(profile_args.get("preset") or "") != "ongoing-no-submit":
        raise ValueError("Windows task plans require preset='ongoing-no-submit'.")
    for key in ("output_dir", "journal_db", "ledger_db", "action_plan"):
        if not str(profile_args.get(key) or "").strip():
            raise ValueError(f"Scheduler profile is missing required no-submit arg: {key}")


def _validate_start_time(value: str) -> None:
    parts = str(value).split(":")
    if len(parts) != 2:
        raise ValueError("--start-time must use HH:MM format.")
    hour, minute = (int(parts[0]), int(parts[1]))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("--start-time must use HH:MM format.")


def _schtasks_command(*, task_name: str, start_time: str, scheduler_command: str) -> str:
    return " ".join(
        [
            "schtasks",
            "/Create",
            "/F",
            "/SC",
            "DAILY",
            "/TN",
            str(task_name),
            "/ST",
            str(start_time),
            "/TR",
            subprocess.list2cmdline([scheduler_command]),
        ]
    )


def _powershell_command(
    *,
    task_name: str,
    python_exe: str,
    scheduler_script: Path,
    profile_path: Path,
    working_dir: Path,
    start_time: str,
) -> str:
    argument = subprocess.list2cmdline([str(scheduler_script), "--config-file", str(profile_path)])
    return (
        f"$action = New-ScheduledTaskAction -Execute {subprocess.list2cmdline([python_exe])} "
        f"-Argument {subprocess.list2cmdline([argument])} "
        f"-WorkingDirectory {subprocess.list2cmdline([str(working_dir)])}; "
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {start_time}; "
        f"Register-ScheduledTask -TaskName {subprocess.list2cmdline([task_name])} "
        "-Action $action -Trigger $trigger -Description "
        f"{subprocess.list2cmdline(['Long-term trader no-submit scheduler.'])}"
    )


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
