"""Capital-needed alert helpers for long-term recommendations."""

from __future__ import annotations

from dataclasses import dataclass

from longterm.decision_journal import LongTermDecisionJournal


@dataclass(frozen=True)
class CapitalNeededAlert:
    should_alert: bool
    top_symbol: str
    estimated_capital_needed: float
    markdown: str


def build_capital_needed_alert(
    journal: LongTermDecisionJournal,
    *,
    active_sleeve_value: float,
    available_cash: float,
    min_confidence: int = 85,
    limit: int = 5,
) -> CapitalNeededAlert:
    """Build an alert payload when high-conviction ideas exceed available cash."""
    rows = [
        row
        for row in journal.list_recommendation_table(limit=limit)
        if int(row.get("confidence") or 0) >= min_confidence
    ]
    if not rows:
        return CapitalNeededAlert(False, "", 0.0, "")

    top = rows[0]
    target_cash = active_sleeve_value * (float(top.get("suggested_size_pct") or 0.0) / 100.0)
    needed = round(max(0.0, target_cash - available_cash), 2)
    if needed <= 0:
        return CapitalNeededAlert(False, top["symbol"], 0.0, "")

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

    return CapitalNeededAlert(True, top["symbol"], needed, "\n".join(lines) + "\n")
