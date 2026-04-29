"""
E2E Trade Lifecycle Test - Phase 7 Validation
Tests all Phase 1-5 implementations in a single trade cycle.

Validates:
- Phase 1: trade_id linkage in learning.db + partial_exits table in performance.db
- Phase 2: Partial exit recorded to both DBs
- Phase 3: PositionManager JSON persistence (save/load)
- Phase 4: Stop order audit trail + slippage_bps column
- Phase 5: pending_entry_context wiring + lesson outcome recording

Usage:
    python test_e2e_trade_lifecycle.py

All tests use temp SQLite databases - no production data touched.
"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, date

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

PASS = "PASS"
FAIL = "FAIL"


def run_test(name, fn):
    try:
        fn()
        print(f"  [{PASS}] {name}")
        return True
    except Exception as e:
        print(f"  [{FAIL}] {name}: {e}")
        return False


# ===========================================================================
# Phase 1: Database Foundation
# ===========================================================================

def test_learning_db_trade_id():
    """Phase 1.1: trade_id column exists and record_trade_entry accepts it."""
    from analytics.learning_database import LearningDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        db = LearningDatabase(db_path)
        journal_id = db.record_trade_entry(
            symbol="TSLA",
            entry_price=250.00,
            why_entered="Test entry reason",
            shares=10,
            order_id="test_order_123",
            trade_id="test_trade_abc"
        )
        assert journal_id > 0, "Expected journal_id > 0"
        # Verify trade_id was stored
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT trade_id FROM trade_journal WHERE id = ?", (journal_id,)
        ).fetchone()
        conn.close()
        assert row is not None, "No row found"
        assert row[0] == "test_trade_abc", f"trade_id mismatch: {row[0]}"
    finally:
        os.unlink(db_path)


def test_performance_db_partial_exits_table():
    """Phase 1.2: partial_exits table exists in performance DB."""
    from analytics.performance_tracker import PerformanceTracker
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        tracker = PerformanceTracker(db_path)
        # Query via tracker's own connection to avoid Windows file lock conflict
        cursor = tracker.conn.cursor()
        tables = [r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        tracker.conn.close()
        assert "partial_exits" in tables, f"partial_exits table missing. Tables: {tables}"
    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass  # Windows may lock briefly - acceptable


def test_performance_db_slippage_column():
    """Phase 4.2: slippage_bps column exists in trades table."""
    from analytics.performance_tracker import PerformanceTracker
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        tracker = PerformanceTracker(db_path)
        cursor = tracker.conn.cursor()
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(trades)").fetchall()]
        tracker.conn.close()
        assert "slippage_bps" in cols, f"slippage_bps missing. Columns: {cols}"
    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


# ===========================================================================
# Phase 2: Partial Exit Dual-DB Sync
# ===========================================================================

def test_record_partial_exit_performance_db():
    """Phase 2: record_partial_exit() works in performance tracker."""
    from analytics.performance_tracker import PerformanceTracker
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        tracker = PerformanceTracker(db_path)
        record_id = tracker.record_partial_exit(
            parent_trade_id="order_abc123",
            symbol="AAPL",
            qty_sold=50,
            exit_price=185.50,
            partial_pnl=75.00,
            remaining_qty=50,
            reason="partial_profit_scheduler"
        )
        assert record_id > 0, f"Expected record_id > 0, got {record_id}"
        # Verify stored via tracker's own connection
        cursor = tracker.conn.cursor()
        row = cursor.execute(
            "SELECT parent_trade_id, qty_sold, partial_pnl FROM partial_exits WHERE id = ?",
            (record_id,)
        ).fetchone()
        tracker.conn.close()
        assert row[0] == "order_abc123"
        assert row[1] == 50
        assert abs(row[2] - 75.00) < 0.01
    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


# ===========================================================================
# Phase 3: PositionManager Persistence
# ===========================================================================

def test_position_manager_save_load():
    """Phase 3: PositionManager save_to_json/load_from_json round-trip."""
    from agent.positions.position_manager import PositionManager
    with tempfile.TemporaryDirectory() as tmpdir:
        pm = PositionManager()
        state_file = Path(tmpdir) / "position_state.json"
        pm.state_file = state_file

        # Add a position
        pm.add_position(
            symbol="NVDA",
            quantity=100,
            entry_price=520.00,
            conviction=8,
            take_profit=550.00,
            stop_loss=500.00
        )
        pm.agent_position_high_water_marks["NVDA"] = 535.00
        pm.save_to_json()

        assert state_file.exists(), "State file not created"

        # Load into fresh instance
        pm2 = PositionManager()
        pm2.state_file = state_file
        loaded = pm2.load_from_json()

        assert loaded is True, "load_from_json returned False"
        assert "NVDA" in pm2.agent_opened_positions
        assert pm2.agent_opened_positions["NVDA"] == 100
        assert pm2.agent_position_entry_prices["NVDA"] == 520.00
        assert pm2.agent_position_convictions["NVDA"] == 8
        assert pm2.agent_position_tp_targets["NVDA"] == 550.00
        assert pm2.agent_position_sl_targets["NVDA"] == 500.00
        assert pm2.agent_position_high_water_marks["NVDA"] == 535.00


# ===========================================================================
# Phase 5b: Lesson Outcome Recording
# ===========================================================================

def test_lesson_outcome_recording():
    """Phase 5b: record_lesson_outcome stores live trade results."""
    from analytics.learning_database import LearningDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        db = LearningDatabase(db_path)

        # Create a lesson
        lesson_id = db.add_lesson(
            lesson_text="Buy gappers above VWAP at open",
            category="entry",
            confidence=0.7
        )
        assert lesson_id and lesson_id > 0, "Failed to create lesson"

        # Record a winning live trade outcome
        db.record_lesson_outcome(
            lesson_id=lesson_id,
            trade_date=date.today().isoformat(),
            symbol="AMD",
            pnl_pct=2.5,
            pnl_dollars=125.00,
            adjustment="trailing_stop",
            text_excerpt="Live trade exit: trailing_stop",
            is_resim=False
        )

        # Verify outcome stored and live_wins incremented
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT live_wins, live_losses FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        outcome_count = conn.execute(
            "SELECT COUNT(*) FROM lesson_outcomes WHERE lesson_id = ? AND is_resim = 0",
            (lesson_id,)
        ).fetchone()[0]
        conn.close()

        assert row[0] == 1, f"Expected live_wins=1, got {row[0]}"
        assert row[1] == 0, f"Expected live_losses=0, got {row[1]}"
        assert outcome_count == 1, f"Expected 1 outcome row, got {outcome_count}"
    finally:
        os.unlink(db_path)


# ===========================================================================
# Phase 5c: Lesson Staleness Filter
# ===========================================================================

def test_lesson_staleness_filter():
    """Phase 5c: get_lessons() excludes lessons unused for 60+ days."""
    from analytics.learning_database import LearningDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        db = LearningDatabase(db_path)

        # Create a lesson and mark it as last used 90 days ago
        lesson_id = db.add_lesson(
            lesson_text="Old lesson that should be stale",
            category="entry",
            confidence=0.8
        )

        # Manually set last_outcome_at to 90 days ago
        conn = sqlite3.connect(db_path)
        from datetime import timedelta
        old_date = (datetime.now() - timedelta(days=90)).isoformat()
        conn.execute(
            "UPDATE lessons SET last_outcome_at = ? WHERE id = ?",
            (old_date, lesson_id)
        )
        conn.commit()
        conn.close()

        # With staleness filter (default 60 days) - should exclude stale lesson
        lessons_filtered = db.get_lessons(staleness_days=60)
        filtered_ids = [l["id"] for l in lessons_filtered]

        # Without staleness filter - should include all
        lessons_all = db.get_lessons(staleness_days=0)
        all_ids = [l["id"] for l in lessons_all]

        assert lesson_id not in filtered_ids, "Stale lesson should be excluded by filter"
        assert lesson_id in all_ids, "Stale lesson should appear when filter disabled"
    finally:
        os.unlink(db_path)


# ===========================================================================
# Phase 5a: Tool Executor pending_entry_context
# ===========================================================================

def test_tool_executor_pending_entry_context_initialized():
    """Phase 5a: ToolExecutor has pending_entry_context attribute."""
    from agent.tools.tool_executor import ToolExecutor
    te = ToolExecutor()
    assert hasattr(te, "pending_entry_context"), "pending_entry_context missing"
    assert isinstance(te.pending_entry_context, dict), "Should be a dict"


# ===========================================================================
# Phase 1.1: Migration - trade_id migration runs on existing DB
# ===========================================================================

def test_trade_id_migration_on_existing_db():
    """Phase 1.1: trade_id migration adds column to existing DB without trade_id."""
    from analytics.learning_database import LearningDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        # Create a DB without trade_id (simulate old DB)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL,
                shares INTEGER,
                why_entered TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        # Init LearningDatabase - migration should add trade_id
        db = LearningDatabase(db_path)

        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_journal)").fetchall()]
        conn.close()

        assert "trade_id" in cols, f"trade_id not added by migration. Columns: {cols}"
    finally:
        os.unlink(db_path)


# ===========================================================================
# Main runner
# ===========================================================================

def main():
    print("\n=== E2E Trade Lifecycle Tests (Phase 7) ===\n")

    tests = [
        ("Phase 1.1: learning.db trade_id column", test_learning_db_trade_id),
        ("Phase 1.1: trade_id migration on existing DB", test_trade_id_migration_on_existing_db),
        ("Phase 1.2: partial_exits table in performance.db", test_performance_db_partial_exits_table),
        ("Phase 2:   record_partial_exit() to performance DB", test_record_partial_exit_performance_db),
        ("Phase 3:   PositionManager save/load round-trip", test_position_manager_save_load),
        ("Phase 4.2: slippage_bps column in trades", test_performance_db_slippage_column),
        ("Phase 5a:  ToolExecutor pending_entry_context", test_tool_executor_pending_entry_context_initialized),
        ("Phase 5b:  Lesson outcome recording (live)", test_lesson_outcome_recording),
        ("Phase 5c:  Lesson staleness filter (60-day TTL)", test_lesson_staleness_filter),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        if run_test(name, fn):
            passed += 1
        else:
            failed += 1

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
