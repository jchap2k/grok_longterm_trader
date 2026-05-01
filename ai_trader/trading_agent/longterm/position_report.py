"""Read-only position intelligence reports for held long-term positions."""

from __future__ import annotations

import json
from typing import Any, Mapping

from longterm.capital_alert import CapitalNeededEmail
from longterm.decision_journal import LongTermDecisionJournal
from longterm.feedback_refresh import outcome_freshness
from longterm.paper_preview_status import PaperPreviewStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.report_builder import RecommendationTableBuilder
from longterm.review_status import ReviewStatusBuilder


def build_position_intelligence_report(
    journal: LongTermDecisionJournal,
    *,
    portfolio_state: PortfolioState,
    paper_ledger: PaperTradeLedger | None = None,
    feedback_summary: Mapping[str, Any] | None = None,
    limit: int = 100,
) -> str:
    """Build a read-only report of collected knowledge for current holdings."""
    paper_status = PaperPreviewStatusBuilder(paper_ledger).build() if paper_ledger else None
    review_status = ReviewStatusBuilder(journal).build(limit=limit)
    recommendation_rows = RecommendationTableBuilder(
        journal,
        review_status_by_symbol=review_status,
        paper_preview_status_by_decision=paper_status.by_decision_id if paper_status else None,
        paper_preview_status_by_symbol=paper_status.by_symbol if paper_status else None,
    ).build(limit=limit)
    rows_by_symbol = {str(row.get("symbol") or "").upper(): row for row in recommendation_rows}
    packets_by_symbol = _latest_packets_by_symbol(journal, limit=limit)
    freshness_by_symbol = _feedback_by_symbol(
        feedback_summary,
        "outcome_freshness",
        default_items=outcome_freshness(journal).get("items") or [],
    )
    eligibility_by_symbol = _feedback_by_symbol(feedback_summary, "eligibility")

    lines = [
        "# Long-Term Position Intelligence Report",
        "",
        "## Portfolio Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Cash | {_money(portfolio_state.cash)} |",
        f"| Active sleeve value | {_money(portfolio_state.active_market_value)} |",
        f"| Protected/core value | {_money(portfolio_state.protected_market_value)} |",
        f"| Total tracked value | {_money(portfolio_state.cash + portfolio_state.active_market_value + portfolio_state.protected_market_value)} |",
        "",
        "## Positions",
        "",
    ]

    for holding in portfolio_state.holdings:
        symbol = holding.symbol
        row = rows_by_symbol.get(symbol, {})
        profile = journal.get_symbol_feedback_profile(symbol) or {}
        packet = packets_by_symbol.get(symbol, {})
        freshness = freshness_by_symbol.get(symbol, {})
        eligibility = eligibility_by_symbol.get(symbol, {})
        title_company = row.get("company_name") or profile.get("company_name") or packet.get("company_name") or symbol
        gaps = _knowledge_gaps(row=row, profile=profile, freshness=freshness)

        lines.extend(
            [
                f"## {symbol} - {title_company}",
                "",
                f"- Current value: {_money(holding.market_value)}",
                f"- Protected/core: {'yes' if symbol in portfolio_state.protected_symbols else 'no'}",
                f"- Latest recommendation: {row.get('recommendation') or 'none'}",
                f"- Rank: {row.get('rank') or 'n/a'}",
                f"- Times recommended: {profile.get('recommendation_count') or row.get('times_recommended') or 0}",
                f"- Latest thesis: {profile.get('latest_thesis') or row.get('key_thesis') or 'No recommendation profile found.'}",
                f"- Review status: {row.get('thesis_state') or 'unknown'}; review due: {_yes_no(row.get('review_due'))}",
                f"- Paper preview: {row.get('paper_preview_status') or profile.get('latest_paper_preview_status') or 'none'}",
                f"- Paper preview blocked reasons: {_join(row.get('paper_preview_blocked_reasons') or profile.get('paper_preview_blocked_reasons')) or 'none'}",
                f"- Paper execution eligibility: {eligibility.get('status') or 'not evaluated'}",
                f"- Reconciliation: {profile.get('latest_reconciliation_status') or 'none'}",
                f"- Reconciliation notes: {_join(profile.get('paper_reconciliation_notes')) or 'none'}",
                f"- Outcome vs FXAIX: {_pct(_latest_excess_return(journal, symbol))}",
                f"- Outcome freshness: {freshness.get('freshness_state') or 'never_refreshed'}",
                f"- New information: {_join(profile.get('new_information') or row.get('new_information_notes')) or 'none'}",
                f"- Invalidation conditions: {_join(packet.get('invalidation_conditions')) or 'none'}",
                f"- Knowledge gaps: {_join(gaps) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)


def build_position_intelligence_email(
    journal: LongTermDecisionJournal,
    *,
    portfolio_state: PortfolioState,
    recipient_email: str,
    paper_ledger: PaperTradeLedger | None = None,
    feedback_summary: Mapping[str, Any] | None = None,
    period: str = "monthly",
) -> CapitalNeededEmail:
    """Build a Brevo-compatible email payload for periodic position review."""
    markdown = build_position_intelligence_report(
        journal,
        portfolio_state=portfolio_state,
        paper_ledger=paper_ledger,
        feedback_summary=feedback_summary,
    )
    normalized_period = period.lower().strip() or "monthly"
    subject = f"{normalized_period.title()} Long-Term Position Intelligence Report"
    disclaimer = (
        "This report is informational only. It summarizes collected research, "
        "feedback, and portfolio context. It is not authorization to submit "
        "paper or live orders."
    )
    text_body = f"{disclaimer}\n\n{markdown}"
    html_body = (
        "<html><body>"
        f"<p><strong>{_escape_html(disclaimer)}</strong></p>"
        f"<pre style=\"font-family: Consolas, monospace; white-space: pre-wrap;\">{_escape_html(markdown)}</pre>"
        "</body></html>"
    )
    return CapitalNeededEmail(
        should_send=bool(recipient_email),
        recipient_email=recipient_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        metadata={
            "period": normalized_period,
            "holding_count": len(portfolio_state.holdings),
            "active_market_value": portfolio_state.active_market_value,
            "protected_market_value": portfolio_state.protected_market_value,
        },
    )


def _latest_packets_by_symbol(journal: LongTermDecisionJournal, *, limit: int) -> dict[str, dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    for row in journal.list_review_candidates(limit=limit):
        symbol = str(row.get("symbol") or "").upper()
        if symbol in packets:
            continue
        packet_json = row.get("packet_json")
        packets[symbol] = json.loads(packet_json) if packet_json else {}
    return packets


def _feedback_by_symbol(
    feedback_summary: Mapping[str, Any] | None,
    key: str,
    *,
    default_items: list[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    payload = (feedback_summary or {}).get(key) or {}
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if items is None:
        items = default_items or []
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and symbol not in result:
            result[symbol] = dict(item)
    return result


def _knowledge_gaps(
    *,
    row: Mapping[str, Any],
    profile: Mapping[str, Any],
    freshness: Mapping[str, Any],
) -> list[str]:
    gaps = []
    if not profile:
        gaps.append("no recommendation profile")
    state = str(freshness.get("freshness_state") or "never_refreshed")
    if state == "never_refreshed":
        gaps.append("outcome never refreshed")
    elif state == "stale":
        gaps.append("outcome stale")
    if not row.get("thesis_state"):
        gaps.append("thesis review missing")
    return gaps


def _latest_excess_return(journal: LongTermDecisionJournal, symbol: str) -> float | None:
    for row in journal.list_recent_decisions(limit=10000):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row.get("excess_return_pct")
    return None


def _money(value: float | int | None) -> str:
    return f"${float(value or 0.0):,.2f}"


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{round(float(value), 2)}%"


def _yes_no(value: Any) -> str:
    if value is None:
        return "unknown"
    return "yes" if bool(value) else "no"


def _join(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value if str(item).strip())
    return str(value)


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = ["build_position_intelligence_email", "build_position_intelligence_report"]
