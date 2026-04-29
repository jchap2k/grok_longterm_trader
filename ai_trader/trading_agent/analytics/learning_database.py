"""
Learning Database - Persistent storage for trading lessons

Unlike the JSON-based learning engine (which recalculates from 30-day window),
this database stores lessons permanently. The LLM reads this at pre-market
time to have full historical context.

Tables:
- lessons: Permanent lessons with categories and deduplication
- symbol_history: All-time performance per symbol
- daily_summaries: End-of-day insights (persist forever)
- patterns: Detected patterns that worked/failed

Usage:
    db = LearningDatabase()

    # Add a lesson (checks for duplicates)
    db.add_lesson("SMCI fades after 10%+ gaps", category="symbol_behavior", symbol="SMCI")

    # Get context for LLM
    context = db.generate_llm_context(symbols_considering=["NVDA", "TSLA"])
"""

import json
import sqlite3
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from contextlib import contextmanager
import hashlib

logger = logging.getLogger(__name__)

# Absolute path to learning_config.json - resolved relative to this file so it
# works regardless of the working directory the caller uses.
_LEARNING_CONFIG_PATH = Path(__file__).parent.parent.parent / 'ai_trader_data' / 'learning_config.json'


def is_lesson_creation_allowed() -> bool:
    """
    Read learning_config.json and return the allow_new_lessons value.

    Defaults to True on any read/parse error so a missing or corrupt config
    never silently blocks lesson creation - it only logs a warning.

    To pause lesson generation:
        Set "allow_new_lessons": false in ai_trader/ai_trader_data/learning_config.json
    To resume:
        Set "allow_new_lessons": true  (or delete the file)
    """
    try:
        with open(_LEARNING_CONFIG_PATH) as f:
            return bool(json.load(f).get('allow_new_lessons', True))
    except FileNotFoundError:
        return True   # No config = allow (default open)
    except Exception as e:
        logger.warning(f"Could not read learning_config.json ({e}) - defaulting to allow")
        return True


def is_followup_creation_allowed() -> bool:
    """
    Read learning_config.json and return the allow_candidate_followups value.

    Defaults to True on any read/parse error and also defaults to True when the
    key is absent, so pausing lesson creation does not unintentionally disable
    missed-opportunity tracking.
    """
    try:
        with open(_LEARNING_CONFIG_PATH) as f:
            return bool(json.load(f).get('allow_candidate_followups', True))
    except FileNotFoundError:
        return True
    except Exception as e:
        logger.warning(f"Could not read learning_config.json ({e}) - defaulting followups to allow")
        return True


class LearningDatabase:
    """
    Persistent learning database for trading lessons.

    Stores lessons, symbol history, and patterns that survive beyond
    the 30-day rolling window used by the JSON-based learning engine.
    """

    def __init__(self, db_path: Path = None):
        """
        Initialize the learning database.

        Args:
            db_path: Path to SQLite database. Defaults to ai_trader_data/learning.db
        """
        if db_path is None:
            script_dir = Path(__file__).parent
            data_dir = script_dir.parent.parent / "ai_trader_data"
            db_path = data_dir / "learning.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_database()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_database(self):
        """Create tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # WAL mode: allows concurrent reads while writing (dashboard + scheduler)
            # synchronous=NORMAL: safe with WAL, significantly faster than FULL
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")

            # Lessons table - permanent lessons with deduplication
            # Structured format: CONDITION -> ACTION with EVIDENCE
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_hash TEXT UNIQUE,
                    lesson_text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    symbol TEXT,
                    confidence REAL DEFAULT 0.5,
                    times_validated INTEGER DEFAULT 0,
                    times_contradicted INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_validated TEXT,
                    is_active INTEGER DEFAULT 1,
                    source TEXT,
                    notes TEXT,
                    -- Structured lesson fields (v2)
                    condition TEXT,
                    action TEXT,
                    evidence_count INTEGER DEFAULT 0,
                    evidence_summary TEXT,
                    -- Time-bound lesson support
                    valid_time_start TEXT,
                    valid_time_end TEXT,
                    valid_days TEXT,
                    -- Backtest validation fields (v3)
                    lesson_type TEXT,
                    pattern_rule TEXT,
                    hypothesis TEXT,
                    validated INTEGER DEFAULT 0,
                    confidence_level TEXT,
                    backtest_win_rate REAL,
                    backtest_avg_return REAL,
                    backtest_sample_size INTEGER,
                    backtest_date TEXT,
                    backtest_data TEXT,
                    -- 3AM pruning cycle (Phase 5d - Feb 19, 2026)
                    survival_count INTEGER DEFAULT 0,
                    pruning_mode INTEGER DEFAULT 0
                )
            """)

            # Backtest cache table - stores historical data for fast backtesting
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS backtest_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    ohlcv_data TEXT,
                    indicators TEXT,
                    catalyst_data TEXT,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(symbol, date)
                )
            """)

            # Trade journal table - captures entry reasoning AT PURCHASE TIME
            # This is crucial because at end-of-day reflection, LLM may not remember morning reasoning
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL,
                    shares INTEGER,
                    -- Entry reasoning captured at purchase time
                    why_entered TEXT NOT NULL,
                    expected_target REAL,
                    expected_stop REAL,
                    setup_type TEXT,
                    catalyst TEXT,
                    candidate_lane TEXT,
                    market_context TEXT,
                    confidence_level TEXT,
                    -- Filled in at exit/EOD
                    exit_time TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    actual_pnl REAL,
                    -- Partial exits (JSON array) - Feb 15, 2026
                    partial_exits TEXT,
                    -- Metadata
                    order_id TEXT,
                    trade_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Migration: Add partial_exits column if it doesn't exist (Feb 15, 2026)
            try:
                cursor.execute("SELECT partial_exits FROM trade_journal LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("Adding partial_exits column to trade_journal table...")
                cursor.execute("ALTER TABLE trade_journal ADD COLUMN partial_exits TEXT")
                logger.info("Migration complete: partial_exits column added")

            # Migration: Add trade_id column if it doesn't exist (Feb 19, 2026)
            try:
                cursor.execute("SELECT trade_id FROM trade_journal LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("Adding trade_id column to trade_journal table...")
                cursor.execute("ALTER TABLE trade_journal ADD COLUMN trade_id TEXT")
                logger.info("Migration complete: trade_id column added")

            # Migration: Add lessons_applied column if it doesn't exist (Feb 21, 2026)
            # Stores JSON array of lesson IDs that triggered this trade, e.g. [142, 229]
            try:
                cursor.execute("SELECT lessons_applied FROM trade_journal LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("Adding lessons_applied column to trade_journal table...")
                cursor.execute("ALTER TABLE trade_journal ADD COLUMN lessons_applied TEXT")
                logger.info("Migration complete: lessons_applied column added")

            # Migration: Add swing-specific columns to trade_journal (Mar 2026)
            # These support the swing trader fork: hold_duration filter, net pnl pct,
            # hold days, entry catalyst tag, hold type, and VIX regime at entry.
            _swing_migrations = [
                ("hold_duration", "TEXT DEFAULT 'swing'"),
                ("pnl_pct_net",   "REAL"),
                ("hold_days",     "INTEGER"),
                ("entry_catalyst","TEXT"),
                ("hold_type",     "TEXT"),
                ("candidate_lane","TEXT"),
                ("vix_mode",      "TEXT"),
            ]
            for col_name, col_def in _swing_migrations:
                try:
                    cursor.execute(f"SELECT {col_name} FROM trade_journal LIMIT 1")
                except sqlite3.OperationalError:
                    logger.info(f"Adding {col_name} column to trade_journal table...")
                    cursor.execute(
                        f"ALTER TABLE trade_journal ADD COLUMN {col_name} {col_def}"
                    )
                    logger.info(f"Migration complete: {col_name} column added")

            # Loss analysis table - detailed "why" for each losing trade
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS loss_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    loss_amount REAL NOT NULL,
                    loss_percent REAL,
                    -- The "why" analysis
                    why_entered TEXT,
                    why_failed TEXT,
                    entry_quality TEXT,
                    market_context TEXT,
                    pattern_attempted TEXT,
                    -- Categorization
                    loss_category TEXT,
                    was_preventable INTEGER DEFAULT 0,
                    lesson_generated TEXT,
                    lesson_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (lesson_id) REFERENCES lessons(id)
                )
            """)

            # Symbol history - all-time stats per symbol
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS symbol_history (
                    symbol TEXT PRIMARY KEY,
                    total_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_win REAL DEFAULT 0,
                    avg_loss REAL DEFAULT 0,
                    best_trade REAL DEFAULT 0,
                    worst_trade REAL DEFAULT 0,
                    first_trade_date TEXT,
                    last_trade_date TEXT,
                    win_rate REAL DEFAULT 0,
                    status TEXT DEFAULT 'NEUTRAL',
                    notes TEXT
                )
            """)

            # Daily summaries - end-of-day insights
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT UNIQUE NOT NULL,
                    trades_taken INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    gross_pnl REAL DEFAULT 0,
                    market_regime TEXT,
                    vix_level REAL,
                    key_insight TEXT,
                    what_worked TEXT,
                    what_failed TEXT,
                    symbols_traded TEXT,
                    lessons_learned TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Patterns - detected patterns that worked/failed
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_hash TEXT UNIQUE,
                    pattern_name TEXT NOT NULL,
                    pattern_description TEXT,
                    category TEXT,
                    occurrences INTEGER DEFAULT 0,
                    successes INTEGER DEFAULT 0,
                    failures INTEGER DEFAULT 0,
                    success_rate REAL DEFAULT 0,
                    avg_pnl_when_followed REAL DEFAULT 0,
                    first_seen TEXT,
                    last_seen TEXT,
                    is_active INTEGER DEFAULT 1,
                    confidence TEXT DEFAULT 'LOW'
                )
            """)

            # Decision journal - tracks trading decisions including NOPOSITIONS
            # Captures when agent decides NOT to trade and why
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS decision_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_date TEXT NOT NULL,
                    decision_time TEXT NOT NULL,
                    decision_type TEXT NOT NULL,  -- 'NOPOSITIONS', 'PARTIAL', 'FULL'

                    -- What was analyzed
                    candidates_considered TEXT,   -- JSON: [{symbol, conviction, entry_price, in_plan}]
                    total_candidates INTEGER DEFAULT 0,
                    viable_candidates INTEGER DEFAULT 0,

                    -- Reasoning
                    market_context TEXT,          -- VIX level, market regime, etc.
                    decision_reason TEXT,         -- Why positions were/weren't taken
                    agent_reasoning TEXT,         -- Full agent explanation

                    -- For reflection (filled in at EOD)
                    symbols_that_ran TEXT,        -- JSON: symbols that moved +5% after we passed
                    missed_opportunity INTEGER DEFAULT 0,
                    reflection_notes TEXT,

                    created_at TEXT NOT NULL
                )
            """)

            # Rebuy blocks - persists 12-hour rebuy cooldown after a loss across restarts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rebuy_blocks (
                    symbol TEXT PRIMARY KEY,
                    block_until TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    loss_pct REAL,
                    reason TEXT DEFAULT 'loss_exit',
                    created_at TEXT NOT NULL
                )
            """)

            # Lesson outcomes - per-trade record of which lessons were cited and result
            # Populated by resimulation and live trading; drives promotion/deactivation
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lesson_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id INTEGER NOT NULL,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    pnl_pct REAL,
                    pnl_dollars REAL,
                    won INTEGER NOT NULL,           -- 1=win, 0=loss
                    adjustment TEXT,                -- what Grok applied (+0.5, -1, etc.)
                    text_excerpt TEXT,              -- first 80 chars of lesson text (for readability)
                    is_resim INTEGER DEFAULT 1,     -- 1=resimulation, 0=live trade
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (lesson_id) REFERENCES lessons(id)
                )
            """)

            # Bad lessons archive - lessons proven to hurt performance
            # Only Claude may move lessons here (never automated scripts)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bad_lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_lesson_id INTEGER,
                    lesson_hash TEXT,
                    lesson_text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    symbol TEXT,
                    confidence REAL,
                    times_validated INTEGER DEFAULT 0,
                    times_contradicted INTEGER DEFAULT 0,
                    original_created_at TEXT,
                    -- Archive metadata
                    archived_at TEXT NOT NULL,
                    archived_by TEXT NOT NULL DEFAULT 'claude',
                    disproven_reason TEXT NOT NULL,
                    validation_stats TEXT,
                    -- Keep original structured fields for future reference
                    condition TEXT,
                    action TEXT,
                    evidence_summary TEXT,
                    backtest_win_rate REAL,
                    backtest_avg_return REAL,
                    backtest_sample_size INTEGER,
                    regime_context TEXT
                )
            """)

            # Run migration to add new columns if they don't exist (do this BEFORE creating indexes)
            self._migrate_backtest_validation_columns(cursor)

            # Idempotent migration: create failed_hypotheses table (Tasks 1+2, Feb 22 2026)
            self._ensure_failed_hypotheses_table(cursor)

            # Idempotent migration: create backtest_trade_journal table (2026-03-11)
            self._ensure_backtest_trade_journal_table(cursor)

            # Idempotent migration: create news_check_events table (2026-03-12)
            self._ensure_news_check_events_table(cursor)

            # Idempotent migration: create hypothesis_verdicts + live_cgh_state tables (2026-03-28)
            self._ensure_hypothesis_verdicts_table(cursor)
            self._ensure_live_cgh_state_table(cursor)
            self._ensure_candidate_followup_tables(cursor)

            # Create indexes for faster queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_category ON lessons(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_symbol ON lessons(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_active ON lessons(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_validated ON lessons(validated)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_confidence_level ON lessons(confidence_level)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_summaries(trade_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_loss_symbol ON loss_analysis(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_loss_date ON loss_analysis(trade_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_loss_category ON loss_analysis(loss_category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_date ON trade_journal(trade_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_symbol ON trade_journal(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decision_date ON decision_journal(decision_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decision_type ON decision_journal(decision_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cf_active_review_date ON candidate_followup_active(next_review_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cf_active_symbol ON candidate_followup_active(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cf_active_scan_date ON candidate_followup_active(scan_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cf_archive_symbol ON candidate_followup_archive(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cf_archive_scan_date ON candidate_followup_archive(scan_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cf_archive_archived_at ON candidate_followup_archive(archived_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_backtest_cache_symbol ON backtest_cache(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_backtest_cache_date ON backtest_cache(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_outcomes_lesson ON lesson_outcomes(lesson_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_outcomes_date ON lesson_outcomes(trade_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_outcomes_resim ON lesson_outcomes(is_resim)")
            # Composite index for the most frequent query pattern:
            # SELECT * FROM lessons WHERE is_active = 1 ORDER BY confidence DESC
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_active_confidence ON lessons(is_active, confidence DESC)")

            logger.info(f"Learning database initialized at {self.db_path}")

    def _migrate_backtest_validation_columns(self, cursor):
        """
        Add backtest validation columns to existing lessons table if needed.
        
        This migration is backward compatible - it checks for each column and
        only adds it if it doesn't exist.
        """
        # Get existing columns
        cursor.execute("PRAGMA table_info(lessons)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        
        # Define new columns to add
        new_columns = {
            'lesson_type': 'TEXT',
            'pattern_rule': 'TEXT',
            'hypothesis': 'TEXT',
            'validated': 'INTEGER DEFAULT 0',
            'confidence_level': 'TEXT',
            'backtest_win_rate': 'REAL',
            'backtest_avg_return': 'REAL',
            'backtest_sample_size': 'INTEGER',
            'backtest_date': 'TEXT',
            'backtest_data': 'TEXT',
            # Regime filtering columns (Phase 2 enhancement - Grok feedback)
            'regime_vix_bucket': 'TEXT',  # low/medium/high volatility
            'regime_spy_trend': 'TEXT',   # bull/bear/flat market
            'regime_sector_strength': 'TEXT',  # sector relative strength (future)
            # Swing regime context for stratified lesson retrieval
            'regime_score': 'INTEGER',         # 0-5 swing regime score at lesson creation
            'qqq_signal': 'TEXT',              # TECH_LEADING/NEUTRAL/TECH_LAGGING
            'spy_vs_iwm_signal': 'TEXT',       # LARGE_LEADING/NEUTRAL/SMALL_LEADING
            'qqq_vs_iwm_signal': 'TEXT',       # GROWTH_LEADING/NEUTRAL/VALUE_LEADING
            # Outcome-driven promotion to live trading
            'live_eligible': 'INTEGER DEFAULT 0',  # 1=promoted to live, 0=resim-only
            'resim_wins': 'INTEGER DEFAULT 0',     # cumulative resim wins
            'resim_losses': 'INTEGER DEFAULT 0',   # cumulative resim losses
            'live_wins': 'INTEGER DEFAULT 0',      # cumulative live wins
            'live_losses': 'INTEGER DEFAULT 0',    # cumulative live losses
            'promoted_at': 'TEXT',                 # when promoted to live_eligible
            'last_outcome_at': 'TEXT',             # last time an outcome was recorded
            # 3AM pruning cycle columns (Phase 5d - Feb 19, 2026)
            'survival_count': 'INTEGER DEFAULT 0', # iterations survived across pruning windows
            'pruning_mode': 'INTEGER DEFAULT 0',   # 1=in testing pool, 0=production/not-in-pruning
            # Swing trader isolation (Task 3) - filters intraday vs swing lessons
            'hold_duration': "TEXT DEFAULT 'swing'",  # Swing fork: all new lessons are swing
        }
        
        # Add missing columns
        for column_name, column_type in new_columns.items():
            if column_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE lessons ADD COLUMN {column_name} {column_type}")
                    logger.info(f"Added column {column_name} to lessons table")
                except Exception as e:
                    logger.warning(f"Could not add column {column_name}: {e}")

    def _ensure_failed_hypotheses_table(self, cursor) -> None:
        """Idempotent migration: create failed_hypotheses table if not exists."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_hypotheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id TEXT NOT NULL,
                lesson_id INTEGER,
                hyp_type TEXT,
                title TEXT,
                rationale TEXT,
                proposed_change TEXT,
                expected_impact TEXT,
                original_confidence INTEGER,
                failure_reason TEXT NOT NULL,
                failed_at TEXT NOT NULL,
                presented_to_grok INTEGER DEFAULT 0,
                presented_at TEXT
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_fh_presented ON failed_hypotheses(presented_to_grok)"
        )

    def _ensure_backtest_trade_journal_table(self, cursor) -> None:
        """Idempotent migration: create backtest_trade_journal table if not exists.

        Separate from live trade_journal to eliminate any risk of backtest
        trades contaminating the live lesson corpus.
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtest_trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                fork TEXT NOT NULL DEFAULT 'swing',
                hypothesis_id TEXT,
                trade_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                exit_date TEXT,
                entry_price REAL,
                exit_price REAL,
                shares INTEGER,
                shares_remaining INTEGER,
                pnl_dollars REAL,
                pnl_pct REAL,
                exit_type TEXT,
                hold_days INTEGER,
                entry_catalyst TEXT,
                hold_type TEXT,
                next_earnings_date TEXT,
                conviction_score REAL,
                regime TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migration guard: add trade_id column if table pre-existed without it.
        # sqlite3.OperationalError is expected (and silenced) when column already exists.
        try:
            cursor.execute(
                "ALTER TABLE backtest_trade_journal ADD COLUMN trade_id TEXT"
            )
            logger.info("[DB] Migrated: added trade_id to backtest_trade_journal")
        except Exception:
            pass  # Column already exists - expected on fresh tables or post-migration
        try:
            cursor.execute(
                "ALTER TABLE backtest_trade_journal ADD COLUMN hold_type TEXT"
            )
            logger.info("[DB] Migrated: added hold_type to backtest_trade_journal")
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE backtest_trade_journal ADD COLUMN next_earnings_date TEXT"
            )
            logger.info("[DB] Migrated: added next_earnings_date to backtest_trade_journal")
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE backtest_trade_journal ADD COLUMN candidate_lane TEXT"
            )
            logger.info("[DB] Migrated: added candidate_lane to backtest_trade_journal")
        except Exception:
            pass
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_btj_run_id ON backtest_trade_journal(run_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_btj_symbol ON backtest_trade_journal(symbol)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_btj_fork ON backtest_trade_journal(fork)"
        )

    def _ensure_news_check_events_table(self, cursor) -> None:
        """Idempotent migration: create news_check_events table if not exists."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news_check_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                check_date TEXT NOT NULL,
                check_time TEXT NOT NULL,
                window TEXT NOT NULL,
                lookback_hours INTEGER NOT NULL,
                news_found INTEGER NOT NULL DEFAULT 0,
                news_summary TEXT,
                agent_action TEXT,
                agent_confidence INTEGER,
                agent_reason TEXT,
                news_category TEXT,
                urgency TEXT,
                daily_call_count INTEGER DEFAULT 1,
                outcome_verified INTEGER DEFAULT 0,
                outcome_correct INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_check_symbol_date "
            "ON news_check_events (symbol, check_date)"
        )

    def _ensure_hypothesis_verdicts_table(self, cursor) -> None:
        """Idempotent migration: create hypothesis_verdicts table if not exists."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hypothesis_verdicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                trigger_trade_count INTEGER NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                hypothesis_id TEXT NOT NULL,
                hypothesis_type TEXT NOT NULL,
                title TEXT,
                proposed_change TEXT,
                confidence INTEGER,
                backtest_win_rate REAL,
                backtest_sample_size INTEGER,
                baseline_win_rate REAL,
                verdict TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hv_run_date ON hypothesis_verdicts(run_date)")

    def _ensure_candidate_followup_tables(self, cursor) -> None:
        """Idempotent migration: create candidate follow-up active/archive tables if not exists."""
        common_columns = """
            candidate_instance_id TEXT UNIQUE NOT NULL,
            scan_date TEXT NOT NULL,
            scan_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            candidate_lane TEXT,
            source TEXT,
            source_bucket TEXT,
            regime_mode TEXT,
            swing_score INTEGER,
            min_conviction_today REAL,
            decision_type TEXT NOT NULL,
            decision_reason TEXT,
            agent_reasoning TEXT,
            forceswing_reason TEXT,
            soft_miss_reasons TEXT,
            catalyst_summary TEXT,
            candidate_snapshot TEXT NOT NULL,
            decision_journal_id TEXT,
            trade_id TEXT,
            checkpoint_1d TEXT,
            checkpoint_3d TEXT,
            checkpoint_5d TEXT,
            checkpoint_10d TEXT
        """
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS candidate_followup_active (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {common_columns},
                next_review_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS candidate_followup_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {common_columns},
                final_outcome_summary TEXT,
                archived_at TEXT NOT NULL,
                archived_reason TEXT NOT NULL DEFAULT 'completed_10d',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hv_verdict ON hypothesis_verdicts(verdict)")

    def _ensure_live_cgh_state_table(self, cursor) -> None:
        """Idempotent migration: create live_cgh_state singleton table if not exists."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_cgh_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                last_trigger_count INTEGER NOT NULL DEFAULT 0,
                first_run_completed INTEGER NOT NULL DEFAULT 0,
                last_run_date TEXT,
                updated_at TEXT
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO live_cgh_state (id, last_trigger_count, first_run_completed)
            VALUES (1, 0, 0)
        """)

    # =========================================================================
    # LESSON MANAGEMENT
    # =========================================================================

    def _hash_lesson(self, lesson_text: str, category: str, symbol: str = None) -> str:
        """Create a hash for deduplication."""
        # Normalize the lesson text
        normalized = lesson_text.lower().strip()
        key = f"{category}:{symbol or 'general'}:{normalized}"
        return hashlib.md5(key.encode()).hexdigest()

    def add_lesson(self, lesson_text: str, category: str, symbol: str = None,
                   confidence: float = 0.5, source: str = None, notes: str = None,
                   hold_duration: str = "swing",
                   regime_score: int = None, regime_vix_bucket: str = None,
                   qqq_signal: str = None, spy_vs_iwm_signal: str = None,
                   qqq_vs_iwm_signal: str = None) -> bool:
        """
        Add a lesson to the database with deduplication.

        Args:
            lesson_text: The lesson content
            category: Category (symbol_behavior, timing, strategy, risk, etc.)
            symbol: Related symbol (optional)
            confidence: 0.0-1.0 confidence level
            source: Where the lesson came from (e.g., "deep_analysis", "manual")
            notes: Additional notes
            hold_duration: "intraday" or "swing" - used to isolate lesson sets

        Returns:
            True if lesson was added, False if it already exists
        """
        if not is_lesson_creation_allowed():
            logger.info("Lesson creation paused (allow_new_lessons=false) - skipping add_lesson")
            return False

        lesson_hash = self._hash_lesson(lesson_text, category, symbol)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if lesson exists
            cursor.execute("SELECT id, times_validated FROM lessons WHERE lesson_hash = ?",
                          (lesson_hash,))
            existing = cursor.fetchone()

            if existing:
                # Lesson exists - increment validation count
                cursor.execute("""
                    UPDATE lessons
                    SET times_validated = times_validated + 1,
                        last_validated = ?,
                        confidence = MIN(1.0, confidence + 0.05)
                    WHERE id = ?
                """, (datetime.now().isoformat(), existing['id']))
                logger.debug(f"Lesson already exists, incremented validation count")
                return False

            # Add new lesson
            cursor.execute("""
                INSERT INTO lessons (lesson_hash, lesson_text, category, symbol,
                                    confidence, source, notes, created_at, hold_duration,
                                    regime_score, regime_vix_bucket,
                                    qqq_signal, spy_vs_iwm_signal, qqq_vs_iwm_signal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (lesson_hash, lesson_text, category, symbol, confidence,
                  source, notes, datetime.now().isoformat(), hold_duration,
                  regime_score, regime_vix_bucket,
                  qqq_signal, spy_vs_iwm_signal, qqq_vs_iwm_signal))

            logger.info(f"Added new lesson: {lesson_text[:50]}...")
            return True

    def add_structured_lesson(self, condition: str, action: str,
                              category: str, symbol: str = None,
                              evidence_count: int = 1, evidence_summary: str = None,
                              valid_time_start: str = None, valid_time_end: str = None,
                              valid_days: str = None, confidence: float = 0.5,
                              source: str = None) -> bool:
        """
        Add a structured lesson with CONDITION -> ACTION format.

        Structured lessons are more actionable than free-text lessons.
        Format: "WHEN [condition] THEN [action]"

        Args:
            condition: The trigger condition (e.g., "SMCI gaps up >10% at open")
            action: What to do (e.g., "SKIP - fades 70% of the time")
            category: Lesson category
            symbol: Related symbol
            evidence_count: Number of trades supporting this lesson
            evidence_summary: Summary of supporting evidence
            valid_time_start: Time when lesson applies (e.g., "09:30")
            valid_time_end: Time when lesson stops applying (e.g., "10:00")
            valid_days: Days when applicable (e.g., "Mon,Tue,Wed")
            confidence: 0.0-1.0 confidence level
            source: Where lesson came from

        Returns:
            True if lesson was added, False if duplicate or insufficient evidence
        """
        if not is_lesson_creation_allowed():
            logger.info("Lesson creation paused (allow_new_lessons=false) - skipping add_structured_lesson")
            return False

        # EVIDENCE THRESHOLD: Require 5+ trades for AVOID lessons
        min_evidence = 5 if "AVOID" in action.upper() or "SKIP" in action.upper() else 3

        if evidence_count < min_evidence:
            logger.debug(f"Insufficient evidence ({evidence_count}/{min_evidence}) for: {condition}")
            return False

        # Create human-readable lesson text
        lesson_text = f"WHEN {condition} THEN {action}"

        # Check for contradictions before adding
        contradictions = self.check_for_contradictions(condition, action, symbol)
        if contradictions:
            logger.warning(f"Contradiction detected! Existing lessons conflict: {contradictions}")
            # Lower confidence if there are contradictions
            confidence = max(0.3, confidence - 0.2)

        lesson_hash = self._hash_lesson(lesson_text, category, symbol)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT id, evidence_count FROM lessons WHERE lesson_hash = ?",
                          (lesson_hash,))
            existing = cursor.fetchone()

            if existing:
                # Update evidence count
                new_count = existing['evidence_count'] + evidence_count if existing['evidence_count'] else evidence_count
                cursor.execute("""
                    UPDATE lessons
                    SET times_validated = times_validated + 1,
                        last_validated = ?,
                        confidence = MIN(1.0, confidence + 0.05),
                        evidence_count = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), new_count, existing['id']))
                return False

            # Add new structured lesson
            cursor.execute("""
                INSERT INTO lessons (lesson_hash, lesson_text, category, symbol,
                                    confidence, source, created_at,
                                    condition, action, evidence_count, evidence_summary,
                                    valid_time_start, valid_time_end, valid_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (lesson_hash, lesson_text, category, symbol, confidence,
                  source, datetime.now().isoformat(),
                  condition, action, evidence_count, evidence_summary,
                  valid_time_start, valid_time_end, valid_days))

            logger.info(f"Added structured lesson: WHEN {condition[:30]}... THEN {action[:30]}...")
            return True

    def check_for_contradictions(self, condition: str, action: str,
                                  symbol: str = None) -> List[Dict]:
        """
        Check if a new lesson contradicts existing lessons.

        Returns list of contradicting lessons if found.
        """
        contradictions = []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Look for lessons about the same symbol with opposite actions
            if symbol:
                cursor.execute("""
                    SELECT * FROM lessons
                    WHERE symbol = ? AND is_active = 1
                    AND (condition LIKE ? OR lesson_text LIKE ?)
                """, (symbol, f"%{condition[:20]}%", f"%{condition[:20]}%"))

                for row in cursor.fetchall():
                    existing_action = row['action'] or row['lesson_text']

                    # Detect contradictions: one says AVOID/SKIP, other says BUY/FAVORABLE
                    new_is_negative = any(w in action.upper() for w in ['AVOID', 'SKIP', 'CAUTION', 'DONT'])
                    existing_is_negative = any(w in existing_action.upper() for w in ['AVOID', 'SKIP', 'CAUTION', 'DONT'])
                    new_is_positive = any(w in action.upper() for w in ['BUY', 'FAVORABLE', 'GOOD', 'ENTER'])
                    existing_is_positive = any(w in existing_action.upper() for w in ['BUY', 'FAVORABLE', 'GOOD', 'ENTER'])

                    if (new_is_negative and existing_is_positive) or (new_is_positive and existing_is_negative):
                        contradictions.append(dict(row))

        return contradictions

    def get_lessons(self, category: str = None, symbol: str = None,
                    active_only: bool = True, min_confidence: float = 0.0,
                    limit: int = 50, staleness_days: int = 60,
                    hold_duration: str = None,
                    as_of_date: str = None,
                    max_regime_score: int = None) -> List[Dict]:
        """
        Get lessons from the database.

        Args:
            category: Filter by category
            symbol: Filter by symbol
            active_only: Only return active lessons
            min_confidence: Minimum confidence threshold
            limit: Maximum number of lessons to return
            staleness_days: Exclude lessons not used in this many days (0 to disable)
            hold_duration: If set, only return lessons matching this value ("intraday" or "swing")
            as_of_date: ISO date string e.g. "2025-10-01" - only return lessons created on or
                        before this date. Used by backtests to prevent look-ahead bias.
            max_regime_score: If set, exclude lessons created in stronger regimes than this.
                              Include lessons with NULL regime_score (old/untagged lessons).
                              Use current swing_score to avoid bull-market lessons in bear markets.
                              e.g. pass 2 (CASH mode) to exclude score>=3 FULL/REDUCED lessons.

        Returns:
            List of lesson dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM lessons WHERE confidence >= ?"
            params = [min_confidence]

            if active_only:
                query += " AND is_active = 1"

            if category:
                query += " AND category = ?"
                params.append(category)

            if symbol:
                query += " AND (symbol = ? OR symbol IS NULL)"
                params.append(symbol)

            if hold_duration:
                query += " AND hold_duration = ?"
                params.append(hold_duration)

            if as_of_date:
                # Prevent look-ahead bias in backtests: only return lessons that
                # existed on the simulation date (created_at <= sim date)
                query += " AND created_at <= ?"
                params.append(as_of_date)

            if max_regime_score is not None:
                # Regime-stratified filtering: exclude lessons from stronger bull regimes.
                # Lessons with NULL regime_score are old/untagged and always included.
                query += " AND (regime_score IS NULL OR regime_score <= ?)"
                params.append(max_regime_score)

            # [Phase 5c] 60-day staleness filter: exclude lessons not used recently
            # A lesson unused for 60+ days may no longer be relevant to current market
            # Logic: include lesson if it has NEVER been used (NULL) OR was used recently
            # Note: created_at is NOT used as fallback - a new lesson with no outcomes is fine,
            # but an old lesson last used 90 days ago should be excluded
            if staleness_days > 0:
                from datetime import timedelta
                cutoff = (datetime.now() - timedelta(days=staleness_days)).isoformat()
                query += (
                    " AND (last_outcome_at IS NULL OR last_outcome_at >= ?)"
                )
                params.append(cutoff)

            query += " ORDER BY confidence DESC, times_validated DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            results = [dict(row) for row in cursor.fetchall()]

            # Log any stale lessons that were excluded
            if staleness_days > 0 and active_only:
                stale_check = cursor.execute(
                    "SELECT COUNT(*) FROM lessons WHERE is_active = 1 AND confidence >= ?"
                    " AND last_outcome_at IS NOT NULL AND last_outcome_at < ?",
                    [min_confidence, cutoff]
                ).fetchone()
                stale_count = stale_check[0] if stale_check else 0
                if stale_count > 0:
                    logger.info(
                        f"[Phase 5c] Staleness filter: {stale_count} lesson(s) excluded "
                        f"(not used in {staleness_days}+ days)"
                    )

            return results

    def validate_lesson(self, lesson_id: int, validated: bool = True):
        """Mark a lesson as validated (it worked) or contradicted (it failed)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if validated:
                cursor.execute("""
                    UPDATE lessons
                    SET times_validated = times_validated + 1,
                        last_validated = ?,
                        confidence = MIN(1.0, confidence + 0.05)
                    WHERE id = ?
                """, (datetime.now().isoformat(), lesson_id))
            else:
                cursor.execute("""
                    UPDATE lessons
                    SET times_contradicted = times_contradicted + 1,
                        confidence = MAX(0.0, confidence - 0.1)
                    WHERE id = ?
                """, (lesson_id,))

                # Deactivate if too many contradictions
                cursor.execute("""
                    UPDATE lessons
                    SET is_active = 0
                    WHERE id = ? AND times_contradicted > times_validated + 3
                """, (lesson_id,))

    # =========================================================================
    # BACKTEST VALIDATION METHODS (Phase 1)
    # =========================================================================

    def save_validated_lesson(self, pattern: Dict, backtest_results: Dict,
                               confidence_level: str, regime: Dict = None,
                               source: str = "backtest_validation") -> int:
        """
        Save a lesson that has been validated through backtesting.

        This is the primary method for Phase 1 - saves lessons with backtest
        evidence to support them.

        Args:
            pattern: Pattern dictionary with structure:
                {
                    'type': 'entry_filter',
                    'conditions': {...},
                    'hypothesis': 'Human-readable description',
                    'extracted_from': 'source'
                }
            backtest_results: Results from backtest engine:
                {
                    'sample_size': 12,
                    'win_rate': 0.75,
                    'avg_return': 0.041,
                    'median_return': 0.032,
                    'max_return': 0.089,
                    'max_loss': -0.023,
                    'sharpe_ratio': 2.1,
                    'matched_trades': [...],
                    'validation_date': '2026-02-09'
                }
            confidence_level: 'HIGH', 'MEDIUM', 'LOW', or 'REJECTED'
            regime: Market regime dictionary (optional):
                {
                    'vix_bucket': 'low' | 'medium' | 'high',
                    'spy_trend': 'bull' | 'bear' | 'flat',
                    'sector_strength': str (future)
                }
            source: Where this lesson came from

        Returns:
            ID of the created lesson
        """
        import json

        # Extract fields from pattern
        lesson_type = pattern.get('type', 'unknown')
        hypothesis = pattern.get('hypothesis', 'No hypothesis provided')
        pattern_rule = json.dumps(pattern.get('conditions', {}))
        
        # Determine category based on lesson type
        category_map = {
            'entry_filter': 'validated_entry',
            'exit_rule': 'validated_exit',
            'timing': 'validated_timing',
            'risk_management': 'validated_risk'
        }
        category = category_map.get(lesson_type, 'validated_pattern')

        # Create lesson hash for deduplication
        lesson_hash = self._hash_lesson(hypothesis, category)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if lesson already exists
            cursor.execute("SELECT id FROM lessons WHERE lesson_hash = ?", (lesson_hash,))
            existing = cursor.fetchone()

            if existing:
                # Update existing lesson with new backtest results
                update_sql = """
                    UPDATE lessons
                    SET validated = 1,
                        confidence_level = ?,
                        backtest_win_rate = ?,
                        backtest_avg_return = ?,
                        backtest_sample_size = ?,
                        backtest_date = ?,
                        backtest_data = ?,
                        pattern_rule = ?,
                        times_validated = times_validated + 1,
                        last_validated = ?
                """
                update_params = [
                    confidence_level,
                    backtest_results.get('win_rate'),
                    backtest_results.get('avg_return'),
                    backtest_results.get('sample_size'),
                    backtest_results.get('validation_date', datetime.now().isoformat()[:10]),
                    json.dumps(backtest_results),
                    pattern_rule,
                    datetime.now().isoformat()
                ]

                # Add regime fields if provided
                if regime:
                    update_sql += """,
                        regime_vix_bucket = ?,
                        regime_spy_trend = ?,
                        regime_sector_strength = ?
                    """
                    update_params.extend([
                        regime.get('vix_bucket'),
                        regime.get('spy_trend'),
                        regime.get('sector_strength')
                    ])

                update_sql += " WHERE id = ?"
                update_params.append(existing['id'])

                cursor.execute(update_sql, update_params)
                logger.info(f"Updated validated lesson {existing['id']}: {hypothesis[:50]}...")
                return existing['id']

            # Insert new validated lesson
            insert_cols = """
                lesson_hash, lesson_text, category, lesson_type,
                pattern_rule, hypothesis, validated, confidence_level,
                backtest_win_rate, backtest_avg_return, backtest_sample_size,
                backtest_date, backtest_data, confidence, source,
                is_active, created_at
            """
            insert_vals = "?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?"
            insert_params = [
                lesson_hash,
                hypothesis,  # lesson_text
                category,
                lesson_type,
                pattern_rule,
                hypothesis,
                confidence_level,
                backtest_results.get('win_rate'),
                backtest_results.get('avg_return'),
                backtest_results.get('sample_size'),
                backtest_results.get('validation_date', datetime.now().isoformat()[:10]),
                json.dumps(backtest_results),
                backtest_results.get('win_rate', 0.5),  # Use win rate as confidence score
                source,
                datetime.now().isoformat()
            ]

            # Add regime fields if provided
            if regime:
                insert_cols += ", regime_vix_bucket, regime_spy_trend, regime_sector_strength"
                insert_vals += ", ?, ?, ?"
                insert_params.extend([
                    regime.get('vix_bucket'),
                    regime.get('spy_trend'),
                    regime.get('sector_strength')
                ])

            cursor.execute(f"""
                INSERT INTO lessons ({insert_cols})
                VALUES ({insert_vals})
            """, insert_params)

            lesson_id = cursor.lastrowid
            logger.info(
                f"Saved validated lesson {lesson_id} ({confidence_level}): "
                f"{hypothesis[:50]}... "
                f"[{backtest_results.get('sample_size')} trades, "
                f"{backtest_results.get('win_rate', 0)*100:.0f}% win rate]"
            )
            return lesson_id

    # ------------------------------------------------------------------
    # Rebuy block persistence - survives scheduler restarts
    # ------------------------------------------------------------------

    def add_rebuy_block(self, symbol: str, hours: int = 12,
                        entry_price: float = None, exit_price: float = None,
                        loss_pct: float = None):
        """
        Persist a rebuy block for a symbol after a loss.

        Replaces the in-memory recently_closed_losers dict so the 12-hour
        cooldown survives scheduler restarts.
        """
        block_until = (datetime.now() + timedelta(hours=hours)).isoformat()
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO rebuy_blocks
                   (symbol, block_until, entry_price, exit_price, loss_pct, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol.upper(), block_until, entry_price, exit_price, loss_pct, "loss_exit", now)
            )
        logger.info(f"Rebuy block persisted for {symbol}: blocked until {block_until} (loss={loss_pct:.1f}%)")

    def is_rebuy_blocked(self, symbol: str) -> Optional[Dict]:
        """
        Check if a symbol is currently blocked from rebuying.

        Returns:
            Dict with block info if blocked, None if not blocked.
            Dict includes: block_until, loss_pct, hours_remaining
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM rebuy_blocks WHERE symbol = ?",
                (symbol.upper(),)
            ).fetchone()

        if not row:
            return None

        block_until = datetime.fromisoformat(row["block_until"])
        if datetime.now() >= block_until:
            # Block expired - clean it up
            self.remove_rebuy_block(symbol)
            return None

        hours_remaining = (block_until - datetime.now()).total_seconds() / 3600
        return {
            "symbol": symbol,
            "block_until": row["block_until"],
            "loss_pct": row["loss_pct"],
            "entry_price": row["entry_price"],
            "exit_price": row["exit_price"],
            "hours_remaining": round(hours_remaining, 1),
        }

    def remove_rebuy_block(self, symbol: str):
        """Remove an expired or manually cleared rebuy block."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM rebuy_blocks WHERE symbol = ?", (symbol.upper(),))

    def get_all_rebuy_blocks(self) -> List[Dict]:
        """Return all active rebuy blocks (for startup restore into memory)."""
        now_str = datetime.now().isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rebuy_blocks WHERE block_until > ?", (now_str,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_validated_lessons(self, min_confidence: str = 'MEDIUM',
                               max_age_days: int = 30,
                               category: str = None,
                               regime_filter: Dict = None,
                               apply_decay: bool = True) -> List[Dict]:
        """
        Get validated lessons for use in trading decisions.

        This method is called in the morning routine to feed Grok
        evidence-based lessons.

        Args:
            min_confidence: Minimum confidence level ('HIGH', 'MEDIUM', 'LOW')
                           Only lessons at or above this level are returned
            max_age_days: Maximum age of lessons (default 30 days)
            category: Optional filter by category
            regime_filter: Optional dict to filter by regime:
                {
                    'vix_bucket': 'low' | 'medium' | 'high',
                    'spy_trend': 'bull' | 'bear' | 'flat'
                }
                If provided, only returns lessons from similar regimes
            apply_decay: Apply exponential confidence decay over time (default: True)
                        Patterns lose confidence as they age:
                        - HIGH patterns: 50% decay after 60 days, LOW after 90 days
                        - MEDIUM patterns: 50% decay after 30 days, LOW after 60 days
                        - LOW patterns: Rejected after 30 days

        Returns:
            List of validated lesson dictionaries with backtest data
        """
        # Map confidence levels to hierarchy
        confidence_hierarchy = {
            'HIGH': ['HIGH'],
            'MEDIUM': ['HIGH', 'MEDIUM'],
            'LOW': ['HIGH', 'MEDIUM', 'LOW']
        }

        allowed_levels = confidence_hierarchy.get(min_confidence.upper(), ['HIGH', 'MEDIUM'])

        # Calculate cutoff date
        cutoff_date = (date.today() - timedelta(days=max_age_days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = """
                SELECT * FROM lessons
                WHERE validated = 1
                AND is_active = 1
                AND confidence_level IN ({})
                AND (backtest_date >= ? OR backtest_date IS NULL)
            """.format(','.join('?' * len(allowed_levels)))

            params = list(allowed_levels) + [cutoff_date]

            if category:
                query += " AND category = ?"
                params.append(category)

            # Add regime filters if provided
            if regime_filter:
                if regime_filter.get('vix_bucket'):
                    query += " AND (regime_vix_bucket = ? OR regime_vix_bucket IS NULL)"
                    params.append(regime_filter['vix_bucket'])

                if regime_filter.get('spy_trend'):
                    query += " AND (regime_spy_trend = ? OR regime_spy_trend IS NULL)"
                    params.append(regime_filter['spy_trend'])

            query += " ORDER BY confidence_level DESC, backtest_win_rate DESC, backtest_sample_size DESC"

            cursor.execute(query, params)
            lessons = [dict(row) for row in cursor.fetchall()]

            # Apply confidence decay if enabled
            if apply_decay:
                lessons = self._apply_confidence_decay(lessons)

            log_msg = f"Retrieved {len(lessons)} validated lessons " \
                      f"(min_confidence={min_confidence}, max_age={max_age_days} days"
            if regime_filter:
                log_msg += f", regime={regime_filter}"
            if apply_decay:
                log_msg += ", decay=enabled"
            log_msg += ")"
            logger.info(log_msg)

            return lessons

    def _apply_confidence_decay(self, lessons: List[Dict]) -> List[Dict]:
        """
        Apply exponential confidence decay to lessons based on age.

        Older patterns are less reliable as market conditions change.
        Different decay rates for different confidence levels:
        - HIGH: Slower decay (60-day half-life)
        - MEDIUM: Medium decay (30-day half-life)
        - LOW: Fast decay (15-day half-life)

        Args:
            lessons: List of lesson dictionaries

        Returns:
            Filtered and modified lesson list with effective_confidence applied
        """
        import math

        filtered_lessons = []
        today = date.today()

        for lesson in lessons:
            # Get lesson age in days
            backtest_date_str = lesson.get('backtest_date')
            if not backtest_date_str:
                # No backtest date, skip decay
                lesson['effective_confidence'] = lesson['confidence_level']
                filtered_lessons.append(lesson)
                continue

            try:
                backtest_date = date.fromisoformat(backtest_date_str)
                days_old = (today - backtest_date).days
            except:
                # Invalid date, skip decay
                lesson['effective_confidence'] = lesson['confidence_level']
                filtered_lessons.append(lesson)
                continue

            original_confidence = lesson['confidence_level']

            # Define decay half-lives (days for 50% probability of downgrade)
            decay_rates = {
                'HIGH': 60,    # HIGH patterns are more stable
                'MEDIUM': 30,  # MEDIUM patterns decay faster
                'LOW': 15      # LOW patterns decay quickly
            }

            half_life = decay_rates.get(original_confidence, 30)

            # Calculate decay factor: exp(-ln(2) * days / half_life)
            # At half_life days, decay_factor = 0.5
            decay_factor = math.exp(-0.693147 * days_old / half_life)

            # Determine effective confidence based on decay
            if original_confidence == 'HIGH':
                if decay_factor > 0.7:  # <25 days old
                    effective = 'HIGH'
                elif decay_factor > 0.4:  # 25-70 days old
                    effective = 'MEDIUM'
                elif decay_factor > 0.25:  # 70-110 days old
                    effective = 'LOW'
                else:  # >110 days old
                    continue  # Skip (effectively rejected)

            elif original_confidence == 'MEDIUM':
                if decay_factor > 0.5:  # <30 days old
                    effective = 'MEDIUM'
                elif decay_factor > 0.25:  # 30-60 days old
                    effective = 'LOW'
                else:  # >60 days old
                    continue  # Skip (effectively rejected)

            else:  # LOW
                if decay_factor > 0.5:  # <15 days old
                    effective = 'LOW'
                else:  # >15 days old
                    continue  # Skip (effectively rejected)

            # Store effective confidence and decay info
            lesson['effective_confidence'] = effective
            lesson['decay_factor'] = decay_factor
            lesson['days_old'] = days_old

            # Add decay note if downgraded
            if effective != original_confidence:
                lesson['decay_note'] = f"Decayed from {original_confidence} after {days_old} days"
                logger.debug(
                    f"Pattern '{lesson.get('hypothesis', 'Unknown')[:50]}' "
                    f"decayed: {original_confidence} -> {effective} ({days_old} days old)"
                )

            filtered_lessons.append(lesson)

        # Log decay summary
        original_count = len(lessons)
        filtered_count = len(filtered_lessons)
        if filtered_count < original_count:
            logger.info(
                f"Confidence decay: Filtered {original_count - filtered_count} expired patterns "
                f"({filtered_count}/{original_count} remaining)"
            )

        return filtered_lessons

    def update_lesson_confidence(self, lesson_id: int, new_confidence: str,
                                  backtest_data: Dict = None) -> bool:
        """
        Update a lesson's confidence level after revalidation.

        Used in Phase 8 (continuous revalidation) to adjust confidence
        as market conditions change.

        Args:
            lesson_id: ID of the lesson to update
            new_confidence: New confidence level ('HIGH', 'MEDIUM', 'LOW', 'REJECTED')
            backtest_data: Optional new backtest results

        Returns:
            True if updated successfully, False if lesson not found
        """
        import json

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if lesson exists
            cursor.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,))
            if not cursor.fetchone():
                logger.warning(f"Lesson {lesson_id} not found")
                return False

            if new_confidence == 'REJECTED':
                # Deactivate rejected lessons
                cursor.execute("""
                    UPDATE lessons
                    SET confidence_level = ?,
                        is_active = 0,
                        backtest_date = ?
                    WHERE id = ?
                """, (new_confidence, date.today().isoformat(), lesson_id))
                logger.info(f"Lesson {lesson_id} REJECTED and deactivated")
            else:
                # Update confidence and optionally backtest data
                if backtest_data:
                    cursor.execute("""
                        UPDATE lessons
                        SET confidence_level = ?,
                            backtest_win_rate = ?,
                            backtest_avg_return = ?,
                            backtest_sample_size = ?,
                            backtest_date = ?,
                            backtest_data = ?,
                            times_validated = times_validated + 1,
                            last_validated = ?
                        WHERE id = ?
                    """, (
                        new_confidence,
                        backtest_data.get('win_rate'),
                        backtest_data.get('avg_return'),
                        backtest_data.get('sample_size'),
                        backtest_data.get('validation_date', date.today().isoformat()),
                        json.dumps(backtest_data),
                        datetime.now().isoformat(),
                        lesson_id
                    ))
                else:
                    cursor.execute("""
                        UPDATE lessons
                        SET confidence_level = ?,
                            backtest_date = ?,
                            times_validated = times_validated + 1,
                            last_validated = ?
                        WHERE id = ?
                    """, (
                        new_confidence,
                        date.today().isoformat(),
                        datetime.now().isoformat(),
                        lesson_id
                    ))
                
                logger.info(f"Updated lesson {lesson_id} confidence to {new_confidence}")

            return True

    # =========================================================================
    # LOSS ANALYSIS (Autopsy with "why")
    # =========================================================================

    def add_loss_analysis(self, symbol: str, loss_amount: float,
                          why_entered: str, why_failed: str,
                          entry_price: float = None, exit_price: float = None,
                          loss_percent: float = None, entry_quality: str = None,
                          market_context: str = None, pattern_attempted: str = None,
                          loss_category: str = None, was_preventable: bool = False,
                          trade_date: date = None) -> int:
        """
        Record a detailed loss analysis ("autopsy") with the "why".

        This captures not just WHAT happened but WHY, enabling better lessons.

        Args:
            symbol: Stock symbol
            loss_amount: Dollar amount lost
            why_entered: Reasoning for entering the trade
            why_failed: Analysis of why the trade failed
            entry_price: Entry price
            exit_price: Exit price
            loss_percent: Percentage loss
            entry_quality: "good", "fair", "poor" - was the entry well-timed?
            market_context: "bullish", "bearish", "choppy", "trending"
            pattern_attempted: What setup was being traded (e.g., "breakout", "gap_and_go")
            loss_category: Classification (e.g., "bad_entry", "market_reversal", "chased")
            was_preventable: Could this loss have been avoided?
            trade_date: Date of trade (defaults to today)

        Returns:
            ID of the loss analysis record
        """
        if trade_date is None:
            trade_date = date.today()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO loss_analysis
                (trade_date, symbol, entry_price, exit_price, loss_amount, loss_percent,
                 why_entered, why_failed, entry_quality, market_context, pattern_attempted,
                 loss_category, was_preventable, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_date.isoformat(), symbol, entry_price, exit_price,
                  loss_amount, loss_percent, why_entered, why_failed,
                  entry_quality, market_context, pattern_attempted,
                  loss_category, 1 if was_preventable else 0,
                  datetime.now().isoformat()))

            loss_id = cursor.lastrowid
            logger.info(f"Added loss analysis for {symbol}: ${loss_amount:.2f} - {why_failed[:50]}...")

            # Auto-generate lesson if we have enough similar losses
            self._maybe_generate_loss_lesson(cursor, symbol, loss_category, pattern_attempted)

            return loss_id

    def _maybe_generate_loss_lesson(self, cursor, symbol: str,
                                     loss_category: str, pattern: str):
        """
        Auto-generate a lesson if we see repeated similar losses.

        Requires 3+ losses with same category/pattern to generate lesson.
        """
        # Check for repeated losses on same symbol with same category
        if loss_category:
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(loss_amount) as total_loss
                FROM loss_analysis
                WHERE symbol = ? AND loss_category = ?
            """, (symbol, loss_category))

            result = cursor.fetchone()
            if result and result['count'] >= 3:
                # Generate structured lesson
                condition = f"{symbol} trade with {loss_category} pattern"
                action = f"CAUTION - {result['count']} losses totaling ${abs(result['total_loss']):.2f}"

                self.add_structured_lesson(
                    condition=condition,
                    action=action,
                    category="loss_pattern",
                    symbol=symbol,
                    evidence_count=result['count'],
                    evidence_summary=f"Lost ${abs(result['total_loss']):.2f} across {result['count']} trades",
                    source="loss_autopsy"
                )

        # Check for repeated losses on same pattern (across symbols)
        if pattern:
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(loss_amount) as total_loss,
                       GROUP_CONCAT(DISTINCT symbol) as symbols
                FROM loss_analysis
                WHERE pattern_attempted = ?
            """, (pattern,))

            result = cursor.fetchone()
            if result and result['count'] >= 5:
                # Generate pattern-level lesson
                condition = f"Trading the {pattern} pattern"
                action = f"REVIEW - {result['count']} losses (${abs(result['total_loss']):.2f}) on: {result['symbols']}"

                self.add_structured_lesson(
                    condition=condition,
                    action=action,
                    category="pattern_warning",
                    evidence_count=result['count'],
                    evidence_summary=f"Pattern failed on: {result['symbols']}",
                    source="loss_autopsy"
                )

    def get_loss_patterns(self, min_occurrences: int = 3) -> List[Dict]:
        """
        Analyze loss patterns to find recurring issues.

        Returns patterns that have caused repeated losses.
        """
        patterns = []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Group by loss category
            cursor.execute("""
                SELECT loss_category, COUNT(*) as count,
                       SUM(loss_amount) as total_loss,
                       AVG(loss_amount) as avg_loss,
                       GROUP_CONCAT(DISTINCT symbol) as symbols
                FROM loss_analysis
                WHERE loss_category IS NOT NULL
                GROUP BY loss_category
                HAVING count >= ?
                ORDER BY total_loss ASC
            """, (min_occurrences,))

            for row in cursor.fetchall():
                patterns.append({
                    "type": "category",
                    "name": row['loss_category'],
                    "count": row['count'],
                    "total_loss": row['total_loss'],
                    "avg_loss": row['avg_loss'],
                    "symbols": row['symbols']
                })

            # Group by symbol
            cursor.execute("""
                SELECT symbol, COUNT(*) as count,
                       SUM(loss_amount) as total_loss,
                       AVG(loss_amount) as avg_loss,
                       GROUP_CONCAT(DISTINCT loss_category) as categories,
                       GROUP_CONCAT(why_failed, ' | ') as reasons
                FROM loss_analysis
                GROUP BY symbol
                HAVING count >= ?
                ORDER BY total_loss ASC
            """, (min_occurrences,))

            for row in cursor.fetchall():
                patterns.append({
                    "type": "symbol",
                    "name": row['symbol'],
                    "count": row['count'],
                    "total_loss": row['total_loss'],
                    "avg_loss": row['avg_loss'],
                    "categories": row['categories'],
                    "reasons": row['reasons'][:200] if row['reasons'] else None
                })

        return patterns

    def get_preventable_loss_summary(self) -> Dict:
        """Get summary of preventable losses for learning."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) as preventable_count,
                       SUM(loss_amount) as preventable_total
                FROM loss_analysis
                WHERE was_preventable = 1
            """)
            preventable = cursor.fetchone()

            cursor.execute("""
                SELECT COUNT(*) as total_count,
                       SUM(loss_amount) as total_loss
                FROM loss_analysis
            """)
            total = cursor.fetchone()

            return {
                "total_losses": total['total_count'] or 0,
                "total_lost": total['total_loss'] or 0,
                "preventable_count": preventable['preventable_count'] or 0,
                "preventable_amount": preventable['preventable_total'] or 0,
                "preventable_ratio": (preventable['preventable_count'] / total['total_count']
                                      if total['total_count'] else 0)
            }

    # =========================================================================
    # TRADE JOURNAL - Entry reasoning captured at purchase time
    # =========================================================================

    def record_trade_entry(self, symbol: str, entry_price: float,
                           why_entered: str, shares: int = None,
                           expected_target: float = None, expected_stop: float = None,
                           setup_type: str = None, catalyst: str = None,
                           entry_catalyst: str = None, hold_type: str = None,
                           candidate_lane: str = None,
                           market_context: str = None, confidence_level: str = None,
                           order_id: str = None, trade_date: date = None,
                           trade_id: str = None,
                           lessons_applied: list = None) -> int:
        """
        Record entry reasoning AT THE TIME OF PURCHASE.

        This captures the LLM's thinking when it decides to buy, so we have
        accurate "why" data for end-of-day reflection (instead of trying to
        remember morning reasoning at 1pm).

        Args:
            symbol: Stock symbol
            entry_price: Entry price
            why_entered: The reasoning for entering (captured at buy time)
            shares: Number of shares
            expected_target: Expected price target
            expected_stop: Stop loss price
            setup_type: Type of setup (e.g., "breakout", "gap_and_go", "vwap_bounce")
            catalyst: What triggered the trade (e.g., "earnings beat", "sector strength")
            market_context: Market conditions (e.g., "SPY green", "VIX low")
            confidence_level: Confidence in trade (e.g., "high", "medium", "low")
            order_id: Broker order ID for linking
            trade_date: Date of trade (defaults to today)

        Returns:
            ID of the journal entry
        """
        if trade_date is None:
            trade_date = date.today()

        entry_time = datetime.now().strftime("%H:%M:%S")

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Serialize lessons_applied to JSON; also extract from why_entered text as fallback
            import re as _re
            import json as _json
            if lessons_applied is None:
                # Parse L-prefixed lesson IDs from reasoning text (e.g. "L229, L142")
                parsed = [int(x) for x in _re.findall(r'L(\d+)', why_entered or '')]
                lessons_applied_json = _json.dumps(parsed) if parsed else None
            else:
                lessons_applied_json = _json.dumps(list(lessons_applied))

            cursor.execute("""
                INSERT INTO trade_journal
                (trade_date, symbol, entry_time, entry_price, shares,
                 why_entered, expected_target, expected_stop, setup_type,
                 catalyst, entry_catalyst, hold_type, candidate_lane, market_context,
                 confidence_level, order_id, trade_id, lessons_applied, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_date.isoformat(), symbol, entry_time, entry_price, shares,
                   why_entered, expected_target, expected_stop, setup_type,
                  catalyst, entry_catalyst or catalyst, hold_type, candidate_lane, market_context,
                  confidence_level, order_id, trade_id,
                  lessons_applied_json, datetime.now().isoformat()))

            journal_id = cursor.lastrowid
            logger.info(f"Recorded trade journal for {symbol}: {why_entered[:50]}...")

            return journal_id

    def update_trade_exit(self, symbol: str, exit_price: float,
                          exit_reason: str, actual_pnl: float,
                          trade_date: date = None, order_id: str = None) -> bool:
        """
        Update a journal entry with exit information.

        Called when a position is closed to complete the journal record.

        Args:
            symbol: Stock symbol
            exit_price: Exit price
            exit_reason: Why the trade was exited
            actual_pnl: Realized P&L
            trade_date: Date of original entry (defaults to today)
            order_id: Order ID to match (if multiple entries same day)

        Returns:
            True if updated, False if no matching entry found
        """
        if trade_date is None:
            trade_date = date.today()

        exit_time = datetime.now().strftime("%H:%M:%S")

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Find the matching journal entry (most recent for this symbol/date)
            if order_id:
                cursor.execute("""
                    SELECT id FROM trade_journal
                    WHERE symbol = ? AND trade_date = ? AND order_id = ?
                    AND exit_time IS NULL
                    ORDER BY entry_time DESC LIMIT 1
                """, (symbol, trade_date.isoformat(), order_id))
            else:
                cursor.execute("""
                    SELECT id FROM trade_journal
                    WHERE symbol = ? AND trade_date = ?
                    AND exit_time IS NULL
                    ORDER BY entry_time DESC LIMIT 1
                """, (symbol, trade_date.isoformat()))

            row = cursor.fetchone()
            if not row:
                logger.warning(f"No matching journal entry for {symbol} on {trade_date}")
                return False

            cursor.execute("""
                UPDATE trade_journal
                SET exit_time = ?, exit_price = ?, exit_reason = ?, actual_pnl = ?
                WHERE id = ?
            """, (exit_time, exit_price, exit_reason, actual_pnl, row['id']))

            logger.info(f"Updated trade journal for {symbol}: exit at ${exit_price:.2f}, P&L ${actual_pnl:.2f}")
            return True

    def get_trade_journal_entry(self, symbol: str, trade_date: str) -> dict:
        """Get the most recent trade_journal entry for symbol + date.

        Returns a dict of all columns, or None if not found.
        Used at trade exit to retrieve the specific lessons_applied Grok
        reported at order placement, enabling precise lesson attribution.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trade_journal WHERE symbol = ? AND trade_date = ? "
                "ORDER BY id DESC LIMIT 1",
                (symbol, trade_date)
            )
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row))
            return None

    def record_partial_exit(self, symbol: str, exit_price: float,
                           exit_reason: str, qty_sold: int,
                           partial_pnl: float, remaining_qty: int,
                           trade_date: date = None) -> bool:
        """
        Record a partial exit (profit-taking) without closing the trade.

        Partial exits are stored as JSON array in the trade_journal.partial_exits column.
        This allows tracking multiple partial exits per trade for accurate P&L attribution.

        Args:
            symbol: Stock symbol
            exit_price: Price at which partial was sold
            exit_reason: Reason for partial exit (e.g., "partial_profit_tier2_2pct")
            qty_sold: Number of shares sold in partial exit
            partial_pnl: Realized P&L from this partial exit
            remaining_qty: Remaining shares after partial exit
            trade_date: Date of original entry (defaults to today)

        Returns:
            True if recorded, False if no matching entry found
        """
        if trade_date is None:
            trade_date = date.today()

        exit_time = datetime.now().strftime("%H:%M:%S")

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Find most recent open journal entry for this symbol
            cursor.execute("""
                SELECT id, partial_exits FROM trade_journal
                WHERE symbol = ? AND trade_date = ?
                AND exit_time IS NULL
                ORDER BY entry_time DESC LIMIT 1
            """, (symbol, trade_date.isoformat()))

            row = cursor.fetchone()
            if not row:
                logger.warning(f"No matching open trade journal entry for {symbol} on {trade_date}")
                return False

            # Parse existing partial exits (JSON array)
            import json
            existing_partials = []
            if row['partial_exits']:
                try:
                    existing_partials = json.loads(row['partial_exits'])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Could not parse existing partial_exits for {symbol}, starting fresh")

            # Append new partial exit
            new_partial = {
                'timestamp': exit_time,
                'price': exit_price,
                'qty_sold': qty_sold,
                'partial_pnl': partial_pnl,
                'remaining_qty': remaining_qty,
                'reason': exit_reason
            }
            existing_partials.append(new_partial)

            # Update the journal entry
            cursor.execute("""
                UPDATE trade_journal
                SET partial_exits = ?
                WHERE id = ?
            """, (json.dumps(existing_partials), row['id']))

            logger.info(f"Recorded partial exit for {symbol}: {qty_sold} shares at ${exit_price:.2f}, P&L ${partial_pnl:.2f}")
            return True

    # =========================================================================
    # NEWS CHECK EVENTS
    # =========================================================================

    def log_news_check_event(
        self,
        trade_id: str,
        symbol: str,
        window: str,
        lookback_hours: int,
        news_found: bool,
        news_summary: str = None,
        agent_action: str = None,
        agent_confidence: int = None,
        agent_reason: str = None,
        news_category: str = None,
        urgency: str = None,
        daily_call_count: int = 1,
    ) -> int:
        """Log a news check evaluation for a swing position. trade_id is mandatory."""
        # daily_call_count: caller passes their local count at time of call (pre-increment);
        # the DB row count via get_news_check_count_today() is the authoritative limit check.
        if not trade_id:
            raise ValueError("trade_id is required for news_check_events - needed for outcome measurement")
        now = datetime.now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO news_check_events
                    (trade_id, symbol, check_date, check_time, window, lookback_hours,
                     news_found, news_summary, agent_action, agent_confidence,
                     agent_reason, news_category, urgency, daily_call_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, symbol,
                now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                window, lookback_hours,
                1 if news_found else 0,
                news_summary, agent_action, agent_confidence,
                agent_reason, news_category, urgency, daily_call_count,
                now.isoformat(),
            ))
            return cursor.lastrowid

    def log_regime_event(self, event_type: str, regime_score: int, gates: dict) -> None:
        """Log a regime event (e.g. REGIME_BLOCK) to decision_journal."""
        import json
        now = datetime.now()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO decision_journal
                    (decision_date, decision_time, decision_type,
                     candidates_considered, total_candidates, viable_candidates,
                     market_context, decision_reason, agent_reasoning, created_at)
                    VALUES (?, ?, ?, 0, 0, 0, ?, ?, NULL, ?)
                """, (
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S"),
                    event_type,
                    json.dumps({"regime_score": regime_score}),
                    json.dumps(gates),
                    now.isoformat(),
                ))
                conn.commit()
                logger.debug("[DB] Logged regime event: %s (score=%d)", event_type, regime_score)
        except Exception as e:
            logger.warning("[DB] log_regime_event failed: %s", e)

    def get_news_check_events(self, trade_id: str = None, symbol: str = None,
                               limit: int = 50) -> list:
        """Retrieve news check events filtered by trade_id or symbol. Both cannot be omitted."""
        if trade_id is None and symbol is None:
            raise ValueError("get_news_check_events requires trade_id or symbol filter")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if trade_id:
                cursor.execute(
                    "SELECT * FROM news_check_events WHERE trade_id = ? ORDER BY created_at DESC LIMIT ?",
                    (trade_id, limit)
                )
            else:
                cursor.execute(
                    "SELECT * FROM news_check_events WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                    (symbol, limit)
                )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_news_check_count_today(self, symbol: str) -> int:
        """Return how many news checks ran today for this symbol (daily limit enforcement)."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM news_check_events WHERE symbol = ? AND check_date = ?",
                (symbol, today)
            )
            return cursor.fetchone()[0]

    def get_journal_entries(self, trade_date: date = None,
                            symbol: str = None, include_open: bool = True) -> List[Dict]:
        """
        Get journal entries for reflection.

        Args:
            trade_date: Date to get entries for (defaults to today)
            symbol: Filter by symbol
            include_open: Include entries without exit data

        Returns:
            List of journal entry dictionaries
        """
        if trade_date is None:
            trade_date = date.today()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM trade_journal WHERE trade_date = ?"
            params = [trade_date.isoformat()]

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            if not include_open:
                query += " AND exit_time IS NOT NULL"

            query += " ORDER BY entry_time ASC"

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_journal_entry_for_trade(self, symbol: str, trade_date: date = None) -> Optional[Dict]:
        """
        Get the journal entry for a specific trade (for EOD reflection).

        Returns the entry reasoning captured at purchase time.
        """
        if trade_date is None:
            trade_date = date.today()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM trade_journal
                WHERE symbol = ? AND trade_date = ?
                ORDER BY entry_time DESC LIMIT 1
            """, (symbol, trade_date.isoformat()))

            row = cursor.fetchone()
            return dict(row) if row else None

    def cleanup_old_journal_entries(self, days_to_keep: int = 7) -> int:
        """
        Remove old journal entries to prevent database growth.

        The journal's primary purpose is intraday context for LLM reflection,
        not long-term storage. We keep a few days as buffer.

        Args:
            days_to_keep: Number of days to retain (default 7)

        Returns:
            Number of entries deleted
        """
        cutoff_date = (date.today() - timedelta(days=days_to_keep)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM trade_journal WHERE trade_date < ?",
                          (cutoff_date,))
            count_before = cursor.fetchone()[0]

            cursor.execute("DELETE FROM trade_journal WHERE trade_date < ?",
                          (cutoff_date,))

            if count_before > 0:
                logger.info(f"Cleaned up {count_before} old journal entries (older than {days_to_keep} days)")

            return count_before

    # =========================================================================
    # DECISION JOURNAL - Tracks all trading decisions including NOPOSITIONS
    # =========================================================================

    def record_decision(self, decision_type: str, candidates_considered: list = None,
                        decision_reason: str = None, agent_reasoning: str = None,
                        market_context: str = None, decision_date: date = None) -> int:
        """
        Record a trading decision to the journal.

        This captures when the agent decides NOT to take positions and why,
        as well as partial position decisions. Critical for learning from
        missed opportunities.

        Args:
            decision_type: 'NOPOSITIONS', 'PARTIAL', or 'FULL'
            candidates_considered: List of candidate dicts with symbol, conviction, etc.
            decision_reason: Short summary of why this decision was made
            agent_reasoning: Full agent explanation (from last_analysis_summary)
            market_context: VIX level, market regime, etc.
            decision_date: Date of decision (defaults to today)

        Returns:
            ID of the decision journal entry
        """
        import json

        if decision_date is None:
            decision_date = date.today()

        decision_time = datetime.now().strftime("%H:%M:%S")

        # Calculate viable candidates
        total_candidates = len(candidates_considered) if candidates_considered else 0
        viable_candidates = sum(1 for c in (candidates_considered or [])
                                if c.get('in_plan', False))

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO decision_journal
                (decision_date, decision_time, decision_type,
                 candidates_considered, total_candidates, viable_candidates,
                 market_context, decision_reason, agent_reasoning, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (decision_date.isoformat(), decision_time, decision_type,
                  json.dumps(candidates_considered) if candidates_considered else None,
                  total_candidates, viable_candidates,
                  market_context, decision_reason, agent_reasoning,
                  datetime.now().isoformat()))

            decision_id = cursor.lastrowid
            logger.info(f"Recorded {decision_type} decision: {total_candidates} candidates -> {viable_candidates} viable")

            return decision_id

    def get_nopositions_decisions(self, decision_date: date = None,
                                   limit: int = 10) -> List[Dict]:
        """
        Get NOPOSITIONS decisions for reflection.

        Args:
            decision_date: Date to query (defaults to today)
            limit: Maximum decisions to return

        Returns:
            List of decision records
        """
        if decision_date is None:
            decision_date = date.today()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM decision_journal
                WHERE decision_date = ? AND decision_type = 'NOPOSITIONS'
                ORDER BY decision_time DESC
                LIMIT ?
            """, (decision_date.isoformat(), limit))

            return [dict(row) for row in cursor.fetchall()]

    def get_recent_decisions(self, days: int = 7, decision_type: str = None) -> List[Dict]:
        """
        Get recent trading decisions for analysis.

        Args:
            days: Number of days to look back
            decision_type: Filter by type ('NOPOSITIONS', 'PARTIAL', 'FULL') or None for all

        Returns:
            List of decision records
        """
        cutoff_date = (date.today() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            if decision_type:
                cursor.execute("""
                    SELECT * FROM decision_journal
                    WHERE decision_date >= ? AND decision_type = ?
                    ORDER BY decision_date DESC, decision_time DESC
                """, (cutoff_date, decision_type))
            else:
                cursor.execute("""
                    SELECT * FROM decision_journal
                    WHERE decision_date >= ?
                    ORDER BY decision_date DESC, decision_time DESC
                """, (cutoff_date,))

            return [dict(row) for row in cursor.fetchall()]

    def mark_missed_opportunity(self, decision_id: int, symbols_that_ran: list,
                                 reflection_notes: str = None) -> bool:
        """
        Mark a NOPOSITIONS decision as a missed opportunity after EOD reflection.

        Args:
            decision_id: ID of the decision journal entry
            symbols_that_ran: List of symbols that moved significantly after we passed
            reflection_notes: What we learned from missing this opportunity

        Returns:
            True if updated successfully
        """
        import json

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE decision_journal
                SET symbols_that_ran = ?,
                    missed_opportunity = 1,
                    reflection_notes = ?
                WHERE id = ?
            """, (json.dumps(symbols_that_ran), reflection_notes, decision_id))

            if cursor.rowcount > 0:
                logger.info(f"Marked decision {decision_id} as missed opportunity: {symbols_that_ran}")
                return True
            return False

    # =========================================================================
    # CANDIDATE FOLLOW-UP QUEUE - Tracks missed opportunities over 10 trading days
    # =========================================================================

    def create_candidate_followup(
        self,
        *,
        candidate_instance_id: str,
        scan_date: str,
        scan_time: str,
        symbol: str,
        decision_type: str,
        candidate_snapshot,
        next_review_date: str,
        candidate_lane: str = None,
        source: str = None,
        source_bucket: str = None,
        regime_mode: str = None,
        swing_score: int = None,
        min_conviction_today: float = None,
        decision_reason: str = None,
        agent_reasoning: str = None,
        forceswing_reason: str = None,
        soft_miss_reasons = None,
        catalyst_summary: str = None,
        decision_journal_id: str = None,
        trade_id: str = None,
    ) -> Optional[int]:
        """Create or return an active candidate follow-up row."""
        if not is_followup_creation_allowed():
            logger.info("Follow-up creation paused (allow_new_lessons=false) - skipping candidate follow-up")
            return None

        created_at = datetime.now().isoformat()
        snapshot_json = json.dumps(candidate_snapshot or {}, sort_keys=True)
        soft_miss_json = json.dumps(list(soft_miss_reasons or []))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO candidate_followup_active (
                    candidate_instance_id, scan_date, scan_time, symbol,
                    candidate_lane, source, source_bucket, regime_mode,
                    swing_score, min_conviction_today, decision_type,
                    decision_reason, agent_reasoning, forceswing_reason,
                    soft_miss_reasons, catalyst_summary, candidate_snapshot,
                    decision_journal_id, trade_id, next_review_date,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_instance_id,
                    scan_date,
                    scan_time,
                    symbol,
                    candidate_lane,
                    source,
                    source_bucket,
                    regime_mode,
                    swing_score,
                    min_conviction_today,
                    decision_type,
                    decision_reason,
                    agent_reasoning,
                    forceswing_reason,
                    soft_miss_json,
                    catalyst_summary,
                    snapshot_json,
                    decision_journal_id,
                    trade_id,
                    next_review_date,
                    "active",
                    created_at,
                    created_at,
                ),
            )
            if cursor.rowcount:
                return cursor.lastrowid

            existing = cursor.execute(
                "SELECT id FROM candidate_followup_active WHERE candidate_instance_id = ?",
                (candidate_instance_id,),
            ).fetchone()
            return existing["id"] if existing else None

    def get_due_candidate_followups(self, as_of_date=None, limit: int = None) -> List[Dict]:
        """Return active follow-up rows due on or before the given review date."""
        if as_of_date is None:
            as_of_date = date.today().isoformat()
        elif isinstance(as_of_date, date):
            as_of_date = as_of_date.isoformat()

        query = """
            SELECT * FROM candidate_followup_active
            WHERE status = 'active' AND next_review_date <= ?
            ORDER BY next_review_date ASC, scan_date ASC, scan_time ASC
        """
        params = [as_of_date]
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_candidate_followup_checkpoint(
        self,
        *,
        candidate_instance_id: str,
        checkpoint_name: str,
        checkpoint_payload,
        next_review_date: str = None,
        status: str = "active",
    ) -> bool:
        """Write one checkpoint payload and advance the active row state."""
        valid_checkpoints = {"checkpoint_1d", "checkpoint_3d", "checkpoint_5d", "checkpoint_10d"}
        if checkpoint_name not in valid_checkpoints:
            raise ValueError(f"Invalid checkpoint_name: {checkpoint_name}")

        payload_json = json.dumps(checkpoint_payload or {}, sort_keys=True)
        updated_at = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if next_review_date is None:
                existing = cursor.execute(
                    "SELECT next_review_date FROM candidate_followup_active WHERE candidate_instance_id = ?",
                    (candidate_instance_id,),
                ).fetchone()
                next_review_date = existing["next_review_date"] if existing else None
            cursor.execute(
                f"""
                UPDATE candidate_followup_active
                SET {checkpoint_name} = ?,
                    next_review_date = ?,
                    status = ?,
                    updated_at = ?
                WHERE candidate_instance_id = ?
                """,
                (
                    payload_json,
                    next_review_date,
                    status,
                    updated_at,
                    candidate_instance_id,
                ),
            )
            return cursor.rowcount > 0

    def archive_completed_candidate_followup(
        self,
        *,
        candidate_instance_id: str,
        final_outcome_summary: str = None,
        archived_reason: str = "completed_10d",
    ) -> bool:
        """Move a completed follow-up row from active to archive."""
        archived_at = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM candidate_followup_active WHERE candidate_instance_id = ?",
                (candidate_instance_id,),
            ).fetchone()
            if row is None:
                return False

            row_dict = dict(row)
            cursor.execute(
                """
                INSERT INTO candidate_followup_archive (
                    candidate_instance_id, scan_date, scan_time, symbol,
                    candidate_lane, source, source_bucket, regime_mode,
                    swing_score, min_conviction_today, decision_type,
                    decision_reason, agent_reasoning, forceswing_reason,
                    soft_miss_reasons, catalyst_summary, candidate_snapshot,
                    decision_journal_id, trade_id,
                    checkpoint_1d, checkpoint_3d, checkpoint_5d, checkpoint_10d,
                    final_outcome_summary, archived_at, archived_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_dict["candidate_instance_id"],
                    row_dict["scan_date"],
                    row_dict["scan_time"],
                    row_dict["symbol"],
                    row_dict.get("candidate_lane"),
                    row_dict.get("source"),
                    row_dict.get("source_bucket"),
                    row_dict.get("regime_mode"),
                    row_dict.get("swing_score"),
                    row_dict.get("min_conviction_today"),
                    row_dict["decision_type"],
                    row_dict.get("decision_reason"),
                    row_dict.get("agent_reasoning"),
                    row_dict.get("forceswing_reason"),
                    row_dict.get("soft_miss_reasons"),
                    row_dict.get("catalyst_summary"),
                    row_dict["candidate_snapshot"],
                    row_dict.get("decision_journal_id"),
                    row_dict.get("trade_id"),
                    row_dict.get("checkpoint_1d"),
                    row_dict.get("checkpoint_3d"),
                    row_dict.get("checkpoint_5d"),
                    row_dict.get("checkpoint_10d"),
                    final_outcome_summary,
                    archived_at,
                    archived_reason,
                    row_dict["created_at"],
                    archived_at,
                ),
            )
            cursor.execute(
                "DELETE FROM candidate_followup_active WHERE candidate_instance_id = ?",
                (candidate_instance_id,),
            )
            return True

    def get_weekly_candidate_followup_summary(self, end_date=None) -> Dict:
        """Return a compact summary of archived follow-ups completed in the trailing week."""
        if end_date is None:
            end_date = date.today()
        elif isinstance(end_date, str):
            end_date = date.fromisoformat(end_date[:10])

        start_date = end_date - timedelta(days=6)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT * FROM candidate_followup_archive
                WHERE date(archived_at) BETWEEN ? AND ?
                ORDER BY archived_at DESC
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
            archived = [dict(row) for row in rows]

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "completed_count": len(archived),
            "rows": archived,
        }

    def cleanup_old_decisions(self, days_to_keep: int = 30) -> int:
        """
        Remove old decision entries to prevent database growth.

        Args:
            days_to_keep: Number of days to retain (default 30)

        Returns:
            Number of entries deleted
        """
        cutoff_date = (date.today() - timedelta(days=days_to_keep)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM decision_journal WHERE decision_date < ?",
                          (cutoff_date,))
            count_before = cursor.fetchone()[0]

            cursor.execute("DELETE FROM decision_journal WHERE decision_date < ?",
                          (cutoff_date,))

            if count_before > 0:
                logger.info(f"Cleaned up {count_before} old decision entries (older than {days_to_keep} days)")

            return count_before

    # =========================================================================
    # SYMBOL HISTORY
    # =========================================================================

    def update_symbol_stats(self, symbol: str, pnl: float, won: bool):
        """
        Update all-time stats for a symbol after a trade.

        Args:
            symbol: Stock symbol
            pnl: Realized P&L
            won: Whether the trade was a winner
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get existing stats
            cursor.execute("SELECT * FROM symbol_history WHERE symbol = ?", (symbol,))
            existing = cursor.fetchone()

            today = date.today().isoformat()

            if existing:
                # Update existing
                new_trades = existing['total_trades'] + 1
                new_wins = existing['wins'] + (1 if won else 0)
                new_losses = existing['losses'] + (0 if won else 1)
                new_total_pnl = existing['total_pnl'] + pnl
                new_win_rate = new_wins / new_trades if new_trades > 0 else 0

                # Update avg win/loss
                if won:
                    old_win_total = existing['avg_win'] * existing['wins']
                    new_avg_win = (old_win_total + pnl) / new_wins if new_wins > 0 else 0
                    new_avg_loss = existing['avg_loss']
                else:
                    old_loss_total = existing['avg_loss'] * existing['losses']
                    new_avg_loss = (old_loss_total + abs(pnl)) / new_losses if new_losses > 0 else 0
                    new_avg_win = existing['avg_win']

                # Best/worst trade
                best_trade = max(existing['best_trade'], pnl)
                worst_trade = min(existing['worst_trade'], pnl)

                # Determine status
                if new_trades >= 5:
                    if new_win_rate < 0.25:
                        status = "AVOID"
                    elif new_win_rate < 0.40:
                        status = "CAUTION"
                    elif new_win_rate >= 0.60:
                        status = "FAVORABLE"
                    else:
                        status = "NEUTRAL"
                else:
                    status = "INSUFFICIENT_DATA"

                cursor.execute("""
                    UPDATE symbol_history
                    SET total_trades = ?, wins = ?, losses = ?, total_pnl = ?,
                        avg_win = ?, avg_loss = ?, best_trade = ?, worst_trade = ?,
                        last_trade_date = ?, win_rate = ?, status = ?
                    WHERE symbol = ?
                """, (new_trades, new_wins, new_losses, new_total_pnl,
                      new_avg_win, new_avg_loss, best_trade, worst_trade,
                      today, new_win_rate, status, symbol))
            else:
                # Insert new
                status = "INSUFFICIENT_DATA"
                cursor.execute("""
                    INSERT INTO symbol_history
                    (symbol, total_trades, wins, losses, total_pnl, avg_win, avg_loss,
                     best_trade, worst_trade, first_trade_date, last_trade_date, win_rate, status)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, 1 if won else 0, 0 if won else 1, pnl,
                      pnl if won else 0, abs(pnl) if not won else 0,
                      pnl, pnl, today, today, 1.0 if won else 0.0, status))

            logger.debug(f"Updated symbol history for {symbol}: {'+' if won else ''}{pnl:.2f}")

    def get_symbol_history(self, symbol: str) -> Optional[Dict]:
        """Get all-time history for a symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM symbol_history WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_symbols_to_avoid(self, min_trades: int = 5) -> List[Dict]:
        """Get symbols that should be avoided based on historical performance."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM symbol_history
                WHERE status IN ('AVOID', 'CAUTION') AND total_trades >= ?
                ORDER BY win_rate ASC
            """, (min_trades,))
            return [dict(row) for row in cursor.fetchall()]

    def get_favorable_symbols(self, min_trades: int = 5) -> List[Dict]:
        """Get symbols with favorable historical performance."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM symbol_history
                WHERE status = 'FAVORABLE' AND total_trades >= ?
                ORDER BY win_rate DESC
            """, (min_trades,))
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # DAILY SUMMARIES
    # =========================================================================

    def add_daily_summary(self, trade_date: date, trades_taken: int, wins: int,
                          gross_pnl: float, market_regime: str = None,
                          vix_level: float = None, key_insight: str = None,
                          what_worked: str = None, what_failed: str = None,
                          symbols_traded: List[str] = None,
                          lessons_learned: List[str] = None):
        """
        Add or update daily summary.

        Called at market close to record the day's learnings.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            symbols_str = ",".join(symbols_traded) if symbols_traded else None
            lessons_str = "; ".join(lessons_learned) if lessons_learned else None
            date_str = trade_date.isoformat()

            # Try to update existing, else insert
            cursor.execute("""
                INSERT INTO daily_summaries
                (trade_date, trades_taken, wins, losses, gross_pnl, market_regime,
                 vix_level, key_insight, what_worked, what_failed, symbols_traded,
                 lessons_learned, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    trades_taken = excluded.trades_taken,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    gross_pnl = excluded.gross_pnl,
                    market_regime = excluded.market_regime,
                    vix_level = excluded.vix_level,
                    key_insight = excluded.key_insight,
                    what_worked = excluded.what_worked,
                    what_failed = excluded.what_failed,
                    symbols_traded = excluded.symbols_traded,
                    lessons_learned = excluded.lessons_learned
            """, (date_str, trades_taken, wins, trades_taken - wins, gross_pnl,
                  market_regime, vix_level, key_insight, what_worked, what_failed,
                  symbols_str, lessons_str, datetime.now().isoformat()))

            logger.info(f"Added daily summary for {date_str}")

    def get_recent_summaries(self, days: int = 10) -> List[Dict]:
        """Get recent daily summaries."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM daily_summaries
                ORDER BY trade_date DESC
                LIMIT ?
            """, (days,))
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # PATTERN TRACKING
    # =========================================================================

    def record_pattern(self, pattern_name: str, description: str,
                       category: str, success: bool, pnl: float = 0):
        """
        Record occurrence of a pattern.

        Args:
            pattern_name: Short name (e.g., "gap_and_go", "vwap_bounce")
            description: Longer description
            category: Category (entry, exit, setup, etc.)
            success: Whether following the pattern was successful
            pnl: P&L from this occurrence
        """
        pattern_hash = hashlib.md5(f"{pattern_name}:{category}".encode()).hexdigest()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM patterns WHERE pattern_hash = ?", (pattern_hash,))
            existing = cursor.fetchone()

            today = date.today().isoformat()

            if existing:
                new_occurrences = existing['occurrences'] + 1
                new_successes = existing['successes'] + (1 if success else 0)
                new_failures = existing['failures'] + (0 if success else 1)
                new_success_rate = new_successes / new_occurrences

                # Update avg pnl
                old_total = existing['avg_pnl_when_followed'] * existing['occurrences']
                new_avg_pnl = (old_total + pnl) / new_occurrences

                # Determine confidence
                if new_occurrences >= 10:
                    if new_success_rate >= 0.65:
                        confidence = "HIGH"
                    elif new_success_rate >= 0.50:
                        confidence = "MEDIUM"
                    else:
                        confidence = "LOW"
                else:
                    confidence = "INSUFFICIENT_DATA"

                cursor.execute("""
                    UPDATE patterns
                    SET occurrences = ?, successes = ?, failures = ?,
                        success_rate = ?, avg_pnl_when_followed = ?,
                        last_seen = ?, confidence = ?
                    WHERE pattern_hash = ?
                """, (new_occurrences, new_successes, new_failures,
                      new_success_rate, new_avg_pnl, today, confidence, pattern_hash))
            else:
                cursor.execute("""
                    INSERT INTO patterns
                    (pattern_hash, pattern_name, pattern_description, category,
                     occurrences, successes, failures, success_rate,
                     avg_pnl_when_followed, first_seen, last_seen, confidence)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'INSUFFICIENT_DATA')
                """, (pattern_hash, pattern_name, description, category,
                      1 if success else 0, 0 if success else 1,
                      1.0 if success else 0.0, pnl, today, today))

    def get_reliable_patterns(self, min_occurrences: int = 10,
                              min_success_rate: float = 0.55) -> List[Dict]:
        """Get patterns that have proven reliable."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM patterns
                WHERE occurrences >= ? AND success_rate >= ? AND is_active = 1
                ORDER BY success_rate DESC, occurrences DESC
            """, (min_occurrences, min_success_rate))
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # LLM CONTEXT GENERATION
    # =========================================================================

    def generate_llm_context(self, symbols_considering: List[str] = None,
                             include_all_lessons: bool = False) -> str:
        """
        Generate context string for LLM from database.

        Called at pre-market time (e.g., 5:00 AM PST) to load lessons.

        Args:
            symbols_considering: Symbols the agent might trade today
            include_all_lessons: Include all lessons vs just high-confidence

        Returns:
            Formatted string for LLM context injection
        """
        context_parts = []
        context_parts.append("=== LEARNING DATABASE (Permanent Lessons) ===\n")

        # 1. Get high-confidence lessons (structured format preferred)
        min_conf = 0.3 if include_all_lessons else 0.5
        lessons = self.get_lessons(min_confidence=min_conf, limit=20, hold_duration='swing')

        if lessons:
            context_parts.append("PERMANENT LESSONS (Evidence-based rules):")
            for lesson in lessons[:10]:  # Top 10
                conf_str = f"[{lesson['confidence']*100:.0f}%]" if lesson['confidence'] < 1.0 else "[CONFIRMED]"
                symbol_str = f" ({lesson['symbol']})" if lesson['symbol'] else ""

                # Use structured format if available
                if lesson.get('condition') and lesson.get('action'):
                    evidence = f" [{lesson['evidence_count']} trades]" if lesson.get('evidence_count') else ""
                    time_str = ""
                    if lesson.get('valid_time_start') and lesson.get('valid_time_end'):
                        time_str = f" (valid {lesson['valid_time_start']}-{lesson['valid_time_end']})"
                    context_parts.append(f"  {conf_str}{symbol_str}{evidence}{time_str}")
                    context_parts.append(f"    WHEN: {lesson['condition']}")
                    context_parts.append(f"    THEN: {lesson['action']}")
                else:
                    context_parts.append(f"  {conf_str}{symbol_str} {lesson['lesson_text']}")
            context_parts.append("")

        # 1b. Add loss patterns summary
        loss_patterns = self.get_loss_patterns(min_occurrences=3)
        if loss_patterns:
            context_parts.append("LOSS PATTERNS (Avoid these mistakes):")
            for pattern in loss_patterns[:5]:
                if pattern['type'] == 'category':
                    context_parts.append(
                        f"  - {pattern['name']}: {pattern['count']} losses, "
                        f"${abs(pattern['total_loss']):.2f} lost ({pattern['symbols']})"
                    )
            context_parts.append("")

        # 2. Symbol-specific history if considering specific stocks
        if symbols_considering:
            symbol_warnings = []
            symbol_favorable = []

            for symbol in symbols_considering:
                history = self.get_symbol_history(symbol)
                if history:
                    if history['status'] == 'AVOID':
                        symbol_warnings.append(
                            f"  - {symbol}: AVOID - {history['win_rate']*100:.0f}% win rate "
                            f"over {history['total_trades']} trades (${history['total_pnl']:.2f} total)"
                        )
                    elif history['status'] == 'CAUTION':
                        symbol_warnings.append(
                            f"  - {symbol}: CAUTION - {history['win_rate']*100:.0f}% win rate "
                            f"over {history['total_trades']} trades"
                        )
                    elif history['status'] == 'FAVORABLE':
                        symbol_favorable.append(
                            f"  - {symbol}: FAVORABLE - {history['win_rate']*100:.0f}% win rate "
                            f"over {history['total_trades']} trades (${history['total_pnl']:.2f} total)"
                        )

            if symbol_warnings:
                context_parts.append("SYMBOL WARNINGS (from all-time history):")
                context_parts.extend(symbol_warnings)
                context_parts.append("")

            if symbol_favorable:
                context_parts.append("FAVORABLE SYMBOLS (historically profitable):")
                context_parts.extend(symbol_favorable)
                context_parts.append("")

        # 3. General avoid list
        avoid_symbols = self.get_symbols_to_avoid(min_trades=5)
        if avoid_symbols:
            context_parts.append("ALL-TIME AVOID LIST:")
            for sym in avoid_symbols[:5]:
                context_parts.append(
                    f"  - {sym['symbol']}: {sym['win_rate']*100:.0f}% win rate "
                    f"({sym['total_trades']} trades, ${sym['total_pnl']:.2f})"
                )
            context_parts.append("")

        # 4. Recent daily lessons
        recent_summaries = self.get_recent_summaries(days=5)
        if recent_summaries:
            context_parts.append("RECENT DAILY INSIGHTS:")
            for summary in recent_summaries:
                if summary['key_insight']:
                    pnl_str = f"${summary['gross_pnl']:+.2f}" if summary['gross_pnl'] else ""
                    context_parts.append(
                        f"  [{summary['trade_date']}] {pnl_str} - {summary['key_insight']}"
                    )
            context_parts.append("")

        # 5. Reliable patterns
        patterns = self.get_reliable_patterns(min_occurrences=5, min_success_rate=0.55)
        if patterns:
            context_parts.append("RELIABLE PATTERNS:")
            for p in patterns[:5]:
                context_parts.append(
                    f"  - {p['pattern_name']}: {p['success_rate']*100:.0f}% success "
                    f"({p['occurrences']} occurrences, avg ${p['avg_pnl_when_followed']:.2f})"
                )
            context_parts.append("")

        # 6. VALIDATED PATTERNS (from backtest system - Phase 7)
        try:
            validated_patterns = self.get_validated_lessons(
                min_confidence='MEDIUM',
                max_age_days=30
            )
            # Limit to top 10 patterns
            validated_patterns = validated_patterns[:10]

            if validated_patterns:
                context_parts.append("VALIDATED PATTERNS (Backtested strategies):")
                for pattern in validated_patterns:
                    # Format: [CONFIDENCE] Hypothesis (Evidence: N trades, X% WR, Y% avg return)
                    # Use effective_confidence if available (after decay), otherwise original
                    effective_conf = pattern.get('effective_confidence', pattern.get('confidence_level'))
                    conf_label = "HIGH" if effective_conf == 'HIGH' else "MED"
                    hypothesis = pattern.get('hypothesis', 'Pattern')

                    # Evidence metrics
                    sample_size = pattern.get('backtest_sample_size', 0)
                    win_rate = pattern.get('backtest_win_rate', 0) * 100 if pattern.get('backtest_win_rate') else 0
                    avg_return = pattern.get('backtest_avg_return', 0) * 100 if pattern.get('backtest_avg_return') else 0

                    # Format with evidence
                    context_parts.append(f"  [{conf_label}] {hypothesis}")

                    # Build evidence line
                    evidence_parts = [
                        f"{sample_size} trades",
                        f"{win_rate:.0f}% WR",
                        f"{avg_return:+.1f}% avg return"
                    ]

                    # Add regime info if available
                    regime_parts = []
                    if pattern.get('regime_vix_bucket'):
                        regime_parts.append(f"VIX:{pattern['regime_vix_bucket']}")
                    if pattern.get('regime_spy_trend'):
                        regime_parts.append(f"SPY:{pattern['regime_spy_trend']}")

                    if regime_parts:
                        evidence_parts.append(f"regime:[{', '.join(regime_parts)}]")

                    # Add age warning if pattern has decayed
                    if pattern.get('decay_note'):
                        evidence_parts.append(f"({pattern.get('days_old')} days old)")

                    context_parts.append(f"       Evidence: {', '.join(evidence_parts)}")

                    # Optional: Show the pattern conditions for reference
                    if pattern.get('pattern_rule'):
                        import json
                        try:
                            rule = json.loads(pattern['pattern_rule'])
                            conditions = rule.get('conditions', {})
                            if conditions:
                                cond_str = ", ".join(f"{k}={v}" for k, v in list(conditions.items())[:3])
                                if len(conditions) > 3:
                                    cond_str += "..."
                                context_parts.append(f"       Conditions: {cond_str}")
                        except:
                            pass  # Skip conditions if JSON parsing fails

                context_parts.append("")
        except Exception as e:
            logger.warning(f"Could not retrieve validated patterns: {e}")

        context_parts.append("=== END LEARNING DATABASE ===")

        return "\n".join(context_parts)

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get database statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM lessons WHERE is_active = 1")
            active_lessons = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM lessons WHERE condition IS NOT NULL AND is_active = 1")
            structured_lessons = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM symbol_history")
            symbols_tracked = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM daily_summaries")
            days_recorded = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM patterns WHERE is_active = 1")
            active_patterns = cursor.fetchone()[0]

            # Loss analysis stats
            cursor.execute("SELECT COUNT(*), SUM(loss_amount) FROM loss_analysis")
            loss_data = cursor.fetchone()
            losses_analyzed = loss_data[0] or 0
            total_losses = loss_data[1] or 0

            # Trade journal stats
            cursor.execute("SELECT COUNT(*) FROM trade_journal")
            journal_entries = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM trade_journal WHERE trade_date = ?",
                          (date.today().isoformat(),))
            today_entries = cursor.fetchone()[0] or 0

            return {
                "active_lessons": active_lessons,
                "structured_lessons": structured_lessons,
                "symbols_tracked": symbols_tracked,
                "days_recorded": days_recorded,
                "active_patterns": active_patterns,
                "losses_analyzed": losses_analyzed,
                "total_loss_amount": total_losses,
                "journal_entries": journal_entries,
                "today_journal_entries": today_entries,
                "database_path": str(self.db_path)
            }

    def lesson_exists(self, lesson_text: str, category: str, symbol: str = None) -> bool:
        """Check if a lesson already exists (for deduplication)."""
        lesson_hash = self._hash_lesson(lesson_text, category, symbol)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM lessons WHERE lesson_hash = ?", (lesson_hash,))
            return cursor.fetchone() is not None

    # =========================================================================
    # MONTHLY COMPACTION - Prevent indefinite growth
    # =========================================================================

    def compact_month(self, year: int, month: int) -> Dict:
        """
        Compact a month's data into summaries to prevent indefinite growth.

        This:
        1. Creates a monthly summary from daily summaries
        2. Consolidates low-confidence lessons
        3. Removes invalidated lessons (contradicted > validated)
        4. Archives raw daily summaries older than 90 days

        Run this on the 1st of each month for the previous month.

        Args:
            year: Year to compact (e.g., 2026)
            month: Month to compact (1-12)

        Returns:
            Dict with compaction statistics
        """
        from calendar import monthrange

        # Determine date range
        _, last_day = monthrange(year, month)
        start_date = date(year, month, 1)
        end_date = date(year, month, last_day)

        stats = {
            "month": f"{year}-{month:02d}",
            "daily_summaries_compacted": 0,
            "lessons_removed": 0,
            "lessons_promoted": 0,
            "patterns_archived": 0
        }

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Create monthly summary from daily summaries
            cursor.execute("""
                SELECT
                    COUNT(*) as trading_days,
                    SUM(trades_taken) as total_trades,
                    SUM(wins) as total_wins,
                    SUM(losses) as total_losses,
                    SUM(gross_pnl) as total_pnl,
                    AVG(vix_level) as avg_vix,
                    GROUP_CONCAT(DISTINCT symbols_traded) as all_symbols
                FROM daily_summaries
                WHERE trade_date >= ? AND trade_date <= ?
            """, (start_date.isoformat(), end_date.isoformat()))

            month_data = cursor.fetchone()

            if month_data and month_data['trading_days'] and month_data['trading_days'] > 0:
                # Get key insights from the month
                cursor.execute("""
                    SELECT key_insight FROM daily_summaries
                    WHERE trade_date >= ? AND trade_date <= ?
                    AND key_insight IS NOT NULL AND key_insight != ''
                """, (start_date.isoformat(), end_date.isoformat()))

                insights = [row['key_insight'] for row in cursor.fetchall()]

                # Create a monthly lesson summarizing the month
                win_rate = month_data['total_wins'] / month_data['total_trades'] if month_data['total_trades'] else 0
                month_summary = (
                    f"{year}-{month:02d} Summary: {month_data['trading_days']} days, "
                    f"{month_data['total_trades']} trades, {win_rate*100:.0f}% win rate, "
                    f"${month_data['total_pnl']:.2f} P/L"
                )

                # Add as a permanent lesson
                self.add_lesson(
                    month_summary,
                    category="monthly_summary",
                    confidence=0.9,
                    source="compaction",
                    notes="; ".join(insights[:5]) if insights else None
                )

                stats["daily_summaries_compacted"] = month_data['trading_days']

            # 2. Remove invalidated lessons (contradicted much more than validated)
            cursor.execute("""
                DELETE FROM lessons
                WHERE times_contradicted > times_validated + 3
                AND confidence < 0.3
            """)
            stats["lessons_removed"] = cursor.rowcount

            # 3. Promote highly validated lessons to higher confidence
            cursor.execute("""
                UPDATE lessons
                SET confidence = MIN(1.0, confidence + 0.1)
                WHERE times_validated >= 5 AND times_contradicted = 0
                AND confidence < 1.0
            """)
            stats["lessons_promoted"] = cursor.rowcount

            # 4. Archive old patterns that haven't been seen recently
            three_months_ago = (date.today() - timedelta(days=90)).isoformat()
            cursor.execute("""
                UPDATE patterns
                SET is_active = 0
                WHERE last_seen < ? AND occurrences < 5
            """, (three_months_ago,))
            stats["patterns_archived"] = cursor.rowcount

            # 5. Keep only last 90 days of detailed daily summaries
            # (monthly summaries are preserved as lessons)
            ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()
            cursor.execute("""
                DELETE FROM daily_summaries
                WHERE trade_date < ?
            """, (ninety_days_ago,))

            logger.info(f"Compacted month {year}-{month:02d}: {stats}")

        return stats

    def run_monthly_maintenance(self) -> Dict:
        """
        Run monthly maintenance on the first of each month.

        Compacts the previous month's data.

        Returns:
            Compaction statistics
        """
        today = date.today()

        # Only run on 1st of month (or manually)
        if today.day != 1:
            logger.info("Monthly maintenance only runs on 1st of month")
            return {"status": "skipped", "reason": "not_first_of_month"}

        # Compact previous month
        if today.month == 1:
            prev_year = today.year - 1
            prev_month = 12
        else:
            prev_year = today.year
            prev_month = today.month - 1

        return self.compact_month(prev_year, prev_month)

    def archive_lesson_as_bad(
        self,
        lesson_id: int,
        disproven_reason: str,
        validation_stats: dict = None
    ) -> bool:
        """
        Move a lesson from 'lessons' to 'bad_lessons' archive.

        IMPORTANT: Only Claude should call this method after explicit analysis.
        Never call from automated scripts or the scheduler.

        Args:
            lesson_id: ID of the lesson in the lessons table
            disproven_reason: Human-readable explanation of why it was disproven
            validation_stats: Optional dict with P&L deltas, win rates, run counts, etc.

        Returns:
            True if successfully archived, False if lesson not found
        """
        import json
        from datetime import datetime

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Fetch the lesson first
            cursor.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
            row = cursor.fetchone()
            if not row:
                logger.warning(f"archive_lesson_as_bad: lesson id {lesson_id} not found")
                return False

            col_names = [d[0] for d in cursor.description]
            lesson = dict(zip(col_names, row))

            now = datetime.now().isoformat()

            cursor.execute("""
                INSERT INTO bad_lessons (
                    original_lesson_id, lesson_hash, lesson_text, category, symbol,
                    confidence, times_validated, times_contradicted, original_created_at,
                    archived_at, archived_by, disproven_reason, validation_stats,
                    condition, action, evidence_summary,
                    backtest_win_rate, backtest_avg_return, backtest_sample_size,
                    regime_context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lesson['id'],
                lesson.get('lesson_hash'),
                lesson['lesson_text'],
                lesson['category'],
                lesson.get('symbol'),
                lesson.get('confidence'),
                lesson.get('times_validated', 0),
                lesson.get('times_contradicted', 0),
                lesson.get('created_at'),
                now,
                'claude',
                disproven_reason,
                json.dumps(validation_stats) if validation_stats else None,
                lesson.get('condition'),
                lesson.get('action'),
                lesson.get('evidence_summary'),
                lesson.get('backtest_win_rate'),
                lesson.get('backtest_avg_return'),
                lesson.get('backtest_sample_size'),
                lesson.get('regime_vix_bucket')
            ))

            # Deactivate in lessons table (do not delete - keeps hash for dedup)
            cursor.execute(
                "UPDATE lessons SET is_active = 0, notes = ? WHERE id = ?",
                (f"ARCHIVED TO BAD_LESSONS: {disproven_reason[:100]}", lesson_id)
            )

            conn.commit()
            logger.info(
                f"Lesson {lesson_id} archived to bad_lessons: {lesson['lesson_text'][:60]}..."
            )
            return True

    def get_bad_lessons(self, category: str = None, limit: int = 100) -> List[Dict]:
        """
        Retrieve archived bad lessons for review or future reference.

        Args:
            category: Optional filter by category
            limit: Max rows to return

        Returns:
            List of bad lesson dicts
        """
        import json

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if category:
                cursor.execute(
                    "SELECT * FROM bad_lessons WHERE category = ? ORDER BY archived_at DESC LIMIT ?",
                    (category, limit)
                )
            else:
                cursor.execute(
                    "SELECT * FROM bad_lessons ORDER BY archived_at DESC LIMIT ?",
                    (limit,)
                )
            rows = cursor.fetchall()
            if not rows:
                return []
            col_names = [d[0] for d in cursor.description]
            results = []
            for row in rows:
                d = dict(zip(col_names, row))
                if d.get('validation_stats') and isinstance(d['validation_stats'], str):
                    try:
                        d['validation_stats'] = json.loads(d['validation_stats'])
                    except Exception:
                        pass
                results.append(d)
            return results

    def get_monthly_summaries(self) -> List[Dict]:
        """Get all monthly summary lessons."""
        return self.get_lessons(category="monthly_summary", limit=24)  # 2 years

    # =========================================================================
    # LESSON OUTCOME TRACKING - drives promotion to live and deactivation
    # =========================================================================

    def record_lesson_outcome(
        self,
        lesson_id: int,
        trade_date: str,
        symbol: str,
        pnl_pct: float,
        pnl_dollars: float,
        adjustment: str = None,
        text_excerpt: str = None,
        is_resim: bool = True
    ) -> bool:
        """
        Record a single trade outcome for a lesson that influenced the decision.

        Called after each resimulation pass (is_resim=True) and after each live
        trade exit (is_resim=False). Accumulates into resim_wins/losses or
        live_wins/losses on the lesson row for quick summary queries.

        Args:
            lesson_id: DB id of the lesson (from lessons_used[].id)
            trade_date: ISO date string of the trade
            symbol: Ticker that was traded
            pnl_pct: Percentage P&L of the trade
            pnl_dollars: Dollar P&L of the trade
            adjustment: Conviction adjustment Grok applied (e.g. '+0.5')
            text_excerpt: First 80 chars of lesson text (for human readability)
            is_resim: True if from resimulation, False if from live trading

        Returns:
            True on success, False if lesson_id not found
        """
        from datetime import datetime as _dt

        won = 1 if (pnl_pct or 0) > 0 else 0
        now = _dt.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Verify lesson exists
            cursor.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,))
            if not cursor.fetchone():
                logger.warning(f"record_lesson_outcome: lesson {lesson_id} not found")
                return False

            # Insert outcome record
            cursor.execute("""
                INSERT INTO lesson_outcomes
                    (lesson_id, trade_date, symbol, pnl_pct, pnl_dollars,
                     won, adjustment, text_excerpt, is_resim, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lesson_id, trade_date, symbol,
                pnl_pct, pnl_dollars, won,
                adjustment, text_excerpt,
                1 if is_resim else 0,
                now
            ))

            # Update cumulative counters on the lesson row
            if is_resim:
                if won:
                    conn.execute(
                        "UPDATE lessons SET resim_wins = resim_wins + 1, last_outcome_at = ? WHERE id = ?",
                        (now, lesson_id)
                    )
                else:
                    conn.execute(
                        "UPDATE lessons SET resim_losses = resim_losses + 1, last_outcome_at = ? WHERE id = ?",
                        (now, lesson_id)
                    )
            else:
                if won:
                    conn.execute(
                        "UPDATE lessons SET live_wins = live_wins + 1, last_outcome_at = ? WHERE id = ?",
                        (now, lesson_id)
                    )
                else:
                    conn.execute(
                        "UPDATE lessons SET live_losses = live_losses + 1, last_outcome_at = ? WHERE id = ?",
                        (now, lesson_id)
                    )

            conn.commit()
            logger.debug(
                f"Lesson {lesson_id} outcome recorded: {'WIN' if won else 'LOSS'} "
                f"{pnl_pct:+.1f}% on {symbol} ({'resim' if is_resim else 'live'})"
            )
            return True

    def get_lesson_performance_summary(
        self,
        min_resim_trades: int = 5,
        promote_win_rate: float = 0.60,
        deactivate_win_rate: float = 0.35,
        deactivate_min_trades: int = 10
    ) -> Dict:
        """
        Summarize lesson performance from recorded outcomes.

        Returns three lists:
        - promote: lessons with enough resim wins to go live (live_eligible=0 currently)
        - deactivate: lessons performing poorly across resim + live
        - healthy: active lessons with solid win rates

        Promotion threshold: >= min_resim_trades AND resim win rate >= promote_win_rate
        Deactivation threshold: >= deactivate_min_trades AND win rate < deactivate_win_rate

        Args:
            min_resim_trades: Minimum resim trades before considering promotion
            promote_win_rate: Resim win rate needed to go live (default 60%)
            deactivate_win_rate: Win rate below which lesson is flagged (default 35%)
            deactivate_min_trades: Min total trades before deactivation is considered

        Returns:
            {'promote': [...], 'deactivate': [...], 'healthy': [...], 'insufficient_data': [...]}
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    id, lesson_text, category, confidence, is_active, live_eligible,
                    resim_wins, resim_losses, live_wins, live_losses,
                    last_outcome_at, created_at, source
                FROM lessons
                WHERE is_active = 1
                ORDER BY (resim_wins + resim_losses + live_wins + live_losses) DESC
            """)
            rows = cursor.fetchall()

        promote = []
        deactivate = []
        healthy = []
        insufficient = []

        for row in rows:
            r = dict(row)
            resim_total = (r['resim_wins'] or 0) + (r['resim_losses'] or 0)
            live_total = (r['live_wins'] or 0) + (r['live_losses'] or 0)
            total = resim_total + live_total

            resim_wr = (r['resim_wins'] or 0) / resim_total if resim_total > 0 else None
            live_wr = (r['live_wins'] or 0) / live_total if live_total > 0 else None
            # Combined win rate weights live 2x (real money matters more)
            if total > 0:
                combined_wr = (
                    (r['resim_wins'] or 0) + (r['live_wins'] or 0) * 2
                ) / (resim_total + live_total * 2)
            else:
                combined_wr = None

            summary = {
                'id': r['id'],
                'text': r['lesson_text'][:80],
                'category': r['category'],
                'live_eligible': r['live_eligible'],
                'resim_wins': r['resim_wins'] or 0,
                'resim_losses': r['resim_losses'] or 0,
                'resim_win_rate': round(resim_wr * 100, 1) if resim_wr is not None else None,
                'live_wins': r['live_wins'] or 0,
                'live_losses': r['live_losses'] or 0,
                'live_win_rate': round(live_wr * 100, 1) if live_wr is not None else None,
                'combined_win_rate': round(combined_wr * 100, 1) if combined_wr is not None else None,
                'total_trades': total,
                'last_outcome_at': r['last_outcome_at'],
            }

            if total == 0 or resim_total < min_resim_trades:
                insufficient.append(summary)
            elif (not r['live_eligible']) and resim_wr is not None and resim_wr >= promote_win_rate:
                promote.append(summary)
            elif combined_wr is not None and combined_wr < deactivate_win_rate and total >= deactivate_min_trades:
                deactivate.append(summary)
            else:
                healthy.append(summary)

        return {
            'promote': promote,
            'deactivate': deactivate,
            'healthy': healthy,
            'insufficient_data': insufficient,
        }

    def promote_lesson_to_live(self, lesson_id: int) -> bool:
        """
        Mark a lesson as live_eligible=1 so it gets injected into the live trading prompt.

        Only called by the weekly review job after performance thresholds are met.
        """
        from datetime import datetime as _dt
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE lessons SET live_eligible = 1, promoted_at = ? WHERE id = ?",
                (_dt.now().isoformat(), lesson_id)
            )
            conn.commit()
        logger.info(f"Lesson {lesson_id} promoted to live_eligible")
        return True

    def increment_survival_count(self, lesson_id: int) -> bool:
        """
        Increment survival_count for a lesson that survived a pruning iteration.
        Called by run_lesson_pruning_cycle after each window pass.
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE lessons SET survival_count = survival_count + 1 WHERE id = ?",
                (lesson_id,)
            )
            conn.commit()
        return True

    def promote_lessons_to_production(self, min_survival_count: int = 16) -> List[int]:
        """
        Promote lessons that have survived enough pruning iterations.
        Called by the 3AM pruning scheduler after multi-window validation.

        Args:
            min_survival_count: Minimum iterations a lesson must survive (default 16)

        Returns:
            List of lesson IDs promoted to production (pruning_mode=0, live_eligible=1)
        """
        from datetime import datetime as _dt
        promoted_ids = []
        now = _dt.now().isoformat()

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Find lessons in testing pool that have survived enough iterations
            cursor.execute("""
                SELECT id, lesson_text, survival_count
                FROM lessons
                WHERE pruning_mode = 1
                  AND is_active = 1
                  AND survival_count >= ?
            """, (min_survival_count,))

            rows = cursor.fetchall()

            for row in rows:
                lesson_id = row['id']
                # Promote: remove from testing pool, mark as live_eligible
                conn.execute("""
                    UPDATE lessons
                    SET pruning_mode = 0,
                        live_eligible = 1,
                        promoted_at = ?
                    WHERE id = ?
                """, (now, lesson_id))
                promoted_ids.append(lesson_id)
                logger.info(
                    f"[PRUNING] Promoted lesson {lesson_id} to production "
                    f"(survival_count={row['survival_count']}): "
                    f"{row['lesson_text'][:80]}"
                )

            conn.commit()

        logger.info(f"[PRUNING] {len(promoted_ids)} lessons promoted to production")
        return promoted_ids

    def get_pruning_pool_count(self) -> int:
        """Return count of lessons currently in the testing/pruning pool."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM lessons WHERE pruning_mode = 1 AND is_active = 1"
            )
            return cursor.fetchone()[0]

    def get_live_eligible_lessons(self, as_of_date=None) -> List[Dict]:
        """
        Return active, live-eligible swing lessons for injection into the live trading prompt.
        These have proven themselves in resimulation and are ready for real money.

        Args:
            as_of_date: Optional date to filter lessons created on or before this date.
                        Used by backtest harness to avoid look-ahead bias.
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            date_filter = ""
            if as_of_date is not None:
                date_str = as_of_date.isoformat() if hasattr(as_of_date, 'isoformat') else str(as_of_date)
                date_filter = f" AND created_at <= '{date_str}'"
            cursor.execute(f"""
                SELECT id, lesson_text, category, condition, action,
                       confidence, resim_wins, resim_losses, live_wins, live_losses,
                       applicable_sectors
                FROM lessons
                WHERE is_active = 1 AND live_eligible = 1 AND hold_duration = 'swing'
                {date_filter}
                ORDER BY confidence DESC
                LIMIT 20
            """)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    # =========================================================================
    # FAILED HYPOTHESES CRUD
    # =========================================================================

    def record_failed_hypothesis(self, hypothesis: dict, failure_reason: str) -> bool:
        """
        Record a hypothesis that failed pruning validation.

        Args:
            hypothesis: Full hypothesis dict with keys ID, Type, Lesson_ID, Title,
                        Confidence, Rationale, Proposed_Change, Expected_Impact
            failure_reason: Human-readable reason e.g. "WR=61.2% over 6 iterations (need 67%)"

        Returns:
            True if inserted, False on error.
        """
        try:
            lesson_id = hypothesis.get("Lesson_ID") or hypothesis.get("lesson_id")
            if lesson_id and str(lesson_id).startswith("L"):
                lesson_id = int(lesson_id[1:])
            elif lesson_id:
                try:
                    lesson_id = int(lesson_id)
                except (TypeError, ValueError):
                    lesson_id = None

            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO failed_hypotheses
                        (hypothesis_id, lesson_id, hyp_type, title, rationale,
                         proposed_change, expected_impact, original_confidence,
                         failure_reason, failed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    hypothesis.get("ID") or hypothesis.get("id", "UNKNOWN"),
                    lesson_id,
                    hypothesis.get("Type") or hypothesis.get("type"),
                    hypothesis.get("Title") or hypothesis.get("title"),
                    hypothesis.get("Rationale") or hypothesis.get("rationale"),
                    hypothesis.get("Proposed_Change") or hypothesis.get("proposed_change"),
                    hypothesis.get("Expected_Impact") or hypothesis.get("expected_impact"),
                    hypothesis.get("Confidence") or hypothesis.get("confidence"),
                    failure_reason,
                    datetime.now().isoformat(),
                ))
            return True
        except Exception as exc:
            logger.warning(f"[failed_hypotheses] record failed: {exc}")
            return False

    def get_unpresented_rejections(self, limit: int = 25) -> list:
        """
        Return up to `limit` most recent failed hypotheses not yet sent to Grok.

        Returns list of dicts with all hypothesis fields.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT * FROM failed_hypotheses
                    WHERE presented_to_grok = 0
                    ORDER BY failed_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(f"[failed_hypotheses] get_unpresented failed: {exc}")
            return []

    def get_top_lessons_by_wr(self, limit: int = 3) -> list:
        """
        Return top `limit` live_eligible lessons by resim win rate for prompt calibration.

        Returns list of dicts with id, lesson_text, resim_wins, resim_losses, wr.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT id, lesson_text, resim_wins, resim_losses,
                           CAST(resim_wins AS REAL) / MAX(resim_wins + resim_losses, 1) AS wr
                    FROM lessons
                    WHERE live_eligible = 1
                      AND resim_wins + resim_losses > 0
                    ORDER BY wr DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(f"[get_top_lessons_by_wr] get_top_lessons failed: {exc}")
            return []

    def mark_rejections_presented(self, row_ids: list) -> None:
        """
        Mark failed hypothesis rows as presented to Grok so they are not re-sent.

        Args:
            row_ids: List of integer primary key IDs from failed_hypotheses table.
        """
        if not row_ids:
            return
        try:
            placeholders = ",".join("?" * len(row_ids))
            with self._get_connection() as conn:
                conn.execute(f"""
                    UPDATE failed_hypotheses
                    SET presented_to_grok = 1, presented_at = ?
                    WHERE id IN ({placeholders})
                """, [datetime.now().isoformat()] + list(row_ids))
        except Exception as exc:
            logger.warning(f"[failed_hypotheses] mark_presented failed: {exc}")

    # =========================================================================
    # BACKTEST TRADE JOURNAL
    # =========================================================================

    def write_backtest_trade(
        self,
        run_id: str,
        trade_id: str,
        fork: str,
        hypothesis_id,
        symbol: str,
        entry_date: str,
        exit_date,
        entry_price,
        exit_price,
        shares,
        shares_remaining,
        pnl_dollars,
        pnl_pct,
        exit_type,
        hold_days,
        entry_catalyst,
        conviction_score,
        regime,
        hold_type=None,
        candidate_lane=None,
        next_earnings_date=None,
    ) -> int:
        """Write a single trade row to backtest_trade_journal.

        NEVER writes to live trade_journal - backtest isolation is absolute.

        Args:
            run_id: UUID4 grouping all trades from one backtest run.
            trade_id: UUID4 linking this trade to any lesson generated from it.
            fork: Source repo ('swing' or 'day_trader').
            hypothesis_id: None for baseline runs; e.g. 'EMA_EXIT_TIGHTER_v1'.
            symbol: Ticker symbol.
            entry_date: ISO date string (YYYY-MM-DD).
            exit_date: ISO date string or None if still open.
            entry_price: Fill price at entry.
            exit_price: Fill price at exit, or None.
            shares: Total shares entered.
            shares_remaining: After partial exits.
            pnl_dollars: Realized P&L in dollars, or None.
            pnl_pct: Realized P&L as fraction (0.05 = 5%), or None.
            exit_type: STOP_HIT / BELOW_21EMA / PROFIT_TARGET / PARTIAL / MANUAL / EOD.
            hold_days: Calendar days held, or None.
            entry_catalyst: Catalyst description at entry, or None.
            hold_type: swing / pead / other hold-mode at entry, or None.
            candidate_lane: FORCESWING / STAGE2_LEADER / PEAD at entry, or None.
            next_earnings_date: ISO date for upcoming earnings known at entry, or None.
            conviction_score: Agent conviction 0-10 at entry, or None.
            regime: FULL / REDUCED / CASH at entry, or None.

        Returns:
            Row id of the inserted record.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO backtest_trade_journal (
                        run_id, fork, hypothesis_id, trade_id,
                        symbol, entry_date, exit_date,
                        entry_price, exit_price,
                        shares, shares_remaining,
                    pnl_dollars, pnl_pct,
                    exit_type, hold_days,
                    entry_catalyst, hold_type, candidate_lane, next_earnings_date,
                    conviction_score, regime
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    run_id, fork, hypothesis_id, trade_id,
                    symbol, entry_date, exit_date,
                    entry_price, exit_price,
                    shares, shares_remaining,
                    pnl_dollars, pnl_pct,
                    exit_type, hold_days,
                    entry_catalyst, hold_type, candidate_lane, next_earnings_date,
                    conviction_score, regime,
                ))
                return cursor.lastrowid
        except Exception as e:
            logger.warning(f"write_backtest_trade failed for {symbol} run={run_id}: {e}")
            return -1

    def get_backtest_trades(self, run_id: str) -> list:
        """Return all trades for a given run_id from backtest_trade_journal.

        Returns list of dicts. Never touches live trade_journal.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM backtest_trade_journal
                    WHERE run_id = ?
                    ORDER BY entry_date ASC, id ASC
                """, (run_id,))
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning(f"get_backtest_trades failed for run_id={run_id}: {e}")
            return []

    def get_backtest_run_ids(self, fork: str = None) -> list:
        """Return all distinct run_ids in backtest_trade_journal, newest first.

        Args:
            fork: Optional filter by fork ('swing', 'day_trader'). None returns all.

        Returns list of dicts with keys: run_id, fork, hypothesis_id, trade_count,
        first_entry_date, last_entry_date.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if fork:
                    cursor.execute("""
                        SELECT run_id, fork, hypothesis_id,
                               COUNT(*) as trade_count,
                               MIN(entry_date) as first_entry_date,
                               MAX(entry_date) as last_entry_date
                        FROM backtest_trade_journal
                        WHERE fork = ?
                        GROUP BY run_id
                        ORDER BY MAX(created_at) DESC
                    """, (fork,))
                else:
                    cursor.execute("""
                        SELECT run_id, fork, hypothesis_id,
                               COUNT(*) as trade_count,
                               MIN(entry_date) as first_entry_date,
                               MAX(entry_date) as last_entry_date
                        FROM backtest_trade_journal
                        GROUP BY run_id
                        ORDER BY MAX(created_at) DESC
                    """)
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning(f"get_backtest_run_ids failed: {e}")
            return []

    def get_swing_trades(self, days_lookback: int = 120) -> list:
        """
        Return swing trades from trade_journal for SwingBacktestEngine analysis.
        Filters to hold_duration='swing' trades within the lookback window.
        Returns list of dicts with trade fields.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cutoff = (datetime.now() - timedelta(days=days_lookback)).isoformat()
                cursor.execute("""
                    SELECT symbol, entry_price, exit_price, pnl_pct_net,
                           hold_days, exit_reason, entry_catalyst, hold_type,
                           vix_mode, trade_date
                    FROM trade_journal
                    WHERE hold_duration = 'swing'
                      AND trade_date >= ?
                    ORDER BY trade_date DESC
                """, (cutoff,))
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning(f"get_swing_trades: {e}")
            return []


    # ---------------------------------------------------------------------------
    # LIVE CGH PIPELINE - hypothesis_verdicts and live_cgh_state I/O
    # ---------------------------------------------------------------------------

    def write_hypothesis_verdict(
        self,
        run_date: str,
        trigger_trade_count: int,
        window_start: str,
        window_end: str,
        hypothesis_id: str,
        hypothesis_type: str,
        confidence: int,
        backtest_win_rate: float,
        backtest_sample_size: int,
        baseline_win_rate: float,
        verdict: str,
        title: str = None,
        proposed_change: str = None,
        notes: str = None,
    ) -> int:
        """Insert a hypothesis verdict record. Returns rowid or None on error."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO hypothesis_verdicts
                        (run_date, trigger_trade_count, window_start, window_end,
                         hypothesis_id, hypothesis_type, title, proposed_change,
                         confidence, backtest_win_rate, backtest_sample_size,
                         baseline_win_rate, verdict, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_date, trigger_trade_count, window_start, window_end,
                    hypothesis_id, hypothesis_type, title, proposed_change,
                    confidence, backtest_win_rate, backtest_sample_size,
                    baseline_win_rate, verdict, notes,
                ))
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"write_hypothesis_verdict failed: {e}")
            return None

    def get_hypothesis_verdicts(
        self, verdict_filter: str = None, limit: int = 50
    ) -> list:
        """Return hypothesis verdict rows, optionally filtered by verdict (PASS/FAIL/INSUFFICIENT)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if verdict_filter:
                    cursor.execute(
                        "SELECT * FROM hypothesis_verdicts WHERE verdict = ? ORDER BY run_date DESC LIMIT ?",
                        (verdict_filter, limit),
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM hypothesis_verdicts ORDER BY run_date DESC LIMIT ?",
                        (limit,),
                    )
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"get_hypothesis_verdicts failed: {e}")
            return []

    def get_live_cgh_state(self) -> dict:
        """Return live CGH pipeline state (trigger count, run history)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM live_cgh_state WHERE id = 1")
                row = cursor.fetchone()
                if not row:
                    return {
                        "last_trigger_count": 0,
                        "first_run_completed": 0,
                        "last_run_date": None,
                        "updated_at": None,
                    }
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row))
        except Exception as e:
            logger.warning(f"get_live_cgh_state failed: {e}")
            return {
                "last_trigger_count": 0,
                "first_run_completed": 0,
                "last_run_date": None,
                "updated_at": None,
            }

    def update_live_cgh_state(
        self,
        last_trigger_count: int,
        first_run_completed: int,
        last_run_date: str = None,
    ) -> None:
        """Update live CGH pipeline state after a run."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                run_date = last_run_date or date.today().isoformat()
                cursor.execute("""
                    UPDATE live_cgh_state
                    SET last_trigger_count = ?,
                        first_run_completed = ?,
                        last_run_date = ?,
                        updated_at = ?
                    WHERE id = 1
                """, (last_trigger_count, first_run_completed, run_date, datetime.now().isoformat()))
                if cursor.rowcount == 0:
                    logger.warning("[live_cgh_state] UPDATE matched 0 rows - id=1 row is missing")
        except Exception as e:
            logger.error(f"update_live_cgh_state failed: {e}")


# Convenience function for quick context generation
def get_learning_database_context(symbols: List[str] = None) -> str:
    """
    Quick function to get LLM context from learning database.

    Usage:
        context = get_learning_database_context(["NVDA", "TSLA"])
        prompt = f"{context}\n\nAnalyze these stocks..."
    """
    db = LearningDatabase()
    return db.generate_llm_context(symbols_considering=symbols)
