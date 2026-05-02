"""Read-only pre-flight readiness report for supervised paper smokes."""

from __future__ import annotations

from typing import Any, Mapping


def build_paper_smoke_readiness_report(
    *,
    account_cleanliness: Mapping[str, Any] | None = None,
    broker_capabilities: Mapping[str, Any] | None = None,
    scheduler_readiness: Mapping[str, Any] | None = None,
    workflow_smoke: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine existing advisory artifacts into one paper-smoke pre-flight report."""
    account_cleanliness = account_cleanliness or {}
    broker_capabilities = broker_capabilities or {}
    scheduler_readiness = scheduler_readiness or {}
    workflow_smoke = workflow_smoke or {}
    blockers: list[str] = []
    warnings: list[str] = []

    if not bool(account_cleanliness.get("clean")):
        blockers.append("paper_account_not_clean")
    if not bool(broker_capabilities.get("compatible")):
        blockers.append("broker_capability_mismatch")
    scheduler_blockers = int(scheduler_readiness.get("blocker_count") or 0)
    if scheduler_blockers:
        blockers.append("scheduler_readiness_blockers")
    if int(scheduler_readiness.get("warning_count") or 0):
        warnings.append("scheduler_readiness_warnings")
    if workflow_smoke and not bool(workflow_smoke.get("ready_for_supervised_submit")):
        blockers.append("workflow_smoke_not_ready")
    workflow_promotion_summary = dict(workflow_smoke.get("promotion_summary") or {})
    if int(workflow_promotion_summary.get("blocked_count") or 0) > 0:
        blockers.append("workflow_buy_promotion_blockers")
    warnings.extend(str(item) for item in (broker_capabilities.get("warnings") or []))

    return {
        "schema_version": 2,
        "mode": "paper_smoke_readiness",
        "order_submission_enabled": False,
        "ready_for_supervised_smoke": not blockers,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "account_cleanliness": dict(account_cleanliness),
        "broker_capabilities": dict(broker_capabilities),
        "scheduler_readiness": dict(scheduler_readiness),
        "workflow_smoke": dict(workflow_smoke),
        "workflow_promotion_summary": workflow_promotion_summary,
        "notes": [
            "Read-only pre-flight report. No broker orders were submitted, canceled, or modified.",
            "A ready report means the artifacts look clean enough for a supervised smoke; it does not authorize automation.",
        ],
    }


def build_paper_smoke_readiness_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Smoke Readiness",
        "",
        "Read-only pre-flight report. No orders were submitted, canceled, or modified.",
        "",
        f"- Ready for supervised smoke: {'yes' if report.get('ready_for_supervised_smoke') else 'no'}",
        f"- Blockers: {int(report.get('blocker_count') or 0)}",
        f"- Warnings: {int(report.get('warning_count') or 0)}",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


__all__ = ["build_paper_smoke_readiness_markdown", "build_paper_smoke_readiness_report"]
