"""Post-scheduler no-submit review gate bundle for dashboard handoff."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from longterm.paper_submit_mode_plan import PaperSubmitModePlanInputs, build_paper_submit_mode_plan


NowFunc = Callable[[], datetime]

SUBMIT_FRAGMENTS = (
    "--submit-paper-orders",
    "--confirm-paper-submit",
    "longterm_paper_execution.py",
    "paper_execution.py",
    "supervised_paper",
)


@dataclass(frozen=True)
class SchedulerReviewBundleInputs:
    """Artifacts needed to bundle verified scheduler output into a review gate."""

    dashboard_manifest: str | Path
    scheduler_handoff: str | Path
    pipeline_scheduler_summary: str | Path
    position_review_queue: str | Path
    post_run_verification: str | Path
    output_dir: str | Path
    max_handoff_age_hours: int = 24
    buy_promotion_artifact: str | Path = ""
    final_action_plan: str | Path = ""
    min_clean_scheduler_runs: int = 3


def build_scheduler_review_bundle(
    inputs: SchedulerReviewBundleInputs,
    *,
    now_func: NowFunc | None = None,
) -> dict[str, Any]:
    """Write the paper-submit plan plus dashboard manifest update without enabling orders."""
    now = (now_func or _utc_now)()
    output_dir = Path(inputs.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    submit_plan_path = output_dir / "paper_submit_mode_plan.json"
    review_manifest_path = output_dir / "dashboard_review_gates_manifest.json"
    summary_path = output_dir / "scheduler_review_bundle.json"

    submit_plan = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=inputs.scheduler_handoff,
            pipeline_scheduler_summary=inputs.pipeline_scheduler_summary,
            position_review_queue=inputs.position_review_queue,
            max_handoff_age_hours=inputs.max_handoff_age_hours,
            min_clean_scheduler_runs=inputs.min_clean_scheduler_runs,
        ),
        now_func=lambda: now,
    )

    manifest = _read_json_object(Path(inputs.dashboard_manifest))
    scheduler = _read_json_object(Path(inputs.pipeline_scheduler_summary))
    verification = _read_json_object(Path(inputs.post_run_verification))
    position_queue = _read_json_object(Path(inputs.position_review_queue))
    scheduler_policy = _load_scheduler_policy_from_manifest(manifest)
    buy_promotion = _read_json_object(Path(inputs.buy_promotion_artifact)) if str(inputs.buy_promotion_artifact) else {}
    final_action_plan = _read_json_object(Path(inputs.final_action_plan)) if str(inputs.final_action_plan) else {}
    blockers: list[str] = []
    warnings: list[str] = []

    _check_submit_plan(submit_plan, blockers=blockers)
    _check_post_run_verification(verification, blockers=blockers, warnings=warnings)
    _check_scheduler_summary(scheduler, blockers=blockers)
    _check_scheduler_policy(scheduler_policy, blockers=blockers, warnings=warnings)
    _check_position_review_queue(position_queue, blockers=blockers)
    _check_optional_buy_promotion(buy_promotion, supplied=bool(str(inputs.buy_promotion_artifact)), blockers=blockers)
    _check_optional_final_action_plan(
        final_action_plan,
        supplied=bool(str(inputs.final_action_plan)),
        blockers=blockers,
    )
    _check_no_submit_text(
        [submit_plan, scheduler, verification, position_queue, scheduler_policy, buy_promotion, final_action_plan],
        blockers=blockers,
    )

    status = "ready_for_manual_review" if not blockers else "blocked"
    review_manifest = _merge_dashboard_manifest(
        manifest,
        generated_at=_format_timestamp(now),
        scheduler_handoff=inputs.scheduler_handoff,
        pipeline_scheduler_summary=inputs.pipeline_scheduler_summary,
        position_review_queue=inputs.position_review_queue,
        paper_submit_mode_plan=submit_plan_path,
        scheduler_review_bundle=summary_path,
    )

    summary = {
        "schema_version": 1,
        "mode": "scheduler_review_bundle",
        "status": status,
        "generated_at": _format_timestamp(now),
        "dashboard_manifest": str(Path(inputs.dashboard_manifest)),
        "dashboard_review_gates_manifest": str(review_manifest_path),
        "paper_submit_mode_plan": str(submit_plan_path),
        "pipeline_scheduler_summary": str(Path(inputs.pipeline_scheduler_summary)),
        "post_run_verification": str(Path(inputs.post_run_verification)),
        "scheduler_handoff": str(Path(inputs.scheduler_handoff)),
        "position_review_queue": str(Path(inputs.position_review_queue)),
        "checks": {
            "paper_submit_mode_plan": "ready" if submit_plan.get("status") == "ready_for_manual_review" else "blocked",
            "post_run_verification": "ready" if _verification_ready(verification) else "blocked",
            "scheduler_summary": "ready" if _scheduler_ready(scheduler) else "blocked",
            "scheduler_policy": "ready" if _scheduler_policy_ready(scheduler_policy) else "blocked",
            "position_review_queue": "ready" if _position_queue_ready(position_queue) else "blocked",
            "buy_promotion": _optional_check_status(buy_promotion, supplied=bool(str(inputs.buy_promotion_artifact))),
            "final_action_plan": _optional_check_status(final_action_plan, supplied=bool(str(inputs.final_action_plan))),
            "order_submission_boundary": "ready" if not _has_boundary_blocker(blockers) else "blocked",
        },
        "resource_controls": _resource_controls(verification, scheduler),
        "scheduler_policy_summary": _scheduler_policy_summary(scheduler_policy),
        "position_review_summary": _position_review_summary(position_queue),
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "order_submission_enabled": False,
        "submit_profile_enabled": False,
        "broker_calls_enabled": False,
        "llm_calls_enabled": False,
        "runnable_submit_command_emitted": False,
        "next_safe_action": (
            "open_dashboard_review_gates_manifest_for_manual_submit_review"
            if not blockers
            else "resolve_scheduler_review_bundle_blockers_before_submit_profile"
        ),
        "notes": [
            "This bundle only connects completed no-submit artifacts for operator review.",
            "It never emits submit commands, enables broker execution, or calls an LLM.",
        ],
    }

    _write_json(submit_plan_path, submit_plan)
    _write_json(review_manifest_path, review_manifest)
    _write_json(summary_path, summary)
    return summary


def _check_submit_plan(plan: Mapping[str, Any], *, blockers: list[str]) -> None:
    if plan.get("status") != "ready_for_manual_review":
        blockers.append("paper_submit_mode_plan_not_ready")
    for key in ("order_submission_enabled", "submit_profile_enabled", "broker_calls_enabled", "llm_calls_enabled"):
        if bool(plan.get(key)):
            blockers.append(f"paper_submit_mode_plan_{key}")


def _check_post_run_verification(
    verification: Mapping[str, Any],
    *,
    blockers: list[str],
    warnings: list[str],
) -> None:
    if not verification:
        blockers.append("post_run_verification_missing_or_unreadable")
        return
    if verification.get("status") != "ready":
        blockers.append("post_run_verification_not_ready")
    for blocker in _string_list(verification.get("blockers")):
        blockers.append(f"post_run_verification:{blocker}")
    for warning in _string_list(verification.get("warnings")):
        warnings.append(f"post_run_verification:{warning}")
    if bool(verification.get("order_submission_enabled")):
        blockers.append("post_run_verification_order_submission_enabled")

    controls = verification.get("resource_controls")
    if isinstance(controls, Mapping):
        _check_resource_controls(dict(controls), verification=verification, blockers=blockers)
    else:
        warnings.append("post_run_verification_resource_controls_missing")


def _check_resource_controls(
    controls: Mapping[str, Any],
    *,
    verification: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if controls.get("bounded") is False:
        blockers.append("resource_controls_not_bounded")
    if bool(controls.get("final_planning_refresh")) and not _positive_number(
        controls.get("final_planning_timeout_seconds")
    ):
        blockers.append("final_planning_refresh_without_timeout")
    timestamps = verification.get("policy_state_timestamps")
    timestamps = timestamps if isinstance(timestamps, Mapping) else {}
    if bool(controls.get("portfolio_news_followup_batches")) and not timestamps.get("last_followup_batch_split_at"):
        blockers.append("portfolio_news_followup_batch_timestamp_missing")
    if bool(controls.get("portfolio_news_followup_committee_batches")) and not timestamps.get("last_followup_committee_at"):
        blockers.append("portfolio_news_followup_committee_timestamp_missing")


def _check_scheduler_summary(scheduler: Mapping[str, Any], *, blockers: list[str]) -> None:
    if not scheduler:
        blockers.append("pipeline_scheduler_summary_missing_or_unreadable")
        return
    if scheduler.get("status") != "completed":
        blockers.append("pipeline_scheduler_summary_not_completed")
    if bool(scheduler.get("order_submission_enabled")):
        blockers.append("pipeline_scheduler_summary_order_submission_enabled")
    if _int_value(scheduler.get("error_count")) != 0:
        blockers.append("pipeline_scheduler_summary_error_count_nonzero")
    latest = _latest_mapping(scheduler.get("runs") if isinstance(scheduler.get("runs"), list) else [])
    if not latest:
        blockers.append("pipeline_scheduler_latest_run_missing")
        return
    if latest.get("status") != "completed":
        blockers.append("pipeline_scheduler_latest_run_not_completed")
    if str(latest.get("blocker") or "").strip():
        blockers.append("pipeline_scheduler_latest_run_blocker_present")
    for key in ("position_review_queue_exit_code", "account_refresh_exit_code", "post_run_verification_exit_code"):
        value = latest.get(key)
        if value is not None and _int_value(value) != 0:
            blockers.append(f"pipeline_scheduler_{key}_nonzero")


def _check_scheduler_policy(
    policy: Mapping[str, Any],
    *,
    blockers: list[str],
    warnings: list[str],
) -> None:
    if not policy:
        warnings.append("scheduler_policy_missing_or_not_in_manifest")
        return
    for blocker in _string_list(policy.get("blockers")):
        blockers.append(f"scheduler_policy:{blocker}")
    benchmark = policy.get("benchmark_guard")
    if isinstance(benchmark, Mapping) and bool(benchmark.get("should_pause_new_buys")):
        blockers.append("scheduler_policy_benchmark_guard_paused")
    controls = policy.get("resource_controls")
    if isinstance(controls, Mapping) and controls.get("bounded") is False:
        blockers.append("scheduler_policy_resource_controls_not_bounded")


def _check_position_review_queue(queue: Mapping[str, Any], *, blockers: list[str]) -> None:
    if not queue:
        blockers.append("position_review_queue_missing_or_unreadable")
        return
    if queue.get("status") != "completed":
        blockers.append("position_review_queue_not_completed")
    for key in ("order_submission_enabled", "broker_calls_enabled", "llm_calls_enabled"):
        if bool(queue.get(key)):
            blockers.append(f"position_review_queue_{key}")


def _check_optional_buy_promotion(
    artifact: Mapping[str, Any],
    *,
    supplied: bool,
    blockers: list[str],
) -> None:
    if not supplied:
        return
    if not artifact:
        blockers.append("buy_promotion_artifact_missing_or_unreadable")
        return
    blocked_count = max(
        _int_value(artifact.get("blocked_count")),
        _int_value(artifact.get("promotion_blocked_count")),
        _int_value(artifact.get("workflow_promotion_blocked_count")),
    )
    if blocked_count > 0:
        blockers.append("buy_promotion_blockers_present")
    if str(artifact.get("status") or "").lower() in {"blocked", "failed", "attention_required"}:
        blockers.append("buy_promotion_status_not_ready")


def _check_optional_final_action_plan(
    artifact: Mapping[str, Any],
    *,
    supplied: bool,
    blockers: list[str],
) -> None:
    if not supplied:
        return
    if not artifact:
        blockers.append("final_action_plan_missing_or_unreadable")
        return
    if bool(artifact.get("order_submission_enabled")):
        blockers.append("final_action_plan_order_submission_enabled")
    if str(artifact.get("status") or "").lower() in {"blocked", "failed", "attention_required"}:
        blockers.append("final_action_plan_status_not_ready")


def _check_no_submit_text(artifacts: list[Mapping[str, Any]], *, blockers: list[str]) -> None:
    text = json.dumps(artifacts, sort_keys=True, default=str).lower()
    if any(fragment in text for fragment in SUBMIT_FRAGMENTS):
        blockers.append("submit_capable_fragment_present")


def _merge_dashboard_manifest(
    manifest: Mapping[str, Any],
    *,
    generated_at: str,
    scheduler_handoff: str | Path,
    pipeline_scheduler_summary: str | Path,
    position_review_queue: str | Path,
    paper_submit_mode_plan: str | Path,
    scheduler_review_bundle: str | Path,
) -> dict[str, Any]:
    merged = dict(manifest)
    merged["schema_version"] = int(merged.get("schema_version") or 1)
    merged["generated_at"] = generated_at
    merged["mode"] = "operator_dashboard_manifest"
    merged["scheduler_handoff"] = str(scheduler_handoff)
    merged["pipeline_scheduler_summary"] = str(pipeline_scheduler_summary)
    merged["position_review_queue"] = str(position_review_queue)
    merged["paper_submit_mode_plan"] = str(paper_submit_mode_plan)
    merged["scheduler_review_bundle"] = str(scheduler_review_bundle)
    merged["order_submission_enabled"] = False
    notes = [str(note) for note in merged.get("notes") or [] if str(note)]
    notes.append("Scheduler review gates were bundled after no-submit post-run verification.")
    merged["notes"] = _dedupe(notes)
    return merged


def _load_scheduler_policy_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    path_value = str(manifest.get("scheduler_policy") or "").strip()
    if not path_value:
        return {}
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    path = Path(path_value)
    if not path.is_absolute():
        path = base_dir / path
    return _read_json_object(path)


def _verification_ready(verification: Mapping[str, Any]) -> bool:
    return bool(verification) and verification.get("status") == "ready" and not verification.get("blockers")


def _scheduler_ready(scheduler: Mapping[str, Any]) -> bool:
    if not scheduler or scheduler.get("status") != "completed" or bool(scheduler.get("order_submission_enabled")):
        return False
    latest = _latest_mapping(scheduler.get("runs") if isinstance(scheduler.get("runs"), list) else [])
    return bool(latest) and latest.get("status") == "completed" and not str(latest.get("blocker") or "").strip()


def _scheduler_policy_ready(policy: Mapping[str, Any]) -> bool:
    if not policy:
        return True
    benchmark = policy.get("benchmark_guard")
    benchmark_paused = isinstance(benchmark, Mapping) and bool(benchmark.get("should_pause_new_buys"))
    return not policy.get("blockers") and not benchmark_paused


def _position_queue_ready(queue: Mapping[str, Any]) -> bool:
    return bool(queue) and queue.get("status") == "completed" and not bool(queue.get("order_submission_enabled"))


def _optional_check_status(artifact: Mapping[str, Any], *, supplied: bool) -> str:
    if not supplied:
        return "not_supplied"
    if not artifact:
        return "blocked"
    return "ready" if str(artifact.get("status") or "ready").lower() not in {"blocked", "failed"} else "blocked"


def _has_boundary_blocker(blockers: list[str]) -> bool:
    return any("submission" in blocker or "broker" in blocker or "submit_capable" in blocker for blocker in blockers)


def _resource_controls(verification: Mapping[str, Any], scheduler: Mapping[str, Any]) -> dict[str, Any]:
    controls = verification.get("resource_controls")
    if isinstance(controls, Mapping):
        return dict(controls)
    latest = _latest_mapping(scheduler.get("runs") if isinstance(scheduler.get("runs"), list) else [])
    controls = latest.get("resource_controls") if isinstance(latest, Mapping) else {}
    return dict(controls) if isinstance(controls, Mapping) else {}


def _scheduler_policy_summary(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not policy:
        return {"status": "not_supplied", "blocker_count": 0, "benchmark_paused": False}
    benchmark = policy.get("benchmark_guard") if isinstance(policy.get("benchmark_guard"), Mapping) else {}
    return {
        "status": str(policy.get("status") or ""),
        "blocker_count": len(_string_list(policy.get("blockers"))),
        "recommended_mode": str(policy.get("recommended_mode") or ""),
        "benchmark_paused": bool(benchmark.get("should_pause_new_buys")),
        "benchmark_reason": str(benchmark.get("reason") or ""),
    }


def _position_review_summary(queue: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(queue.get("status") or ""),
        "review_count": _int_value(queue.get("review_count")),
        "high_priority_count": _int_value(queue.get("high_priority_count")),
        "next_safe_action": str(queue.get("next_safe_action") or ""),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _latest_mapping(items: list[Any]) -> dict[str, Any]:
    for item in reversed(items):
        if isinstance(item, Mapping):
            return dict(item)
    return {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value or [] if str(item)] if isinstance(value, list) else []


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "SchedulerReviewBundleInputs",
    "build_scheduler_review_bundle",
]
