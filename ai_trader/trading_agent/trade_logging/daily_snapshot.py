"""
Daily Market Snapshot - Capture all stocks + catalysts seen during live trading

This captures the INPUTS to the trading agent so we can replay the exact same
conditions in the daily re-simulation.

Captures:
- All earnings catalysts fetched from Alpaca
- All gap scan candidates
- Opening prices, gaps, VIX
- Sector info

Purpose: Enable daily re-simulation with identical market conditions
"""

import sqlite3
import logging
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Any, Optional
import json

logger = logging.getLogger(__name__)


class DailySnapshot:
    """Captures daily market snapshot for re-simulation."""

    def __init__(self, db_path: str = None):
        """
        Initialize daily snapshot logger.

        Args:
            db_path: Path to trading performance database. Defaults to the
                     canonical location relative to this file so it is
                     CWD-independent.
        """
        if db_path is None:
            # Resolve absolute path: trade_logging/ -> trading_agent/ -> ai_trader/ -> ai_trader_data/
            db_path = Path(__file__).parent.parent.parent / "ai_trader_data" / "trading_performance.db"
        self.db_path = Path(db_path)
        self._ensure_tables_exist()
        self._migrate_add_rejection_columns()
        self._migrate_add_strategy_column()
        self._migrate_add_lessons_applied_column()
        self._migrate_add_scan_tracking_columns()

    def _ensure_tables_exist(self):
        """Create snapshot tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            # WAL mode: allows concurrent reads while writing candidate snapshots
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")

            # Daily candidate snapshot - captures ALL stocks considered
            c.execute('''
                CREATE TABLE IF NOT EXISTS daily_candidate_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,

                    -- Why we scanned this stock
                    source TEXT NOT NULL,  -- 'earnings_catalyst', 'gap_scan', 'watchlist'

                    -- Catalyst info (if from earnings)
                    catalyst_type TEXT,
                    catalyst_headline TEXT,
                    catalyst_sentiment TEXT,

                    -- Market conditions at open
                    opening_price REAL,
                    gap_pct REAL,
                    vix REAL,
                    sector TEXT,

                    -- Metadata
                    created_at TEXT NOT NULL,

                    UNIQUE(trade_date, symbol, source)
                )
            ''')

            # Daily market conditions - overall market state
            c.execute('''
                CREATE TABLE IF NOT EXISTS daily_market_conditions (
                    condition_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL UNIQUE,

                    -- Market open conditions
                    vix_open REAL,
                    spy_gap_pct REAL,
                    market_sentiment TEXT,

                    -- Catalyst summary
                    total_earnings_catalysts INTEGER,
                    total_gap_candidates INTEGER,

                    -- Metadata
                    created_at TEXT NOT NULL
                )
            ''')

            conn.commit()
        finally:
            conn.close()

        logger.info("Daily snapshot tables initialized")

    def _migrate_add_rejection_columns(self):
        """Idempotent: add conviction_score and rejection_reason columns if missing."""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            try:
                c.execute(
                    "ALTER TABLE daily_candidate_snapshot ADD COLUMN conviction_score INTEGER"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                c.execute(
                    "ALTER TABLE daily_candidate_snapshot ADD COLUMN rejection_reason TEXT"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.commit()
        finally:
            conn.close()

    def _migrate_add_strategy_column(self):
        """Idempotent: add strategy column to daily_candidate_snapshot if missing."""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            try:
                c.execute(
                    "ALTER TABLE daily_candidate_snapshot ADD COLUMN strategy TEXT"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.commit()
        finally:
            conn.close()

    def _migrate_add_lessons_applied_column(self):
        """Idempotent: add lessons_applied column to daily_candidate_snapshot if missing.
        Backfills existing NULL rows with '[]' so JSON parsing is always safe downstream."""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            try:
                c.execute(
                    "ALTER TABLE daily_candidate_snapshot ADD COLUMN lessons_applied TEXT"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Backfill existing rows so JSON parsing never encounters NULL
            c.execute(
                "UPDATE daily_candidate_snapshot SET lessons_applied = '[]' WHERE lessons_applied IS NULL"
            )
            conn.commit()
        finally:
            conn.close()

    def _migrate_add_scan_tracking_columns(self):
        """Idempotent: add scan_count and scan_last_seen columns if missing.
        Backfills scan_count=1 and scan_last_seen=created_at for existing rows."""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            try:
                c.execute(
                    "ALTER TABLE daily_candidate_snapshot ADD COLUMN scan_count INTEGER DEFAULT 1"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                c.execute(
                    "ALTER TABLE daily_candidate_snapshot ADD COLUMN scan_last_seen TEXT"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Backfill existing rows so queries never encounter NULL
            c.execute(
                "UPDATE daily_candidate_snapshot SET scan_count = 1 WHERE scan_count IS NULL"
            )
            c.execute(
                "UPDATE daily_candidate_snapshot SET scan_last_seen = created_at"
                " WHERE scan_last_seen IS NULL"
            )
            conn.commit()
        finally:
            conn.close()

    def record_candidate_decision(
        self,
        trade_date: date,
        symbol: str,
        conviction_score: int,
        rejection_reason: str,
        strategy: str = None,
        lessons_applied: list = None
    ) -> None:
        """
        Update conviction_score, rejection_reason, strategy, and lessons_applied for a scanned candidate.
        Call this when the agent decides NOT to trade a candidate.
        Silently no-ops if the symbol/date row does not exist yet.
        """
        lessons_json = json.dumps(lessons_applied) if lessons_applied else '[]'
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute(
                """
                UPDATE daily_candidate_snapshot
                SET conviction_score = ?, rejection_reason = ?, strategy = ?, lessons_applied = ?
                WHERE trade_date = ? AND symbol = ?
                """,
                (conviction_score, rejection_reason, strategy, lessons_json,
                 trade_date.isoformat(), symbol),
            )
            conn.commit()
        finally:
            conn.close()

    def capture_earnings_candidates(
        self,
        trade_date: date,
        catalysts: List[Dict[str, Any]]
    ):
        """
        Capture earnings catalyst candidates for the day.

        Args:
            trade_date: Trading date
            catalysts: List of earnings catalysts from Alpaca
                      Format: {'symbol', 'date', 'type', 'sentiment', 'headline'}
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        for catalyst in catalysts:
            snapshot_id = f"{trade_date.isoformat()}_{catalyst['symbol']}_earnings"

            c.execute('''
                INSERT OR REPLACE INTO daily_candidate_snapshot (
                    snapshot_id, trade_date, symbol, source,
                    catalyst_type, catalyst_headline, catalyst_sentiment,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                snapshot_id,
                trade_date.isoformat(),
                catalyst['symbol'],
                'earnings_catalyst',
                catalyst.get('type', 'earnings'),
                catalyst.get('headline', ''),
                catalyst.get('sentiment', 'neutral'),
                datetime.now().isoformat()
            ))

        conn.commit()
        conn.close()

        logger.info(f"Captured {len(catalysts)} earnings candidates for {trade_date}")

    def capture_gap_candidates(
        self,
        trade_date: date,
        gaps: Dict[str, float],
        prices: Dict[str, float],
        vix: float,
        sectors: Optional[Dict[str, str]] = None
    ):
        """
        Capture gap scan candidates for the day.

        Uses INSERT ... ON CONFLICT DO UPDATE so that:
        - New symbols are inserted with full market data.
        - Symbols already seen from an earlier scan are NOT overwritten --
          conviction_score, rejection_reason, strategy, and lessons_applied
          are preserved intact.
        - scan_count is incremented and scan_last_seen updated atomically
          on every scan, giving downstream a signal for how many scans
          surfaced each stock.

        Args:
            trade_date: Trading date
            gaps: Dict of symbol -> gap_pct
            prices: Dict of symbol -> opening_price
            vix: VIX at time of scan
            sectors: Optional dict of symbol -> sector
        """
        now_str = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            for symbol, gap_pct in gaps.items():
                snapshot_id = f"{trade_date.isoformat()}_{symbol}_gap"
                price = prices.get(symbol, 0.0)
                sector = sectors.get(symbol, '') if sectors else ''

                c.execute('''
                    INSERT INTO daily_candidate_snapshot (
                        snapshot_id, trade_date, symbol, source,
                        opening_price, gap_pct, vix, sector,
                        created_at, scan_count, scan_last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(trade_date, symbol, source) DO UPDATE SET
                        scan_count    = COALESCE(scan_count, 0) + 1,
                        scan_last_seen = excluded.scan_last_seen
                ''', (
                    snapshot_id,
                    trade_date.isoformat(),
                    symbol,
                    'gap_scan',
                    price,
                    gap_pct,
                    vix,
                    sector,
                    now_str,
                    now_str,
                ))

            conn.commit()
            logger.info(f"Captured {len(gaps)} gap scan candidates for {trade_date}")
        finally:
            conn.close()

    def update_candidate_market_data(
        self,
        trade_date: date,
        symbol: str,
        opening_price: float,
        gap_pct: float,
        sector: Optional[str] = None
    ):
        """
        Update market data for a candidate (useful if earnings + gap both apply).

        Args:
            trade_date: Trading date
            symbol: Stock symbol
            opening_price: Opening price
            gap_pct: Gap percentage
            sector: Sector classification
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            UPDATE daily_candidate_snapshot
            SET opening_price = ?, gap_pct = ?, sector = ?
            WHERE trade_date = ? AND symbol = ?
        ''', (opening_price, gap_pct, sector, trade_date.isoformat(), symbol))

        conn.commit()
        conn.close()

    def capture_market_conditions(
        self,
        trade_date: date,
        vix_open: float,
        spy_gap_pct: float,
        market_sentiment: str,
        total_earnings: int,
        total_gaps: int
    ):
        """
        Capture overall market conditions for the day.

        Args:
            trade_date: Trading date
            vix_open: VIX at market open
            spy_gap_pct: SPY gap percentage
            market_sentiment: 'bullish', 'bearish', 'neutral'
            total_earnings: Number of earnings catalysts
            total_gaps: Number of gap candidates
        """
        condition_id = f"market_{trade_date.isoformat()}"

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            INSERT OR REPLACE INTO daily_market_conditions (
                condition_id, trade_date,
                vix_open, spy_gap_pct, market_sentiment,
                total_earnings_catalysts, total_gap_candidates,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            condition_id,
            trade_date.isoformat(),
            vix_open,
            spy_gap_pct,
            market_sentiment,
            total_earnings,
            total_gaps,
            datetime.now().isoformat()
        ))

        conn.commit()
        conn.close()

        logger.info(f"Captured market conditions for {trade_date}")

    def get_daily_candidates(self, trade_date: date) -> List[Dict[str, Any]]:
        """
        Get all candidates for a specific trading day.

        Args:
            trade_date: Trading date

        Returns:
            List of candidate dicts
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute('''
            SELECT * FROM daily_candidate_snapshot
            WHERE trade_date = ?
            ORDER BY source, symbol
        ''', (trade_date.isoformat(),))

        candidates = [dict(row) for row in c.fetchall()]

        conn.close()

        return candidates

    def get_market_conditions(self, trade_date: date) -> Optional[Dict[str, Any]]:
        """
        Get market conditions for a specific trading day.

        Args:
            trade_date: Trading date

        Returns:
            Dict of market conditions or None
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute('''
            SELECT * FROM daily_market_conditions
            WHERE trade_date = ?
        ''', (trade_date.isoformat(),))

        row = c.fetchone()
        result = dict(row) if row else None

        conn.close()

        return result

    def export_eod_snapshot(self, trade_date: date) -> str:
        """
        Export end-of-day snapshot to JSON file for re-simulation.

        Called automatically at end of trading day (after market close).
        The re-simulator loads this file to replay the exact same conditions.

        Args:
            trade_date: Trading date to export

        Returns:
            Path to the exported JSON file
        """
        snapshot_dir = Path("ai_trader/ai_trader_data/daily_snapshots")
        snapshot_dir.mkdir(exist_ok=True)

        output_file = snapshot_dir / f"{trade_date.isoformat()}_snapshot.json"

        candidates = self.get_daily_candidates(trade_date)
        conditions = self.get_market_conditions(trade_date)

        snapshot = {
            "trade_date": trade_date.isoformat(),
            "exported_at": datetime.now().isoformat(),
            "market_conditions": conditions,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "sources": {
                "earnings": [c for c in candidates if c["source"] == "earnings_catalyst"],
                "gaps": [c for c in candidates if c["source"] == "gap_scan"],
            }
        }

        with open(output_file, 'w') as f:
            json.dump(snapshot, f, indent=2)

        logger.info(f"Exported EOD snapshot to {output_file}")
        logger.info(f"  Candidates: {len(candidates)}")
        logger.info(f"  Earnings: {len(snapshot['sources']['earnings'])}")
        logger.info(f"  Gaps: {len(snapshot['sources']['gaps'])}")

        return str(output_file)

    def load_snapshot(self, snapshot_path: str) -> Dict[str, Any]:
        """
        Load a previously exported snapshot for re-simulation.

        This is the LOAD MODE - feed a saved EOD snapshot into the re-sim
        so it uses the exact same stocks/catalysts the agent saw that day.

        Args:
            snapshot_path: Path to the JSON snapshot file

        Returns:
            Snapshot dict with candidates and market conditions

        Raises:
            FileNotFoundError: If snapshot file doesn't exist
        """
        path = Path(snapshot_path)

        if not path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

        with open(path, 'r') as f:
            snapshot = json.load(f)

        trade_date = snapshot.get("trade_date", "unknown")
        candidate_count = snapshot.get("candidate_count", 0)

        logger.info(f"Loaded snapshot for {trade_date}: {candidate_count} candidates")

        return snapshot

    def load_snapshot_for_date(self, trade_date: date) -> Dict[str, Any]:
        """
        Convenience method to load snapshot by date.

        Args:
            trade_date: Trading date

        Returns:
            Snapshot dict

        Raises:
            FileNotFoundError: If no snapshot exists for this date
        """
        snapshot_dir = Path("ai_trader/ai_trader_data/daily_snapshots")
        snapshot_file = snapshot_dir / f"{trade_date.isoformat()}_snapshot.json"

        return self.load_snapshot(str(snapshot_file))

    def list_available_snapshots(self) -> List[Dict[str, str]]:
        """
        List all available snapshots for re-simulation.

        Returns:
            List of dicts with date and path
        """
        snapshot_dir = Path("ai_trader/ai_trader_data/daily_snapshots")

        if not snapshot_dir.exists():
            return []

        snapshots = []
        for f in sorted(snapshot_dir.glob("*_snapshot.json")):
            date_str = f.stem.replace("_snapshot", "")
            snapshots.append({
                "date": date_str,
                "path": str(f),
                "size_kb": round(f.stat().st_size / 1024, 1)
            })

        return snapshots
