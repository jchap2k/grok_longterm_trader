"""Audit-only whole-share paper workflow smoke report."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_execution import PaperExecutionBoundary
from longterm.paper_order_preview import build_paper_order_preview
from longterm.paper_price_map import (
    QuoteProvider,
    build_price_map_from_action_plan,
    build_price_map_from_explicit_map,
)
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_paper_workflow_smoke_report(
    action_plan: Mapping[str, Any],
    *,
    journal: LongTermDecisionJournal,
    ledger: PaperTradeLedger,
    profile: PortfolioProfile,
    portfolio_state: PortfolioState,
    quote_provider: QuoteProvider | None,
    explicit_price_map: Mapping[str, Any] | None = None,
    max_preview_age_hours: int = 24,
    allow_existing_submissions: bool = False,
) -> dict[str, Any]:
    """Run price-map -> whole-share preview -> execution audit without submitting."""
    protected_symbols = set(profile.protected_symbols) | set(portfolio_state.protected_symbols)
    if explicit_price_map is None:
        if quote_provider is None:
            raise ValueError("quote_provider is required when explicit_price_map is not supplied.")
        price_map_report = build_price_map_from_action_plan(
            action_plan,
            quote_provider=quote_provider,
            protected_symbols=protected_symbols,
        ).to_dict()
    else:
        price_map_report = build_price_map_from_explicit_map(
            action_plan,
            price_map=explicit_price_map,
            protected_symbols=protected_symbols,
        ).to_dict()
    preview = build_paper_order_preview(
        action_plan,
        portfolio_state=portfolio_state,
        profile=profile,
        order_model="whole_share",
        price_map=price_map_report["price_map"],
    )
    preview_log_id = ledger.record_preview(preview)
    preview["preview_log_id"] = preview_log_id
    audit = PaperExecutionBoundary(max_preview_age_hours=max_preview_age_hours).run(
        action_plan,
        journal=journal,
        ledger=ledger,
        profile=profile,
        portfolio_state=portfolio_state,
        submit=False,
    )
    promotion_summary = _promotion_summary(preview=preview, audit=audit)
    already_submitted_count = _duplicate_only_blocked_count(audit)
    unresolved_execution_blocked_count = _unresolved_execution_blocked_count(
        audit,
        allow_existing_submissions=allow_existing_submissions,
    )
    blockers = _blockers(
        price_map_report=price_map_report,
        preview=preview,
        audit=audit,
        promotion_summary=promotion_summary,
        allow_existing_submissions=allow_existing_submissions,
        already_submitted_count=already_submitted_count,
        unresolved_execution_blocked_count=unresolved_execution_blocked_count,
    )
    return {
        "schema_version": 2,
        "mode": "paper_workflow_smoke",
        "order_submission_enabled": False,
        "ready_for_supervised_submit": not blockers,
        "allow_existing_submissions": bool(allow_existing_submissions),
        "already_submitted_count": already_submitted_count if allow_existing_submissions else 0,
        "unresolved_execution_blocked_count": unresolved_execution_blocked_count,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "price_map": price_map_report,
        "promotion_summary": promotion_summary,
        "preview": preview,
        "execution_audit": audit,
        "notes": [
            "Audit-only paper workflow smoke. No broker orders were submitted.",
            "A ready report means artifacts are clean enough for a supervised paper submit review.",
        ],
    }


def build_paper_workflow_smoke_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Workflow Smoke",
        "",
        "Audit-only workflow. No broker orders were submitted.",
        "",
        f"- Ready for supervised submit: {'yes' if report.get('ready_for_supervised_submit') else 'no'}",
        f"- Blockers: {int(report.get('blocker_count') or 0)}",
        f"- Preview rows: {(report.get('preview') or {}).get('preview_count', 0)}",
        f"- Execution ready rows: {(report.get('execution_audit') or {}).get('ready_count', 0)}",
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


def _blockers(
    *,
    price_map_report: Mapping[str, Any],
    preview: Mapping[str, Any],
    audit: Mapping[str, Any],
    promotion_summary: Mapping[str, Any],
    allow_existing_submissions: bool,
    already_submitted_count: int,
    unresolved_execution_blocked_count: int,
) -> list[str]:
    blockers: list[str] = []
    if price_map_report.get("missing_symbols"):
        blockers.append("price_map_missing_symbols")
    if int(preview.get("blocked_count") or 0) > 0:
        blockers.append("preview_blocked_rows")
    if int(preview.get("allowed_count") or 0) <= 0:
        blockers.append("no_allowed_preview_rows")
    if unresolved_execution_blocked_count > 0:
        blockers.append("execution_audit_blocked_items")
    effective_ready_count = int(audit.get("ready_count") or 0)
    if allow_existing_submissions:
        effective_ready_count += already_submitted_count
    if effective_ready_count <= 0:
        blockers.append("no_execution_ready_items")
    if int(promotion_summary.get("blocked_count") or 0) > 0:
        blockers.append("buy_promotion_blocked_rows")
    if int(audit.get("submitted_count") or 0) > 0 or int(audit.get("rejected_count") or 0) > 0:
        blockers.append("execution_audit_not_submit_free")
    return blockers


def _duplicate_only_blocked_count(audit: Mapping[str, Any]) -> int:
    return sum(1 for item in audit.get("items") or [] if _is_duplicate_only_blocked_item(item))


def _unresolved_execution_blocked_count(
    audit: Mapping[str, Any],
    *,
    allow_existing_submissions: bool,
) -> int:
    count = 0
    for item in audit.get("items") or []:
        if str(item.get("status") or "") != "submit_blocked":
            continue
        if allow_existing_submissions and _is_duplicate_only_blocked_item(item):
            continue
        count += 1
    return count


def _is_duplicate_only_blocked_item(item: Mapping[str, Any]) -> bool:
    if str(item.get("status") or "") != "submit_blocked":
        return False
    reasons = {str(reason) for reason in (item.get("blocked_reasons") or [])}
    return reasons == {"duplicate_submission"}


def _promotion_summary(*, preview: Mapping[str, Any], audit: Mapping[str, Any]) -> dict[str, Any]:
    missing_items: set[str] = set()
    non_actionable_items: set[str] = set()
    for row in preview.get("previews") or []:
        reasons = {str(reason) for reason in (row.get("blocked_reasons") or [])}
        key = str(row.get("decision_id") or row.get("symbol") or "")
        if "missing_buy_promotion_review" in reasons:
            missing_items.add(key)
        if "buy_promotion_not_actionable" in reasons:
            non_actionable_items.add(key)
    for item in audit.get("items") or []:
        reasons = {str(reason) for reason in (item.get("blocked_reasons") or [])}
        key = str(item.get("decision_id") or item.get("symbol") or "")
        if "missing_buy_promotion_review" in reasons:
            missing_items.add(key)
        if "buy_promotion_not_actionable" in reasons:
            non_actionable_items.add(key)
    missing_count = len(missing_items)
    non_actionable_count = len(non_actionable_items)
    return {
        "blocked_count": missing_count + non_actionable_count,
        "missing_count": missing_count,
        "non_actionable_count": non_actionable_count,
    }


__all__ = ["build_paper_workflow_smoke_markdown", "build_paper_workflow_smoke_report"]
