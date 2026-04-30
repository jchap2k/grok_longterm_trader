"""Markdown reports for long-term decisions and benchmark comparison."""

from __future__ import annotations

from datetime import date
from typing import Mapping, Protocol

from longterm.decision_journal import LongTermDecisionJournal
from longterm.review_status import ReviewStatusBuilder


class RecommendationEnricher(Protocol):
    """Optional read-only enrichment provider for recommendation rows."""

    def enrich(self, symbol: str) -> dict:
        ...


class RecommendationTableBuilder:
    """Build enriched recommendation rows without mutating the decision journal."""

    def __init__(
        self,
        journal: LongTermDecisionJournal,
        *,
        enricher: RecommendationEnricher | None = None,
        review_status_by_symbol: dict[str, dict] | None = None,
    ):
        self.journal = journal
        self.enricher = enricher
        self.review_status_by_symbol = {
            symbol.upper(): status
            for symbol, status in (review_status_by_symbol or {}).items()
        }

    def build(self, *, limit: int = 20) -> list[dict]:
        rows = [dict(row) for row in self.journal.list_recommendation_table(limit=limit)]
        for row in rows:
            symbol = row["symbol"].upper()
            if self.enricher is not None:
                row.update(self.enricher.enrich(symbol) or {})
            row.update(self.review_status_by_symbol.get(symbol, {}))
        return rows


def _fmt_pct(value) -> str:
    if value is None:
        return ""
    return f"{round(float(value), 2)}%"


def _fmt_price(value) -> str:
    if value is None:
        return ""
    return f"${float(value):,.2f}"


def _fmt_number(value) -> str:
    if value is None:
        return ""
    return f"{float(value):g}"


def _short_id(value: str | None) -> str:
    return (value or "")[:8]


def build_markdown_report(
    journal: LongTermDecisionJournal,
    *,
    limit: int = 20,
    enricher: RecommendationEnricher | None = None,
    review_status_by_symbol: dict[str, dict] | None = None,
    review_status_today: date | None = None,
    last_review_dates_by_symbol: Mapping[str, date] | None = None,
) -> str:
    """Build a concise markdown report from the long-term decision journal."""
    summary = journal.summarize_benchmark_performance()
    rows = journal.list_recent_decisions(limit=limit)
    if review_status_by_symbol is None:
        review_status_by_symbol = ReviewStatusBuilder(
            journal,
            today=review_status_today,
            last_review_dates_by_symbol=last_review_dates_by_symbol,
        ).build(limit=limit)

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
        "| Rank | Rank Score | Decision ID | Symbol | Company | Action | Service | Price | Change | Previous Rank | Market Cap | Type | 1Y Rev. Growth | Return Since Rec | Rec Date | Est. Return | Est. Max Drawdown | Review Due | Thesis State | Data As Of | Times Rec'd | Notes | Rank Reason | Reason | Link |",
        "|---:|---:|---|---|---|---|---|---:|---:|---:|---:|---|---:|---:|---|---:|---:|---|---|---|---:|---:|---|---|---|",
    ]

    for row in RecommendationTableBuilder(
        journal,
        enricher=enricher,
        review_status_by_symbol=review_status_by_symbol,
        ).build(limit=limit):
        lines.append(
            "| {rank} | {rank_score} | {decision_id} | {symbol} | {company} | {action} | {service} | {price} | {change} | {previous_rank} | {market_cap} | {risk_type} | {growth} | {return_since_rec} | {rec_date} | {return_range} | {drawdown} | {review_due} | {thesis_state} | {data_as_of} | {times} | {notes} | {rank_reason} | {reason} | {link} |".format(
                rank=row.get("rank", ""),
                rank_score=_fmt_number(row.get("ranking_score")),
                decision_id=_short_id(row.get("decision_id")),
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
                review_due=row.get("review_due") if row.get("review_due") is not None else "",
                thesis_state=row.get("thesis_state") or "",
                data_as_of=row.get("data_as_of") or "",
                times=row.get("times_recommended") or "",
                notes=row.get("discussion_count") or "",
                rank_reason=row.get("rank_reason") or "",
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
