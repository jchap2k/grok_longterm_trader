"""Normalize scheduler config-validation artifacts for no-submit consumers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


NO_SUBMIT_BROKER_BOUNDARY = "blocked_by_no_submit_scheduler"


def normalize_scheduler_config_validation(
    payload: Mapping[str, Any] | None,
    *,
    source_path: str | Path = "",
    unavailable_reason: str = "scheduler_config_validation_artifact_missing",
) -> dict[str, Any]:
    """Return a safe, dashboard/handoff-ready scheduler validation object."""
    if not payload:
        normalized: dict[str, Any] = {
            "schema_version": 1,
            "mode": "pipeline_scheduler_config_validation",
            "status": "unavailable",
            "source_path": str(source_path or ""),
            "config_file": "",
            "preset": "",
            "resource_controls": {},
            "warnings": [unavailable_reason],
            "next_safe_action": "run_scheduler_config_validation_before_recurring_launch",
            "order_submission_enabled": False,
        }
        return _with_operating_mode_summary(normalized, extra_blockers=[unavailable_reason])

    normalized = dict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("mode", "pipeline_scheduler_config_validation")
    normalized.setdefault("status", "unknown")
    normalized.setdefault("resource_controls", {})
    normalized.setdefault("next_safe_action", "validate_scheduler_profile_before_launch")
    if source_path:
        normalized["source_path"] = str(source_path)
    else:
        normalized.setdefault("source_path", "")
    normalized["order_submission_enabled"] = False
    return _with_operating_mode_summary(normalized)


def scheduler_validation_ready_for_unattended_no_submit(payload: Mapping[str, Any] | None) -> bool:
    """Return true only for explicit, current recurring no-submit readiness."""
    return bool(normalize_scheduler_config_validation(payload).get("recurring_no_submit_ready"))


def _with_operating_mode_summary(
    normalized: dict[str, Any],
    *,
    extra_blockers: list[str] | None = None,
) -> dict[str, Any]:
    raw_summary = normalized.get("operating_mode_summary")
    summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {}
    resource_controls = normalized.get("resource_controls") if isinstance(normalized.get("resource_controls"), Mapping) else {}
    blockers = _string_list(summary.get("readiness_blockers")) + list(extra_blockers or [])
    if "recurring_no_submit_ready" not in normalized:
        blockers.append("recurring_no_submit_ready_missing")
    if not summary:
        blockers.append("operating_mode_summary_missing")
    if summary and summary.get("ready_for_unattended_no_submit") is not True:
        blockers.append("operating_mode_not_ready")
    if summary and str(summary.get("broker_submit_boundary") or "").strip() != NO_SUBMIT_BROKER_BOUNDARY:
        blockers.append("broker_submit_boundary_not_no_submit")
    if resource_controls.get("bounded") is False:
        blockers.append(str(resource_controls.get("bounded_reason") or "resource_controls_unbounded"))
    unique_blockers = sorted({blocker for blocker in blockers if blocker})
    ready = (
        str(normalized.get("status") or "").lower().strip() == "ready"
        and bool(normalized.get("recurring_no_submit_ready")) is True
        and summary.get("ready_for_unattended_no_submit") is True
        and str(summary.get("broker_submit_boundary") or "").strip() == NO_SUBMIT_BROKER_BOUNDARY
        and not unique_blockers
    )
    summary.setdefault("schema_version", 1)
    summary.setdefault("name", "recurring_no_submit" if normalized.get("preset") == "ongoing-no-submit" else "custom_no_submit")
    summary["ready_for_unattended_no_submit"] = ready
    summary["readiness_blockers"] = [] if ready else unique_blockers
    summary.setdefault("broker_submit_boundary", NO_SUBMIT_BROKER_BOUNDARY)
    summary.setdefault("stage_flags", {})
    summary.setdefault(
        "operator_next_step",
        (
            "schedule_or_run_no_submit_profile_after_operator_window_approval"
            if ready
            else "resolve_readiness_blockers_before_scheduling"
        ),
    )
    normalized["operating_mode_summary"] = summary
    normalized["recurring_no_submit_ready"] = ready
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


__all__ = [
    "NO_SUBMIT_BROKER_BOUNDARY",
    "normalize_scheduler_config_validation",
    "scheduler_validation_ready_for_unattended_no_submit",
]
