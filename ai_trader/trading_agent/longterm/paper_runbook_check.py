"""Read-only checks for Monday paper runbook artifacts."""

from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

MIN_PROMOTION_AWARE_SCHEMA_VERSION = 2


def build_paper_runbook_check(
    *,
    workflow_smoke: str | Path,
    paper_smoke_readiness: str | Path,
    action_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate saved pre-submit paper artifacts before supervised submit."""
    workflow_payload, workflow_error = _load_optional_json(workflow_smoke)
    readiness_payload, readiness_error = _load_optional_json(paper_smoke_readiness)
    action_plan = action_plan or {}
    blockers: list[str] = []
    plan_id = _workflow_plan_id(workflow_payload)
    action_plan_hash = hash_action_plan(action_plan) if action_plan else ""
    promotion_summary = _promotion_summary(workflow_payload, readiness_payload)
    if workflow_error:
        blockers.append(f"workflow_smoke_{workflow_error}")
    else:
        if _schema_version(workflow_payload) < MIN_PROMOTION_AWARE_SCHEMA_VERSION:
            blockers.append("workflow_smoke_schema_too_old")
        if not bool(workflow_payload.get("ready_for_supervised_submit")):
            blockers.append("workflow_smoke_not_ready")
        if int(promotion_summary.get("workflow_blocked_count") or 0) > 0:
            blockers.append("workflow_buy_promotion_blockers")
    if readiness_error:
        blockers.append(f"paper_smoke_readiness_{readiness_error}")
    else:
        if _schema_version(readiness_payload) < MIN_PROMOTION_AWARE_SCHEMA_VERSION:
            blockers.append("paper_smoke_readiness_schema_too_old")
        if not bool(readiness_payload.get("ready_for_supervised_smoke")):
            blockers.append("paper_smoke_readiness_not_ready")
        if int(promotion_summary.get("readiness_blocked_count") or 0) > 0:
            blockers.append("paper_smoke_readiness_buy_promotion_blockers")
    return {
        "schema_version": 2,
        "mode": "paper_runbook_check",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "plan_id": plan_id,
        "action_plan_hash": action_plan_hash,
        "order_submission_enabled": False,
        "ready_for_supervised_submit": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "promotion_summary": promotion_summary,
        "artifacts": {
            "workflow_smoke": str(workflow_smoke),
            "paper_smoke_readiness": str(paper_smoke_readiness),
        },
        "workflow_smoke": workflow_payload,
        "paper_smoke_readiness": readiness_payload,
        "notes": [
            "Read-only runbook artifact check. No broker calls were made.",
            "A ready result is still not authorization for scheduler or live trading.",
        ],
    }


def build_paper_runbook_check_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Runbook Check",
        "",
        "Read-only artifact check. No broker calls were made.",
        "",
        f"- Ready for supervised submit: {'yes' if report.get('ready_for_supervised_submit') else 'no'}",
        f"- Blockers: {int(report.get('blocker_count') or 0)}",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _load_optional_json(path: str | Path) -> tuple[dict[str, Any], str]:
    target = Path(path)
    if not target.exists():
        return {}, "missing"
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, "malformed"
    if not isinstance(payload, dict):
        return {}, "malformed"
    return payload, ""


def _workflow_plan_id(payload: Mapping[str, Any]) -> str:
    execution_audit = payload.get("execution_audit") if isinstance(payload, Mapping) else {}
    if isinstance(execution_audit, Mapping) and execution_audit.get("plan_id"):
        return str(execution_audit.get("plan_id") or "")
    preview = payload.get("preview") if isinstance(payload, Mapping) else {}
    if isinstance(preview, Mapping) and preview.get("plan_id"):
        return str(preview.get("plan_id") or "")
    return str(payload.get("plan_id") or "") if isinstance(payload, Mapping) else ""


def _schema_version(payload: Mapping[str, Any]) -> int:
    try:
        return int(payload.get("schema_version") or 1)
    except (TypeError, ValueError):
        return 1


def _promotion_summary(
    workflow_payload: Mapping[str, Any],
    readiness_payload: Mapping[str, Any],
) -> dict[str, Any]:
    workflow = workflow_payload.get("promotion_summary") if isinstance(workflow_payload, Mapping) else {}
    readiness = readiness_payload.get("workflow_promotion_summary") if isinstance(readiness_payload, Mapping) else {}
    workflow = workflow if isinstance(workflow, Mapping) else {}
    readiness = readiness if isinstance(readiness, Mapping) else {}
    workflow_blocked = int(workflow.get("blocked_count") or 0)
    readiness_blocked = int(readiness.get("blocked_count") or 0)
    return {
        "workflow_blocked_count": workflow_blocked,
        "workflow_missing_count": int(workflow.get("missing_count") or 0),
        "workflow_non_actionable_count": int(workflow.get("non_actionable_count") or 0),
        "readiness_blocked_count": readiness_blocked,
        "readiness_missing_count": int(readiness.get("missing_count") or 0),
        "readiness_non_actionable_count": int(readiness.get("non_actionable_count") or 0),
    }


def hash_action_plan(action_plan: Mapping[str, Any]) -> str:
    canonical = json.dumps(action_plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["build_paper_runbook_check", "build_paper_runbook_check_markdown", "hash_action_plan"]
