"""
Decision Logger - Capture all AI trading decisions for post-market analysis

This module logs every decision the trading agent makes:
- Entry decisions (trade or skip)
- Exit decisions (when and why)
- Candidate evaluations (all stocks considered)

Purpose: Enable daily re-simulation to validate decision quality
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import json
import uuid

logger = logging.getLogger(__name__)


class DecisionLogger:
    """Logs all trading decisions for post-market analysis."""

    def __init__(self, db_path: str = "ai_trader/ai_trader_data/trading_performance.db"):
        """
        Initialize decision logger.

        Args:
            db_path: Path to trading performance database
        """
        self.db_path = Path(db_path)
        self._ensure_tables_exist()

    def _ensure_tables_exist(self):
        """Create decision logging tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Decision log table
        c.execute('''
            CREATE TABLE IF NOT EXISTS decision_log (
                decision_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decision_type TEXT NOT NULL,

                ai_reasoning TEXT,
                conviction_score REAL,
                risk_assessment TEXT,

                price REAL,
                vix REAL,
                gap_pct REAL,
                catalyst TEXT,
                sector TEXT,

                lessons_applied TEXT,

                actual_outcome TEXT,
                actual_pnl REAL,
                trade_id TEXT
            )
        ''')

        # Candidate evaluations table
        c.execute('''
            CREATE TABLE IF NOT EXISTS candidate_evaluations (
                eval_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,

                scan_reason TEXT,
                ai_score REAL,
                ai_reasoning TEXT,
                decision TEXT,
                skip_reason TEXT,

                price REAL,
                gap_pct REAL,
                vix REAL,
                catalyst TEXT,
                sector TEXT
            )
        ''')

        conn.commit()
        conn.close()

        logger.info("Decision logging tables initialized")

    def log_entry_decision(
        self,
        symbol: str,
        decision: str,  # 'trade' or 'skip'
        ai_reasoning: str,
        conviction_score: float,
        context: Dict[str, Any],
        lessons_applied: Optional[List[str]] = None
    ) -> str:
        """
        Log an entry decision (trade or skip).

        Args:
            symbol: Stock symbol
            decision: 'trade' or 'skip'
            ai_reasoning: Full AI explanation
            conviction_score: 0.0 to 1.0
            context: Market context (price, vix, gap, catalyst, etc)
            lessons_applied: List of lesson IDs that influenced decision

        Returns:
            decision_id: Unique ID for this decision
        """
        decision_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            INSERT INTO decision_log (
                decision_id, timestamp, symbol, decision_type,
                ai_reasoning, conviction_score, risk_assessment,
                price, vix, gap_pct, catalyst, sector,
                lessons_applied
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            decision_id,
            timestamp,
            symbol,
            'entry',
            ai_reasoning,
            conviction_score,
            context.get('risk_assessment', ''),
            context.get('price'),
            context.get('vix'),
            context.get('gap_pct'),
            context.get('catalyst', ''),
            context.get('sector', ''),
            json.dumps(lessons_applied) if lessons_applied else None
        ))

        conn.commit()
        conn.close()

        logger.info(f"Logged entry decision for {symbol}: {decision} (conviction: {conviction_score:.2f})")
        return decision_id

    def log_exit_decision(
        self,
        symbol: str,
        exit_reason: str,
        ai_reasoning: str,
        context: Dict[str, Any],
        trade_id: Optional[str] = None
    ) -> str:
        """
        Log an exit decision.

        Args:
            symbol: Stock symbol
            exit_reason: Why we exited (stop_loss, take_profit, etc)
            ai_reasoning: AI's explanation
            context: Current market context
            trade_id: Associated trade ID

        Returns:
            decision_id: Unique ID for this decision
        """
        decision_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            INSERT INTO decision_log (
                decision_id, timestamp, symbol, decision_type,
                ai_reasoning, price, vix, trade_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            decision_id,
            timestamp,
            symbol,
            'exit',
            ai_reasoning,
            context.get('price'),
            context.get('vix'),
            trade_id
        ))

        conn.commit()
        conn.close()

        logger.info(f"Logged exit decision for {symbol}: {exit_reason}")
        return decision_id

    def log_candidate_evaluation(
        self,
        symbol: str,
        scan_reason: str,
        ai_score: float,
        ai_reasoning: str,
        decision: str,  # 'trade', 'skip', 'watch'
        context: Dict[str, Any],
        skip_reason: Optional[str] = None
    ) -> str:
        """
        Log evaluation of a candidate stock.

        This logs ALL candidates considered, not just those traded.
        Critical for measuring opportunity cost.

        Args:
            symbol: Stock symbol
            scan_reason: Why it was scanned (earnings_catalyst, gap_scan, etc)
            ai_score: AI's score (0.0 to 1.0)
            ai_reasoning: AI's analysis
            decision: 'trade', 'skip', or 'watch'
            context: Market context
            skip_reason: If skipped, why?

        Returns:
            eval_id: Unique ID for this evaluation
        """
        eval_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            INSERT INTO candidate_evaluations (
                eval_id, timestamp, symbol,
                scan_reason, ai_score, ai_reasoning, decision, skip_reason,
                price, gap_pct, vix, catalyst, sector
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            eval_id,
            timestamp,
            symbol,
            scan_reason,
            ai_score,
            ai_reasoning,
            decision,
            skip_reason,
            context.get('price'),
            context.get('gap_pct'),
            context.get('vix'),
            context.get('catalyst', ''),
            context.get('sector', '')
        ))

        conn.commit()
        conn.close()

        logger.debug(f"Logged candidate evaluation for {symbol}: {decision} (score: {ai_score:.2f})")
        return eval_id

    def update_decision_outcome(
        self,
        decision_id: str,
        outcome: str,  # 'win', 'loss', 'scratch'
        pnl: float
    ):
        """
        Update decision with actual outcome after trade completes.

        Args:
            decision_id: Decision ID from log_entry_decision
            outcome: Actual outcome
            pnl: Actual P&L
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            UPDATE decision_log
            SET actual_outcome = ?, actual_pnl = ?
            WHERE decision_id = ?
        ''', (outcome, pnl, decision_id))

        conn.commit()
        conn.close()

        logger.info(f"Updated decision {decision_id} outcome: {outcome} (${pnl:.2f})")

    def get_todays_decisions(self) -> List[Dict[str, Any]]:
        """
        Get all decisions made today.

        Returns:
            List of decision dictionaries
        """
        today = datetime.now().date().isoformat()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute('''
            SELECT * FROM decision_log
            WHERE DATE(timestamp) = ?
            ORDER BY timestamp
        ''', (today,))

        decisions = [dict(row) for row in c.fetchall()]

        conn.close()

        return decisions

    def get_todays_candidates(self) -> List[Dict[str, Any]]:
        """
        Get all candidate evaluations from today.

        Returns:
            List of evaluation dictionaries
        """
        today = datetime.now().date().isoformat()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute('''
            SELECT * FROM candidate_evaluations
            WHERE DATE(timestamp) = ?
            ORDER BY timestamp
        ''', (today,))

        candidates = [dict(row) for row in c.fetchall()]

        conn.close()

        return candidates
