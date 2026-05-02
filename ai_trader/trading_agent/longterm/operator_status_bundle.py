"""Read-only operator status bundle for long-term paper-trading oversight."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_lifecycle import build_paper_lifecycle_summary
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.position_report import build_position_intelligence_report
from longterm.scheduler_readiness import (
    build_scheduler_readiness_markdown,
    build_scheduler_readiness_report,
)


def build_operator_status_bundle(
    journal: LongTermDecisionJournal,
    *,
    portfolio_state: PortfolioState | None = None,
    paper_ledger: PaperTradeLedger | None = None,
    action_plan: Mapping[str, Any] | None = None,
    price_map: Mapping[str, Any] | None = None,
    feedback_summary: Mapping[str, Any] | None = None,
    monday_operator_check: Mapping[str, Any] | None = None,
    live_readiness_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the read-only operator artifacts needed before automation."""
    lifecycle = (
        build_paper_lifecycle_summary(paper_ledger, price_map=price_map)
        if paper_ledger is not None
        else {"schema_version": 1, "mode": "paper_lifecycle_summary", "state_counts": {}, "items": []}
    )
    readiness = build_scheduler_readiness_report(
        journal,
        portfolio_state=portfolio_state,
        action_plan=action_plan,
        feedback_summary=feedback_summary,
        paper_lifecycle_summary=lifecycle,
    )
    position_report = (
        build_position_intelligence_report(
            journal,
            portfolio_state=portfolio_state,
            paper_ledger=paper_ledger,
            paper_outcome_price_map=price_map,
            feedback_summary=feedback_summary,
        )
        if portfolio_state is not None
        else ""
    )
    return {
        "schema_version": 1,
        "mode": "operator_status_bundle",
        "order_submission_enabled": False,
        "paper_lifecycle": lifecycle,
        "buy_promotion_summary": _buy_promotion_summary(action_plan),
        "monday_operator_check_summary": _monday_operator_check_summary(monday_operator_check),
        "live_readiness_summary": _live_readiness_summary(live_readiness_bundle),
        "scheduler_readiness": readiness,
        "position_report_markdown": position_report,
        "notes": [
            "Read-only operator bundle. No broker orders were submitted or modified.",
            "Operator means the current supervised control surface and the future autonomous agent control surface.",
            "Scheduler readiness remains advisory-only in V1.",
        ],
    }


def build_operator_status_markdown(payload: Mapping[str, Any]) -> str:
    readiness_markdown = build_scheduler_readiness_markdown(payload.get("scheduler_readiness") or {})
    lines = [
        "# Long-Term Operator Status Bundle",
        "",
        f"- Order submission enabled: `{str(payload.get('order_submission_enabled')).lower()}`",
        "",
        "## Paper Lifecycle",
        "",
        "| State | Count |",
        "| --- | ---: |",
    ]
    for state, count in sorted((payload.get("paper_lifecycle", {}).get("state_counts") or {}).items()):
        lines.append(f"| {state} | {count} |")
    lines.extend(_buy_promotion_markdown_lines(payload.get("buy_promotion_summary") or {}))
    lines.extend(_monday_operator_check_markdown_lines(payload.get("monday_operator_check_summary") or {}))
    lines.extend(_live_readiness_markdown_lines(payload.get("live_readiness_summary") or {}))
    lines.extend(["", "## Scheduler Readiness", ""])
    lines.extend(readiness_markdown.splitlines()[2:])
    position_report = str(payload.get("position_report_markdown") or "").strip()
    if position_report:
        lines.extend(["", "## Position Intelligence", "", position_report])
    return "\n".join(lines) + "\n"


def _buy_promotion_summary(action_plan: Mapping[str, Any] | None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for intent in (action_plan or {}).get("intents") or []:
        if not isinstance(intent, Mapping):
            continue
        review = intent.get("promotion_review")
        if not isinstance(review, Mapping):
            review = (intent.get("risk_review") or {}).get("buy_promotion")
        if not isinstance(review, Mapping):
            continue
        decision = str(review.get("promotion_decision") or "UNKNOWN")
        counts[decision] = counts.get(decision, 0) + 1
        items.append(
            {
                "symbol": str(review.get("symbol") or intent.get("symbol") or "").upper(),
                "promotion_decision": decision,
                "followups": list(review.get("followups") or []),
                "blockers": list(review.get("blockers") or []),
                "intent_type": str(intent.get("intent_type") or ""),
                "order_intent": str(intent.get("order_intent") or ""),
                "allowed": bool(intent.get("allowed")),
            }
        )
    return {
        "counts": counts,
        "items": items,
        "actionable_count": counts.get("ACTIONABLE_BUY", 0),
        "pending_count": sum(
            count for decision, count in counts.items()
            if decision.startswith("WATCHLIST_") or decision == "REVIEW_EXISTING_POSITION"
        ),
        "blocked_count": counts.get("BLOCKED", 0),
    }


def _buy_promotion_markdown_lines(summary: Mapping[str, Any]) -> list[str]:
    items = summary.get("items") or []
    if not items:
        return []
    lines = [
        "",
        "## Buy Promotion",
        "",
        "| Symbol | Decision | Followups | Blockers |",
        "|---|---|---|---|",
    ]
    for item in items:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            "| "
            f"{_cell(str(item.get('symbol') or ''))} | "
            f"{_cell(str(item.get('promotion_decision') or ''))} | "
            f"{_cell(', '.join(str(value) for value in (item.get('followups') or [])))} | "
            f"{_cell(', '.join(str(value) for value in (item.get('blockers') or [])))} |"
        )
    return lines


def _live_readiness_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    observed = payload.get("observed") if isinstance(payload, Mapping) else {}
    observed = observed if isinstance(observed, Mapping) else {}
    paper_smoke_safety = payload.get("paper_smoke_safety") if isinstance(payload, Mapping) else {}
    paper_smoke_safety = paper_smoke_safety if isinstance(paper_smoke_safety, Mapping) else {}
    unmet = [str(value) for value in (payload.get("unmet_gate_keys") or [])]
    return {
        "mode": str(payload.get("mode") or ""),
        "ready": bool(payload.get("ready")),
        "unmet_gate_count": len(unmet),
        "unmet_gate_keys": unmet,
        "paper_trading_verified": bool(observed.get("paper_trading_verified")),
        "broker_capability_match": bool(observed.get("broker_capability_match")),
        "paper_smoke_ready": bool(observed.get("paper_smoke_ready")),
        "paper_smoke_schema_ok": bool(paper_smoke_safety.get("schema_ok")),
        "paper_smoke_promotion_blocked_count": int(paper_smoke_safety.get("promotion_blocked_count") or 0),
    }


def _live_readiness_markdown_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary:
        return []
    lines = [
        "",
        "## Live Readiness Evidence",
        "",
        f"- Ready: {_yes_no(summary.get('ready'))}",
        f"- Unmet gates: {int(summary.get('unmet_gate_count') or 0)}",
        f"- Paper trading verified: {_yes_no(summary.get('paper_trading_verified'))}",
        f"- Broker capability match: {_yes_no(summary.get('broker_capability_match'))}",
        f"- Paper smoke ready: {_yes_no(summary.get('paper_smoke_ready'))}",
        f"- Paper smoke schema ok: {_yes_no(summary.get('paper_smoke_schema_ok'))}",
        f"- Paper smoke promotion blocked: {int(summary.get('paper_smoke_promotion_blocked_count') or 0)}",
        "",
        "### Unmet Live-Readiness Gates",
        "",
    ]
    unmet = summary.get("unmet_gate_keys") or []
    if unmet:
        lines.extend(f"- {gate}" for gate in unmet)
    else:
        lines.append("- none")
    return lines


def _monday_operator_check_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "mode": str(payload.get("mode") or ""),
        "ready_for_review": bool(payload.get("ready_for_review")),
        "blocker_count": int(payload.get("blocker_count") or 0),
        "blockers": list(payload.get("blockers") or []),
        "workflow_smoke_ready": bool(payload.get("workflow_smoke_ready")),
        "paper_smoke_ready": bool(payload.get("paper_smoke_ready")),
        "runbook_check_ready": bool(payload.get("runbook_check_ready")),
        "workflow_preview_allowed_count": int(payload.get("workflow_preview_allowed_count") or 0),
        "workflow_execution_ready_count": int(payload.get("workflow_execution_ready_count") or 0),
        "workflow_execution_excluded_count": int(payload.get("workflow_execution_excluded_count") or 0),
        "workflow_promotion_blocked_count": int(payload.get("workflow_promotion_blocked_count") or 0),
        "paper_smoke_promotion_blocked_count": int(payload.get("paper_smoke_promotion_blocked_count") or 0),
        "submit_command_revealed": bool(payload.get("submit_command_revealed")),
        "account_clean": payload.get("account_clean"),
        "status_refresh_error_count": int(payload.get("status_refresh_error_count") or 0),
    }


def _monday_operator_check_markdown_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary:
        return []
    lines = [
        "",
        "## Monday Paper Artifacts",
        "",
        f"- Ready for review: {_yes_no(summary.get('ready_for_review'))}",
        f"- Blockers: {int(summary.get('blocker_count') or 0)}",
        f"- Workflow smoke ready: {_yes_no(summary.get('workflow_smoke_ready'))}",
        f"- Paper smoke ready: {_yes_no(summary.get('paper_smoke_ready'))}",
        f"- Runbook check ready: {_yes_no(summary.get('runbook_check_ready'))}",
        f"- Workflow preview allowed: {int(summary.get('workflow_preview_allowed_count') or 0)}",
        f"- Workflow execution ready: {int(summary.get('workflow_execution_ready_count') or 0)}",
        f"- Workflow execution excluded: {int(summary.get('workflow_execution_excluded_count') or 0)}",
        f"- Workflow promotion blocked: {int(summary.get('workflow_promotion_blocked_count') or 0)}",
        f"- Paper smoke promotion blocked: {int(summary.get('paper_smoke_promotion_blocked_count') or 0)}",
        f"- Submit command revealed: {_yes_no(summary.get('submit_command_revealed'))}",
        f"- Account clean: {_yes_no_unknown(summary.get('account_clean'))}",
        f"- Status refresh errors: {int(summary.get('status_refresh_error_count') or 0)}",
        "",
        "### Monday Artifact Blockers",
        "",
    ]
    blockers = summary.get("blockers") or []
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    return lines


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


def _yes_no_unknown(value: Any) -> str:
    if value is None:
        return "unknown"
    return _yes_no(value)


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")


__all__ = ["build_operator_status_bundle", "build_operator_status_markdown"]
