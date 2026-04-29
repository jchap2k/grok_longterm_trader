import sqlite3
import sys
from datetime import datetime, date, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from fill_reconciler import FillReconciler


def _create_trade_journal(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            symbol TEXT,
            entry_time TEXT,
            entry_price REAL,
            shares INTEGER,
            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            actual_pnl REAL,
            partial_exits TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_reconcile_parses_trade_journal_time_only_entry_time(tmp_path):
    db_path = tmp_path / "learning.db"
    _create_trade_journal(db_path)

    today = date.today().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO trade_journal
            (trade_date, symbol, entry_time, entry_price, shares)
        VALUES (?, ?, ?, ?, ?)
        """,
        (today, "AAPL", "09:30:00", 100.0, 10),
    )
    conn.commit()
    conn.close()

    filled_at = datetime.combine(
        date.today(),
        datetime.strptime("10:00:00", "%H:%M:%S").time()
    ).replace(tzinfo=timezone.utc)
    broker = SimpleNamespace(
        get_filled_sell_orders=lambda since_days: [{
            "symbol": "AAPL",
            "filled_at": filled_at,
            "filled_avg_price": 105.0,
            "qty": 10,
            "order_id": "sell-1",
        }]
    )

    result = FillReconciler(broker=broker, learning_db_path=db_path).reconcile()

    assert result["updated"] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT exit_time, exit_price, exit_reason, actual_pnl FROM trade_journal WHERE symbol = 'AAPL'"
    ).fetchone()
    conn.close()
    assert row == (
        filled_at.strftime("%Y-%m-%d %H:%M:%S"),
        105.0,
        "alpaca_fill_reconciled",
        50.0,
    )
