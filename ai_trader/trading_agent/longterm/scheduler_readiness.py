"""Advisory scheduler-readiness checks for the long-term trader.

This module is intentionally read-only. It does not call Alpaca, does not submit
orders, and does not enable scheduler execution. V1 readiness is advisory only:
``ready_for_scheduler_paper_submit`` is always false until a later explicitly
reviewed scheduler automation stage changes that contract.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from longterm.review_status import ReviewStatusBuilder, review_risk_bucket


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"


def build_scheduler_readiness_report(
    journal: LongTermDecisionJournal,
    *,
    portfolio_state: PortfolioState | None = None,
    action_plan: Mapping[str, Any] | None = None,
    feedback_summary: Mapping[str, Any] | None = None,
    paper_lifecycle_summary: Mapping[str, Any] | None = None,
    active_rules_path: str | Path | None = DEFAULT_RULES_PATH,
) -> dict[str, Any]:
    """Build an advisory readiness report from existing dry-run artifacts."""
    checks: list[dict[str, Any]] = []
    protected_symbols = {"FXAIX"}
    if portfolio_state is not None:
        protected_symbols.update(portfolio_state.protected_symbols)

    _add_presence_check(checks, "portfolio_state_present", portfolio_state is not None, "Portfolio state is required.")
    _add_presence_check(checks, "action_plan_present", action_plan is not None, "Action plan is required.")
    _add_rules_check(checks, active_rules_path or DEFAULT_RULES_PATH)
    _add_feedback_checks(checks, feedback_summary)
    _add_benchmark_check(checks, journal, feedback_summary)
    _add_review_checks(checks, journal)
    _add_action_plan_checks(checks, journal, action_plan or {}, protected_symbols)
    _add_buy_promotion_checks(checks, action_plan or {})
    _add_lifecycle_checks(checks, paper_lifecycle_summary or {})
    _add_deferred_research_check(checks, feedback_summary)
    checks.append(
        {
            "check_id": "scheduler_advisory_only_v1",
            "status": "warning",
            "message": "Scheduler paper submission remains disabled in V1; this report is advisory only.",
        }
    )

    blockers = [check for check in checks if check["status"] == "blocker"]
    warnings = [check for check in checks if check["status"] == "warning"]
    return {
        "schema_version": 1,
        "mode": "scheduler_readiness_report",
        "scheduler_submission_enabled": False,
        "ready_for_scheduler_paper_submit": False,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "checks": checks,
        "recommended_next_steps": _recommended_next_steps(checks),
    }


def build_scheduler_readiness_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Scheduler Readiness",
        "",
        f"- Scheduler submission enabled: `{str(payload.get('scheduler_submission_enabled')).lower()}`",
        f"- Ready for scheduler paper submit: `{str(payload.get('ready_for_scheduler_paper_submit')).lower()}`",
        f"- Blockers: {payload.get('blocker_count', 0)}",
        f"- Warnings: {payload.get('warning_count', 0)}",
        "",
        "| Status | Check | Message |",
        "| --- | --- | --- |",
    ]
    for check in payload.get("checks") or []:
        lines.append(
            "| {status} | {check_id} | {message} |".format(
                status=check.get("status") or "",
                check_id=check.get("check_id") or "",
                message=str(check.get("message") or "").replace("|", "\\|"),
            )
        )
    lines.extend(["", "## Recommended Next Steps", ""])
    for step in payload.get("recommended_next_steps") or []:
        lines.append(f"- {step}")
    return "\n".join(lines) + "\n"


def _add_presence_check(checks: list[dict[str, Any]], check_id: str, present: bool, missing_message: str) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": "pass" if present else "blocker",
            "message": "Present." if present else missing_message,
        }
    )


def _add_rules_check(checks: list[dict[str, Any]], active_rules_path: str | Path) -> None:
    path = Path(active_rules_path)
    if not path.exists():
        checks.append(
            {
                "check_id": "active_rules_reference",
                "status": "warning",
                "message": f"Active rules file missing: {path}",
                "data_blocker": "active_rules_missing",
            }
        )
        return
    text = path.read_text(encoding="utf-8")
    checks.append(
        {
            "check_id": "active_rules_reference",
            "status": "pass",
            "message": "Active rules file present.",
            "path": str(path),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    )


def _add_feedback_checks(checks: list[dict[str, Any]], feedback_summary: Mapping[str, Any] | None) -> None:
    if not feedback_summary:
        checks.append(
            {
                "check_id": "feedback_summary_present",
                "status": "warning",
                "message": "Feedback summary missing; run longterm_feedback_refresh.py before scheduler consideration.",
            }
        )
        return
    submission_enabled = bool(feedback_summary.get("order_submission_enabled"))
    checks.append(
        {
            "check_id": "feedback_submission_disabled",
            "status": "blocker" if submission_enabled else "pass",
            "message": (
                "Feedback summary unexpectedly reports order submission enabled."
                if submission_enabled
                else "Feedback summary confirms order submission disabled."
            ),
        }
    )


def _add_benchmark_check(
    checks: list[dict[str, Any]],
    journal: LongTermDecisionJournal,
    feedback_summary: Mapping[str, Any] | None,
) -> None:
    benchmark_payload = (feedback_summary or {}).get("benchmark_guard") or {}
    should_pause = bool(benchmark_payload.get("should_pause_new_buys"))
    reason = str(benchmark_payload.get("reason") or "")
    if not benchmark_payload:
        result = BenchmarkGuard().evaluate(journal.summarize_benchmark_performance())
        should_pause = result.should_pause_new_buys
        reason = result.reason
    checks.append(
        {
            "check_id": "benchmark_guard",
            "status": "blocker" if should_pause else "pass",
            "message": reason or "Benchmark guard allows new buys.",
        }
    )


def _add_review_checks(checks: list[dict[str, Any]], journal: LongTermDecisionJournal) -> None:
    statuses = ReviewStatusBuilder(journal).build(limit=1000)
    buckets: dict[str, int] = {}
    for status in statuses.values():
        bucket = review_risk_bucket(status)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    broken_or_weakening = buckets.get("broken", 0) + buckets.get("weakening", 0)
    stale_or_due = buckets.get("stale", 0) + buckets.get("review_due", 0)
    if broken_or_weakening:
        status = "blocker"
        message = f"{broken_or_weakening} thesis reviews are broken or weakening."
    elif stale_or_due:
        status = "warning"
        message = f"{stale_or_due} thesis reviews are stale or due."
    else:
        status = "pass"
        message = "No broken, weakening, stale, or review-due theses found."
    checks.append(
        {
            "check_id": "review_thesis_state",
            "status": status,
            "message": message,
            "risk_bucket_counts": buckets,
        }
    )


def _add_action_plan_checks(
    checks: list[dict[str, Any]],
    journal: LongTermDecisionJournal,
    action_plan: Mapping[str, Any],
    protected_symbols: set[str],
) -> None:
    intents = [item for item in (action_plan.get("intents") or []) if isinstance(item, Mapping)]
    protected_hits = []
    missing_decisions = []
    for item in intents:
        symbol = str(item.get("symbol") or "").upper()
        source_symbol = str(item.get("source_symbol") or "").upper()
        order_intent = str(item.get("order_intent") or "").upper()
        if order_intent in {"BUY", "SELL", "SELL_TO_FUND_BUY"} and (
            symbol in protected_symbols or source_symbol in protected_symbols
        ):
            protected_hits.append(symbol or source_symbol)
        decision_id = str(item.get("decision_id") or "")
        if decision_id and not _decision_exists(journal, decision_id):
            missing_decisions.append(decision_id)
    checks.append(
        {
            "check_id": "protected_symbol_intents",
            "status": "blocker" if protected_hits else "pass",
            "message": (
                f"Protected symbols appear in order intents: {', '.join(sorted(set(protected_hits)))}."
                if protected_hits
                else "No protected symbols appear in order intents."
            ),
        }
    )
    checks.append(
        {
            "check_id": "decision_traceability",
            "status": "warning" if missing_decisions else "pass",
            "message": (
                f"Action plan references missing decisions: {', '.join(missing_decisions)}."
                if missing_decisions
                else "Action plan decision IDs are traceable where supplied."
            ),
        }
    )


def _add_buy_promotion_checks(
    checks: list[dict[str, Any]],
    action_plan: Mapping[str, Any],
) -> None:
    intents = [item for item in (action_plan.get("intents") or []) if isinstance(item, Mapping)]
    relevant_buy_orders: list[str] = []
    missing_promotion: list[str] = []
    non_actionable_order: list[str] = []
    pending_items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for item in intents:
        symbol = str(item.get("symbol") or "").upper()
        intent_type = str(item.get("intent_type") or "").upper()
        order_intent = str(item.get("order_intent") or "").upper()
        if intent_type == "BUY" and order_intent == "BUY":
            relevant_buy_orders.append(symbol)
        review = _promotion_review_from_intent(item)
        if not review:
            if intent_type == "BUY" and order_intent == "BUY":
                missing_promotion.append(symbol)
            continue
        decision = str(review.get("promotion_decision") or "UNKNOWN")
        counts[decision] = counts.get(decision, 0) + 1
        if intent_type == "BUY" and order_intent == "BUY" and decision != "ACTIONABLE_BUY":
            non_actionable_order.append(symbol)
        elif decision != "ACTIONABLE_BUY":
            pending_items.append(
                {
                    "symbol": symbol,
                    "promotion_decision": decision,
                    "followups": list(review.get("followups") or []),
                    "blockers": list(review.get("blockers") or []),
                }
            )

    if missing_promotion or non_actionable_order:
        status = "blocker"
        problems = []
        if missing_promotion:
            problems.append(f"missing promotion review for BUY intents: {', '.join(missing_promotion)}")
        if non_actionable_order:
            problems.append(f"non-actionable promotion attached to BUY intents: {', '.join(non_actionable_order)}")
        message = "Buy promotion state blocks automation: " + "; ".join(problems) + "."
    elif pending_items:
        status = "warning"
        message = "Buy promotion follow-up remains: " + "; ".join(
            _promotion_item_message(item) for item in pending_items
        )
    else:
        status = "pass"
        if relevant_buy_orders:
            message = "All stock BUY order intents have actionable promotion reviews."
        else:
            message = "No stock BUY order intents require promotion review."

    checks.append(
        {
            "check_id": "buy_promotion_state",
            "status": status,
            "message": message,
            "promotion_counts": counts,
            "actionable_count": counts.get("ACTIONABLE_BUY", 0),
            "pending_count": len(pending_items),
            "missing_promotion_count": len(missing_promotion),
            "non_actionable_order_count": len(non_actionable_order),
        }
    )


def _add_lifecycle_checks(checks: list[dict[str, Any]], paper_lifecycle_summary: Mapping[str, Any]) -> None:
    items = [item for item in (paper_lifecycle_summary.get("items") or []) if isinstance(item, Mapping)]
    error_symbols = [
        str(item.get("symbol") or "")
        for item in items
        if str(item.get("lifecycle_state") or "") == "execution_status_error"
    ]
    rejected_symbols = [
        str(item.get("symbol") or "")
        for item in items
        if str(item.get("lifecycle_state") or "") == "execution_rejected"
    ]
    checks.append(
        {
            "check_id": "paper_lifecycle_errors",
            "status": "blocker" if error_symbols else "pass",
            "message": (
                f"Paper lifecycle has status-refresh errors for: {', '.join(error_symbols)}."
                if error_symbols
                else "No paper lifecycle status-refresh errors found."
            ),
        }
    )
    checks.append(
        {
            "check_id": "paper_execution_rejections",
            "status": "warning" if rejected_symbols else "pass",
            "message": (
                f"Paper execution rejections need operator review: {', '.join(rejected_symbols)}."
                if rejected_symbols
                else "No paper execution rejections found."
            ),
        }
    )


def _add_deferred_research_check(
    checks: list[dict[str, Any]],
    feedback_summary: Mapping[str, Any] | None,
) -> None:
    queue = (feedback_summary or {}).get("deferred_research_queue") or {}
    items = queue.get("items") if isinstance(queue, Mapping) else None
    count = len(items or [])
    checks.append(
        {
            "check_id": "deferred_research_visible",
            "status": "warning" if count else "pass",
            "message": (
                f"{count} deferred research items remain open."
                if count
                else "No deferred research items supplied in feedback summary."
            ),
        }
    )


def _decision_exists(journal: LongTermDecisionJournal, decision_id: str) -> bool:
    try:
        journal.get_decision(decision_id)
        return True
    except KeyError:
        return False


def _promotion_review_from_intent(intent: Mapping[str, Any]) -> Mapping[str, Any]:
    review = intent.get("promotion_review")
    if isinstance(review, Mapping):
        return review
    risk_review = intent.get("risk_review")
    if isinstance(risk_review, Mapping):
        nested = risk_review.get("buy_promotion")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _promotion_item_message(item: Mapping[str, Any]) -> str:
    details = [*list(item.get("followups") or []), *list(item.get("blockers") or [])]
    suffix = f" ({', '.join(str(detail) for detail in details)})" if details else ""
    return f"{item.get('symbol')}: {item.get('promotion_decision')}{suffix}"


def _recommended_next_steps(checks: list[Mapping[str, Any]]) -> list[str]:
    steps = []
    for check in checks:
        if check.get("status") == "pass":
            continue
        steps.append(str(check.get("message") or check.get("check_id") or "Review readiness check."))
    if not steps:
        steps.append("No blockers found, but V1 remains advisory-only. Keep paper scheduler submission disabled.")
    return steps


__all__ = ["build_scheduler_readiness_markdown", "build_scheduler_readiness_report"]
