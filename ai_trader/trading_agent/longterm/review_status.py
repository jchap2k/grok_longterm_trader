"""Build thesis review status maps from the long-term decision journal."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.thesis_monitor import ThesisMonitor
from research.intake import create_research_packet_from_idea


THESIS_RISK_BUCKETS = ("broken", "weakening", "stale", "review_due", "healthy", "unreviewed")


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
        latest_reviews = self.journal.latest_thesis_review_by_symbol()
        for row in self.journal.list_review_candidates(limit=limit):
            symbol = str(row["symbol"]).upper()
            if symbol in statuses:
                continue
            packet_data = json.loads(row.get("packet_json") or "{}")
            packet = create_research_packet_from_idea(packet_data)
            latest_review = latest_reviews.get(symbol)
            decision_timestamp = row.get("timestamp")
            review_is_current = _review_is_newer_or_same(latest_review, decision_timestamp)
            if symbol in self.last_review_dates_by_symbol:
                last_review_date = self.last_review_dates_by_symbol[symbol]
            elif review_is_current:
                last_review_date = _parse_date(latest_review.get("timestamp"))
            else:
                last_review_date = _parse_date(row.get("outcome_updated_at") or decision_timestamp)
            status = monitor.evaluate(
                packet,
                last_review_date=last_review_date,
                current_evidence=self.evidence_by_symbol.get(symbol, []),
            )
            if (
                review_is_current
                and symbol not in self.evidence_by_symbol
                and str(latest_review.get("thesis_state") or "").lower() in {"broken", "weakening"}
            ):
                status_payload = _status_from_recorded_review(latest_review, self.today)
                statuses[symbol] = status_payload
                continue
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


def _review_is_newer_or_same(review: Mapping[str, object] | None, decision_timestamp: str | None) -> bool:
    if not review:
        return False
    review_timestamp = str(review.get("timestamp") or "")
    if not decision_timestamp:
        return True
    return review_timestamp >= str(decision_timestamp)


def _status_from_recorded_review(review: Mapping[str, object], today: date) -> dict:
    review_date = _parse_date(str(review.get("timestamp") or ""))
    thesis_state = str(review.get("thesis_state") or "").lower()
    evidence = [str(item) for item in review.get("evidence") or []]
    return {
        "review_due": thesis_state in {"broken", "weakening"},
        "days_since_review": (today - review_date).days,
        "thesis_state": thesis_state,
        "matched_invalidation_conditions": evidence,
        "review_reason": f"Latest recorded thesis review marked the thesis {thesis_state}.",
        "latest_thesis_review_id": review.get("review_id"),
        "latest_thesis_review_timestamp": review.get("timestamp"),
    }


def review_risk_bucket(status: Mapping[str, object]) -> str:
    """Normalize review status into the shared rebalance/outcome risk bucket."""
    thesis_state = str(status.get("thesis_state") or "").lower()
    if thesis_state in {"broken", "invalidated"}:
        return "broken"
    if thesis_state in {"weakening", "deteriorating", "at_risk"}:
        return "weakening"
    if thesis_state == "stale":
        return "stale"
    if thesis_state == "healthy":
        return "review_due" if bool(status.get("review_due")) else "healthy"
    if bool(status.get("review_due")):
        return "review_due"
    return "unreviewed"
