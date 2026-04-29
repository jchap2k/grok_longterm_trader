"""
SwingNewsWatcher - Proactive news monitoring for open swing positions.

Fetches news via Perplexity Sonar, evaluates impact via the trading agent LLM,
and routes decisions: EXIT / FLAG / HOLD / BOOST.

Features:
- Parallel per-symbol fetch+eval via threading.Thread
- 2-hour rolling cache per symbol (avoids redundant Perplexity calls)
- Daily call limit: max 3 checks per symbol per day
- Friday escalation: FLAG -> EXIT on Fridays (weekend risk management)
- All evaluations logged to learning.db via log_news_check_event()
- Past EXIT lessons injected into evaluation prompt

Called by automated_scheduler at 3 windows per day:
  05:45 AM PT (pre-market), 09:00 AM PT (open), 12:15 PM PT (midday)
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_POSITIONS_PER_PASS = 5
MAX_DAILY_CALLS_PER_SYMBOL = 3
EXIT_CONFIDENCE_THRESHOLD = 7   # >= 7 -> EXIT
FLAG_CONFIDENCE_THRESHOLD = 5   # >= 5 -> FLAG, else HOLD
CACHE_TTL_SECONDS = 7200        # 2h rolling cache per symbol

# ---------------------------------------------------------------------------
# Prompt template (plain ASCII only)
# ---------------------------------------------------------------------------

EVALUATION_PROMPT_TEMPLATE = """You are evaluating breaking news for an open swing trade position.

POSITION CONTEXT:
  Symbol: {symbol}
  Entry Price: ${entry_price:.2f}
  Current Stop: ${current_stop:.2f}
  Shares: {shares}
  Days Held: {days_held}
  Hold Type: {hold_type}
  Unrealized P&L: ${unrealized_pnl:+.2f}

NEWS FOUND (last {lookback_hours}h):
{news_text}

PAST NEWS EXIT LESSONS FOR {symbol}:
{past_lessons}

CONVICTION RUBRIC (from active_rules.txt):
- EXIT signals: CEO fraud, product recall, FDA rejection, earnings miss >15%, SEC investigation
- FLAG signals: analyst downgrade, minor litigation, sector rotation news
- HOLD signals: macro noise, unconfirmed rumors, earnings beat context
- BOOST signals: FDA approval, major contract win, index inclusion

Respond with ONLY this JSON (no markdown):
{{
  "action": "EXIT|FLAG|HOLD|BOOST",
  "confidence": <1-10>,
  "reason": "<concise 1-sentence reason>",
  "urgency": "immediate|next_session|none",
  "news_category": "<one of: FDA_CATALYST|LEGAL_RISK|ANALYST_ACTION|EARNINGS|MACRO|SECTOR|OTHER>"
}}

Rules:
- confidence >= {exit_threshold} required to EXIT
- confidence >= {flag_threshold} required to FLAG (else HOLD)
- If news is ambiguous or could cut both ways, return HOLD
- If news is clearly positive for the position, return BOOST"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class NewsCheckResult:
    symbol: str
    action: str          # EXIT | FLAG | HOLD | BOOST | DAILY_LIMIT | NO_NEWS
    confidence: int = 0
    reason: str = ""
    news_category: str = ""
    urgency: str = "none"
    trade_id: str = ""
    news_summary: str = ""
    raw_response: str = ""


# ---------------------------------------------------------------------------
# SwingNewsWatcher
# ---------------------------------------------------------------------------

class SwingNewsWatcher:
    """
    Monitors open swing positions for adverse news.

    Injected dependencies:
      agent              - has send_message(prompt: str) -> str
      perplexity_provider - PerplexityProvider instance (or None)
      position_manager   - has get_swing_metadata(symbol) -> dict
      learning_db        - LearningDatabase instance
      logger             - optional logger override
    """

    def __init__(self, agent, perplexity_provider, position_manager,
                 learning_db, logger=None):
        self.agent = agent
        self.perplexity_provider = perplexity_provider
        self.position_manager = position_manager
        self.learning_db = learning_db
        self._logger = logger or logging.getLogger(__name__)

        # Cache: symbol:lookback_hours -> (timestamp, news_items)
        self._cache = {}
        self._cache_lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def check_open_positions(self, positions: dict, lookback_hours: int,
                              window: str) -> List[NewsCheckResult]:
        """
        Evaluate all open positions for news, in parallel threads.

        Args:
            positions: dict of {symbol: {unrealized_pnl, trade_id, qty, ...}}
            lookback_hours: how many hours back to search for news
            window: time window label (e.g. "9:00AM") for logging

        Returns:
            List of NewsCheckResult sorted by priority (most negative P&L first)
        """
        if not positions:
            return []

        # Sort by unrealized_pnl ascending (most negative = highest priority)
        sorted_items = sorted(
            positions.items(),
            key=lambda kv: float(kv[1].get("unrealized_pnl", 0))
        )

        # Cap at MAX_POSITIONS_PER_PASS
        to_check = sorted_items[:MAX_POSITIONS_PER_PASS]

        results = [None] * len(to_check)
        threads = []

        def _worker(idx, symbol, pos_ctx):
            try:
                results[idx] = self._evaluate_single_position(
                    symbol, pos_ctx, lookback_hours, window
                )
            except Exception as exc:
                self._logger.warning(
                    "SwingNewsWatcher: unhandled error for %s: %s", symbol, exc
                )
                results[idx] = NewsCheckResult(
                    symbol=symbol,
                    action="HOLD",
                    reason=f"evaluation error: {exc}",
                    trade_id=pos_ctx.get("trade_id", ""),
                )

        for idx, (symbol, pos_ctx) in enumerate(to_check):
            t = threading.Thread(
                target=_worker,
                args=(idx, symbol, pos_ctx),
                daemon=True,
            )
            t._news_symbol = symbol  # store for timeout logging
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        for t in threads:
            if t.is_alive():
                self._logger.warning(
                    "SwingNewsWatcher: thread timeout (>30s) for symbol %s - result dropped",
                    getattr(t, "_news_symbol", "unknown")
                )

        # Filter out any slots that timed out (still None)
        final = [r for r in results if r is not None]
        self._logger.info(
            "SwingNewsWatcher.check_open_positions: %d/%d evaluated",
            len(final), len(to_check)
        )
        return final

    # -----------------------------------------------------------------------
    # Core evaluation
    # -----------------------------------------------------------------------

    def _evaluate_single_position(self, symbol: str, pos_ctx: dict,
                                   lookback_hours: int, window: str) -> NewsCheckResult:
        """
        Full news evaluation pipeline for one position.

        Steps:
        1. Daily limit check
        2. Fetch news (with cache)
        3. Build past-lesson context
        4. LLM evaluation
        5. Confidence-threshold enforcement
        6. Friday escalation
        7. DB logging
        """
        trade_id = pos_ctx.get("trade_id", "") or f"{symbol}-unknown"

        # Step 1: Daily limit guard
        try:
            count_today = self.learning_db.get_news_check_count_today(symbol)
        except Exception as exc:
            self._logger.warning("get_news_check_count_today failed for %s: %s", symbol, exc)
            count_today = 0

        if count_today >= MAX_DAILY_CALLS_PER_SYMBOL:
            self._log_event(
                trade_id=trade_id,
                symbol=symbol,
                window=window,
                lookback_hours=lookback_hours,
                news_found=False,
                action="DAILY_LIMIT",
                daily_call_count=count_today,
            )
            return NewsCheckResult(
                symbol=symbol,
                action="HOLD",
                reason="daily limit reached",
                trade_id=trade_id,
            )

        # Step 2: Fetch news
        news_items = self._fetch_news(symbol, lookback_hours)
        if not news_items:
            self._log_event(
                trade_id=trade_id,
                symbol=symbol,
                window=window,
                lookback_hours=lookback_hours,
                news_found=False,
                action="HOLD",
                reason="no news found",
                daily_call_count=count_today + 1,
            )
            return NewsCheckResult(
                symbol=symbol,
                action="HOLD",
                reason="no news found",
                trade_id=trade_id,
            )

        # Step 3: Gather position metadata
        meta = {}
        try:
            meta = self.position_manager.get_swing_metadata(symbol) or {}
        except Exception as exc:
            self._logger.warning("get_swing_metadata failed for %s: %s", symbol, exc)

        entry_price = float(meta.get("entry_price", 0.0))
        current_stop = float(meta.get("current_stop", 0.0))
        shares = int(meta.get("shares_remaining", pos_ctx.get("qty", 0)))
        entry_date = meta.get("entry_date", "")
        hold_type = meta.get("hold_type", "swing")
        unrealized_pnl = float(pos_ctx.get("unrealized_pnl", 0.0))
        days_held = self._calc_days_held(entry_date)

        # Step 4: Past lessons
        past_lessons = self._get_past_news_lessons(symbol)

        # Step 5: Format news and build prompt
        news_text = self._format_news_items(news_items)
        news_summary_for_log = news_text[:500]

        prompt = EVALUATION_PROMPT_TEMPLATE.format(
            symbol=symbol,
            entry_price=entry_price,
            current_stop=current_stop,
            shares=shares,
            days_held=days_held,
            hold_type=hold_type,
            unrealized_pnl=unrealized_pnl,
            lookback_hours=lookback_hours,
            news_text=news_text,
            past_lessons=past_lessons,
            exit_threshold=EXIT_CONFIDENCE_THRESHOLD,
            flag_threshold=FLAG_CONFIDENCE_THRESHOLD,
        )

        # Step 6: LLM call
        raw_response = ""
        try:
            raw_response = self.agent.send_message(prompt)
        except Exception as exc:
            self._logger.warning("agent.send_message failed for %s: %s", symbol, exc)
            self._log_event(
                trade_id=trade_id, symbol=symbol, window=window,
                lookback_hours=lookback_hours, news_found=True,
                action="HOLD", reason=f"agent error: {exc}",
                news_summary=news_summary_for_log,
                daily_call_count=count_today + 1,
            )
            return NewsCheckResult(
                symbol=symbol,
                action="HOLD",
                reason=f"agent error: {exc}",
                trade_id=trade_id,
                news_summary=news_summary_for_log,
            )

        # Step 7: Parse response
        parsed = self._parse_agent_response(raw_response)
        action = parsed.get("action", "HOLD").upper()
        try:
            confidence = max(0, min(10, int(parsed.get("confidence", 0))))
        except (ValueError, TypeError):
            confidence = 0
        reason = parsed.get("reason", "")
        urgency = parsed.get("urgency", "none")
        news_category = parsed.get("news_category", "OTHER")

        # Step 8: Confidence threshold enforcement
        if action == "EXIT" and confidence < EXIT_CONFIDENCE_THRESHOLD:
            action = "FLAG"
        if action == "FLAG" and confidence < FLAG_CONFIDENCE_THRESHOLD:
            action = "HOLD"

        result = NewsCheckResult(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason=reason,
            news_category=news_category,
            urgency=urgency,
            trade_id=trade_id,
            news_summary=news_summary_for_log,
            raw_response=raw_response,
        )

        # Step 9: Friday escalation
        result = self._apply_friday_escalation(result)

        # Step 10: Log to DB
        self._log_event(
            trade_id=trade_id,
            symbol=symbol,
            window=window,
            lookback_hours=lookback_hours,
            news_found=True,
            action=result.action,
            confidence=result.confidence,
            reason=result.reason,
            news_category=result.news_category,
            urgency=result.urgency,
            news_summary=news_summary_for_log,
            daily_call_count=count_today + 1,
        )

        return result

    # -----------------------------------------------------------------------
    # News fetching
    # -----------------------------------------------------------------------

    def _fetch_news(self, symbol: str, lookback_hours: int) -> list:
        """
        Fetch news for symbol via Perplexity Sonar.

        Returns a list of dicts with "title" and "snippet" keys.
        Returns [] on any failure.

        Cache: results are cached for CACHE_TTL_SECONDS per symbol+lookback pair.
        """
        cache_key = f"{symbol}:{lookback_hours}"

        # Check cache
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                ts, items = cached
                if (time.time() - ts) < CACHE_TTL_SECONDS:
                    self._logger.debug(
                        "_fetch_news: cache hit for %s (age=%.0fs)",
                        cache_key, time.time() - ts
                    )
                    return items

        if self.perplexity_provider is None:
            self._logger.warning("_fetch_news: no perplexity_provider configured")
            return []

        try:
            # Use get_catalyst_summary - the standard PerplexityProvider method.
            # Pass a minimal candidate dict so the prompt focuses on recent news.
            candidate = {"change_pct": 0.0, "rel_volume": 1.0, "volume": 0}
            result = self.perplexity_provider.get_catalyst_summary(symbol, candidate=candidate)

            summary = result.get("summary") if result else None
            if not summary:
                # No catalysts found - cache the empty result
                with self._cache_lock:
                    # Re-check: another thread may have populated it while we were fetching
                    cached = self._cache.get(cache_key)
                    if cached is not None:
                        ts, existing_items = cached
                        if (time.time() - ts) < CACHE_TTL_SECONDS:
                            return existing_items
                    self._cache[cache_key] = (time.time(), [])
                return []

            # Convert summary string to a single news item list
            # so downstream _format_news_items works uniformly
            sentiment = result.get("sentiment", "neutral")
            items = [
                {
                    "title": f"{symbol} catalyst ({sentiment})",
                    "snippet": summary,
                }
            ]

            with self._cache_lock:
                # Re-check: another thread may have populated it while we were fetching
                cached = self._cache.get(cache_key)
                if cached is not None:
                    ts, existing_items = cached
                    if (time.time() - ts) < CACHE_TTL_SECONDS:
                        return existing_items
                self._cache[cache_key] = (time.time(), items)

            return items

        except Exception as exc:
            self._logger.warning("_fetch_news(%s): %s", symbol, exc)
            return []

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _format_news_items(self, news_items: list) -> str:
        """
        Format news items as a numbered list for prompt injection.

        Takes first 5 items. Each formatted as:
          1. {title}
             {snippet[:200]}
        """
        lines = []
        for i, item in enumerate(news_items[:5], start=1):
            title = item.get("title", "")
            snippet = item.get("snippet", item.get("content", ""))[:200]
            lines.append(f"{i}. {title}\n   {snippet}")
        return "\n".join(lines) if lines else "(none)"

    def _parse_agent_response(self, raw: str) -> dict:
        """
        Parse JSON from agent response.

        Strips markdown fences if present, then json.loads().
        Returns dict with keys: action, confidence, reason, urgency, news_category.
        Returns safe defaults on any parse failure.
        """
        text = raw.strip()

        # Strip markdown fences: find first { and last }
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                text = text[start:end + 1]

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError) as exc:
            self._logger.warning("_parse_agent_response: JSON parse failed: %s | raw: %.200s", exc, raw)

        return {
            "action": "HOLD",
            "confidence": 0,
            "reason": "parse error - defaulting to HOLD",
            "urgency": "none",
            "news_category": "OTHER",
        }

    def _apply_friday_escalation(self, result: NewsCheckResult) -> NewsCheckResult:
        """
        On Fridays, escalate FLAG -> EXIT to avoid weekend headline risk.

        Modifies result in place and returns it.
        """
        if datetime.now().weekday() == 4 and result.action == "FLAG":
            result.action = "EXIT"
            result.reason = "friday escalation: " + result.reason
            result.urgency = "immediate"
        return result

    def _get_past_news_lessons(self, symbol: str) -> str:
        """
        Retrieve last 3 EXIT events for this symbol from learning_db.

        Returns formatted string for prompt injection.
        """
        try:
            events = self.learning_db.get_news_check_events(symbol=symbol)
        except Exception as exc:
            self._logger.warning("get_news_check_events failed for %s: %s", symbol, exc)
            return "(none)"

        # Filter for EXIT actions only
        exit_events = [
            e for e in events
            if (e.get("agent_action") or "").upper() == "EXIT"
        ]

        if not exit_events:
            return "(none)"

        lines = []
        for evt in exit_events[:3]:
            date = evt.get("check_date", "")
            reason = evt.get("agent_reason", "")
            category = evt.get("news_category", "")
            confidence = evt.get("agent_confidence", "")
            lines.append(
                f"- [{date}] EXIT (conf={confidence}, cat={category}): {reason}"
            )

        return "\n".join(lines)

    def _calc_days_held(self, entry_date_str: str) -> int:
        """
        Calculate business days held from entry_date_str (YYYY-MM-DD) to today.

        Uses pandas bdate_range. Entry day does not count (returns len - 1).
        Returns 0 on any error.
        """
        try:
            entry = pd.Timestamp(entry_date_str)
            today = pd.Timestamp(datetime.now().date())
            bdays = pd.bdate_range(entry, today)
            return max(len(bdays) - 1, 0)
        except Exception as exc:
            self._logger.debug("_calc_days_held(%s): %s", entry_date_str, exc)
            return 0

    def _log_event(self, trade_id: str, symbol: str, window: str,
                   lookback_hours: int, news_found: bool, action: str,
                   confidence: int = 0, reason: str = "",
                   news_category: str = None, urgency: str = None,
                   news_summary: str = None, daily_call_count: int = 1):
        """
        Write a news check event to learning_db.

        Uses trade_id fallback to prevent ValueError from DB layer.
        Swallows all exceptions - logging must not block trade execution.
        """
        safe_trade_id = trade_id or f"{symbol}-unknown"
        try:
            self.learning_db.log_news_check_event(
                trade_id=safe_trade_id,
                symbol=symbol,
                window=window,
                lookback_hours=lookback_hours,
                news_found=news_found,
                news_summary=news_summary,
                agent_action=action,
                agent_confidence=confidence if confidence else None,
                agent_reason=reason or None,
                news_category=news_category,
                urgency=urgency,
                daily_call_count=daily_call_count,
            )
        except Exception as exc:
            self._logger.warning(
                "_log_event failed for %s (trade_id=%s): %s",
                symbol, safe_trade_id, exc
            )
