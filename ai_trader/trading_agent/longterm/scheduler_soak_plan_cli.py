"""CLI for previewing a one-cycle no-submit scheduler soak."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SUBMIT_CAPABLE_KEYS = {"submit_paper_orders", "confirm_paper_submit"}
TRADING_AGENT_DIR = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a no-submit scheduler soak preview without running it.")
    parser.add_argument("--profile-file", required=True)
    parser.add_argument("--working-dir", default=str(TRADING_AGENT_DIR))
    parser.add_argument("--python", default="python")
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_file).expanduser().resolve()
    working_dir = Path(args.working_dir).expanduser().resolve()
    profile = _load_profile(profile_path)
    profile_args = _profile_args(profile)
    _validate_no_submit_soak_profile(profile_args)
    max_runs = _max_runs(profile_args)
    interval_seconds = _interval_seconds(profile_args)
    scheduler_script = working_dir / "scripts" / "longterm_pipeline_scheduler.py"
    preview_command = " ".join(
        [
            subprocess.list2cmdline([str(args.python)]),
            subprocess.list2cmdline([str(scheduler_script)]),
            "--config-file",
            str(profile_path),
        ]
    )
    output_dir = str(profile_args.get("output_dir") or "")
    payload = {
        "schema_version": 1,
        "mode": "scheduler_no_submit_soak_plan",
        "status": "ready_for_no_submit_soak_review",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "profile_file": str(profile_path),
        "working_dir": str(working_dir),
        "preview_command": preview_command,
        "expected_artifacts": {
            "pipeline_scheduler_summary": str(profile_args.get("summary_output") or ""),
            "scheduler_config_validation": str(profile_args.get("scheduler_config_validation") or ""),
            "output_dir": output_dir,
        },
        "resource_controls": {
            "max_runs": max_runs,
            "interval_seconds": interval_seconds,
            "bounded": True,
        },
        "scheduler_executed": False,
        "order_submission_enabled": False,
        "next_safe_action": "operator_may_run_preview_command_for_one_no_submit_soak",
        "notes": [
            "This is a preview artifact only.",
            "It does not run the scheduler, submit orders, or register Windows tasks.",
        ],
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Scheduler soak plan {payload['status']}.")
        print("No scheduler command was executed.")
    return 0


def _load_profile(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--profile-file must contain a JSON object.")
    return payload


def _profile_args(profile: dict[str, Any]) -> dict[str, Any]:
    raw = profile.get("args", profile)
    if not isinstance(raw, dict):
        raise ValueError("Scheduler profile args must be a JSON object.")
    return dict(raw)


def _validate_no_submit_soak_profile(profile_args: dict[str, Any]) -> None:
    unsafe = sorted(key for key in SUBMIT_CAPABLE_KEYS if profile_args.get(key))
    if unsafe:
        raise ValueError("Submit-capable scheduler profile keys are not allowed: " + ", ".join(unsafe))
    if bool(profile_args.get("validate_config_only")):
        raise ValueError("Soak preview requires a no-submit run profile, not a validation-only profile.")
    if str(profile_args.get("preset") or "") != "ongoing-no-submit":
        raise ValueError("Soak preview requires preset='ongoing-no-submit'.")
    if _max_runs(profile_args) != 1:
        raise ValueError("Soak preview requires max_runs=1.")
    for key in ("output_dir", "journal_db", "ledger_db", "action_plan"):
        if not str(profile_args.get(key) or "").strip():
            raise ValueError(f"Soak profile is missing required arg: {key}")


def _max_runs(profile_args: dict[str, Any]) -> int:
    return int(profile_args.get("max_runs") or profile_args.get("max_cycles") or 0)


def _interval_seconds(profile_args: dict[str, Any]) -> float:
    if profile_args.get("interval_seconds") is not None:
        return float(profile_args.get("interval_seconds") or 0)
    return float(profile_args.get("run_interval_seconds") or 0)


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
