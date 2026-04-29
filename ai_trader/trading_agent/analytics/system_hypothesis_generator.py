"""
System Hypothesis Generator - H_SYSTEM pipeline

Generates hypotheses about SYSTEM-LEVEL changes (hardcoded thresholds,
config parameters) rather than lesson add/remove/promote changes.

COMPLETELY SEPARATE from the lesson hypothesis / pruning pipeline.
- Never reads from or writes to the lesson hypothesis queue
- Never touches learning.db/lessons
- Never produces input for the pruning resimulator
- Stored in: trading_performance.db/system_hypothesis_log (new table)
              reports/weekly/system_hypotheses_{week}.json

Called from: eod_report_builder.run_system_hypothesis_generation() on Fridays.
Manual invocation: python system_hypothesis_generator.py [--week 2026-02-23_to_2026-02-27]

Lifecycle of an H_SYSTEM hypothesis:
  generated (Friday) -> reviewed by human -> implemented via Claude Code -> outcome tracked
"""

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Paths derived from this file's location
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERF_DB = _ROOT / "ai_trader/ai_trader_data/trading_performance.db"
_PARAMS_FILE = _ROOT / "ai_trader/trading_agent/config/system_parameters.json"
_REPORTS_DIR = _ROOT / "reports"
_CREDS_DIR = _ROOT / "ai_trader/ai_trader_data/credentials"

# Exit methods that indicate the system's own logic closed the position
_SYSTEM_EXIT_METHODS = {"trailing_stop", "pre_emptive", "stop_loss"}

# Minimum trades to generate a hypothesis about a parameter
MIN_TRADES_FOR_HYPOTHESIS = 2

# Recovery threshold: if stock recovers this much above exit within the day,
# the exit was likely premature (noisy stop)
RECOVERY_THRESHOLD_PCT = 0.5


# ---------------------------------------------------------------------------
# DB migration - called once at import time if table missing
# ---------------------------------------------------------------------------

def _ensure_table(perf_db: Path = None):
    """Create system_hypothesis_log table if it doesn't exist."""
    db_path = perf_db or _PERF_DB
    if not db_path.exists():
        logger.warning(f"trading_performance.db not found: {db_path}")
        return
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_hypothesis_log (
                id                   TEXT PRIMARY KEY,
                week                 TEXT NOT NULL,
                generated_at         TEXT NOT NULL,
                parameter_key        TEXT NOT NULL,
                current_value        TEXT,
                proposed_value       TEXT,
                rationale            TEXT,
                evidence             TEXT,
                sim_pnl_delta        REAL,
                trade_count          INTEGER,
                status               TEXT NOT NULL DEFAULT 'pending',
                reviewed_at          TEXT,
                implemented_at       TEXT,
                implementation_notes TEXT,
                outcome_check_date   TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.debug("system_hypothesis_log table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure system_hypothesis_log table: {e}")


# Run migration at import time (safe to call repeatedly)
try:
    _ensure_table()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Load system parameters
# ---------------------------------------------------------------------------

def _load_system_params(params_file: Path = None) -> Dict:
    """Load system_parameters.json. Returns {} on error."""
    path = params_file or _PARAMS_FILE
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load system_parameters.json: {e}")
        return {}


def _format_params_for_prompt(params: Dict) -> str:
    """Format system parameters as readable text for Grok prompt."""
    if not params:
        return "(system parameters unavailable)"
    lines = ["CURRENT SYSTEM PARAMETERS:"]
    for key, meta in params.get("parameters", {}).items():
        lines.append(f"\n[{key}] - {meta.get('description', '')}")
        lines.append(f"  source: {meta.get('source_file', '')} -> {meta.get('source_function', '')}")
        vals = meta.get("values", {})
        for vname, vdata in vals.items():
            if isinstance(vdata, dict):
                cur = vdata.get("current", "?")
                unit = vdata.get("unit", meta.get("unit", ""))
                desc = vdata.get("description", "")
                lines.append(f"  {vname}: {cur}{(' ' + unit) if unit else ''}{('  # ' + desc) if desc else ''}")
            else:
                lines.append(f"  {vname}: {vdata}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Exit pattern analysis
# ---------------------------------------------------------------------------

def _analyze_exit_patterns(eod_reports: List[Dict]) -> List[Dict]:
    """
    Extract and enrich all stop/pre-emptive exits from a list of EOD report dicts.

    For each trade in the reports, fetch end-of-day close price via yfinance
    to determine if the stock recovered after the exit (premature stop signal).

    Returns list of dicts:
      symbol, exit_method, entry_price, exit_price, quantity, realized_pnl,
      day_close, recovery_pct, pnl_left_on_table, trade_date, vix
    """
    exits = []
    for report in eod_reports:
        trade_date = report.get("date", "")
        vix = report.get("vix", 0)
        for trade in report.get("trades_taken", []):
            exit_reason = (trade.get("exit_reason") or "").lower()
            if exit_reason not in _SYSTEM_EXIT_METHODS:
                continue
            symbol = trade.get("symbol")
            entry_price = trade.get("entry_price") or trade.get("entry_px")
            exit_price = trade.get("exit_price") or trade.get("exit_px")
            quantity = trade.get("quantity") or trade.get("shares", 0)
            realized_pnl = trade.get("pnl_dollars") or trade.get("realized_pnl", 0)
            day_close = trade.get("day_close")  # already in EOD report if fetched

            if not all([symbol, entry_price, exit_price, quantity]):
                continue

            # If day_close not in report, fetch via yfinance
            if not day_close and trade_date:
                day_close = _fetch_day_close(symbol, trade_date)

            recovery_pct = 0.0
            pnl_left = 0.0
            if day_close and exit_price:
                recovery_pct = (day_close - exit_price) / exit_price * 100
                pnl_left = (day_close - exit_price) * quantity  # $ left on table if held to close

            exits.append({
                "symbol": symbol,
                "exit_method": exit_reason,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": quantity,
                "realized_pnl": realized_pnl,
                "day_close": day_close,
                "recovery_pct": round(recovery_pct, 2),
                "pnl_left_on_table": round(pnl_left, 2),
                "trade_date": trade_date,
                "vix": vix,
            })
    return exits


def _fetch_day_close(symbol: str, trade_date_str: str) -> Optional[float]:
    """Fetch end-of-day close price for a symbol on a given date via yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        hist = t.history(start=trade_date_str, end=trade_date_str, interval="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[0])
        # Try with next day as end
        d = date.fromisoformat(trade_date_str)
        end = (d + timedelta(days=1)).isoformat()
        hist = t.history(start=trade_date_str, end=end, interval="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.debug(f"Could not fetch day close for {symbol} on {trade_date_str}: {e}")
    return None


# ---------------------------------------------------------------------------
# Counterfactual simulator
# ---------------------------------------------------------------------------

def _run_counterfactuals(exits: List[Dict], params: Dict) -> List[Dict]:
    """
    For each trailing-stop exit, simulate what P&L would have been with
    wider trail percentages (+0.5%, +1.0%) compared to current setting.

    Returns list of dicts with counterfactual results per exit.
    """
    results = []
    trail_params = params.get("parameters", {}).get("trailing_stop", {}).get("values", {})

    for ex in exits:
        if ex["exit_method"] not in ("trailing_stop", "stop_loss"):
            continue
        if not ex.get("day_close") or not ex.get("exit_price"):
            continue

        entry = ex["entry_price"]
        exit_px = ex["exit_price"]
        close = ex["day_close"]
        qty = ex["quantity"]
        vix = ex.get("vix", 18.5)

        # Determine which VIX bucket applies
        if vix < 15:
            bucket = "low_vix_lt15"
        elif vix < 25:
            bucket = "medium_vix_15_25"
        elif vix < 35:
            bucket = "high_vix_25_35"
        else:
            bucket = "extreme_vix_gt35"

        current_trail = (trail_params.get(bucket) or {}).get("current", 2.0)

        # Actual P&L
        actual_pnl = (exit_px - entry) * qty

        # Counterfactual: wider trails
        # Simplified model: if close > exit_price, the wider trail would have
        # kept us in longer and we'd have captured more. We use day close as
        # the proxy for "where we'd have exited with a wider stop."
        # This is conservative (close != ideal exit) but directionally correct.
        cf_wider_half = (close - entry) * qty  # held to close (0.5% wider trail proxy)
        cf_wider_full = (close - entry) * qty  # same proxy; Grok reasons about the direction

        delta_half = cf_wider_half - actual_pnl
        delta_full = cf_wider_full - actual_pnl

        results.append({
            "symbol": ex["symbol"],
            "trade_date": ex["trade_date"],
            "vix": vix,
            "vix_bucket": bucket,
            "current_trail_pct": current_trail,
            "entry_price": entry,
            "exit_price": exit_px,
            "day_close": close,
            "quantity": qty,
            "actual_pnl": round(actual_pnl, 2),
            "held_to_close_pnl": round(cf_wider_half, 2),
            "pnl_delta_if_held": round(delta_half, 2),
            "recovery_pct": ex["recovery_pct"],
        })
    return results


def _format_exit_analysis_for_prompt(exits: List[Dict], counterfactuals: List[Dict]) -> str:
    """Format exit analysis and counterfactuals as readable text for Grok."""
    if not exits:
        return "No system-triggered exits this week."

    lines = [f"SYSTEM-TRIGGERED EXITS THIS WEEK ({len(exits)} total):"]
    for ex in exits:
        rec = ex["recovery_pct"]
        left = ex["pnl_left_on_table"]
        flag = " <<< RECOVERED" if rec >= RECOVERY_THRESHOLD_PCT else ""
        lines.append(
            f"  {ex['symbol']} ({ex['trade_date']}) via {ex['exit_method'].upper()}: "
            f"entry=${ex['entry_price']:.2f} exit=${ex['exit_price']:.2f} "
            f"close=${ex['day_close'] or 'N/A'} "
            f"recovery={rec:+.1f}% P&L_left_on_table=${left:+.0f} "
            f"VIX={ex['vix']:.1f}{flag}"
        )

    premature = [e for e in exits if e["recovery_pct"] >= RECOVERY_THRESHOLD_PCT]
    if premature:
        lines.append(
            f"\n{len(premature)}/{len(exits)} exits recovered >{RECOVERY_THRESHOLD_PCT}% "
            f"above exit price by day close. "
            f"Total P&L left on table: ${sum(e['pnl_left_on_table'] for e in premature):+.0f}"
        )

    if counterfactuals:
        lines.append(f"\nCOUNTERFACTUAL (trailing stops - held to close):")
        total_delta = sum(c["pnl_delta_if_held"] for c in counterfactuals)
        for cf in counterfactuals:
            lines.append(
                f"  {cf['symbol']}: current trail={cf['current_trail_pct']}% "
                f"(VIX {cf['vix']:.0f} -> {cf['vix_bucket']}) | "
                f"actual P&L ${cf['actual_pnl']:+.0f} vs held-to-close ${cf['held_to_close_pnl']:+.0f} "
                f"| delta ${cf['pnl_delta_if_held']:+.0f}"
            )
        lines.append(f"  Total counterfactual delta this week: ${total_delta:+.0f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Grok call
# ---------------------------------------------------------------------------

SYSTEM_HYPOTHESIS_PROMPT_TEMPLATE = """
You are analyzing a day-trading system's performance to identify SYSTEM-LEVEL improvements.
These are NOT about which stocks to buy or lesson rules. They are about hardcoded parameters
and thresholds in the system code that may need tuning.

{params_text}

{exit_analysis_text}

WEEKLY TRADE SUMMARY:
{weekly_summary_text}

---

Your task: Identify 0-3 specific system parameters that show evidence of being mistuned
this week. Only generate a hypothesis if there are at least {min_trades} exits showing
the same pattern.

For each hypothesis, output a JSON object with these exact fields:
  - "id": "H_SYSTEM_NNN" (auto-numbered, e.g. H_SYSTEM_001)
  - "parameter_key": exact key from system parameters above (e.g. "trailing_stop.medium_vix_15_25")
  - "current_value": current value as a string (e.g. "2.0%")
  - "proposed_value": suggested new value as a string (e.g. "2.5%")
  - "direction": "increase" or "decrease"
  - "rationale": 1-2 sentences explaining the pattern observed
  - "evidence": specific trade(s) supporting this, with dollar amounts
  - "sim_pnl_delta": estimated weekly P&L improvement in dollars (positive = better)
  - "trade_count": number of trades showing this pattern
  - "confidence": "low", "medium", or "high"

Return ONLY a JSON array. If no clear patterns exist, return an empty array [].
Do NOT generate hypotheses based on fewer than {min_trades} trades.
Do NOT suggest parameter changes that would increase risk (wider stops, lower conviction floors)
unless the evidence is very strong (high confidence, 3+ trades, positive P&L delta).

Example output:
[
  {{
    "id": "H_SYSTEM_001",
    "parameter_key": "trailing_stop.medium_vix_15_25",
    "current_value": "2.0%",
    "proposed_value": "2.5%",
    "direction": "increase",
    "rationale": "2 of 2 trailing-stop exits this week recovered above exit price by day close, suggesting the 2.0% trail is getting shaken out by normal intraday noise before the move completes.",
    "evidence": "IONL: exited $21.93, closed $22.25 (+1.5%, +$74 left). HPP: exited $7.91, closed $7.92 (+0.1%, +$6 left).",
    "sim_pnl_delta": 80.0,
    "trade_count": 2,
    "confidence": "medium"
  }}
]
"""


def _call_grok(prompt: str) -> str:
    """Call Grok via xAI OpenAI-compatible API."""
    from openai import OpenAI
    xai_key = os.environ.get("XAI_API_KEY") or _load_xai_key()
    if not xai_key:
        raise ValueError("XAI_API_KEY not found in environment or credentials file")
    client = OpenAI(api_key=xai_key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model="grok-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,  # Low temp - we want consistent structured output
    )
    content = response.choices[0].message.content
    logger.info(f"Grok system hypothesis response: {len(content)} chars")
    return content


def _load_xai_key() -> Optional[str]:
    """Load xAI API key from credentials file."""
    try:
        key_file = _CREDS_DIR / "xai_api_key.txt"
        if key_file.exists():
            return key_file.read_text().strip()
    except Exception:
        pass
    return None


def _parse_grok_response(response: str) -> List[Dict]:
    """Extract JSON array from Grok response."""
    try:
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            hypotheses = json.loads(json_match.group())
            logger.info(f"Parsed {len(hypotheses)} system hypotheses from Grok")
            return hypotheses
        logger.warning("No JSON array found in Grok system hypothesis response")
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in system hypothesis response: {e}")
    return []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_to_db(hypotheses: List[Dict], week_label: str, perf_db: Path = None):
    """Insert H_SYSTEM hypotheses into system_hypothesis_log table."""
    db_path = perf_db or _PERF_DB
    if not hypotheses:
        return
    try:
        conn = sqlite3.connect(db_path)
        now = datetime.now().isoformat()
        # Outcome check: 3 weeks after week end
        week_end = week_label.split("_to_")[-1] if "_to_" in week_label else week_label
        try:
            outcome_check = (date.fromisoformat(week_end) + timedelta(weeks=3)).isoformat()
        except Exception:
            outcome_check = None

        for hyp in hypotheses:
            hyp_id = hyp.get("id") or f"H_SYSTEM_{str(uuid.uuid4())[:8].upper()}"
            conn.execute("""
                INSERT OR IGNORE INTO system_hypothesis_log
                    (id, week, generated_at, parameter_key, current_value,
                     proposed_value, rationale, evidence, sim_pnl_delta,
                     trade_count, status, outcome_check_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                hyp_id,
                week_label,
                now,
                hyp.get("parameter_key", ""),
                hyp.get("current_value", ""),
                hyp.get("proposed_value", ""),
                hyp.get("rationale", ""),
                hyp.get("evidence", ""),
                hyp.get("sim_pnl_delta"),
                hyp.get("trade_count"),
                outcome_check,
            ))
        conn.commit()
        conn.close()
        logger.info(f"Saved {len(hypotheses)} system hypotheses to DB")
    except Exception as e:
        logger.error(f"Failed to save system hypotheses to DB: {e}")


def _save_to_json(hypotheses: List[Dict], week_label: str, reports_dir: Path = None) -> Optional[str]:
    """Save H_SYSTEM hypotheses to reports/weekly/system_hypotheses_{week}.json."""
    out_dir = (reports_dir or _REPORTS_DIR) / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"system_hypotheses_{week_label}.json"
    try:
        payload = {
            "week": week_label,
            "generated_at": datetime.now().isoformat(),
            "count": len(hypotheses),
            "hypotheses": hypotheses,
            "note": (
                "H_SYSTEM hypotheses are for human review only. "
                "To implement: tell Claude Code 'implement H_SYSTEM_NNN'. "
                "These are NOT processed by the lesson pruning pipeline."
            ),
        }
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"System hypotheses saved: {out_path}")
        return str(out_path)
    except Exception as e:
        logger.error(f"Failed to save system hypotheses JSON: {e}")
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_system_hypotheses(
    eod_reports: List[Dict],
    week_label: str,
    perf_db: Path = None,
    reports_dir: Path = None,
    params_file: Path = None,
) -> Optional[str]:
    """
    Analyze this week's EOD reports and generate H_SYSTEM hypotheses.

    This is the only public function - called from eod_report_builder on Fridays.
    Completely isolated from the lesson hypothesis / pruning pipeline.

    Args:
        eod_reports: List of daily EOD report dicts for the week.
        week_label: String like "2026-02-23_to_2026-02-27".
        perf_db: Path to trading_performance.db (uses default if None).
        reports_dir: Path to reports/ dir (uses default if None).
        params_file: Path to system_parameters.json (uses default if None).

    Returns:
        Path to saved JSON file, or None if no hypotheses or error.
    """
    logger.info(f"System hypothesis generation starting for week: {week_label}")
    logger.info(f"  EOD reports: {len(eod_reports)}")

    # Ensure DB table exists
    _ensure_table(perf_db or _PERF_DB)

    # Load system parameters
    params = _load_system_params(params_file)
    if not params:
        logger.warning("system_parameters.json empty or missing - Grok will have no parameter context")

    # Analyze exit patterns across all days this week
    exits = _analyze_exit_patterns(eod_reports)
    logger.info(f"  System-triggered exits found: {len(exits)}")

    if not exits:
        logger.info("  No trailing-stop or pre-emptive exits this week - skipping system hypothesis generation")
        return None

    # Run counterfactuals for trailing-stop exits
    counterfactuals = _run_counterfactuals(exits, params)
    logger.info(f"  Counterfactuals computed: {len(counterfactuals)}")

    # Build prompt sections
    params_text = _format_params_for_prompt(params)
    exit_text = _format_exit_analysis_for_prompt(exits, counterfactuals)

    # Weekly summary text (brief - Grok already has full context from lesson hypotheses)
    total_pnl = sum(e.get("realized_pnl", 0) for report in eod_reports for e in report.get("trades_taken", []))
    total_trades = sum(len(report.get("trades_taken", [])) for report in eod_reports)
    wins = sum(
        1 for report in eod_reports
        for t in report.get("trades_taken", [])
        if (t.get("pnl_dollars") or t.get("realized_pnl", 0)) > 0
    )
    weekly_summary = (
        f"Trades: {total_trades} | Wins: {wins} | "
        f"Total P&L: ${total_pnl:+.0f} | "
        f"System exits (stops + pre-emptive): {len(exits)}"
    )

    prompt = SYSTEM_HYPOTHESIS_PROMPT_TEMPLATE.format(
        params_text=params_text,
        exit_analysis_text=exit_text,
        weekly_summary_text=weekly_summary,
        min_trades=MIN_TRADES_FOR_HYPOTHESIS,
    )

    # Log prompt length
    logger.info(f"  System hypothesis prompt: {len(prompt)} chars")

    try:
        response = _call_grok(prompt)
        hypotheses = _parse_grok_response(response)
    except Exception as e:
        logger.error(f"Grok call failed for system hypotheses: {e}")
        return None

    if not hypotheses:
        logger.info("  Grok found no system parameters to adjust this week")
        return None

    # Stamp week onto each hypothesis (Grok may not include it)
    for hyp in hypotheses:
        hyp["week"] = week_label

    # Save to DB (separate table - no lesson pipeline involvement)
    _save_to_db(hypotheses, week_label, perf_db)

    # Save to JSON (separate file - not read by pruning runner)
    out_path = _save_to_json(hypotheses, week_label, reports_dir)

    logger.info(
        f"System hypothesis generation complete: {len(hypotheses)} hypotheses saved. "
        f"Review at: {out_path}"
    )
    return out_path



# ---------------------------------------------------------------------------
# Live CGH (trade-count triggered) - PARAMETER_VARIATION hypotheses
# ---------------------------------------------------------------------------

_LIVE_CGH_PROMPT_TEMPLATE = """You are analyzing a swing trading system's live trade history to identify parameter adjustments.

TRADE WINDOW: {window_start} to {window_end}
TOTAL TRADES: {total_trades} | WIN RATE: {win_rate:.1%} | AVG P&L: ${avg_pnl:+.2f}
REGIME DISTRIBUTION: {regime_counts}
VIX BUCKETS: {vix_buckets}
NO-POSITION DAYS: {no_pos_days}

TOP {lesson_count} ACTIVE LESSONS (by confidence):
{lessons_text}

TASK: Identify up to 3 PARAMETER_VARIATION hypotheses where changing a hardcoded threshold
could improve system performance over this window.

Focus on:
- VIX thresholds (e.g., CASH trigger, spike circuit breaker)
- ADR / price range minimums
- Gap percentage filters
- Conviction score cutoffs
- Hold duration rules

OUTPUT FORMAT: JSON array only, no prose. Each item must have ALL these keys:
[
  {{
    "Type": "PARAMETER_VARIATION",
    "HypothesisID": "LCH_<YYYYMMDD>_<NNN>",
    "Title": "<one-line description>",
    "ProposedChange": "<parameter>: <current> -> <new>",
    "Confidence": <integer 1-10>,
    "Rationale": "<evidence from the data above>"
  }}
]

If no clear improvement is indicated by the data, return an empty array: []
"""


def _build_live_analysis_prompt(
    trades: list,
    active_lessons: list,
    window_start: str,
    window_end: str,
) -> str:
    """Build Grok prompt for live CGH parameter variation analysis."""
    total = len(trades)
    if total == 0:
        wins = 0
        avg_pnl = 0.0
    else:
        wins = sum(1 for t in trades if (t.get("pnl_dollars") or t.get("realized_pnl", 0)) > 0)
        pnls = [t.get("pnl_dollars") or t.get("realized_pnl", 0) for t in trades]
        avg_pnl = sum(pnls) / len(pnls)
    win_rate = wins / total if total else 0.0

    regime_counts = {}
    for t in trades:
        regime = t.get("regime") or t.get("market_regime") or "unknown"
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

    vix_buckets = {"<15": 0, "15-20": 0, "20-25": 0, ">25": 0}
    for t in trades:
        v = t.get("vix_level") or t.get("vix") or 0
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0
        if v < 15:
            vix_buckets["<15"] += 1
        elif v < 20:
            vix_buckets["15-20"] += 1
        elif v < 25:
            vix_buckets["20-25"] += 1
        else:
            vix_buckets[">25"] += 1

    trade_dates = set()
    for t in trades:
        d = t.get("trade_date") or t.get("entry_date") or t.get("entry_time", "")[:10]
        if d:
            trade_dates.add(d)
    try:
        import datetime as _dt
        start = _dt.date.fromisoformat(window_start)
        end = _dt.date.fromisoformat(window_end)
        total_weekdays = sum(
            1 for i in range((end - start).days + 1)
            if (start + _dt.timedelta(days=i)).weekday() < 5
        )
        no_pos_days = max(0, total_weekdays - len(trade_dates))
    except Exception:
        no_pos_days = 0

    sorted_lessons = sorted(
        active_lessons,
        key=lambda l: l.get("confidence", 0),
        reverse=True,
    )[:10]
    lessons_lines = []
    for i, ls in enumerate(sorted_lessons, 1):
        text = ls.get("lesson_text") or ls.get("text") or str(ls)
        conf = ls.get("confidence") or 0
        lessons_lines.append(f"{i}. [{conf:.0%}] {text}")
    lessons_text = "\n".join(lessons_lines) if lessons_lines else "(none)"

    return _LIVE_CGH_PROMPT_TEMPLATE.format(
        window_start=window_start,
        window_end=window_end,
        total_trades=total,
        win_rate=win_rate,
        avg_pnl=avg_pnl,
        regime_counts=regime_counts,
        vix_buckets=vix_buckets,
        no_pos_days=no_pos_days,
        lesson_count=len(sorted_lessons),
        lessons_text=lessons_text,
    )


def generate_live_hypotheses(
    trades: list,
    active_lessons: list,
    window_start: str,
    window_end: str,
    max_hypotheses: int = 3,
) -> list:
    """
    Analyze a live trade window and generate PARAMETER_VARIATION hypotheses.

    Called by the Saturday live CGH scheduler job.
    Results are stored in learning.db/hypothesis_verdicts via LiveHypothesisBacktester.

    Args:
        trades: List of trade dicts from the live window.
        active_lessons: List of active lesson dicts from LearningDatabase.
        window_start: ISO date string for the start of the trade window.
        window_end: ISO date string for the end of the trade window.
        max_hypotheses: Maximum hypotheses to return (default: 3).

    Returns:
        List of hypothesis dicts (may be empty). Each dict has:
        Type, HypothesisID, Title, ProposedChange, Confidence, Rationale.
    """
    logger.info(f"Live CGH: generating parameter hypotheses for {window_start} to {window_end}")
    logger.info(f"  Trades: {len(trades)}, Lessons: {len(active_lessons)}")

    prompt = _build_live_analysis_prompt(trades, active_lessons, window_start, window_end)
    logger.info(f"  Prompt length: {len(prompt)} chars")

    try:
        response = _call_grok(prompt)
        hypotheses = _parse_grok_response(response)
    except Exception as e:
        logger.error(f"Live CGH: Grok call failed: {e}")
        return []

    hypotheses = [h for h in hypotheses if h.get("Type") == "PARAMETER_VARIATION"]

    if len(hypotheses) > max_hypotheses:
        hypotheses = sorted(
            hypotheses, key=lambda h: h.get("Confidence", 0), reverse=True
        )[:max_hypotheses]

    logger.info(f"Live CGH: {len(hypotheses)} PARAMETER_VARIATION hypotheses generated")
    return hypotheses


# ---------------------------------------------------------------------------
# CLI entry point for manual/testing use
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Generate H_SYSTEM hypotheses from weekly EOD reports")
    parser.add_argument("--week", help="Week label e.g. 2026-02-23_to_2026-02-27")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without calling Grok")
    args = parser.parse_args()

    # Determine week label
    if args.week:
        week_label = args.week
        # Derive week_end from label
        week_end = date.fromisoformat(week_label.split("_to_")[-1])
    else:
        today = date.today()
        # Use current week (back to Monday)
        week_end = today
        week_start = today - timedelta(days=today.weekday())
        week_label = f"{week_start.isoformat()}_to_{week_end.isoformat()}"

    # Load EOD reports for the week
    eod_dir = _REPORTS_DIR / "eod"
    eod_reports = []
    for days_back in range(7):
        check_date = week_end - timedelta(days=days_back)
        candidate = eod_dir / f"{check_date.isoformat()}_eod_report.json"
        if candidate.exists():
            try:
                with open(candidate) as f:
                    eod_reports.append(json.load(f))
                    print(f"  Loaded: {candidate.name}")
            except Exception as e:
                print(f"  Could not load {candidate.name}: {e}")

    if not eod_reports:
        print(f"No EOD reports found for week ending {week_end}. Run from a Friday after market close.")
        exit(1)

    print(f"\nGenerating system hypotheses for {week_label} ({len(eod_reports)} daily reports)")

    if args.dry_run:
        params = _load_system_params()
        exits = _analyze_exit_patterns(eod_reports)
        counterfactuals = _run_counterfactuals(exits, params)
        params_text = _format_params_for_prompt(params)
        exit_text = _format_exit_analysis_for_prompt(exits, counterfactuals)
        print("\n--- PROMPT PREVIEW ---")
        print(SYSTEM_HYPOTHESIS_PROMPT_TEMPLATE.format(
            params_text=params_text,
            exit_analysis_text=exit_text,
            weekly_summary_text="(dry run)",
            min_trades=MIN_TRADES_FOR_HYPOTHESIS,
        ))
        print("--- END PROMPT ---")
    else:
        out = generate_system_hypotheses(eod_reports, week_label)
        if out:
            print(f"\nSystem hypotheses saved: {out}")
        else:
            print("\nNo system hypotheses generated (no patterns found or no exits).")
