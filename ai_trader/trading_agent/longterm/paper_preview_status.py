"""Read-only status hydration from the paper preview ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from longterm.paper_trade_ledger import PaperTradeLedger


@dataclass(frozen=True)
class PaperPreviewStatusMaps:
    by_decision_id: dict[str, dict[str, Any]]
    by_symbol: dict[str, dict[str, Any]]


class PaperPreviewStatusBuilder:
    """Build recommendation/next-action status maps from recorded previews."""

    def __init__(self, ledger: PaperTradeLedger | None):
        self.ledger = ledger

    def build(self, *, limit: int = 1000) -> PaperPreviewStatusMaps:
        if self.ledger is None:
            return PaperPreviewStatusMaps(by_decision_id={}, by_symbol={})
        rows = self.ledger.list_previews(limit=limit)
        by_decision: dict[str, dict[str, Any]] = {}
        by_symbol: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = _status_payload(row)
            decision_id = str(row.get("decision_id") or "")
            symbol = str(row.get("symbol") or "").upper()
            if decision_id and decision_id not in by_decision:
                by_decision[decision_id] = dict(payload)
            if symbol:
                existing = by_symbol.get(symbol, {})
                by_symbol[symbol] = _merge_symbol_status(existing, payload)
        return PaperPreviewStatusMaps(by_decision_id=by_decision, by_symbol=by_symbol)


def _status_payload(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "")
    return {
        "paper_preview_status": status,
        "paper_preview_log_id": row.get("preview_log_id") or "",
        "paper_preview_id": row.get("preview_id") or "",
        "paper_preview_symbol": row.get("symbol") or "",
        "paper_preview_side": row.get("side") or "",
        "paper_preview_ready_count": 1 if status == "ready" else 0,
        "paper_preview_blocked_count": 1 if status == "blocked" else 0,
        "paper_preview_no_order_count": 1 if status == "no_order" else 0,
        "paper_preview_blocked_reasons": list(row.get("blocked_reasons") or []),
    }


def _merge_symbol_status(existing: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return dict(payload)
    merged = dict(existing)
    merged["paper_preview_ready_count"] = int(merged.get("paper_preview_ready_count") or 0) + int(
        payload.get("paper_preview_ready_count") or 0
    )
    merged["paper_preview_blocked_count"] = int(merged.get("paper_preview_blocked_count") or 0) + int(
        payload.get("paper_preview_blocked_count") or 0
    )
    merged["paper_preview_no_order_count"] = int(merged.get("paper_preview_no_order_count") or 0) + int(
        payload.get("paper_preview_no_order_count") or 0
    )
    reasons = list(merged.get("paper_preview_blocked_reasons") or [])
    reasons.extend(payload.get("paper_preview_blocked_reasons") or [])
    merged["paper_preview_blocked_reasons"] = reasons
    if payload.get("paper_preview_status") == "blocked":
        merged.update(
            {
                key: value
                for key, value in payload.items()
                if key
                in {
                    "paper_preview_status",
                    "paper_preview_log_id",
                    "paper_preview_id",
                    "paper_preview_symbol",
                    "paper_preview_side",
                }
            }
        )
    return merged


__all__ = ["PaperPreviewStatusBuilder", "PaperPreviewStatusMaps"]
