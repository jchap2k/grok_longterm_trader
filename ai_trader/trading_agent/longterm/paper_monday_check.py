"""Read-only Monday paper operator checklist."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

MIN_PROMOTION_AWARE_SCHEMA_VERSION = 2


def build_paper_monday_check(
    *,
    runbook: str | Path,
    workflow_smoke: str | Path,
    paper_smoke_readiness: str | Path,
    runbook_check: str | Path,
    status_refresh: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize saved Monday paper artifacts without calling brokers."""
    runbook_payload = _load_json(runbook)
    workflow_payload = _load_json(workflow_smoke)
    readiness_payload = _load_json(paper_smoke_readiness)
    check_payload = _load_json(runbook_check)
    status_payload = _load_json(status_refresh) if status_refresh else {}
    workflow_preview = workflow_payload.get("preview") or {}
    workflow_execution = workflow_payload.get("execution_audit") or {}
    workflow_promotion = workflow_payload.get("promotion_summary") or {}
    readiness_promotion = readiness_payload.get("workflow_promotion_summary") or {}
    workflow_promotion = workflow_promotion if isinstance(workflow_promotion, Mapping) else {}
    readiness_promotion = readiness_promotion if isinstance(readiness_promotion, Mapping) else {}
    workflow_promotion_blocked_count = int(workflow_promotion.get("blocked_count") or 0)
    readiness_promotion_blocked_count = int(readiness_promotion.get("blocked_count") or 0)

    submit_revealed = _submit_command_revealed(runbook_payload)
    account_cleanliness = readiness_payload.get("account_cleanliness") or {}
    action_plan_hash = str(check_payload.get("action_plan_hash") or "")
    blockers: list[str] = []
    if not bool(workflow_payload.get("ready_for_supervised_submit")):
        blockers.append("workflow_smoke_not_ready")
    if not bool(readiness_payload.get("ready_for_supervised_smoke")):
        blockers.append("paper_smoke_readiness_not_ready")
    if not bool(check_payload.get("ready_for_supervised_submit")):
        blockers.append("runbook_check_not_ready")
    if account_cleanliness and not bool(account_cleanliness.get("clean")):
        blockers.append("paper_account_not_clean")
    if not action_plan_hash:
        blockers.append("action_plan_hash_missing")
    if int(status_payload.get("error_count") or 0):
        blockers.append("status_refresh_errors")
    if _schema_version(workflow_payload) < MIN_PROMOTION_AWARE_SCHEMA_VERSION:
        blockers.append("workflow_smoke_schema_too_old")
    if _schema_version(readiness_payload) < MIN_PROMOTION_AWARE_SCHEMA_VERSION:
        blockers.append("paper_smoke_readiness_schema_too_old")
    if _schema_version(check_payload) < MIN_PROMOTION_AWARE_SCHEMA_VERSION:
        blockers.append("runbook_check_schema_too_old")
    if workflow_promotion_blocked_count:
        blockers.append("workflow_buy_promotion_blockers")
    if readiness_promotion_blocked_count:
        blockers.append("paper_smoke_buy_promotion_blockers")

    return {
        "schema_version": 2,
        "mode": "paper_monday_operator_check",
        "order_submission_enabled": False,
        "ready_for_review": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "workflow_smoke_ready": bool(workflow_payload.get("ready_for_supervised_submit")),
        "workflow_preview_allowed_count": int(workflow_preview.get("allowed_count") or 0),
        "workflow_preview_blocked_count": int(workflow_preview.get("blocked_count") or 0),
        "workflow_preview_no_order_count": int(workflow_preview.get("no_order_count") or 0),
        "workflow_execution_ready_count": int(workflow_execution.get("ready_count") or 0),
        "workflow_execution_blocked_count": int(workflow_execution.get("blocked_count") or 0),
        "workflow_execution_excluded_count": int(workflow_execution.get("excluded_count") or 0),
        "workflow_promotion_blocked_count": workflow_promotion_blocked_count,
        "paper_smoke_promotion_blocked_count": readiness_promotion_blocked_count,
        "paper_smoke_ready": bool(readiness_payload.get("ready_for_supervised_smoke")),
        "runbook_check_ready": bool(check_payload.get("ready_for_supervised_submit")),
        "action_plan_hash_present": bool(action_plan_hash),
        "submit_command_revealed": submit_revealed,
        "manual_submit_review_required": submit_revealed,
        "account_clean": bool(account_cleanliness.get("clean")) if account_cleanliness else None,
        "leftover_position_count": int(account_cleanliness.get("position_count") or 0),
        "leftover_symbols": list(account_cleanliness.get("unexpected_symbols") or []),
        "status_refresh_error_count": int(status_payload.get("error_count") or 0),
        "status_refresh_submitted_order_count": int(status_payload.get("submitted_order_count") or 0),
        "notes": [
            "Read-only operator check. No broker calls were made.",
            "A ready result means saved artifacts are reviewable; it does not authorize automation.",
        ],
    }


def build_paper_monday_check_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Monday Paper Operator Check",
        "",
        "Read-only artifact summary. No broker calls were made.",
        "",
        f"- Ready for review: {'yes' if payload.get('ready_for_review') else 'no'}",
        f"- Blockers: {int(payload.get('blocker_count') or 0)}",
        f"- Workflow smoke ready: {'yes' if payload.get('workflow_smoke_ready') else 'no'}",
        f"- Workflow preview allowed: {int(payload.get('workflow_preview_allowed_count') or 0)}",
        f"- Workflow preview no-order/excluded: {int(payload.get('workflow_preview_no_order_count') or 0)}",
        f"- Workflow execution ready: {int(payload.get('workflow_execution_ready_count') or 0)}",
        f"- Workflow execution excluded: {int(payload.get('workflow_execution_excluded_count') or 0)}",
        f"- Workflow promotion blocked: {int(payload.get('workflow_promotion_blocked_count') or 0)}",
        f"- Paper smoke ready: {'yes' if payload.get('paper_smoke_ready') else 'no'}",
        f"- Runbook check ready: {'yes' if payload.get('runbook_check_ready') else 'no'}",
        f"- Action-plan hash present: {'yes' if payload.get('action_plan_hash_present') else 'no'}",
        f"- Submit command revealed: {'yes' if payload.get('submit_command_revealed') else 'no'}",
        f"- Manual submit review required: {'yes' if payload.get('manual_submit_review_required') else 'no'}",
        f"- Account clean: {_yes_no_unknown(payload.get('account_clean'))}",
        f"- Leftover symbols: {', '.join(payload.get('leftover_symbols') or []) or 'none'}",
        "",
        "## Blockers",
        "",
    ]
    blockers = payload.get("blockers") or []
    lines.extend(f"- {blocker}" for blocker in blockers) if blockers else lines.append("- none")
    return "\n".join(lines) + "\n"


def _load_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _submit_command_revealed(runbook: Mapping[str, Any]) -> bool:
    for step in runbook.get("steps") or []:
        if not isinstance(step, Mapping) or step.get("step_id") != "supervised_submit":
            continue
        command = str(step.get("command") or "")
        return bool("--submit-paper-orders" in command or step.get("requires_explicit_reveal") is False)
    return False


def _schema_version(payload: Mapping[str, Any]) -> int:
    try:
        return int(payload.get("schema_version") or 1)
    except (TypeError, ValueError):
        return 1


def _yes_no_unknown(value: Any) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


__all__ = ["build_paper_monday_check", "build_paper_monday_check_markdown"]
