"""Hydrate paper execution ledger events into report-friendly status maps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from longterm.paper_trade_ledger import PaperTradeLedger


@dataclass(frozen=True)
class PaperExecutionStatus:
    by_decision_id: dict[str, dict[str, Any]]
    by_symbol: dict[str, dict[str, Any]]


class PaperExecutionStatusBuilder:
    """Build latest paper execution status maps from append-only ledger events."""

    def __init__(self, ledger: PaperTradeLedger):
        self.ledger = ledger

    def build(self, *, limit: int = 10000) -> PaperExecutionStatus:
        by_decision: dict[str, dict[str, Any]] = {}
        by_symbol: dict[str, dict[str, Any]] = {}
        for row in self.ledger.list_execution_events(limit=limit):
            decision_id = str(row.get("decision_id") or "")
            symbol = str(row.get("symbol") or "").upper()
            normalized = _normalize_event(row)
            if decision_id and decision_id not in by_decision:
                by_decision[decision_id] = normalized
            if symbol:
                item = by_symbol.setdefault(
                    symbol,
                    {
                        "paper_execution_latest_status": normalized["paper_execution_status"],
                        "paper_execution_broker_order_id": normalized["paper_execution_broker_order_id"],
                        "paper_execution_filled_count": 0,
                        "paper_execution_rejected_count": 0,
                        "paper_execution_error_count": 0,
                    },
                )
                status = normalized["paper_execution_status"]
                if status == "filled":
                    item["paper_execution_filled_count"] += 1
                elif status == "rejected":
                    item["paper_execution_rejected_count"] += 1
                elif status == "status_refresh_error":
                    item["paper_execution_error_count"] += 1
        return PaperExecutionStatus(by_decision_id=by_decision, by_symbol=by_symbol)


def _normalize_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("event_json") or {}
    return {
        "paper_execution_status": row.get("status") or "",
        "paper_execution_broker_order_id": row.get("broker_order_id") or "",
        "paper_execution_event_id": row.get("event_id") or "",
        "paper_execution_timestamp": row.get("timestamp") or "",
        "paper_execution_client_order_id": payload.get("client_order_id") or "",
        "paper_execution_submission_attempt_id": payload.get("submission_attempt_id") or "",
        "paper_execution_filled_quantity": payload.get("filled_quantity") or 0,
        "paper_execution_filled_price": payload.get("filled_price"),
        "paper_execution_error": row.get("error") or "",
    }


__all__ = ["PaperExecutionStatus", "PaperExecutionStatusBuilder"]
