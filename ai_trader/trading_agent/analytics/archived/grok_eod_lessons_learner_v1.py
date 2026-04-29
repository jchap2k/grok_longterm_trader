"""
GrokEODLessonsLearner - Daily "lessons learned" review using Grok 4.20 browser automation.

Analyzes completed trades at end-of-day and generates actionable lessons using Grok,
with automatic retry logic perfect for overnight cron jobs or scheduled tasks.

Usage:
    learner = GrokEODLessonsLearner()
    lessons = learner.review_today_trades()
    for lesson in lessons:
        print(f"- {lesson['lesson_text']} (confidence: {lesson['confidence']})")
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, date
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Daily trade from trading_performance.db."""

    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    conviction: float
    entry_rationale: str
    stop_price: Optional[float] = None
    target_price: Optional[float] = None


@dataclass
class TradeContext:
    """Trade context (quantitative + qualitative)."""

    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    conviction: float
    entry_rationale: str
    catalyst: Optional[str] = None
    market_context: Optional[str] = None
    confidence_level: Optional[str] = None


class GrokEODLessonsLearner:
    """
    Daily lessons learner using Grok 4.20 via browser automation.

    Reads today's trades from:
    - trading_performance.db (quantitative: entry/exit times, prices, P&L)
    - learning.db trade_journal (qualitative: catalyst, confidence, rationale)

    Sends to Grok with structured prompt requesting actionable lessons.
    Returns JSON lessons with: lesson_text, category, confidence, reasoning, symbols.
    """

    SYSTEM_PROMPT = """You are an elite trading coach analyzing today's trading performance.

Your job: Extract 3-5 actionable lessons from the trades shown.

Guidelines:
1. Focus on repeatable patterns (not random luck)
2. Separate WINNING lessons from AVOIDANCE lessons:
   - Winning: "Enter when X happens because Y worked today"
   - Avoidance: "Don't enter Y setup when X is happening"
3. Be brutally honest - if a trade went bad, extract the real lesson
4. Make lessons specific and testable (not "manage risk better")
5. For each lesson, cite the symbol(s) and rough context

Output ONLY valid JSON (no markdown, no explanation) with this structure:
{
    "lessons": [
        {
            "lesson_text": "Specific actionable rule",
            "category": "entry_timing|exit_timing|symbol_behavior|market_context|avoid_entry|avoid_timing",
            "confidence": 0.65,
            "reasoning": "Why this matters based on today",
            "symbols": ["AAPL", "MSFT"],
            "trade_count": 2
        }
    ],
    "daily_summary": "One-sentence theme of today's trading"
}"""

    def __init__(
        self,
        trading_db: str = "ai_trader/ai_trader_data/trading_performance.db",
        learning_db: str = "ai_trader/ai_trader_data/learning.db",
        headless: bool = False,
        minimized: bool = True,
        timeout: int = 120,
    ):
        """
        Initialize GrokEODLessonsLearner.

        Args:
            trading_db: Path to trading_performance.db
            learning_db: Path to learning.db (for trade_journal context)
            headless: Run Grok browser headless (not recommended)
            minimized: Start browser off-screen (recommended)
            timeout: Max seconds per Grok response
        """
        self.trading_db = trading_db
        self.learning_db = learning_db
        self.timeout = timeout

        # Import and initialize SafeGrokClient
        try:
            from .safe_grok_client import SafeGrokClient

            self.client = SafeGrokClient(
                headless=headless,
                minimized=minimized,
                timeout=timeout,
                max_retries=2,
            )
        except ImportError as e:
            raise ImportError(f"SafeGrokClient not found: {e}") from e

    def _get_today_trades(self) -> List[Trade]:
        """Load today's trades from trading_performance.db."""
        trades = []
        try:
            conn = sqlite3.connect(self.trading_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            today_str = date.today().strftime("%Y-%m-%d")

            # Get all trades from today
            cursor.execute(
                """
                SELECT 
                    symbol, entry_time, exit_time, entry_price, exit_price,
                    quantity, pnl, pnl_pct, exit_reason, conviction, entry_rationale,
                    stop_price, target_price
                FROM trades
                WHERE DATE(entry_time) = ?
                ORDER BY entry_time
                """,
                (today_str,),
            )

            for row in cursor.fetchall():
                trade = Trade(
                    symbol=row["symbol"],
                    entry_time=row["entry_time"],
                    exit_time=row["exit_time"],
                    entry_price=row["entry_price"],
                    exit_price=row["exit_price"],
                    quantity=row["quantity"],
                    pnl=row["pnl"],
                    pnl_pct=row["pnl_pct"],
                    exit_reason=row["exit_reason"],
                    conviction=row["conviction"],
                    entry_rationale=row["entry_rationale"],
                    stop_price=row["stop_price"],
                    target_price=row["target_price"],
                )
                trades.append(trade)

            cursor.close()
            conn.close()
            logger.info(f"[GrokLearner] Loaded {len(trades)} trades from {today_str}")
            return trades

        except Exception as e:
            logger.warning(f"[GrokLearner] Could not load trades: {e}")
            return []

    def _get_trade_journal_context(self, symbol: str, entry_time: str) -> dict:
        """Load qualitative context from learning.db trade_journal."""
        try:
            conn = sqlite3.connect(self.learning_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT catalyst, market_context, setup_type, confidence_level
                FROM trade_journal
                WHERE symbol = ? AND entry_time = ?
                LIMIT 1
                """,
                (symbol, entry_time),
            )

            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if row:
                return {
                    "catalyst": row["catalyst"],
                    "market_context": row["market_context"],
                    "setup_type": row["setup_type"],
                    "confidence_level": row["confidence_level"],
                }
            return {}

        except Exception as e:
            logger.debug(f"[GrokLearner] No journal context for {symbol}: {e}")
            return {}

    def _format_trades_for_grok(self, trades: List[Trade]) -> str:
        """Format trades into a readable prompt for Grok."""
        if not trades:
            return "No trades today."

        lines = [f"## Today's Trades ({len(trades)} total)\n"]

        # Summary stats
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl < 0]
        total_pnl = sum(t.pnl for t in trades)

        lines.append(f"**Summary**: {len(winners)}W / {len(losers)}L, Total P&L: ${total_pnl:+.2f}")
        lines.append("")

        # Detail each trade
        for i, trade in enumerate(trades, 1):
            lines.append(
                f"### Trade {i}: {trade.symbol}"
            )
            lines.append(f"- Entry: {trade.entry_time} @ ${trade.entry_price:.2f}")
            lines.append(f"- Exit: {trade.exit_time} @ ${trade.exit_price:.2f}")
            lines.append(f"- P&L: ${trade.pnl:+.2f} ({trade.pnl_pct:+.1f}%)")
            lines.append(f"- Reason: {trade.exit_reason}")
            lines.append(f"- Conviction: {trade.conviction:.1f}/10")
            lines.append(f"- Entry idea: {trade.entry_rationale}")

            # Add journal context if available
            journal = self._get_trade_journal_context(trade.symbol, trade.entry_time)
            if journal:
                if journal.get("catalyst"):
                    lines.append(f"- Catalyst: {journal['catalyst']}")
                if journal.get("confidence_level"):
                    lines.append(f"- Setup confidence: {journal['confidence_level']}")

            lines.append("")

        return "\n".join(lines)

    def review_today_trades(self, max_retries: int = 2) -> List[dict]:
        """
        Analyze today's trades and generate lessons from Grok.

        Args:
            max_retries: Number of retries if Grok fails (default 2 = 3 attempts)

        Returns:
            List of lessons dicts with: lesson_text, category, confidence, reasoning, symbols, trade_count
        """
        logger.info("[GrokLearner] Starting EOD lessons review...")

        # Load trades
        trades = self._get_today_trades()
        if not trades:
            logger.warning("[GrokLearner] No trades today, skipping Grok review")
            return []

        # Format for Grok
        trades_text = self._format_trades_for_grok(trades)
        logger.debug(f"[GrokLearner] Trades formatted:\n{trades_text}")

        # Build prompt
        prompt = f"{self.SYSTEM_PROMPT}\n\n{trades_text}"

        # Ask Grok with retry
        logger.info("[GrokLearner] Sending to Grok with retry logic...")
        try:
            response_text = self.client.ask(
                prompt,
                max_retries=max_retries,
                max_wait=self.timeout,
            )
            logger.info(f"[GrokLearner] Got Grok response ({len(response_text)} chars)")

            # Parse JSON
            import re

            fence_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
            json_str = fence_match.group(1) if fence_match else response_text.strip()

            result = json.loads(json_str)
            lessons = result.get("lessons", [])

            logger.info(f"[GrokLearner] Extracted {len(lessons)} lessons")
            for lesson in lessons:
                logger.info(f"  - {lesson.get('lesson_text', '???')}")

            return lessons

        except Exception as e:
            logger.error(f"[GrokLearner] Failed to get Grok lessons: {e}")
            raise

    def close(self) -> None:
        """Close Grok browser and cleanup."""
        if self.client:
            self.client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


if __name__ == "__main__":
    # Example usage
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    learner = GrokEODLessonsLearner()
    try:
        lessons = learner.review_today_trades(max_retries=2)
        print("\n" + "=" * 70)
        print("LESSONS LEARNED TODAY")
        print("=" * 70)
        for lesson in lessons:
            print(f"\n{lesson.get('lesson_text')}")
            print(f"  Category: {lesson.get('category')}")
            print(f"  Confidence: {lesson.get('confidence'):.0%}")
            print(f"  Symbols: {', '.join(lesson.get('symbols', []))}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        learner.close()
