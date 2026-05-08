"""Read-only verification for no-submit pipeline scheduler runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUBMIT_CAPABLE_FRAGMENTS = (
    "--submit-paper-orders",
    "--confirm-paper-submit",
    "supervised_paper",
    "longterm_paper_execution.py",
    "paper_execute",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a saved no-submit scheduler run without rerunning broker actions."
    )
    parser.add_argument("--pipeline-scheduler-summary", required=True)
    parser.add_argument("--pipeline-summary", default="")
    parser.add_argument("--policy-state", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--min-runs", type=int, default=1)
    parser.add_argument("--require-resource-bounded", action="store_true")
    parser.add_argument("--require-final-planning-bound", action="store_true")
    parser.add_argument(
        "--require-policy-timestamp",
        action="append",
        default=[],
        help=(
            "Require a timestamp key in scheduler_policy_state.json, for example "
            "last_no_submit_preflight_at."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = build_scheduler_cadence_verification_report(
        pipeline_scheduler_summary=args.pipeline_scheduler_summary,
        pipeline_summary=args.pipeline_summary,
        policy_state=args.policy_state,
        min_runs=args.min_runs,
        require_resource_bounded=args.require_resource_bounded,
        require_final_planning_bound=args.require_final_planning_bound,
        required_policy_timestamps=args.require_policy_timestamp,
    )
    if args.report_output:
        output = Path(args.report_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Scheduler cadence verification: {report['status']}")
        if report["blockers"]:
            print("Blockers: " + ", ".join(report["blockers"]))
    return 0 if report["status"] == "ready" else 1


def build_scheduler_cadence_verification_report(
    *,
    pipeline_scheduler_summary: str | Path,
    pipeline_summary: str | Path = "",
    policy_state: str | Path = "",
    min_runs: int = 1,
    require_resource_bounded: bool = False,
    require_final_planning_bound: bool = False,
    required_policy_timestamps: list[str] | None = None,
) -> dict[str, Any]:
    scheduler_summary_path = Path(pipeline_scheduler_summary)
    scheduler_summary = _read_json_object(scheduler_summary_path)
    blockers: list[str] = []
    warnings: list[str] = []

    runs = scheduler_summary.get("runs") if isinstance(scheduler_summary.get("runs"), list) else []
    latest_run = _latest_mapping(runs)
    resource_controls = latest_run.get("resource_controls") if latest_run else {}
    if not isinstance(resource_controls, dict):
        resource_controls = {}

    _check_scheduler_summary(
        scheduler_summary=scheduler_summary,
        runs=runs,
        min_runs=min_runs,
        blockers=blockers,
    )
    _check_latest_run(latest_run=latest_run, blockers=blockers)
    _check_no_submit_commands(latest_run=latest_run, blockers=blockers)
    _check_resource_controls(
        resource_controls=resource_controls,
        require_resource_bounded=require_resource_bounded,
        require_final_planning_bound=require_final_planning_bound,
        blockers=blockers,
        warnings=warnings,
    )

    resolved_pipeline_summary_path = _resolve_pipeline_summary_path(pipeline_summary, latest_run)
    pipeline = _read_json_object(resolved_pipeline_summary_path) if resolved_pipeline_summary_path else {}
    _check_pipeline_summary(pipeline=pipeline, blockers=blockers)

    policy_state_path = Path(policy_state) if str(policy_state or "").strip() else _default_policy_state_path(
        scheduler_summary_path
    )
    policy = _read_json_object(policy_state_path)
    _check_policy_state(
        policy_state=policy,
        required_policy_timestamps=required_policy_timestamps or [],
        blockers=blockers,
        warnings=warnings,
    )

    dashboard_manifest = _latest_run_path(latest_run, "dashboard_manifest.json")
    if dashboard_manifest and not dashboard_manifest.exists():
        warnings.append("latest_dashboard_manifest_missing")

    status = "ready" if not blockers else "attention_required"
    return {
        "schema_version": 1,
        "mode": "pipeline_scheduler_cadence_verification",
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "pipeline_scheduler_summary": str(scheduler_summary_path),
        "pipeline_summary": str(resolved_pipeline_summary_path or ""),
        "policy_state": str(policy_state_path),
        "scheduler_status": str(scheduler_summary.get("status", "")),
        "run_count": len(runs),
        "success_count": _int_value(scheduler_summary.get("success_count")),
        "error_count": _int_value(scheduler_summary.get("error_count")),
        "order_submission_enabled": bool(scheduler_summary.get("order_submission_enabled", False)),
        "latest_run": {
            "run_number": latest_run.get("run_number", ""),
            "status": latest_run.get("status", ""),
            "pipeline_exit_code": latest_run.get("pipeline_exit_code"),
            "position_review_queue_exit_code": latest_run.get("position_review_queue_exit_code"),
            "scheduler_policy_exit_code": latest_run.get("scheduler_policy_exit_code"),
            "account_refresh_exit_code": latest_run.get("account_refresh_exit_code"),
            "blocker": latest_run.get("blocker", ""),
        }
        if latest_run
        else {},
        "resource_controls": resource_controls,
        "policy_state_timestamps": {
            key: policy.get(key, "")
            for key in (
                "last_full_research_at",
                "last_no_submit_preflight_at",
                "last_account_refresh_at",
                "last_final_planning_at",
                "last_news_monitor_at",
                "last_position_review_at",
                "last_followup_batch_split_at",
                "last_followup_committee_at",
            )
            if key in policy
        },
        "dashboard_manifest": str(dashboard_manifest or ""),
        "next_safe_action": (
            "review_scheduler_verification_blockers"
            if blockers
            else "scheduler_run_verified_for_no_submit_cadence"
        ),
    }


def _check_scheduler_summary(
    *,
    scheduler_summary: dict[str, Any],
    runs: list[Any],
    min_runs: int,
    blockers: list[str],
) -> None:
    if scheduler_summary.get("status") != "completed":
        blockers.append("scheduler_status_not_completed")
    if bool(scheduler_summary.get("order_submission_enabled", False)):
        blockers.append("scheduler_order_submission_enabled")
    if _int_value(scheduler_summary.get("error_count")) != 0:
        blockers.append("scheduler_error_count_nonzero")
    if len(runs) < max(1, min_runs):
        blockers.append("scheduler_run_count_below_minimum")
    if _int_value(scheduler_summary.get("success_count")) < max(1, min_runs):
        blockers.append("scheduler_success_count_below_minimum")


def _check_latest_run(*, latest_run: dict[str, Any], blockers: list[str]) -> None:
    if not latest_run:
        blockers.append("latest_run_missing")
        return
    if latest_run.get("status") != "completed":
        blockers.append("latest_run_not_completed")
    if str(latest_run.get("blocker", "")).strip():
        blockers.append("latest_run_blocker_present")
    for key in (
        "pre_pipeline_refresh_exit_code",
        "position_review_queue_exit_code",
        "pipeline_exit_code",
        "scheduler_policy_exit_code",
        "account_refresh_exit_code",
    ):
        value = latest_run.get(key)
        if value is not None and _int_value(value) != 0:
            blockers.append(f"{key}_nonzero")


def _check_no_submit_commands(*, latest_run: dict[str, Any], blockers: list[str]) -> None:
    if not latest_run:
        return
    command_text = "\n".join(
        str(latest_run.get(key, ""))
        for key in (
            "pre_pipeline_refresh_command",
            "pipeline_command",
            "committee_preset_policy_command",
            "scheduler_policy_command",
            "portfolio_news_monitor_command",
            "position_review_queue_command",
            "post_run_verification_command",
            "account_refresh_command",
        )
    ).lower()
    if any(fragment in command_text for fragment in SUBMIT_CAPABLE_FRAGMENTS):
        blockers.append("submit_capable_command_fragment_present")


def _check_resource_controls(
    *,
    resource_controls: dict[str, Any],
    require_resource_bounded: bool,
    require_final_planning_bound: bool,
    blockers: list[str],
    warnings: list[str],
) -> None:
    if not resource_controls:
        warnings.append("resource_controls_missing")
        return
    if require_resource_bounded and not bool(resource_controls.get("bounded", False)):
        blockers.append("resource_controls_not_bounded")
    if bool(resource_controls.get("final_planning_refresh", False)):
        if resource_controls.get("final_planning_timeout_seconds") in (None, "", 0):
            blockers.append("final_planning_refresh_without_timeout")
    elif require_final_planning_bound:
        blockers.append("final_planning_refresh_missing")


def _check_pipeline_summary(*, pipeline: dict[str, Any], blockers: list[str]) -> None:
    if not pipeline:
        blockers.append("pipeline_summary_missing")
        return
    if pipeline.get("status") != "completed":
        blockers.append("pipeline_status_not_completed")
    if bool(pipeline.get("order_submission_enabled", False)):
        blockers.append("pipeline_order_submission_enabled")
    if _int_value(pipeline.get("blocker_count")) != 0:
        blockers.append("pipeline_blocker_count_nonzero")
    for stage in pipeline.get("stages", []):
        if not isinstance(stage, dict):
            continue
        if stage.get("status") not in {"passed", "skipped"}:
            blockers.append(f"pipeline_stage_not_passed:{stage.get('stage_id', '')}")
    rollup = pipeline.get("artifact_rollup") if isinstance(pipeline.get("artifact_rollup"), dict) else {}
    workflow = rollup.get("workflow_smoke") if isinstance(rollup.get("workflow_smoke"), dict) else {}
    if _int_value(workflow.get("submitted_count")) != 0:
        blockers.append("workflow_smoke_submitted_count_nonzero")


def _check_policy_state(
    *,
    policy_state: dict[str, Any],
    required_policy_timestamps: list[str],
    blockers: list[str],
    warnings: list[str],
) -> None:
    if not policy_state:
        if required_policy_timestamps:
            blockers.append("policy_state_missing")
            for key in required_policy_timestamps:
                blockers.append(f"policy_timestamp_missing:{key}")
        else:
            warnings.append("policy_state_missing")
        return
    for key in required_policy_timestamps:
        if not str(policy_state.get(key, "")).strip():
            blockers.append(f"policy_timestamp_missing:{key}")


def _resolve_pipeline_summary_path(path: str | Path, latest_run: dict[str, Any]) -> Path | None:
    if str(path or "").strip():
        return Path(path)
    candidate = latest_run.get("pipeline_summary_path") if latest_run else ""
    return Path(candidate) if str(candidate or "").strip() else None


def _default_policy_state_path(scheduler_summary_path: Path) -> Path:
    return scheduler_summary_path.parent / "scheduler_policy_state.json"


def _latest_run_path(latest_run: dict[str, Any], filename: str) -> Path | None:
    run_dir = latest_run.get("run_dir") if latest_run else ""
    return Path(run_dir) / filename if str(run_dir or "").strip() else None


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"status": "malformed_json", "error": str(exc)}
    return data if isinstance(data, dict) else {}


def _latest_mapping(items: list[Any]) -> dict[str, Any]:
    for item in reversed(items):
        if isinstance(item, dict):
            return item
    return {}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = [
    "build_parser",
    "build_scheduler_cadence_verification_report",
    "main",
    "run_cli",
]
