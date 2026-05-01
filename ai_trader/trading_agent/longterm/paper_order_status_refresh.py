"""Read-only paper order status refresh for submitted Alpaca paper orders."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol

from longterm.paper_trade_ledger import PaperTradeLedger


class PaperOrderStatusBroker(Protocol):
    def get_order_status(self, order_id: str) -> Any: ...


class PaperOrderStatusRefresh:
    """Refresh submitted paper order statuses and append ledger events."""

    def run(
        self,
        *,
        ledger: PaperTradeLedger,
        broker: PaperOrderStatusBroker,
        limit: int = 100,
    ) -> dict[str, Any]:
        submitted = _latest_submitted_by_broker_order_id(ledger, limit=limit)
        items = []
        status_counts: dict[str, int] = {}
        events_recorded = 0
        skipped_count = 0
        error_count = 0
        for broker_order_id, submitted_event in submitted.items():
            try:
                order = broker.get_order_status(broker_order_id)
                status = _status_value(getattr(order, "status", ""))
                item = _item_from_order(submitted_event, order=order, status=status)
                if ledger.has_execution_status(
                    broker_order_id=broker_order_id,
                    status=status,
                ):
                    item["recorded"] = False
                    item["skipped_reason"] = "status_already_recorded"
                    skipped_count += 1
                else:
                    ledger.record_execution_event(item)
                    item["recorded"] = True
                    events_recorded += 1
                status_counts[status] = status_counts.get(status, 0) + 1
                items.append(item)
            except Exception as exc:
                item = _error_item(submitted_event, error=str(exc))
                ledger.record_execution_event(item)
                items.append(item)
                error_count += 1
                status_counts["status_refresh_error"] = status_counts.get("status_refresh_error", 0) + 1
        return {
            "schema_version": 1,
            "mode": "paper_order_status_refresh",
            "paper_mode": True,
            "live_mode": False,
            "submitted_order_count": len(submitted),
            "refreshed_count": len(items),
            "events_recorded": events_recorded,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "status_counts": status_counts,
            "items": items,
            "notes": [
                "Read-only status refresh. No paper or live orders were submitted.",
                "Status updates are appended to PaperTradeLedger execution events.",
            ],
        }


def build_paper_order_status_refresh_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Order Status Refresh",
        "",
        "Read-only status refresh. No orders were submitted.",
        "",
        f"- Submitted orders checked: {payload.get('submitted_order_count', 0)}",
        f"- Events recorded: {payload.get('events_recorded', 0)}",
        f"- Skipped: {payload.get('skipped_count', 0)}",
        f"- Errors: {payload.get('error_count', 0)}",
        "",
        "| Broker Order | Symbol | Status | Recorded |",
        "| --- | --- | --- | --- |",
    ]
    for item in payload.get("items") or []:
        lines.append(
            "| {order} | {symbol} | {status} | {recorded} |".format(
                order=item.get("broker_order_id") or "",
                symbol=item.get("symbol") or "",
                status=item.get("status") or "",
                recorded="yes" if item.get("recorded") else "no",
            )
        )
    return "\n".join(lines) + "\n"


def _latest_submitted_by_broker_order_id(
    ledger: PaperTradeLedger,
    *,
    limit: int,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in ledger.list_execution_events(limit=limit):
        broker_order_id = str(row.get("broker_order_id") or "")
        if row.get("status") == "submitted" and broker_order_id and broker_order_id not in result:
            result[broker_order_id] = row
    return result


def _item_from_order(submitted_event: Mapping[str, Any], *, order: Any, status: str) -> dict[str, Any]:
    payload = submitted_event.get("event_json") or {}
    return {
        "decision_id": submitted_event.get("decision_id") or "",
        "preview_log_id": submitted_event.get("preview_log_id") or "",
        "preview_id": submitted_event.get("preview_id") or "",
        "plan_id": submitted_event.get("plan_id") or "",
        "broker_order_id": submitted_event.get("broker_order_id") or "",
        "symbol": submitted_event.get("symbol") or getattr(order, "symbol", ""),
        "side": submitted_event.get("side") or _status_value(getattr(order, "side", "")),
        "notional": submitted_event.get("notional") or 0.0,
        "status": status,
        "error": "",
        "paper_mode": True,
        "live_mode": False,
        "client_order_id": payload.get("client_order_id") or "",
        "submission_attempt_id": payload.get("submission_attempt_id") or "",
        "filled_quantity": float(getattr(order, "filled_quantity", 0) or 0),
        "filled_price": getattr(order, "filled_price", None),
        "filled_at": _iso_or_empty(getattr(order, "filled_at", None)),
        "status_refreshed_at": datetime.now().isoformat(),
    }


def _error_item(submitted_event: Mapping[str, Any], *, error: str) -> dict[str, Any]:
    payload = submitted_event.get("event_json") or {}
    return {
        "decision_id": submitted_event.get("decision_id") or "",
        "preview_log_id": submitted_event.get("preview_log_id") or "",
        "preview_id": submitted_event.get("preview_id") or "",
        "plan_id": submitted_event.get("plan_id") or "",
        "broker_order_id": submitted_event.get("broker_order_id") or "",
        "symbol": submitted_event.get("symbol") or "",
        "side": submitted_event.get("side") or "",
        "notional": submitted_event.get("notional") or 0.0,
        "status": "status_refresh_error",
        "error": error,
        "paper_mode": True,
        "live_mode": False,
        "client_order_id": payload.get("client_order_id") or "",
        "submission_attempt_id": payload.get("submission_attempt_id") or "",
    }


def _status_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "").lower()


def _iso_or_empty(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


__all__ = ["PaperOrderStatusRefresh", "build_paper_order_status_refresh_markdown"]
