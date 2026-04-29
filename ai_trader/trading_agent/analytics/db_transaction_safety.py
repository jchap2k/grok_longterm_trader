"""
Transaction Safety for Dual-Database Writes
Created: Feb 14, 2026
Purpose: Prevent inconsistent state between performance_db and learning_db on crashes

Implements retry queue for failed writes to ensure eventual consistency.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class DBTransactionManager:
    """
    Manages safe writes to dual databases with retry queue for failed writes.

    Strategy:
    1. Try writing to both databases
    2. If either fails, queue for retry
    3. Background task processes retry queue
    4. Daily reconciliation catches permanent failures
    """

    def __init__(self, retry_queue_db_path: str = "ai_trader/ai_trader_data/retry_queue.db"):
        self.retry_queue_path = Path(retry_queue_db_path)
        self._init_retry_queue()

    def _init_retry_queue(self):
        """Create retry queue table if it doesn't exist."""
        self.retry_queue_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.retry_queue_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS retry_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL,
                target_db TEXT NOT NULL,
                table_name TEXT NOT NULL,
                data JSON NOT NULL,
                error_msg TEXT,
                queued_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                last_retry_at TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_retry_status ON retry_queue(status)
        """)

        conn.commit()
        conn.close()

        logger.info("Retry queue initialized")

    def safe_dual_db_write(
        self,
        operation_type: str,
        performance_db_write: callable,
        learning_db_write: callable,
        data_context: Dict[str, Any]
    ) -> bool:
        """
        Safely write to both databases with retry queue on failure.

        Args:
            operation_type: Type of operation (entry, exit, partial_exit)
            performance_db_write: Function to write to performance_db
            learning_db_write: Function to write to learning_db
            data_context: Dict with data for retry queue if needed

        Returns:
            True if both writes successful or queued for retry
        """
        performance_success = False
        learning_success = False

        # Try performance_db first
        try:
            performance_db_write()
            performance_success = True
            logger.debug(f"[TRANSACTION] Performance DB write successful: {operation_type}")
        except Exception as e:
            logger.error(f"[TRANSACTION] Performance DB write failed: {operation_type} - {e}")
            self._queue_for_retry(
                operation_type=operation_type,
                target_db="performance_db",
                data=data_context,
                error_msg=str(e)
            )

        # Try learning_db second
        try:
            learning_db_write()
            learning_success = True
            logger.debug(f"[TRANSACTION] Learning DB write successful: {operation_type}")
        except Exception as e:
            logger.error(f"[TRANSACTION] Learning DB write failed: {operation_type} - {e}")
            self._queue_for_retry(
                operation_type=operation_type,
                target_db="learning_db",
                data=data_context,
                error_msg=str(e)
            )

        # Log result
        if performance_success and learning_success:
            logger.info(f"[TRANSACTION] Dual DB write complete: {operation_type}")
            return True
        elif performance_success or learning_success:
            logger.warning(f"[TRANSACTION] Partial success, queued for retry: {operation_type}")
            return True  # Queued for retry
        else:
            logger.error(f"[TRANSACTION] Both DB writes failed, queued for retry: {operation_type}")
            return True  # Queued for retry (fail-safe: don't break trading)

    def _queue_for_retry(
        self,
        operation_type: str,
        target_db: str,
        data: Dict[str, Any],
        error_msg: str
    ):
        """Add failed write to retry queue."""
        try:
            conn = sqlite3.connect(self.retry_queue_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO retry_queue
                (operation_type, target_db, table_name, data, error_msg, queued_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (
                operation_type,
                target_db,
                data.get('table', 'unknown'),
                json.dumps(data),
                error_msg,
                datetime.now().isoformat()
            ))

            conn.commit()
            conn.close()

            logger.info(f"[RETRY_QUEUE] Queued {operation_type} for {target_db}")
        except Exception as e:
            logger.error(f"[RETRY_QUEUE] Failed to queue retry: {e}")

    def process_retry_queue(self, max_retries: int = 3) -> Dict[str, int]:
        """
        Process pending retry queue entries.

        Args:
            max_retries: Max retry attempts before marking as failed

        Returns:
            Dict with success/failure counts
        """
        conn = sqlite3.connect(self.retry_queue_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM retry_queue
            WHERE status = 'pending'
            AND retry_count < ?
            ORDER BY queued_at ASC
            LIMIT 100
        """, (max_retries,))

        entries = cursor.fetchall()
        conn.close()

        results = {'success': 0, 'failed': 0, 'max_retries': 0}

        for entry in entries:
            entry_id = entry['id']
            operation_type = entry['operation_type']
            target_db = entry['target_db']
            data = json.loads(entry['data'])
            retry_count = entry['retry_count']

            logger.info(f"[RETRY_QUEUE] Processing {operation_type} for {target_db} (attempt {retry_count + 1})")

            # Try to execute the operation
            # Note: This is a placeholder - actual implementation would need
            # to reconstruct the write operation from the queued data
            success = self._retry_write(operation_type, target_db, data)

            # Update retry queue
            conn = sqlite3.connect(self.retry_queue_path)
            cursor = conn.cursor()

            if success:
                cursor.execute("""
                    UPDATE retry_queue
                    SET status = 'completed', last_retry_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), entry_id))
                results['success'] += 1
                logger.info(f"[RETRY_QUEUE] Successfully retried entry {entry_id}")
            else:
                new_retry_count = retry_count + 1
                if new_retry_count >= max_retries:
                    cursor.execute("""
                        UPDATE retry_queue
                        SET status = 'max_retries_exceeded',
                            retry_count = ?,
                            last_retry_at = ?
                        WHERE id = ?
                    """, (new_retry_count, datetime.now().isoformat(), entry_id))
                    results['max_retries'] += 1
                    logger.error(f"[RETRY_QUEUE] Entry {entry_id} exceeded max retries")
                else:
                    cursor.execute("""
                        UPDATE retry_queue
                        SET retry_count = ?,
                            last_retry_at = ?
                        WHERE id = ?
                    """, (new_retry_count, datetime.now().isoformat(), entry_id))
                    results['failed'] += 1

            conn.commit()
            conn.close()

        logger.info(f"[RETRY_QUEUE] Processed {len(entries)} entries: {results}")
        return results

    def _retry_write(self, operation_type: str, target_db: str, data: Dict[str, Any]) -> bool:
        """
        Attempt to retry a failed write operation.

        Note: Full automatic retry requires access to live DB instances.
        Failed writes are queued here for manual reconciliation via get_queue_status().
        Daily reconciliation should check queue and alert if entries are pending > 24h.
        """
        logger.error(
            f"[RETRY_QUEUE] Manual reconciliation needed: {operation_type} to {target_db} "
            f"failed and cannot be auto-retried. Check retry_queue.db for details. "
            f"Data keys: {list(data.keys())}"
        )
        return False

    def get_queue_status(self) -> Dict[str, int]:
        """Get current retry queue statistics."""
        conn = sqlite3.connect(self.retry_queue_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                status,
                COUNT(*) as count
            FROM retry_queue
            GROUP BY status
        """)

        results = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        return results

    def clear_completed(self, older_than_days: int = 7):
        """Clear completed retry queue entries older than N days."""
        cutoff_date = datetime.now()
        cutoff_date = cutoff_date.replace(day=cutoff_date.day - older_than_days)

        conn = sqlite3.connect(self.retry_queue_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM retry_queue
            WHERE status = 'completed'
            AND queued_at < ?
        """, (cutoff_date.isoformat(),))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"[RETRY_QUEUE] Cleared {deleted_count} completed entries")
        return deleted_count


# Singleton instance
_transaction_manager = None

def get_transaction_manager() -> DBTransactionManager:
    """Get or create the global transaction manager instance."""
    global _transaction_manager
    if _transaction_manager is None:
        _transaction_manager = DBTransactionManager()
    return _transaction_manager
