"""SQLite ledger for non-submitting paper preview artifacts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class PaperTradeLedger:
    """Persist paper preview rows before any real paper execution exists."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = (
                Path(__file__).resolve().parents[2]
                / "ai_trader_data"
                / "longterm_paper_trade_ledger.db"
            )
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS longterm_paper_preview_log (
                    row_id TEXT PRIMARY KEY,
                    preview_log_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    plan_id TEXT,
                    preview_id TEXT,
                    decision_id TEXT,
                    transaction_id TEXT,
                    trade_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT,
                    notional REAL NOT NULL,
                    allowed INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    order_submission_enabled INTEGER NOT NULL,
                    blocked_reasons_json TEXT NOT NULL,
                    preview_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_longterm_paper_preview_decision
                ON longterm_paper_preview_log (decision_id, timestamp)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS longterm_paper_execution_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    trade_id TEXT,
                    preview_log_id TEXT,
                    preview_id TEXT,
                    plan_id TEXT,
                    decision_id TEXT,
                    broker_order_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    notional REAL,
                    status TEXT NOT NULL,
                    error TEXT,
                    event_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record_preview(
        self,
        preview_payload: Mapping[str, Any],
        *,
        timestamp: str | None = None,
    ) -> str:
        """Persist each row in a paper-order preview payload."""
        preview_log_id = str(uuid.uuid4())
        timestamp = timestamp or datetime.now().isoformat()
        plan_id = str(preview_payload.get("plan_id") or "")
        order_submission_enabled = bool(preview_payload.get("order_submission_enabled"))
        rows = list(preview_payload.get("previews") or [])
        conn = sqlite3.connect(self.db_path)
        try:
            for row in rows:
                status = _preview_status(row)
                conn.execute(
                    """
                    INSERT INTO longterm_paper_preview_log (
                        row_id, preview_log_id, timestamp, plan_id, preview_id,
                        decision_id, transaction_id, trade_id, symbol, side,
                        order_type, notional, allowed, status,
                        order_submission_enabled, blocked_reasons_json, preview_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        preview_log_id,
                        timestamp,
                        plan_id or str(row.get("plan_id") or ""),
                        str(row.get("preview_id") or ""),
                        str(row.get("decision_id") or ""),
                        str(row.get("transaction_id") or ""),
                        row.get("trade_id"),
                        str(row.get("symbol") or "").upper(),
                        str(row.get("side") or ""),
                        str(row.get("order_type") or ""),
                        float(row.get("notional") or 0.0),
                        1 if row.get("allowed") else 0,
                        status,
                        1 if order_submission_enabled else 0,
                        json.dumps(list(row.get("blocked_reasons") or []), sort_keys=True),
                        json.dumps(dict(row), sort_keys=True),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return preview_log_id

    def record_execution_event(self, event: Mapping[str, Any]) -> str:
        """Persist a paper execution event without submitting broker orders."""
        decision_id = str(event.get("decision_id") or "").strip()
        if not decision_id:
            raise ValueError("Paper execution events require decision_id for traceability.")
        event_id = str(event.get("event_id") or uuid.uuid4())
        timestamp = str(event.get("timestamp") or datetime.now().isoformat())
        payload = dict(event)
        payload["event_id"] = event_id
        payload["decision_id"] = decision_id
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO longterm_paper_execution_events (
                    event_id, timestamp, trade_id, preview_log_id, preview_id,
                    plan_id, decision_id, broker_order_id, symbol, side,
                    notional, status, error, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    timestamp,
                    event.get("trade_id"),
                    str(event.get("preview_log_id") or ""),
                    str(event.get("preview_id") or ""),
                    str(event.get("plan_id") or ""),
                    decision_id,
                    str(event.get("broker_order_id") or ""),
                    str(event.get("symbol") or "").upper(),
                    str(event.get("side") or ""),
                    float(event.get("notional") or 0.0),
                    str(event.get("status") or ""),
                    str(event.get("error") or ""),
                    json.dumps(payload, sort_keys=True),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return event_id

    def record_eligibility_events(self, eligibility_payload: Mapping[str, Any]) -> dict[str, Any]:
        """Persist idempotent eligibility evaluation events for auditability."""
        recorded = 0
        skipped = 0
        event_ids: list[str] = []
        for item in eligibility_payload.get("items") or []:
            decision_id = str(item.get("decision_id") or "").strip()
            if not decision_id:
                raise ValueError("Eligibility events require decision_id for traceability.")
            preview_id = str(item.get("preview_id") or "")
            is_ready = bool(item.get("eligible")) or str(item.get("status") or "") == "eligible"
            status = "eligibility_ready" if is_ready else "eligibility_blocked"
            if self._eligibility_event_exists(decision_id, preview_id, status):
                skipped += 1
                continue
            event = {
                "decision_id": decision_id,
                "journal_short_id": decision_id[:8],
                "preview_log_id": item.get("preview_log_id") or "",
                "preview_id": preview_id,
                "trade_id": item.get("trade_id") or "",
                "plan_id": eligibility_payload.get("plan_id") or item.get("plan_id") or "",
                "symbol": item.get("symbol") or "",
                "side": item.get("side") or "",
                "notional": item.get("notional") or 0.0,
                "status": status,
                "error": "; ".join(str(reason) for reason in (item.get("blocked_reasons") or [])),
                "action": item.get("action") or "",
                "blocked_reasons": list(item.get("blocked_reasons") or []),
                "eligibility_status": item.get("status") or "",
                "eligibility_item": dict(item),
                "requires_revalidation": True,
                "order_submission_enabled": False,
            }
            event_ids.append(self.record_execution_event(event))
            recorded += 1
        return {
            "events_recorded": recorded,
            "events_skipped": skipped,
            "event_ids": event_ids,
        }

    def list_execution_events(
        self,
        limit: int = 50,
        *,
        decision_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List paper execution events, newest first."""
        where = "WHERE decision_id = ?" if decision_id else ""
        params = (decision_id, int(limit)) if decision_id else (int(limit),)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT *
                FROM longterm_paper_execution_events
                {where}
                ORDER BY timestamp DESC, event_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        results = []
        for row in rows:
            record = dict(row)
            record["event_json"] = json.loads(record.get("event_json") or "{}")
            results.append(record)
        return results

    def has_submitted_execution(
        self,
        *,
        preview_id: str = "",
        client_order_id: str = "",
    ) -> bool:
        """Return whether a preview/client id already produced a submitted event."""
        for row in self.list_execution_events(limit=10000):
            if row.get("status") != "submitted":
                continue
            payload = row.get("event_json") or {}
            if preview_id and str(row.get("preview_id") or "") == preview_id:
                return True
            if client_order_id and str(payload.get("client_order_id") or "") == client_order_id:
                return True
        return False

    def latest_execution_by_decision(self) -> dict[str, dict[str, Any]]:
        """Map decision ids to the latest recorded execution event."""
        result: dict[str, dict[str, Any]] = {}
        for row in self.list_execution_events(limit=10000):
            decision_id = str(row.get("decision_id") or "")
            if decision_id and decision_id not in result:
                result[decision_id] = row
        return result

    def _eligibility_event_exists(self, decision_id: str, preview_id: str, status: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT 1
                FROM longterm_paper_execution_events
                WHERE decision_id = ?
                  AND preview_id = ?
                  AND status = ?
                LIMIT 1
                """,
                (decision_id, preview_id, status),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def list_previews(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recorded preview rows, newest first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM longterm_paper_preview_log
                ORDER BY timestamp DESC, preview_id ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        return [_hydrate_preview_row(dict(row)) for row in rows]

    def preview_status_by_decision(self) -> dict[str, dict[str, Any]]:
        """Summarize recorded preview rows by decision id."""
        summary: dict[str, dict[str, Any]] = {}
        for row in self.list_previews(limit=10000):
            decision_id = str(row.get("decision_id") or "")
            if not decision_id:
                continue
            item = summary.setdefault(
                decision_id,
                {
                    "decision_id": decision_id,
                    "ready_count": 0,
                    "blocked_count": 0,
                    "no_order_count": 0,
                    "latest_preview_log_id": row.get("preview_log_id"),
                },
            )
            status = str(row.get("status") or "")
            if status == "ready":
                item["ready_count"] += 1
            elif status == "blocked":
                item["blocked_count"] += 1
            elif status == "no_order":
                item["no_order_count"] += 1
        return summary

    def preview_status_by_plan(self) -> dict[str, dict[str, Any]]:
        """Summarize recorded preview rows by plan id."""
        summary: dict[str, dict[str, Any]] = {}
        for row in self.list_previews(limit=10000):
            plan_id = str(row.get("plan_id") or "")
            if not plan_id:
                continue
            item = summary.setdefault(plan_id, {"plan_id": plan_id, "row_count": 0})
            item["row_count"] += 1
        return summary

    def summarize_previews(self) -> dict[str, Any]:
        """Summarize all recent preview rows."""
        rows = self.list_previews(limit=10000)
        status_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "total_rows": len(rows),
            "status_counts": status_counts,
            "decision_count": len({row.get("decision_id") for row in rows if row.get("decision_id")}),
            "order_submission_enabled": any(bool(row.get("order_submission_enabled")) for row in rows),
        }


def build_paper_preview_ledger_summary_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Preview Ledger Summary",
        "",
        f"- Total rows: {summary.get('total_rows', 0)}",
        f"- Decisions linked: {summary.get('decision_count', 0)}",
        f"- Any submission enabled: `{str(summary.get('order_submission_enabled')).lower()}`",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in sorted((summary.get("status_counts") or {}).items()):
        lines.append(f"| {status} | {count} |")
    return "\n".join(lines) + "\n"


def _preview_status(row: Mapping[str, Any]) -> str:
    if str(row.get("side") or "").lower() == "none":
        return "no_order"
    return "ready" if row.get("allowed") else "blocked"


def _hydrate_preview_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed"] = bool(row.get("allowed"))
    row["order_submission_enabled"] = bool(row.get("order_submission_enabled"))
    row["blocked_reasons"] = json.loads(row.pop("blocked_reasons_json") or "[]")
    row["preview_json"] = json.loads(row.get("preview_json") or "{}")
    return row


__all__ = [
    "PaperTradeLedger",
    "build_paper_preview_ledger_summary_markdown",
]
