"""Markdown reports for long-term decisions and benchmark comparison."""

from __future__ import annotations

from longterm.decision_journal import LongTermDecisionJournal


def _fmt_pct(value) -> str:
    if value is None:
        return ""
    return f"{round(float(value), 2)}%"


def _fmt_price(value) -> str:
    if value is None:
        return ""
    return f"${float(value):,.2f}"


def build_markdown_report(
    journal: LongTermDecisionJournal,
    *,
    limit: int = 20,
) -> str:
    """Build a concise markdown report from the long-term decision journal."""
    summary = journal.summarize_benchmark_performance()
    rows = journal.list_recent_decisions(limit=limit)

    lines = [
        "# Long-Term Trader Decision Report",
        "",
        f"Evaluated decisions: {summary['evaluated_decisions']}",
        f"Average candidate return: {_fmt_pct(summary['average_candidate_return_pct'])}",
        f"Average benchmark return: {_fmt_pct(summary['average_benchmark_return_pct'])}",
        f"Average excess return vs benchmark: {_fmt_pct(summary['average_excess_return_pct'])}",
        f"Decisions beating benchmark: {summary['decisions_beating_benchmark']}",
        "",
        "## Recommendation Table",
        "",
        "| Rank | Symbol | Company | Action | Service | Price | Change | Previous Rank | Market Cap | Type | 1Y Rev. Growth | Return Since Rec | Rec Date | Est. Return | Est. Max Drawdown | Times Rec'd | Notes | Reason | Link |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]

    for row in journal.list_recommendation_table(limit=limit):
        lines.append(
            "| {rank} | {symbol} | {company} | {action} | {service} | {price} | {change} | {previous_rank} | {market_cap} | {risk_type} | {growth} | {return_since_rec} | {rec_date} | {return_range} | {drawdown} | {times} | {notes} | {reason} | {link} |".format(
                rank=row.get("rank", ""),
                symbol=row.get("symbol", ""),
                company=row.get("company_name") or "",
                action=row.get("action") or "",
                service=row.get("service") or "",
                price=_fmt_price(row.get("current_price")),
                change=_fmt_pct(row.get("change_pct")),
                previous_rank=row.get("previous_rank") or "-",
                market_cap=row.get("market_cap") or "",
                risk_type=row.get("risk_type") or "",
                growth=_fmt_pct(row.get("revenue_growth_1y_pct")),
                return_since_rec=_fmt_pct(row.get("return_since_rec_pct")),
                rec_date=row.get("rec_date") or "",
                return_range=row.get("estimated_return_range") or "",
                drawdown=_fmt_pct(row.get("estimated_max_drawdown_pct")),
                times=row.get("times_recommended") or "",
                notes=row.get("discussion_count") or "",
                reason=row.get("reason") or "",
                link=row.get("info_link") or "",
            )
        )

    lines.extend(
        [
            "",
            "## Benchmark Outcomes",
            "",
            "| Symbol | Recommendation | Confidence | Excess Return |",
            "|---|---:|---:|---:|",
        ]
    )

    for row in rows:
        lines.append(
            "| {symbol} | {recommendation} | {confidence} | {excess} |".format(
                symbol=row.get("symbol") or "",
                recommendation=row.get("recommendation") or "",
                confidence=row.get("confidence") if row.get("confidence") is not None else "",
                excess=_fmt_pct(row.get("excess_return_pct")),
            )
        )

    return "\n".join(lines) + "\n"
