"""Read-only checks for Monday paper runbook artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def build_paper_runbook_check(
    *,
    workflow_smoke: str | Path,
    paper_smoke_readiness: str | Path,
) -> dict[str, Any]:
    """Validate saved pre-submit paper artifacts before supervised submit."""
    workflow_payload, workflow_error = _load_optional_json(workflow_smoke)
    readiness_payload, readiness_error = _load_optional_json(paper_smoke_readiness)
    blockers: list[str] = []
    if workflow_error:
        blockers.append(f"workflow_smoke_{workflow_error}")
    elif not bool(workflow_payload.get("ready_for_supervised_submit")):
        blockers.append("workflow_smoke_not_ready")
    if readiness_error:
        blockers.append(f"paper_smoke_readiness_{readiness_error}")
    elif not bool(readiness_payload.get("ready_for_supervised_smoke")):
        blockers.append("paper_smoke_readiness_not_ready")
    return {
        "schema_version": 1,
        "mode": "paper_runbook_check",
        "order_submission_enabled": False,
        "ready_for_supervised_submit": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
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


__all__ = ["build_paper_runbook_check", "build_paper_runbook_check_markdown"]
