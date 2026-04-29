"""SQLite journal for long-term research decisions and benchmark outcomes."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from research.research_packet import ResearchPacket


class LongTermDecisionJournal:
    """Persist long-term decisions and later compare them against a benchmark."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = (
                Path(__file__).resolve().parents[2]
                / "ai_trader_data"
                / "longterm_decisions.db"
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
                CREATE TABLE IF NOT EXISTS longterm_decision_journal (
                    decision_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    company_name TEXT,
                    idea_source TEXT,
                    recommendation TEXT,
                    confidence INTEGER,
                    suggested_size_pct REAL,
                    key_thesis TEXT,
                    benchmark_symbol TEXT,
                    candidate_price_at_decision REAL,
                    benchmark_price_at_decision REAL,
                    candidate_return_pct REAL,
                    benchmark_return_pct REAL,
                    excess_return_pct REAL,
                    outcome_updated_at TEXT,
                    outcome_notes TEXT,
                    packet_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    raw_response TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record_decision(
        self,
        packet: ResearchPacket,
        *,
        decision: Mapping[str, Any],
        candidate_price: float | None = None,
        benchmark_price: float | None = None,
        raw_response: str = "",
    ) -> str:
        """Record one long-term research decision."""
        decision_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        recommendation = str(decision.get("recommendation", "")).upper()
        confidence = decision.get("confidence")
        suggested_size_pct = decision.get("suggested_size_pct")

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO longterm_decision_journal (
                    decision_id, timestamp, symbol, company_name, idea_source,
                    recommendation, confidence, suggested_size_pct, key_thesis,
                    benchmark_symbol, candidate_price_at_decision,
                    benchmark_price_at_decision, packet_json, decision_json,
                    raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    timestamp,
                    packet.symbol,
                    packet.company_name,
                    packet.idea_source,
                    recommendation,
                    int(confidence) if confidence is not None else None,
                    float(suggested_size_pct) if suggested_size_pct is not None else None,
                    decision.get("key_thesis", ""),
                    packet.benchmark_symbol,
                    candidate_price,
                    benchmark_price,
                    json.dumps(packet.to_dict(), sort_keys=True),
                    json.dumps(dict(decision), sort_keys=True),
                    raw_response,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return decision_id

    def update_outcome(
        self,
        decision_id: str,
        *,
        candidate_price: float,
        benchmark_price: float,
        notes: str = "",
    ) -> None:
        """Update a decision with active-vs-benchmark return data."""
        row = self.get_decision(decision_id)
        candidate_start = row["candidate_price_at_decision"]
        benchmark_start = row["benchmark_price_at_decision"]
        if not candidate_start or not benchmark_start:
            raise ValueError("Decision must have candidate and benchmark start prices.")

        candidate_return = (float(candidate_price) / float(candidate_start) - 1.0) * 100.0
        benchmark_return = (float(benchmark_price) / float(benchmark_start) - 1.0) * 100.0
        excess_return = candidate_return - benchmark_return

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE longterm_decision_journal
                SET candidate_return_pct = ?,
                    benchmark_return_pct = ?,
                    excess_return_pct = ?,
                    outcome_updated_at = ?,
                    outcome_notes = ?
                WHERE decision_id = ?
                """,
                (
                    round(candidate_return, 4),
                    round(benchmark_return, 4),
                    round(excess_return, 4),
                    datetime.now().isoformat(),
                    notes,
                    decision_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        """Fetch one decision row by id."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM longterm_decision_journal WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"Decision not found: {decision_id}")
        return dict(row)

    def list_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent long-term decisions, newest first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT decision_id, timestamp, symbol, company_name, idea_source,
                       recommendation, confidence, suggested_size_pct, key_thesis,
                       benchmark_symbol, candidate_price_at_decision,
                       benchmark_price_at_decision, candidate_return_pct,
                       benchmark_return_pct, excess_return_pct, outcome_updated_at
                FROM longterm_decision_journal
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def list_recommendation_table(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return latest actionable decisions ranked from most to least liked."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT decision_id, timestamp, symbol, company_name, recommendation,
                       confidence, suggested_size_pct, key_thesis, decision_json
                FROM longterm_decision_journal
                WHERE recommendation IN ('BUY', 'ADD', 'HOLD')
                ORDER BY timestamp DESC
                """
            ).fetchall()
        finally:
            conn.close()

        latest_by_symbol: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = dict(row)
            symbol = record["symbol"]
            if symbol in latest_by_symbol:
                continue
            decision = json.loads(record.get("decision_json") or "{}")
            reason = (
                decision.get("key_thesis")
                or decision.get("why_recommend")
                or decision.get("rationale")
                or record.get("key_thesis")
                or ""
            )
            info_link = (
                decision.get("info_link")
                or decision.get("research_url")
                or decision.get("source_url")
                or decision.get("source_link")
                or ""
            )
            record["reason"] = reason
            record["info_link"] = info_link
            record["company_name"] = record.get("company_name") or decision.get("company") or symbol
            record["action"] = decision.get("action") or record.get("recommendation") or ""
            record["service"] = decision.get("service") or record.get("idea_source") or ""
            record["rec_date"] = decision.get("rec_date") or decision.get("recommendation_date") or ""
            record["return_since_rec_pct"] = decision.get("return_since_rec_pct")
            record["current_price"] = decision.get("current_price")
            record["change_pct"] = decision.get("change_pct")
            record["previous_rank"] = decision.get("previous_rank") or "-"
            record["market_cap"] = decision.get("market_cap") or ""
            record["risk_type"] = decision.get("risk_type") or decision.get("type") or ""
            record["revenue_growth_1y_pct"] = decision.get("revenue_growth_1y_pct")
            record["estimated_return_range"] = decision.get("estimated_return_range") or ""
            record["estimated_max_drawdown_pct"] = decision.get("estimated_max_drawdown_pct")
            record["times_recommended"] = sum(1 for item in rows if item["symbol"] == symbol)
            record["discussion_count"] = decision.get("discussion_count")
            latest_by_symbol[symbol] = record

        ranked = sorted(
            latest_by_symbol.values(),
            key=lambda row: (
                int(row.get("confidence") or 0),
                float(row.get("suggested_size_pct") or 0.0),
            ),
            reverse=True,
        )[: int(limit)]

        for index, row in enumerate(ranked, start=1):
            row["rank"] = index
        return ranked

    def list_review_candidates(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent decisions with full packet/decision JSON for thesis review."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT decision_id, timestamp, symbol, company_name, recommendation,
                       confidence, key_thesis, packet_json, decision_json,
                       outcome_updated_at
                FROM longterm_decision_journal
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def summarize_benchmark_performance(self) -> dict[str, Any]:
        """Summarize decisions that have both active and benchmark outcomes."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT candidate_return_pct, benchmark_return_pct, excess_return_pct
                FROM longterm_decision_journal
                WHERE candidate_return_pct IS NOT NULL
                  AND benchmark_return_pct IS NOT NULL
                  AND excess_return_pct IS NOT NULL
                """
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "evaluated_decisions": 0,
                "average_candidate_return_pct": 0.0,
                "average_benchmark_return_pct": 0.0,
                "average_excess_return_pct": 0.0,
                "decisions_beating_benchmark": 0,
            }

        count = len(rows)
        candidate_returns = [float(row["candidate_return_pct"]) for row in rows]
        benchmark_returns = [float(row["benchmark_return_pct"]) for row in rows]
        excess_returns = [float(row["excess_return_pct"]) for row in rows]
        return {
            "evaluated_decisions": count,
            "average_candidate_return_pct": round(sum(candidate_returns) / count, 4),
            "average_benchmark_return_pct": round(sum(benchmark_returns) / count, 4),
            "average_excess_return_pct": round(sum(excess_returns) / count, 4),
            "decisions_beating_benchmark": sum(1 for value in excess_returns if value > 0),
        }
