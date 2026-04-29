"""
EOD Report Builder - Production pipeline for hypothesis generation

Reads production DB data at market close and builds daily EOD reports
in the format expected by WeeklySummaryGenerator + GrokHypothesisGenerator.

Data sources:
  - trading_performance.db/trades         -> completed trade results (actual fill prices)
  - learning.db/trade_journal             -> catalyst, why_entered, lessons_applied
  - trading_performance.db/daily_candidate_snapshot -> skipped stocks (opening_price at scan time)
  - trading_performance.db/daily_market_conditions  -> VIX, regime
  - yfinance                              -> OHLC for all symbols (day_high, day_low, open, close)

Called from: automated_scheduler.market_close_routine() after lesson generation
"""

import sqlite3
import json
import re
import sys
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# Default DB paths (absolute, derived from this file's location)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERF_DB = _ROOT / "ai_trader/ai_trader_data/trading_performance.db"
_LEARN_DB = _ROOT / "ai_trader/ai_trader_data/learning.db"
_REPORTS_DIR = _ROOT / "reports/eod"


def _project_root() -> Path:
    """Return the project root (4 levels up from this file in ai_trader/trading_agent/analytics/)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _ensure_imports():
    """Add project root to sys.path so cross-package imports work."""
    root = str(_project_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def build_eod_report(
    trade_date: date,
    perf_db: Optional[Path] = None,
    learn_db: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Build a daily EOD report from production DB data.

    This is the production counterpart to create_test_eod_reports.py.
    Called from market_close_routine() after lesson generation.

    Returns:
        Path to saved JSON report file, or None if no data / error.
    """
    perf_db = perf_db or _PERF_DB
    learn_db = learn_db or _LEARN_DB
    output_dir = output_dir or _REPORTS_DIR

    if not perf_db.exists():
        logger.error(f"EOD report builder: trading DB not found: {perf_db}")
        return None

    date_str = trade_date.isoformat()
    logger.info(f"EOD report builder: building report for {date_str}")

    # --- 1. Market regime from daily_market_conditions ---
    regime, regime_vix = _get_market_regime(perf_db, trade_date)
    logger.info(f"  Regime: {regime} (VIX={regime_vix})")

    # --- 2. Completed trades ---
    trades = _get_completed_trades(perf_db, trade_date)
    logger.info(f"  Trades: {len(trades)}")

    # --- 3. Trade journal context (catalyst, why_entered, lessons_applied) ---
    journal = _get_journal_context(learn_db, trade_date)
    logger.info(f"  Journal entries: {len(journal)}")

    # --- 4. Skipped candidates from daily_candidate_snapshot ---
    skipped = _get_skipped_candidates(perf_db, trade_date)
    logger.info(f"  Skipped candidates: {len(skipped)}")

    if not trades and not skipped:
        logger.info("  No data to report - skipping EOD report creation")
        return None

    # --- 5. Fetch OHLC for all symbols via yfinance ---
    all_symbols = list(set(
        [t['symbol'] for t in trades] + [s['symbol'] for s in skipped]
    ))
    ohlc = _fetch_ohlc_yfinance(all_symbols, trade_date)

    # --- 6. Build EODReportGenerator ---
    _ensure_imports()
    from ai_trader.analytics.eod_report_generator import EODReportGenerator

    output_dir.mkdir(parents=True, exist_ok=True)
    gen = EODReportGenerator(output_dir=str(output_dir))
    gen.initialize(date_str, regime, vix=regime_vix)

    # --- 7. Add each trade ---
    for trade in trades:
        sym = trade['symbol']
        ctx = journal.get(sym, {})
        bars = ohlc.get(sym, {})

        # Lesson IDs: use stored list, else parse from why_entered text
        lessons_raw = ctx.get('lessons_applied')
        if lessons_raw and isinstance(lessons_raw, str):
            try:
                lessons = json.loads(lessons_raw)
            except (json.JSONDecodeError, ValueError):
                lessons = [int(x) for x in re.findall(r'L?(\d+)', lessons_raw)]
        elif lessons_raw and isinstance(lessons_raw, list):
            lessons = lessons_raw
        else:
            why = ctx.get('why_entered', '') or trade.get('strategy', '')
            lessons = [int(x) for x in re.findall(r'L(\d+)', why)]

        pnl = trade.get('realized_pnl') or 0.0
        result = 'WIN' if pnl > 0 else 'LOSS'
        conviction = float(ctx.get('confidence_level') or 7.0)

        gen.add_trade_result(
            symbol=sym,
            conviction=conviction,
            entry_price=trade['entry_price'],
            exit_price=trade['exit_price'],
            pnl_dollars=pnl,
            result=result,
            lessons_applied=lessons,
            catalyst=ctx.get('catalyst') or 'premarket_gap',
            gap=bars.get('gap_pct', 0.0),
            day_high=bars.get('high'),
            day_low=bars.get('low'),
            day_open=bars.get('open'),
            day_close=bars.get('close'),
            exit_reason=trade.get('exit_reason') or 'unknown',
        )

    # --- 8. Add each skipped candidate ---
    for cand in skipped:
        sym = cand['symbol']
        bars = ohlc.get(sym, {})
        oc_move = bars.get('oc_move_pct')
        actual_move = f"{oc_move:+.1f}%" if oc_move is not None else None

        # Parse lessons_applied JSON safely
        raw_lessons = cand.get('lessons_applied') or '[]'
        try:
            parsed_lessons = json.loads(raw_lessons) if isinstance(raw_lessons, str) else (raw_lessons or [])
            parsed_lessons = [int(x) for x in parsed_lessons if isinstance(x, (int, float))]
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning(f"EOD: Could not parse lessons_applied for {sym}: {raw_lessons!r}")
            parsed_lessons = []

        gen.add_skipped_opportunity(
            symbol=sym,
            conviction=float(cand.get('conviction_score') or 5.0),
            catalyst=cand.get('catalyst_headline') or cand.get('source', 'gap_scan'),
            gap=cand.get('gap_pct') or 0.0,
            reason=cand.get('rejection_reason') or 'Agent passed',
            actual_move=actual_move,
            consideration_price=cand.get('opening_price'),   # price at 7AM PST scan time
            day_open=bars.get('open'),
            day_high=bars.get('high'),
            day_low=bars.get('low'),
            day_close=bars.get('close'),
            oc_move_pct=oc_move,
            oh_move_pct=bars.get('oh_move_pct'),
            strategy=cand.get('strategy'),
            lessons_would_have_applied=parsed_lessons or None,
        )

    # Log lessons pipeline health
    with_lessons = sum(1 for c in skipped if c.get('lessons_applied') and c['lessons_applied'] not in ('[]', None, ''))
    logger.info(
        f"EOD: {with_lessons}/{len(skipped)} skipped candidates had lessons_applied data"
    )

    # --- 9. Save report ---
    # EODReportGenerator.save() uses self.output_dir internally, returns (json_path, txt_path)
    json_path, txt_path = gen.save()

    logger.info(
        f"EOD report saved: {json_path} "
        f"({len(trades)} trades, {len(skipped)} skipped)"
    )
    return str(json_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_market_regime(perf_db: Path, trade_date: date) -> Tuple[str, float]:
    """
    Read market regime from daily_market_conditions.
    Returns: (regime_str, vix_float) - falls back to ('NEUTRAL', 0.0) on error.
    """
    try:
        conn = sqlite3.connect(perf_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            "SELECT market_sentiment, vix_open FROM daily_market_conditions WHERE trade_date = ?",
            (trade_date.isoformat(),)
        )
        row = c.fetchone()
        conn.close()
        if row:
            sentiment = row['market_sentiment'] or ''
            vix = float(row['vix_open'] or 0)
            # Map to NEUTRAL/BEAR/BULL based on VIX and sentiment
            if 'bear' in sentiment.lower() or vix > 25:
                return 'BEAR', vix
            elif 'bull' in sentiment.lower() or vix < 15:
                return 'BULL', vix
            else:
                return 'NEUTRAL', vix
    except Exception as e:
        logger.warning(f"Could not read market regime: {e}")
    return 'NEUTRAL', 0.0


def _get_completed_trades(perf_db: Path, trade_date: date) -> List[Dict]:
    """Read completed trades from trading_performance.db for the given date."""
    results = []
    try:
        conn = sqlite3.connect(perf_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT symbol, entry_price, exit_price, quantity,
                   realized_pnl, realized_pnl_percent,
                   exit_reason, strategy, entry_time, exit_time
            FROM trades
            WHERE DATE(exit_time) = ?
            ORDER BY exit_time
        """, (trade_date.isoformat(),))
        for row in c.fetchall():
            results.append(dict(row))
        conn.close()
    except Exception as e:
        logger.warning(f"Could not read completed trades: {e}")
    return results


def _get_journal_context(learn_db: Path, trade_date: date) -> Dict[str, Dict]:
    """
    Read trade journal entries keyed by symbol.
    Returns: {symbol: {why_entered, catalyst, lessons_applied, confidence_level}}
    """
    journal = {}
    if not learn_db.exists():
        return journal
    try:
        conn = sqlite3.connect(learn_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # lessons_applied column may not exist on older DBs
        try:
            c.execute("""
                SELECT symbol, why_entered, catalyst, confidence_level, lessons_applied
                FROM trade_journal
                WHERE trade_date = ?
            """, (trade_date.isoformat(),))
        except sqlite3.OperationalError:
            c.execute("""
                SELECT symbol, why_entered, catalyst, confidence_level
                FROM trade_journal
                WHERE trade_date = ?
            """, (trade_date.isoformat(),))
        for row in c.fetchall():
            sym = row['symbol']
            entry = dict(row)
            # Prefer most recent journal entry if multiple for same symbol
            if sym not in journal:
                journal[sym] = entry
        conn.close()
    except Exception as e:
        logger.warning(f"Could not read trade journal: {e}")
    return journal


def _get_skipped_candidates(perf_db: Path, trade_date: date) -> List[Dict]:
    """
    Read skipped (non-traded) candidates from daily_candidate_snapshot.
    Returns candidates NOT in the trades table for today.
    """
    results = []
    try:
        conn = sqlite3.connect(perf_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Get traded symbols
        c.execute(
            "SELECT DISTINCT symbol FROM trades WHERE DATE(exit_time) = ?",
            (trade_date.isoformat(),)
        )
        traded = {row[0] for row in c.fetchall()}

        # Get all candidates for the day
        c.execute("""
            SELECT symbol, source, gap_pct, catalyst_headline,
                   opening_price, conviction_score, rejection_reason, strategy, lessons_applied
            FROM daily_candidate_snapshot
            WHERE trade_date = ?
            ORDER BY gap_pct DESC
        """, (trade_date.isoformat(),))

        for row in c.fetchall():
            if row['symbol'] not in traded:
                results.append(dict(row))

        conn.close()
    except Exception as e:
        logger.warning(f"Could not read skipped candidates: {e}")
    return results


def _fetch_ohlc_yfinance(symbols: List[str], trade_date: date, max_retries: int = 3) -> Dict[str, Dict]:
    """
    Fetch OHLC for all symbols on trade_date via yfinance.
    Returns: {symbol: {open, high, low, close, gap_pct, oc_move_pct, oh_move_pct}}
    Falls back gracefully if yfinance unavailable or data missing.

    Args:
        max_retries: Per-symbol retry attempts on transient failures (rate limits, network)
    """
    import time

    result = {}
    if not symbols:
        return result
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not available - OHLC data will be missing from report")
        return result

    # Fetch a 7-day window to ensure prev_close is available across weekends/holidays
    start = (trade_date - timedelta(days=7)).isoformat()
    end = (trade_date + timedelta(days=1)).isoformat()

    for symbol in symbols:
        for attempt in range(max_retries):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(start=start, end=end, auto_adjust=True)
                if hist.empty:
                    logger.debug(f"yfinance: no data for {symbol}")
                    break  # No data - don't retry

                # Find the row for trade_date
                dates = [ts.date() for ts in hist.index]
                if trade_date not in dates:
                    logger.debug(f"yfinance: {symbol} has no row for {trade_date}")
                    break  # Missing date - don't retry

                idx = dates.index(trade_date)
                row = hist.iloc[idx]

                open_p = round(float(row['Open']), 2)
                high_p = round(float(row['High']), 2)
                low_p = round(float(row['Low']), 2)
                close_p = round(float(row['Close']), 2)

                # Gap vs previous close
                gap_pct = 0.0
                if idx > 0:
                    prev_close = float(hist.iloc[idx - 1]['Close'])
                    gap_pct = round((open_p - prev_close) / prev_close * 100, 2)

                oc_move_pct = round((close_p - open_p) / open_p * 100, 2)
                oh_move_pct = round((high_p - open_p) / open_p * 100, 2)

                result[symbol] = {
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'close': close_p,
                    'gap_pct': gap_pct,
                    'oc_move_pct': oc_move_pct,
                    'oh_move_pct': oh_move_pct,
                }
                break  # Success - no more retries needed

            except Exception as e:
                if attempt < max_retries - 1:
                    wait_secs = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
                    logger.warning(
                        f"yfinance fetch failed for {symbol} (attempt {attempt + 1}/{max_retries}): {e}"
                        f" - retrying in {wait_secs}s"
                    )
                    time.sleep(wait_secs)
                else:
                    logger.warning(f"yfinance fetch failed for {symbol} after {max_retries} attempts: {e}")

    logger.info(f"  OHLC fetched: {len(result)}/{len(symbols)} symbols")
    return result


def run_weekly_hypothesis_generation(
    week_end_date: date,
    perf_db: Optional[Path] = None,
    learn_db: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Aggregate the past week's EOD reports and call Grok for hypotheses.

    Called from market_close_routine() on Fridays.
    Returns path to saved hypotheses JSON, or None if failed.
    """
    reports_dir = reports_dir or _REPORTS_DIR
    learn_db = learn_db or _LEARN_DB

    # Find all EOD reports from the past 7 days
    report_files = []
    for days_back in range(7):
        check_date = week_end_date - timedelta(days=days_back)
        candidate = reports_dir / f"{check_date.isoformat()}_eod_report.json"
        if candidate.exists():
            report_files.append(candidate)

    if not report_files:
        logger.info("Weekly hypothesis: no EOD reports found for this week - skipping")
        return None

    logger.info(f"Weekly hypothesis: found {len(report_files)} daily reports")

    try:
        _ensure_imports()
        from ai_trader.analytics.weekly_summary_generator import WeeklySummaryGenerator

        weekly_dir = _ROOT / "reports/weekly"
        weekly_dir.mkdir(parents=True, exist_ok=True)

        # Read each EOD report JSON into a dict (WeeklySummaryGenerator takes list of dicts)
        eod_dicts = []
        for f in sorted(report_files):
            try:
                with open(f) as fp:
                    eod_dicts.append(json.load(fp))
            except Exception as e:
                logger.warning(f"Could not load EOD report {f}: {e}")

        if not eod_dicts:
            logger.warning("Weekly hypothesis: failed to load any EOD report dicts")
            return None

        gen = WeeklySummaryGenerator(output_dir=str(weekly_dir))
        summary = gen.aggregate_daily_reports(eod_dicts)

        if not summary:
            logger.warning("Weekly hypothesis: summary generation returned empty result")
            return None

        # Build week_label from actual report date range
        week_start = eod_dicts[0].get('date', sorted(report_files)[0].stem.split('_')[0])
        week_label = f"{week_start}_to_{week_end_date.isoformat()}"
        gen.save_weekly_summary(summary, week_label)
        logger.info(f"Weekly summary saved: reports/weekly/{week_label}_weekly_summary.json")

        # Load active lessons to pass to hypothesis generator
        active_lessons = _load_active_lessons(learn_db)

        # Call Grok hypothesis generator
        from grok_hypothesis_generator import GrokHypothesisGenerator
        hyp_gen = GrokHypothesisGenerator()

        # Send rejected hypotheses from previous cycles BEFORE generating new ones.
        # Only fires if there are unpresented failures - no-op on first weeks.
        try:
            hyp_gen.send_rejected_hypotheses_to_grok(str(learn_db))
        except Exception as _e:
            logger.warning(f"Rejected hypothesis feedback failed (non-critical): {_e}")

        hypotheses = hyp_gen.generate_hypotheses(summary, active_lessons=active_lessons)

        if hypotheses:
            out_path = weekly_dir / f"hypotheses_{week_label}.json"
            with open(out_path, 'w') as f:
                json.dump(hypotheses, f, indent=2)
            logger.info(f"Saved {len(hypotheses)} hypotheses to {out_path}")

            # Wire into pruning cycle: generate a suggested pruning config for Sunday
            try:
                suggestion_path = _generate_pruning_suggestion(
                    hypotheses, weekly_dir, week_label, learn_db
                )
                if suggestion_path:
                    logger.info(f"Pruning suggestion ready: {suggestion_path}")
                    logger.info("  -> Review and run: python run_lesson_pruning_cycle.py --config <path>")
            except Exception as e:
                logger.warning(f"Pruning suggestion generation failed (non-critical): {e}")

            return str(out_path)
        elif hypotheses is not None:
            # generate_hypotheses returned [] - Grok ran successfully but found no new patterns.
            # Distinct from None (error) or exception. Log explicitly so scheduler doesn't
            # misreport this as "generation skipped (no reports found)".
            logger.info("Weekly hypothesis generation: no new hypotheses - all patterns already covered by active lessons")

    except Exception as e:
        logger.error(f"Weekly hypothesis generation failed: {e}", exc_info=True)

    return None


def run_system_hypothesis_generation(
    week_end_date: date,
    perf_db: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Aggregate the past week's EOD reports and generate H_SYSTEM hypotheses.

    Completely separate from the lesson hypothesis pipeline - results go to
    system_hypothesis_log table and reports/weekly/system_hypotheses_*.json.
    Never feeds into learning.db or the pruning resimulator.

    Called from market_close_routine() on Fridays, after run_weekly_hypothesis_generation().
    Returns path to saved system hypotheses JSON, or None if failed/skipped.
    """
    reports_dir = reports_dir or _REPORTS_DIR

    # Find all EOD reports from the past 7 days (same as weekly hypothesis)
    report_files = []
    for days_back in range(7):
        check_date = week_end_date - timedelta(days=days_back)
        candidate = reports_dir / f"{check_date.isoformat()}_eod_report.json"
        if candidate.exists():
            report_files.append(candidate)

    if not report_files:
        logger.info("System hypothesis: no EOD reports found for this week - skipping")
        return None

    logger.info(f"System hypothesis: found {len(report_files)} daily reports")

    try:
        # Load EOD report dicts
        eod_dicts = []
        for f in sorted(report_files):
            try:
                with open(f) as fp:
                    eod_dicts.append(json.load(fp))
            except Exception as e:
                logger.warning(f"Could not load EOD report {f}: {e}")

        if not eod_dicts:
            logger.warning("System hypothesis: failed to load any EOD report dicts")
            return None

        # Build week_label from actual report date range
        week_start = eod_dicts[0].get('date', sorted(report_files)[0].stem.split('_')[0])
        week_label = f"{week_start}_to_{week_end_date.isoformat()}"

        from system_hypothesis_generator import generate_system_hypotheses
        out_path = generate_system_hypotheses(
            eod_reports=eod_dicts,
            week_label=week_label,
            perf_db=perf_db or _PERF_DB,
            reports_dir=_ROOT / "reports/weekly",
        )

        if out_path:
            logger.info(f"System hypotheses saved: {out_path}")
            logger.info("  -> Review manually and invoke Claude Code to implement approved changes")
        else:
            logger.info("System hypothesis generation: no hypotheses generated this week")

        return out_path

    except Exception as e:
        logger.error(f"System hypothesis generation failed: {e}", exc_info=True)

    return None


def _generate_pruning_suggestion(
    hypotheses: List[Dict],
    weekly_dir: Path,
    week_label: str,
    learn_db: Path,
) -> Optional[str]:
    """
    Convert Grok hypotheses into lesson candidates in the DB and write a
    pruning test config for the Saturday cycle.

    Each hypothesis becomes a lesson candidate (pruning_mode=1, is_active=0,
    validated=0) so the Saturday cycle can test whether applying that rule
    improves P&L vs baseline. Survivors get promoted to production lessons.

    The generated config uses test_lesson_ids (the key run_lesson_pruning_cycle
    reads) pointing to the newly created candidate lesson IDs.

    Returns:
        Path to saved config file, or None on failure.
    """
    import hashlib as _hashlib

    CATEGORY_MAP = {
        "CONVICTION_ADJUSTMENT": "conviction",
        "REGIME_ADAPTATION": "regime",
        "TIMING": "timing",
        "RISK": "risk",
    }

    CONFIDENCE_LEVEL_MAP = [
        (80, "HIGH"),
        (60, "MEDIUM"),
        (0,  "LOW"),
    ]

    candidate_ids = []

    if not learn_db.exists():
        logger.warning("_generate_pruning_suggestion: learning.db not found, skipping")
        return None

    try:
        conn = sqlite3.connect(learn_db)
        c = conn.cursor()

        for hyp in hypotheses:
            hyp_id   = hyp.get("ID", hyp.get("id", ""))
            hyp_type = hyp.get("Type", hyp.get("type", ""))
            title    = hyp.get("Title", hyp.get("title", ""))
            change   = hyp.get("Proposed_Change", hyp.get("proposed_change", ""))
            conf_pct = float(hyp.get("Confidence", hyp.get("confidence", 60)))
            rationale = hyp.get("Rationale", hyp.get("rationale", ""))

            # Lesson text = proposed change (what Grok would apply during a trade eval)
            lesson_text = f"[{hyp_id}] {title}: {change}".strip()
            if not lesson_text:
                continue

            category = CATEGORY_MAP.get(hyp_type.upper(), "strategy")
            confidence = round(conf_pct / 100.0, 2)
            conf_level = next(lvl for thresh, lvl in CONFIDENCE_LEVEL_MAP if conf_pct >= thresh)

            # Hash to deduplicate (same hypothesis across weeks won't create duplicates)
            raw = f"{week_label}:{hyp_id}:{lesson_text}"
            lesson_hash = _hashlib.md5(raw.encode()).hexdigest()

            # Check for existing candidate from this week
            c.execute("SELECT id FROM lessons WHERE lesson_hash=?", (lesson_hash,))
            row = c.fetchone()
            if row:
                candidate_ids.append(row[0])
                logger.info(f"[PRUNING-SETUP] Reusing existing candidate L{row[0]} for {hyp_id}")
                continue

            # Insert new lesson candidate - inactive until pruning cycle promotes it
            c.execute("""
                INSERT INTO lessons (
                    lesson_hash, lesson_text, category, source, hypothesis,
                    confidence, confidence_level, is_active, validated,
                    pruning_mode, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?)
            """, (
                lesson_hash,
                lesson_text,
                category,
                "hypothesis_testing",
                f"{hyp_id}: {title}",
                confidence,
                conf_level,
                rationale[:500] if rationale else None,
                datetime.now().isoformat(),
            ))
            new_id = c.lastrowid
            candidate_ids.append(new_id)
            logger.info(f"[PRUNING-SETUP] Created candidate L{new_id} for {hyp_id}: {title[:60]}")

        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"_generate_pruning_suggestion: DB error creating candidates: {e}", exc_info=True)
        return None

    if not candidate_ids:
        logger.warning("[PRUNING-SETUP] No hypothesis candidates created - no hypotheses to test")
        return None

    # Write the pruning test config that Saturday cycle picks up via --config
    # Must use test_lesson_ids (the key run_lesson_pruning_cycle.py reads on line 930)
    iterations = max(3, len(candidate_ids))   # at least 3 iterations

    config = {
        "description": f"Grok hypothesis candidates for week {week_label} - {len(candidate_ids)} to test",
        "generated_at": datetime.now().isoformat(),
        "hypotheses_source": f"hypotheses_{week_label}.json",
        "lookback_days": 120,
        "iterations": iterations,
        "rotation_size": 1,
        "test_lesson_ids": candidate_ids,
        "notes": (
            "Auto-generated from hypothesis testing pipeline. "
            "Candidates are is_active=0, pruning_mode=1 until cycle promotes them. "
            "Run: python run_lesson_pruning_cycle.py --skip-discovery --config <this_file>"
        )
    }

    config_path = weekly_dir / f"pruning_suggestion_{week_label}.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    logger.info(
        f"[PRUNING-SETUP] Wrote pruning suggestion: {len(candidate_ids)} hypothesis candidates, "
        f"iterations={iterations}, path={config_path.name}"
    )
    return str(config_path)


def _load_active_lessons(learn_db: Path) -> Dict[str, Any]:
    """Load active lessons from learning.db for the hypothesis prompt."""
    lessons = {}
    if not learn_db.exists():
        return lessons
    try:
        conn = sqlite3.connect(learn_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT lesson_id, description, win_rate, total_uses, avg_pnl, status
            FROM lessons
            WHERE status IN ('active', 'ACTIVE', 'good', 'GOOD')
            ORDER BY lesson_id
        """)
        for row in c.fetchall():
            lid = str(row['lesson_id'])
            lessons[lid] = {
                'description': row['description'],
                'win_rate': row['win_rate'],
                'total_uses': row['total_uses'],
                'avg_pnl': row['avg_pnl'],
                'status': row['status'],
            }
        conn.close()
    except Exception as e:
        logger.warning(f"Could not load active lessons: {e}")
    return lessons


def record_hypothesis_outcomes(learn_db: Path = None, weekly_dir: Path = None) -> dict:
    """
    After Saturday pruning + auto-promote, record which hypotheses passed or failed.

    Finds the most recent reports/weekly/hypotheses_*.json, checks each hypothesis
    Lesson_ID against live_eligible in learning.db, and records failures to the
    failed_hypotheses table.

    Also appends new failures to rejected_hypotheses.json archive and rebuilds
    active_rejection_context.json (capped at 25 unpresented).

    Returns dict with keys: recorded (int), skipped (int), errors (int).
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))

    learn_db = learn_db or _LEARN_DB
    weekly_dir = weekly_dir or Path("reports/weekly")
    data_dir = Path(__file__).resolve().parent.parent.parent / "ai_trader_data"

    stats = {"recorded": 0, "skipped": 0, "errors": 0}

    # Find most recent hypotheses file
    hyp_files = sorted(weekly_dir.glob("hypotheses_*.json")) if weekly_dir.exists() else []
    if not hyp_files:
        logger.info("[hypothesis-outcomes] No hypotheses files found - skipping outcome recording")
        return stats

    hyp_path = hyp_files[-1]
    try:
        with open(hyp_path) as f:
            hypotheses = json.load(f)
    except Exception as exc:
        logger.warning(f"[hypothesis-outcomes] Could not load {hyp_path}: {exc}")
        return stats

    logger.info(f"[hypothesis-outcomes] Processing {len(hypotheses)} hypotheses from {hyp_path.name}")

    # Get LearningDatabase
    try:
        from analytics.learning_database import LearningDatabase
        ldb = LearningDatabase(str(learn_db))
    except Exception as exc:
        logger.warning(f"[hypothesis-outcomes] Could not load LearningDatabase: {exc}")
        return stats

    archive_path = data_dir / "rejected_hypotheses.json"
    new_failures = []

    for hyp in hypotheses:
        lesson_id_raw = hyp.get("Lesson_ID") or hyp.get("lesson_id")
        if not lesson_id_raw:
            stats["skipped"] += 1
            continue

        # Parse "L229" -> 229
        try:
            lid = int(str(lesson_id_raw).lstrip("L"))
        except ValueError:
            stats["skipped"] += 1
            continue

        # Check if lesson got promoted (live_eligible=1)
        try:
            with ldb._get_connection() as conn:
                row = conn.execute(
                    "SELECT live_eligible FROM lessons WHERE id = ?", (lid,)
                ).fetchone()
        except Exception:
            stats["errors"] += 1
            continue

        if row and row["live_eligible"] == 1:
            # Promoted - success, do not record as failure
            stats["skipped"] += 1
            continue

        # Not promoted - record as failure
        try:
            with ldb._get_connection() as conn:
                wins_row = conn.execute(
                    "SELECT resim_wins, resim_losses FROM lessons WHERE id = ?", (lid,)
                ).fetchone()
        except Exception:
            wins_row = None

        if wins_row:
            w = wins_row["resim_wins"] or 0
            l = wins_row["resim_losses"] or 0
            total = w + l
            wr = w / total if total > 0 else 0.0
            reason = f"WR={wr:.1%} over {total} resim trades (threshold: 67%)"
        else:
            reason = "Lesson not found or no resim data after pruning cycle"

        try:
            ok = ldb.record_failed_hypothesis(hyp, reason)
            if ok:
                stats["recorded"] += 1
                new_failures.append({
                    **hyp,
                    "failure_reason": reason,
                    "failed_at": datetime.now().isoformat(),
                })
            else:
                stats["errors"] += 1
        except Exception as exc:
            logger.warning(f"[hypothesis-outcomes] Failed to record hypothesis {hyp.get('ID')}: {exc}")
            stats["errors"] += 1

    # Append new failures to archive JSON
    if new_failures:
        try:
            existing = []
            if archive_path.exists():
                with open(archive_path) as f:
                    existing = json.load(f)
            with open(archive_path, "w") as f:
                json.dump(existing + new_failures, f, indent=2)
            logger.info(f"[hypothesis-outcomes] Archive updated: {len(existing + new_failures)} total failures")
        except Exception as exc:
            logger.warning(f"[hypothesis-outcomes] Could not update archive: {exc}")

    # Rebuild active_rejection_context.json (25 most recent unpresented)
    try:
        unpresented = ldb.get_unpresented_rejections(25)
        context_path = data_dir / "active_rejection_context.json"
        with open(context_path, "w") as f:
            json.dump(unpresented, f, indent=2)
        logger.info(f"[hypothesis-outcomes] Active context: {len(unpresented)} unpresented failures")
    except Exception as exc:
        logger.warning(f"[hypothesis-outcomes] Could not rebuild active context: {exc}")

    logger.info(f"[hypothesis-outcomes] Done: {stats}")
    return stats
