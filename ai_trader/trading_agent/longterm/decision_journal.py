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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS longterm_action_plan_journal (
                    plan_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS longterm_deferred_research_queue (
                    deferred_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    missing_fields_json TEXT NOT NULL,
                    provenance_bucket TEXT,
                    suggested_next_step TEXT,
                    suggested_enrichment_command TEXT,
                    parent_decision_id TEXT,
                    source_run_id TEXT,
                    priority_score REAL NOT NULL,
                    deferred_json TEXT NOT NULL,
                    resolved_at TEXT,
                    resolution_notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_longterm_deferred_status_symbol
                ON longterm_deferred_research_queue (status, symbol, timestamp)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_longterm_deferred_parent_decision
                ON longterm_deferred_research_queue (parent_decision_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS longterm_recommendation_rank_history (
                    snapshot_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    ranking_score REAL,
                    recommendation TEXT,
                    decision_id TEXT,
                    PRIMARY KEY (snapshot_id, symbol)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_longterm_rank_history_symbol_timestamp
                ON longterm_recommendation_rank_history (symbol, timestamp)
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
            record["rank_movement"] = decision.get("rank_movement") or "new"
            record["market_cap"] = decision.get("market_cap") or ""
            record["risk_type"] = decision.get("risk_type") or decision.get("type") or ""
            record["revenue_growth_1y_pct"] = decision.get("revenue_growth_1y_pct")
            record["estimated_return_range"] = decision.get("estimated_return_range") or ""
            record["estimated_max_drawdown_pct"] = decision.get("estimated_max_drawdown_pct")
            record["times_recommended"] = sum(1 for item in rows if item["symbol"] == symbol)
            record["discussion_count"] = decision.get("discussion_count")
            ranking_score = _recommendation_ranking_score(record)
            record["ranking_score"] = ranking_score
            record["rank_reason"] = _rank_reason(record, ranking_score)
            latest_by_symbol[symbol] = record

        ranked = sorted(
            latest_by_symbol.values(),
            key=lambda row: (
                float(row.get("ranking_score") or 0.0),
                str(row.get("timestamp") or ""),
            ),
            reverse=True,
        )[: int(limit)]

        for index, row in enumerate(ranked, start=1):
            row["rank"] = index
        previous_ranks = self.latest_recommendation_rank_by_symbol()
        for row in ranked:
            previous = previous_ranks.get(str(row.get("symbol") or "").upper())
            if previous:
                row["previous_rank"] = int(previous["rank"])
                row["rank_movement"] = _rank_movement(int(row["rank"]), int(previous["rank"]))
            else:
                row["previous_rank"] = "-"
                row["rank_movement"] = "new"
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

    def record_action_plan(self, plan: Mapping[str, Any]) -> str:
        """Persist a dry-run account action plan for paper/live reconciliation."""
        plan_id = str(plan.get("plan_id") or uuid.uuid4())
        timestamp = datetime.now().isoformat()
        mode = str(plan.get("mode") or "dry_run")
        status = str(plan.get("status") or "")
        payload = dict(plan)
        payload["plan_id"] = plan_id

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO longterm_action_plan_journal (
                    plan_id, timestamp, mode, status, plan_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    timestamp,
                    mode,
                    status,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return plan_id

    def list_action_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent dry-run account action plans, newest first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT plan_id, timestamp, mode, status, plan_json
                FROM longterm_action_plan_journal
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            record = dict(row)
            record["plan_json"] = json.loads(record.get("plan_json") or "{}")
            results.append(record)
        return results

    def record_deferred_research_item(
        self,
        item: Mapping[str, Any],
        *,
        parent_decision_id: str | None = None,
        source_run_id: str | None = None,
    ) -> str:
        """Persist a skipped research item so enrichment work survives runs."""
        deferred_id = str(item.get("deferred_id") or uuid.uuid4())
        payload = dict(item)
        payload["deferred_id"] = deferred_id
        timestamp = datetime.now().isoformat()
        missing_fields = _normalize_missing_fields(payload.get("missing_fields"))
        priority_score = _deferred_research_priority(payload, missing_fields)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO longterm_deferred_research_queue (
                    deferred_id, timestamp, symbol, status, reason,
                    missing_fields_json, provenance_bucket, suggested_next_step,
                    suggested_enrichment_command, parent_decision_id, source_run_id,
                    priority_score, deferred_json, resolved_at, resolution_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deferred_id,
                    timestamp,
                    str(payload.get("symbol") or "UNKNOWN").upper(),
                    str(payload.get("status") or "open"),
                    str(payload.get("reason") or ""),
                    json.dumps(missing_fields, sort_keys=True),
                    str(payload.get("provenance_bucket") or ""),
                    str(payload.get("suggested_next_step") or ""),
                    str(payload.get("suggested_enrichment_command") or ""),
                    parent_decision_id or payload.get("parent_decision_id"),
                    source_run_id or payload.get("source_run_id"),
                    priority_score,
                    json.dumps(payload, sort_keys=True),
                    payload.get("resolved_at"),
                    payload.get("resolution_notes"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return deferred_id

    def list_deferred_research_items(
        self,
        *,
        limit: int = 20,
        include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        """Return deferred research/enrichment tasks, newest first."""
        where_clause = "" if include_resolved else "WHERE status != 'resolved'"
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT deferred_id, timestamp, symbol, status, reason,
                       missing_fields_json, provenance_bucket, suggested_next_step,
                       suggested_enrichment_command, parent_decision_id, source_run_id,
                       priority_score, deferred_json, resolved_at, resolution_notes
                FROM longterm_deferred_research_queue
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            record = dict(row)
            record["missing_fields"] = json.loads(record.pop("missing_fields_json") or "[]")
            record["deferred_json"] = json.loads(record.get("deferred_json") or "{}")
            results.append(record)
        return results

    def resolve_deferred_research_item(self, deferred_id: str, *, notes: str = "") -> None:
        """Mark a deferred research item resolved after enrichment/retry work."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                UPDATE longterm_deferred_research_queue
                SET status = 'resolved',
                    resolved_at = ?,
                    resolution_notes = ?
                WHERE deferred_id = ?
                """,
                (datetime.now().isoformat(), notes, deferred_id),
            )
            conn.commit()
        finally:
            conn.close()
        if cursor.rowcount == 0:
            raise KeyError(f"Deferred research item not found: {deferred_id}")

    def record_recommendation_rank_snapshot(self, rows: list[Mapping[str, Any]]) -> str:
        """Persist the current recommendation-table ordering for future movement."""
        snapshot_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                if not symbol:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO longterm_recommendation_rank_history (
                        snapshot_id, timestamp, symbol, rank, ranking_score,
                        recommendation, decision_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        timestamp,
                        symbol,
                        int(row.get("rank") or 0),
                        float(row["ranking_score"]) if row.get("ranking_score") is not None else None,
                        str(row.get("recommendation") or row.get("action") or ""),
                        str(row.get("decision_id") or ""),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return snapshot_id

    def latest_recommendation_rank_by_symbol(self) -> dict[str, dict[str, Any]]:
        """Return symbol -> latest persisted recommendation rank row."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            latest_snapshot = conn.execute(
                """
                SELECT snapshot_id
                FROM longterm_recommendation_rank_history
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()
            if latest_snapshot is None:
                return {}
            rows = conn.execute(
                """
                SELECT snapshot_id, timestamp, symbol, rank, ranking_score,
                       recommendation, decision_id
                FROM longterm_recommendation_rank_history
                WHERE snapshot_id = ?
                """,
                (latest_snapshot["snapshot_id"],),
            ).fetchall()
        finally:
            conn.close()
        return {str(row["symbol"]).upper(): dict(row) for row in rows}

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


def _recommendation_ranking_score(row: Mapping[str, Any]) -> float:
    """Score recommendation rows for active-sleeve actionability."""
    action = str(row.get("recommendation") or row.get("action") or "").upper()
    action_boost = {
        "BUY": 10.0,
        "ADD": 8.0,
        "HOLD": 0.0,
    }.get(action, 0.0)
    confidence = float(row.get("confidence") or 0.0)
    size = float(row.get("suggested_size_pct") or 0.0)
    return round(confidence + (size * 2.0) + action_boost, 4)


def _rank_reason(row: Mapping[str, Any], ranking_score: float) -> str:
    action = str(row.get("recommendation") or row.get("action") or "").upper()
    confidence = int(row.get("confidence") or 0)
    size = float(row.get("suggested_size_pct") or 0.0)
    return (
        f"{action} recommendation, confidence {confidence}, "
        f"suggested size {size:g}%, ranking score {ranking_score:g}."
    )


def _rank_movement(current_rank: int, previous_rank: int) -> str:
    if current_rank < previous_rank:
        return "up"
    if current_rank > previous_rank:
        return "down"
    return "unchanged"


def _normalize_missing_fields(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value:
        return [str(value)]
    return []


def _deferred_research_priority(item: Mapping[str, Any], missing_fields: list[str]) -> float:
    provenance = str(item.get("provenance_bucket") or "").lower()
    score = 10.0
    if "motley" in provenance:
        score += 20.0
    elif provenance in {"sp500", "etf_holdings", "manual"}:
        score += 10.0
    score += max(0.0, 10.0 - (len(missing_fields) * 2.0))
    return round(score, 4)
