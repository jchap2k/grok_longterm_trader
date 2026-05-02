"""Audit-only whole-share paper workflow smoke report."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_execution import PaperExecutionBoundary
from longterm.paper_order_preview import build_paper_order_preview
from longterm.paper_price_map import QuoteProvider, build_price_map_from_action_plan
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
    quote_provider: QuoteProvider,
    max_preview_age_hours: int = 24,
) -> dict[str, Any]:
    """Run price-map -> whole-share preview -> execution audit without submitting."""
    price_map_report = build_price_map_from_action_plan(
        action_plan,
        quote_provider=quote_provider,
        protected_symbols=set(profile.protected_symbols) | set(portfolio_state.protected_symbols),
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
    blockers = _blockers(price_map_report=price_map_report, preview=preview, audit=audit)
    return {
        "schema_version": 1,
        "mode": "paper_workflow_smoke",
        "order_submission_enabled": False,
        "ready_for_supervised_submit": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "price_map": price_map_report,
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
) -> list[str]:
    blockers: list[str] = []
    if price_map_report.get("missing_symbols"):
        blockers.append("price_map_missing_symbols")
    if int(preview.get("blocked_count") or 0) > 0:
        blockers.append("preview_blocked_rows")
    if int(preview.get("allowed_count") or 0) <= 0:
        blockers.append("no_allowed_preview_rows")
    if int(audit.get("blocked_count") or 0) > 0:
        blockers.append("execution_audit_blocked_items")
    if int(audit.get("ready_count") or 0) <= 0:
        blockers.append("no_execution_ready_items")
    if int(audit.get("submitted_count") or 0) > 0 or int(audit.get("rejected_count") or 0) > 0:
        blockers.append("execution_audit_not_submit_free")
    return blockers


__all__ = ["build_paper_workflow_smoke_markdown", "build_paper_workflow_smoke_report"]
