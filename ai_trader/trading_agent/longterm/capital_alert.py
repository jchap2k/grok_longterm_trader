"""Capital-needed alert helpers for long-term recommendations."""

from __future__ import annotations

from dataclasses import dataclass

from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState


@dataclass(frozen=True)
class CapitalNeededAlert:
    should_alert: bool
    top_symbol: str
    estimated_capital_needed: float
    markdown: str
    reason: str = ""


@dataclass(frozen=True)
class CapitalNeededEmail:
    should_send: bool
    recipient_email: str
    subject: str
    text_body: str
    html_body: str
    metadata: dict


def build_capital_needed_alert(
    journal: LongTermDecisionJournal,
    *,
    active_sleeve_value: float,
    available_cash: float,
    portfolio_state: PortfolioState | None = None,
    min_confidence: int = 85,
    limit: int = 5,
) -> CapitalNeededAlert:
    """Build an alert payload when high-conviction ideas exceed available cash."""
    suppression_reason = _capital_request_suppression_reason(journal, portfolio_state)
    if suppression_reason:
        return CapitalNeededAlert(False, "", 0.0, "", suppression_reason)

    rows = [
        row
        for row in journal.list_recommendation_table(limit=limit)
        if int(row.get("confidence") or 0) >= min_confidence
    ]
    if not rows:
        return CapitalNeededAlert(False, "", 0.0, "", "No high-conviction candidates.")

    top = rows[0]
    target_cash = active_sleeve_value * (float(top.get("suggested_size_pct") or 0.0) / 100.0)
    needed = round(max(0.0, target_cash - available_cash), 2)
    if needed <= 0:
        return CapitalNeededAlert(False, top["symbol"], 0.0, "", "Available cash is sufficient.")

    lines = [
        "# Capital Needed Alert",
        "",
        (
            f"Top candidate: {top['symbol']} "
            f"({top.get('recommendation')}, confidence {top.get('confidence')})"
        ),
        f"Estimated additional active-sleeve cash needed: ${needed:,.2f}",
        "",
        "| Rank | Symbol | Confidence | Target Size | Reason | Link |",
        "|---:|---|---:|---:|---|---|",
    ]
    for row in rows:
        link = row.get("info_link") or ""
        lines.append(
            "| {rank} | {symbol} | {confidence} | {size}% | {reason} | {link} |".format(
                rank=row.get("rank", ""),
                symbol=row.get("symbol", ""),
                confidence=row.get("confidence", ""),
                size=row.get("suggested_size_pct") or "",
                reason=row.get("reason") or "",
                link=link,
            )
        )

    return CapitalNeededAlert(True, top["symbol"], needed, "\n".join(lines) + "\n", "Capital shortfall.")


def build_capital_needed_email(
    journal: LongTermDecisionJournal,
    *,
    active_sleeve_value: float,
    available_cash: float,
    recipient_email: str,
    portfolio_state: PortfolioState | None = None,
    min_confidence: int = 85,
    limit: int = 5,
) -> CapitalNeededEmail:
    """Build a provider-agnostic email payload for later Brevo delivery."""
    alert = build_capital_needed_alert(
        journal,
        active_sleeve_value=active_sleeve_value,
        available_cash=available_cash,
        portfolio_state=portfolio_state,
        min_confidence=min_confidence,
        limit=limit,
    )
    if not alert.should_alert:
        return CapitalNeededEmail(False, recipient_email, "", "", "", {})

    rows = [
        row
        for row in journal.list_recommendation_table(limit=limit)
        if int(row.get("confidence") or 0) >= min_confidence
    ]
    subject = (
        f"Capital needed: {alert.top_symbol} "
        f"needs ${alert.estimated_capital_needed:,.2f} active-sleeve cash"
    )
    disclaimer = (
        "This alert is informational only. It is not an instruction to deposit funds, "
        "and the system must not automatically deposit money. Do not automatically deposit "
        "funds or sell protected holdings."
    )
    text_body = "\n".join([disclaimer, "", alert.markdown])
    html_rows = "\n".join(
        "<tr>"
        f"<td>{row.get('rank', '')}</td>"
        f"<td>{_short_id(row.get('decision_id'))}</td>"
        f"<td>{row.get('symbol', '')}</td>"
        f"<td>{row.get('confidence', '')}</td>"
        f"<td>{row.get('suggested_size_pct') or ''}%</td>"
        f"<td>{_escape_html(row.get('reason') or '')}</td>"
        f"<td>{_format_link(row.get('info_link') or '')}</td>"
        "</tr>"
        for row in rows
    )
    html_body = (
        "<html><body>"
        f"<p>{_escape_html(disclaimer)}</p>"
        f"<p><strong>Estimated additional active-sleeve cash needed:</strong> "
        f"${alert.estimated_capital_needed:,.2f}</p>"
        "<table>"
        "<thead><tr><th>Rank</th><th>Decision ID</th><th>Symbol</th>"
        "<th>Confidence</th><th>Target Size</th><th>Reason</th><th>Link</th></tr></thead>"
        f"<tbody>{html_rows}</tbody>"
        "</table>"
        "</body></html>"
    )
    return CapitalNeededEmail(
        True,
        recipient_email,
        subject,
        text_body,
        html_body,
        {
            "top_symbol": alert.top_symbol,
            "estimated_capital_needed": alert.estimated_capital_needed,
            "candidate_count": len(rows),
        },
    )


def _short_id(value: str | None) -> str:
    return (value or "")[:8]


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_link(value: str) -> str:
    if not value:
        return ""
    escaped = _escape_html(value)
    return f'<a href="{escaped}">{escaped}</a>'


def _capital_request_suppression_reason(
    journal: LongTermDecisionJournal,
    portfolio_state: PortfolioState | None,
) -> str:
    if portfolio_state is None:
        return ""

    latest_by_symbol: dict[str, str] = {}
    for row in journal.list_recent_decisions(limit=100):
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = str(row.get("recommendation") or "").upper()

    for holding in portfolio_state.holdings:
        symbol = holding.symbol.upper()
        if symbol in portfolio_state.protected_symbols or holding.market_value <= 0:
            continue
        recommendation = latest_by_symbol.get(symbol, "")
        if recommendation in {"SELL", "REDUCE"}:
            return (
                f"Existing active holding {symbol} has a sell/reduce recommendation; "
                "fund the stronger idea from active-sleeve rotation before requesting more capital."
            )
    return ""
