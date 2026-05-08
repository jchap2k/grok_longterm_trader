"""Validate the reviewed scheduler handoff artifact chain."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check scheduler validation/task/dashboard handoff artifacts.")
    parser.add_argument("--scheduler-config-validation", required=True)
    parser.add_argument("--scheduler-task-plan", required=True)
    parser.add_argument("--dashboard-manifest", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    validation_path = Path(args.scheduler_config_validation).expanduser().resolve()
    task_plan_path = Path(args.scheduler_task_plan).expanduser().resolve()
    manifest_path = Path(args.dashboard_manifest).expanduser().resolve()
    validation = _load_json(validation_path)
    task_plan = _load_json(task_plan_path)
    manifest = _load_json(manifest_path)
    blockers = _blockers(
        validation=validation,
        validation_path=validation_path,
        task_plan=task_plan,
        task_plan_path=task_plan_path,
        manifest=manifest,
        manifest_path=manifest_path,
    )
    ready = not blockers
    payload = {
        "schema_version": 1,
        "mode": "scheduler_handoff_check",
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "scheduler_config_validation": str(validation_path),
        "scheduler_task_plan": str(task_plan_path),
        "dashboard_manifest": str(manifest_path),
        "task_name": str(task_plan.get("task_name") or ""),
        "profile_file": str(task_plan.get("profile_file") or validation.get("config_file") or ""),
        "checks": {
            "scheduler_config_validation": "ready" if _is_ready(validation) else "blocked",
            "scheduler_task_plan": "ready" if _is_ready(task_plan) else "blocked",
            "dashboard_manifest": "ready" if not _manifest_blockers(manifest, validation_path, task_plan_path) else "blocked",
        },
        "blockers": blockers,
        "order_submission_enabled": False,
        "next_safe_action": (
            "review_dashboard_then_register_task_manually_if_approved"
            if ready
            else "resolve_scheduler_handoff_blockers_before_registering_task"
        ),
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Scheduler handoff {payload['status']}.")
        if blockers:
            print("Blockers: " + ", ".join(blockers))
    return 0 if ready else 1


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _blockers(
    *,
    validation: Mapping[str, Any],
    validation_path: Path,
    task_plan: Mapping[str, Any],
    task_plan_path: Path,
    manifest: Mapping[str, Any],
    manifest_path: Path,
) -> list[str]:
    blockers: list[str] = []
    if not _is_ready(validation):
        blockers.append("scheduler_config_validation_not_ready")
    if not _is_ready(task_plan):
        blockers.append("scheduler_task_plan_not_ready")
    if bool(validation.get("order_submission_enabled")) or bool(task_plan.get("order_submission_enabled")):
        blockers.append("order_submission_enabled_unexpected")
    profile_from_validation = str(validation.get("config_file") or "").strip()
    profile_from_task = str(task_plan.get("profile_file") or "").strip()
    if profile_from_validation and profile_from_task and Path(profile_from_validation).resolve() != Path(profile_from_task).resolve():
        blockers.append("profile_file_mismatch")
    blockers.extend(_manifest_blockers(manifest, validation_path, task_plan_path))
    if bool(manifest.get("order_submission_enabled")):
        blockers.append("dashboard_manifest_order_submission_enabled")
    if not manifest_path.exists():
        blockers.append("dashboard_manifest_missing")
    return sorted(set(blockers))


def _manifest_blockers(manifest: Mapping[str, Any], validation_path: Path, task_plan_path: Path) -> list[str]:
    blockers: list[str] = []
    manifest_validation = str(manifest.get("scheduler_config_validation") or "").strip()
    manifest_task_plan = str(manifest.get("scheduler_task_plan") or "").strip()
    if not manifest_validation or Path(manifest_validation).resolve() != validation_path:
        blockers.append("dashboard_manifest_validation_mismatch")
    if not manifest_task_plan or Path(manifest_task_plan).resolve() != task_plan_path:
        blockers.append("dashboard_manifest_task_plan_mismatch")
    return blockers


def _is_ready(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status") or "").lower().strip() == "ready"


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
