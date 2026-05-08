"""Read-only readiness gate for a future supervised paper-submit profile."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping


NowFunc = Callable[[], datetime]


@dataclass(frozen=True)
class PaperSubmitModePlanInputs:
    """Artifacts required before a submit-capable profile may be considered."""

    scheduler_handoff: str | Path
    pipeline_scheduler_summary: str | Path
    position_review_queue: str | Path
    max_handoff_age_hours: int = 24


def build_paper_submit_mode_plan(
    inputs: PaperSubmitModePlanInputs,
    *,
    now_func: NowFunc | None = None,
) -> dict[str, Any]:
    """Validate readiness gates without creating commands or touching a broker."""
    now = (now_func or _utc_now)()
    handoff_path = Path(inputs.scheduler_handoff)
    scheduler_path = Path(inputs.pipeline_scheduler_summary)
    position_queue_path = Path(inputs.position_review_queue)
    handoff = _read_json_object(handoff_path)
    scheduler = _read_json_object(scheduler_path)
    position_queue = _read_json_object(position_queue_path)
    blockers: list[str] = []
    warnings: list[str] = []

    _check_handoff(
        handoff=handoff,
        max_handoff_age_hours=inputs.max_handoff_age_hours,
        now=now,
        blockers=blockers,
        warnings=warnings,
    )
    _check_scheduler_summary(scheduler=scheduler, blockers=blockers)
    _check_position_review_queue(position_queue=position_queue, blockers=blockers)
    _check_submission_boundary(
        handoff=handoff,
        scheduler=scheduler,
        position_queue=position_queue,
        blockers=blockers,
    )

    status = "ready_for_manual_review" if not blockers else "blocked"
    return {
        "schema_version": 1,
        "mode": "paper_submit_mode_plan",
        "status": status,
        "generated_at": _format_timestamp(now),
        "scheduler_handoff": str(handoff_path),
        "pipeline_scheduler_summary": str(scheduler_path),
        "position_review_queue": str(position_queue_path),
        "checks": {
            "scheduler_handoff": "ready" if _handoff_ready(handoff, blockers) else "blocked",
            "no_submit_scheduler_summary": "ready" if _scheduler_ready(scheduler, blockers) else "blocked",
            "position_review_queue": "ready" if _position_queue_ready(position_queue, blockers) else "blocked",
            "order_submission_boundary": "ready" if not _has_submission_boundary_blocker(blockers) else "blocked",
        },
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "order_submission_enabled": False,
        "submit_profile_enabled": False,
        "broker_calls_enabled": False,
        "llm_calls_enabled": False,
        "runnable_submit_command_emitted": False,
        "next_safe_action": (
            "manual_review_required_before_submit_profile"
            if not blockers
            else "resolve_submit_mode_plan_blockers_before_submit_profile"
        ),
        "notes": [
            "This artifact is a readiness checklist only.",
            "It never emits a submit command, enables a submit profile, or calls Alpaca.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a disabled paper-submit readiness plan.")
    parser.add_argument("--scheduler-handoff", required=True)
    parser.add_argument("--pipeline-scheduler-summary", required=True)
    parser.add_argument("--position-review-queue", required=True)
    parser.add_argument("--max-handoff-age-hours", type=int, default=24)
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=args.scheduler_handoff,
            pipeline_scheduler_summary=args.pipeline_scheduler_summary,
            position_review_queue=args.position_review_queue,
            max_handoff_age_hours=args.max_handoff_age_hours,
        )
    )
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Paper submit-mode plan: {report['status']}")
        if report["blockers"]:
            print("Blockers: " + ", ".join(report["blockers"]))
    return 0 if report["status"] == "ready_for_manual_review" else 1


def _check_handoff(
    *,
    handoff: Mapping[str, Any],
    max_handoff_age_hours: int,
    now: datetime,
    blockers: list[str],
    warnings: list[str],
) -> None:
    if not handoff:
        blockers.append("scheduler_handoff_missing_or_unreadable")
        return
    if handoff.get("status") != "ready" or handoff.get("ready") is not True:
        blockers.append("scheduler_handoff_not_ready")
    checks = handoff.get("checks") if isinstance(handoff.get("checks"), Mapping) else {}
    if checks.get("order_submission_boundary") != "ready":
        blockers.append("scheduler_handoff_order_submission_boundary_not_ready")
    generated_at = _parse_timestamp(str(handoff.get("generated_at") or ""))
    if generated_at is None:
        warnings.append("scheduler_handoff_generated_at_missing_or_invalid")
    elif max_handoff_age_hours > 0 and now.astimezone(UTC) - generated_at > _hours(max_handoff_age_hours):
        blockers.append("scheduler_handoff_stale")


def _check_scheduler_summary(*, scheduler: Mapping[str, Any], blockers: list[str]) -> None:
    if not scheduler:
        blockers.append("pipeline_scheduler_summary_missing_or_unreadable")
        return
    if scheduler.get("status") != "completed":
        blockers.append("pipeline_scheduler_summary_not_completed")
    if _int_value(scheduler.get("success_count")) < 1:
        blockers.append("pipeline_scheduler_success_count_missing")
    if _int_value(scheduler.get("error_count")) != 0:
        blockers.append("pipeline_scheduler_error_count_nonzero")
    latest = _latest_mapping(scheduler.get("runs") if isinstance(scheduler.get("runs"), list) else [])
    if latest and latest.get("position_review_queue_exit_code") not in (None, 0):
        blockers.append("position_review_queue_stage_failed")


def _check_position_review_queue(*, position_queue: Mapping[str, Any], blockers: list[str]) -> None:
    if not position_queue:
        blockers.append("position_review_queue_missing_or_unreadable")
        return
    if position_queue.get("status") != "completed":
        blockers.append("position_review_queue_not_completed")
    if position_queue.get("mode") != "position_review_queue":
        blockers.append("position_review_queue_mode_unexpected")


def _check_submission_boundary(
    *,
    handoff: Mapping[str, Any],
    scheduler: Mapping[str, Any],
    position_queue: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if bool(handoff.get("order_submission_enabled")):
        blockers.append("submission_boundary_scheduler_handoff_enabled")
    if bool(scheduler.get("order_submission_enabled")):
        blockers.append("submission_boundary_scheduler_summary_enabled")
    if bool(position_queue.get("order_submission_enabled")):
        blockers.append("submission_boundary_position_review_queue_enabled")
    if bool(position_queue.get("broker_calls_enabled")):
        blockers.append("broker_boundary_position_review_queue_enabled")
    if bool(position_queue.get("llm_calls_enabled")):
        blockers.append("llm_boundary_position_review_queue_enabled")


def _handoff_ready(handoff: Mapping[str, Any], blockers: list[str]) -> bool:
    if not handoff:
        return False
    return not any(blocker.startswith("scheduler_handoff") for blocker in blockers)


def _scheduler_ready(scheduler: Mapping[str, Any], blockers: list[str]) -> bool:
    if not scheduler:
        return False
    return not any(blocker.startswith("pipeline_scheduler") for blocker in blockers)


def _position_queue_ready(position_queue: Mapping[str, Any], blockers: list[str]) -> bool:
    if not position_queue:
        return False
    return not any(
        blocker.startswith("position_review_queue") or blocker.endswith("position_review_queue_enabled")
        for blocker in blockers
    )


def _has_submission_boundary_blocker(blockers: list[str]) -> bool:
    return any("submission_boundary" in blocker or "broker_boundary" in blocker for blocker in blockers)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _parse_timestamp(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hours(value: int) -> timedelta:
    return timedelta(hours=max(0, value))


def _latest_mapping(items: list[Any]) -> dict[str, Any]:
    for item in reversed(items):
        if isinstance(item, Mapping):
            return dict(item)
    return {}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = [
    "PaperSubmitModePlanInputs",
    "build_paper_submit_mode_plan",
    "build_parser",
    "main",
    "run_cli",
]
