"""Build thesis review status maps from the long-term decision journal."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.thesis_monitor import ThesisMonitor
from research.intake import create_research_packet_from_idea


class ReviewStatusBuilder:
    """Derive per-symbol review status without mutating journal records."""

    def __init__(
        self,
        journal: LongTermDecisionJournal,
        *,
        today: date | None = None,
        last_review_dates_by_symbol: Mapping[str, date] | None = None,
        evidence_by_symbol: Mapping[str, list[str]] | None = None,
    ):
        self.journal = journal
        self.today = today or date.today()
        self.last_review_dates_by_symbol = {
            symbol.upper(): last_review_date
            for symbol, last_review_date in (last_review_dates_by_symbol or {}).items()
        }
        self.evidence_by_symbol = {
            symbol.upper(): evidence
            for symbol, evidence in (evidence_by_symbol or {}).items()
        }

    def build(self, *, limit: int = 20) -> dict[str, dict]:
        statuses: dict[str, dict] = {}
        monitor = ThesisMonitor(today=self.today)
        for row in self.journal.list_review_candidates(limit=limit):
            symbol = str(row["symbol"]).upper()
            if symbol in statuses:
                continue
            packet_data = json.loads(row.get("packet_json") or "{}")
            packet = create_research_packet_from_idea(packet_data)
            last_review_date = self.last_review_dates_by_symbol.get(symbol) or _parse_date(
                row.get("outcome_updated_at") or row.get("timestamp")
            )
            status = monitor.evaluate(
                packet,
                last_review_date=last_review_date,
                current_evidence=self.evidence_by_symbol.get(symbol, []),
            )
            statuses[symbol] = {
                "review_due": status.review_due,
                "days_since_review": status.days_since_review,
                "thesis_state": status.thesis_state,
                "matched_invalidation_conditions": status.matched_invalidation_conditions,
                "review_reason": status.reason,
            }
        return statuses


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.fromisoformat(value).date()
