"""
GrokEODLessonsLearner v2 - Multi-prompt context-building for rich EOD analysis.

Instead of one giant prompt, uses 4-step conversation flow with Grok to build context:
1. Market regime analysis (VIX, SPY/QQQ, volumes, sector flows)
2. Active lessons relevance ranking (which lessons should matter today?)
3. Trade data analysis (each trade through lens of active lessons)
4. Final EOD synthesis (new lessons, lesson assessment, gap analysis)

This leverages Grok's reasoning capability to connect complex patterns across
market conditions, lesson history, and trade outcomes.

Usage:
    learner = GrokEODLessonsLearnerV2()
    result = learner.analyze_eod(max_retries=2)
    
    result = {
        'market_regime': {...},
        'active_lessons_today': [...],
        'new_lessons': [...],
        'lessons_to_reassess': [...],
        'gaps_identified': [...],
        'meta_insights': str,
        'reasoning_summary': str,
    }
"""

import json
import sqlite3
import logging
from datetime import datetime, date
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class GrokEODLessonsLearnerV2:
    """
    Multi-prompt EOD analysis using Grok 4.20 for rich lesson generation.
    
    Conversation flow:
    1. Establish market context (regime, volatility, flows)
    2. Discuss active lessons and their relevance today
    3. Analyze today's trades in context
    4. Generate new lessons and assessments
    """

    def __init__(
        self,
        trading_db: str = "ai_trader/ai_trader_data/trading_performance.db",
        learning_db: str = "ai_trader/ai_trader_data/learning.db",
        headless: bool = False,
        minimized: bool = True,
        timeout: int = 180,  # Longer for multi-step reasoning
    ):
        """Initialize learner with database paths and Grok client."""
        self.trading_db = trading_db
        self.learning_db = learning_db
        self.timeout = timeout

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

        self.conversation_history = []  # Track multi-turn conversation

    def _add_to_history(self, role: str, content: str) -> None:
        """Add message to conversation history (for logging/analysis)."""
        self.conversation_history.append({"role": role, "content": content})

    def _get_market_data(self) -> Dict[str, Any]:
        """Fetch market context for today (VIX, SPY/QQQ changes, etc)."""
        # In a real system, would fetch from scheduler's market data
        # For now, return structure that Grok expects
        return {
            "date": date.today().isoformat(),
            "vix": "~19.5",  # From scheduler logs
            "spy_change_pct": "-0.18%",
            "qqq_change_pct": "-0.20%",
            "regime": "neutral",
            "volatility_level": "medium",
            "sector_trends": "tech mixed, consumer stable",
            "note": "Fetch from scheduler's market regime analysis",
        }

    def _get_active_lessons(self) -> List[Dict[str, Any]]:
        """Load active lessons from learning.db (sample of 5-10 most relevant)."""
        try:
            conn = sqlite3.connect(self.learning_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT lesson_hash, lesson_text, category, confidence, 
                       times_validated, times_contradicted
                FROM lessons
                WHERE times_validated > times_contradicted
                ORDER BY confidence DESC
                LIMIT 10
                """
            )

            lessons = []
            for row in cursor.fetchall():
                lessons.append(
                    {
                        "hash": row["lesson_hash"],
                        "text": row["lesson_text"],
                        "category": row["category"],
                        "confidence": row["confidence"],
                        "times_validated": row["times_validated"],
                        "times_contradicted": row["times_contradicted"],
                    }
                )

            cursor.close()
            conn.close()
            return lessons
        except Exception as e:
            logger.warning(f"Could not load active lessons: {e}")
            return []

    def _get_today_trades(self) -> List[Dict[str, Any]]:
        """Load today's completed trades with full context."""
        try:
            conn = sqlite3.connect(self.trading_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            today_str = date.today().isoformat()

            cursor.execute(
                """
                SELECT trade_id, symbol, entry_time, exit_time, entry_price, 
                       exit_price, quantity, realized_pnl, realized_pnl_percent,
                       exit_reason, strategy, stop_price, target_price
                FROM trades
                WHERE DATE(entry_time) = ?
                ORDER BY entry_time
                """,
                (today_str,),
            )

            trades = []
            for row in cursor.fetchall():
                trades.append(
                    {
                        "symbol": row["symbol"],
                        "entry_time": row["entry_time"],
                        "exit_time": row["exit_time"],
                        "entry_price": row["entry_price"],
                        "exit_price": row["exit_price"],
                        "quantity": row["quantity"],
                        "pnl": row["realized_pnl"],
                        "pnl_pct": row["realized_pnl_percent"],
                        "exit_reason": row["exit_reason"],
                        "strategy": row["strategy"],
                        "stop_price": row["stop_price"],
                        "target_price": row["target_price"],
                    }
                )

            cursor.close()
            conn.close()
            return trades
        except Exception as e:
            logger.warning(f"Could not load trades: {e}")
            return []

    def _prompt_1_market_context(self) -> str:
        """
        Prompt 1: Establish market regime and context.
        Grok analyzes today's market conditions.
        """
        market_data = self._get_market_data()

        prompt = f"""# Market Context Analysis - {market_data['date']}

Please analyze today's market environment:

```json
{{
  "vix": "{market_data['vix']}",
  "spy_change": "{market_data['spy_change_pct']}",
  "qqq_change": "{market_data['qqq_change_pct']}",
  "regime": "{market_data['regime']}",
  "volatility": "{market_data['volatility_level']}",
  "sector_trends": "{market_data['sector_trends']}"
}}
```

Provide a brief assessment:
1. **Regime confirmation**: Is this truly neutral/bullish/bearish?
2. **Volatility implications**: How does VIX={market_data['vix']} affect trade risk/reward?
3. **Sector dynamics**: What sector trends matter for today's trades?
4. **Entry window impact**: Does this regime affect optimal entry timing?

Be concise but insightful."""

        logger.info("[GrokEOD] Prompt 1: Asking Grok about market context...")
        response = self.client.ask(prompt, max_wait=self.timeout, new_chat=True, close_after=False)
        self._add_to_history("user", prompt)
        self._add_to_history("assistant", response)

        logger.info(f"[GrokEOD] Market context: {response[:200]}...")
        return response

    def _prompt_2_lessons_relevance(self, market_context: str) -> str:
        """
        Prompt 2: Evaluate which active lessons are most relevant today.
        Uses market context from Prompt 1 to assess lesson applicability.
        """
        active_lessons = self._get_active_lessons()

        if not active_lessons:
            return "No active lessons to evaluate."

        lessons_json = json.dumps(active_lessons[:5], indent=2)  # Top 5

        prompt = f"""# Active Lessons Relevance Assessment

Given today's market context (from above), evaluate which of our active lessons 
should have been most relevant:

```json
{lessons_json}
```

For each lesson, answer:
1. **Should this lesson have applied today?** (yes/no/partially)
2. **Why or why not?** (regime mismatch, catalyst missing, etc.)
3. **Confidence in answer**: 0.0-1.0

Then provide:
- **Most relevant lessons today**: (which 2-3 should have guided trades)
- **Surprisingly irrelevant lessons**: (any lessons we expected to matter but didn't)
- **Lesson gaps**: (what lessons are MISSING for today's conditions?)"""

        logger.info("[GrokEOD] Prompt 2: Evaluating active lessons relevance...")
        response = self.client.ask(prompt, max_wait=self.timeout, new_chat=False, close_after=False)
        self._add_to_history("user", prompt)
        self._add_to_history("assistant", response)

        logger.info(f"[GrokEOD] Lessons relevance: {response[:200]}...")
        return response

    def _prompt_3_trade_analysis(self, lessons_context: str) -> str:
        """
        Prompt 3: Analyze today's trades through the lens of relevant lessons.
        """
        trades = self._get_today_trades()

        if not trades:
            return "No trades to analyze today."

        trades_json = json.dumps(trades, indent=2, default=str)

        prompt = f"""# Trade-by-Trade Analysis

Here are today's trades:

```json
{trades_json}
```

For each trade, analyze:
1. **Which active lessons applied to this entry?** (Did we follow our own wisdom?)
2. **Was the lesson followed correctly?** (Good execution, poor execution, or lesson was wrong?)
3. **Did the exit align with lesson guidance?** (hit_target, stop_hit, time_exit - was it right?)
4. **What lesson contradiction (if any)?** (Did this trade violate any active lesson?)

Then provide overall assessment:
- **Win rate vs lesson alignment**: Did trades that followed lessons win more?
- **Biggest lesson-trade mismatch**: Where did we deviate from our own lessons?
- **Validation/contradiction status**: Which lessons got validated today? Contradicted?"""

        logger.info("[GrokEOD] Prompt 3: Analyzing trades through lessons lens...")
        response = self.client.ask(prompt, max_wait=self.timeout, new_chat=False, close_after=False)
        self._add_to_history("user", prompt)
        self._add_to_history("assistant", response)

        logger.info(f"[GrokEOD] Trade analysis: {response[:200]}...")
        return response

    def _prompt_4_final_synthesis(self) -> str:
        """
        Prompt 4: Final synthesis - generate new lessons, assess old ones.
        Uses full conversation history as context.
        """
        prompt = """# Final EOD Synthesis - Return ONLY the JSON below, no commentary.

Using all above context (market regime, lessons relevance, trade analysis), produce this EXACT JSON structure:

```json
{
  "new_lessons": [
    {
      "lesson_text": "Specific actionable rule (1-2 sentences)",
      "category": "entry_timing",
      "confidence": 0.75,
      "reasoning": "Why this matters based on today (1 sentence)",
      "trade_examples": ["AAPL", "NVDA"]
    }
  ],
  "lessons_to_validate": ["lesson_001", "lesson_005"],
  "lessons_to_contradict": ["lesson_002"],
  "lessons_to_refine": [
    {
      "lesson_hash": "lesson_003",
      "refined_text": "Updated rule text incorporating today's data",
      "reason": "Original was too broad - here is why"
    }
  ],
  "gaps_identified": [
    "No lessons for neutral VIX 18-22 regimes",
    "Missing guidance on pre-market vs post-open entry timing"
  ],
  "meta_insight": "2-3 sentence summary of the biggest pattern from today."
}
```

Rules:
- category must be one of: entry_timing, exit_timing, symbol_behavior, avoid_entry, market_context
- confidence must be a float between 0.50 and 0.95
- lesson_hash values in lessons_to_validate/contradict/refine must match the hashes provided in Prompt 2
- Return ONLY the JSON block above - no explanation before or after"""

        logger.info("[GrokEOD] Prompt 4: Generating lessons and assessments...")
        response = self.client.ask(prompt, max_wait=self.timeout, new_chat=False, close_after=True)
        self._add_to_history("user", prompt)
        self._add_to_history("assistant", response)

        logger.info(f"[GrokEOD] Final synthesis: {response[:200]}...")
        return response

    def analyze_eod(self, max_retries: int = 2) -> Dict[str, Any]:
        """
        Execute full 4-prompt EOD analysis flow.

        Returns:
            Dictionary with market_regime, lessons_analysis, new_lessons, etc.
        """
        logger.info("[GrokEOD] Starting multi-prompt EOD analysis...")

        try:
            # Prompt 1: Market context
            market_context = self._prompt_1_market_context()

            # Prompt 2: Lessons relevance
            lessons_context = self._prompt_2_lessons_relevance(market_context)

            # Prompt 3: Trade analysis
            trade_analysis = self._prompt_3_trade_analysis(lessons_context)

            # Prompt 4: Final synthesis
            final_synthesis = self._prompt_4_final_synthesis()

            # Parse JSON from final synthesis - handle multiple Grok output formats
            import re

            synthesis_data = None

            # Try 1: markdown json fence  ```json {...} ```
            fence_match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```", final_synthesis, re.DOTALL | re.IGNORECASE
            )
            if fence_match:
                try:
                    synthesis_data = json.loads(fence_match.group(1))
                    logger.debug("[GrokEOD] Parsed synthesis from markdown fence")
                except json.JSONDecodeError:
                    pass

            # Try 2: Grok "JSON\nCopy\n{...}" or bare JSON object
            if synthesis_data is None:
                brace_match = re.search(r"(\{.*\})", final_synthesis, re.DOTALL)
                if brace_match:
                    try:
                        synthesis_data = json.loads(brace_match.group(1))
                        logger.debug("[GrokEOD] Parsed synthesis from bare JSON object")
                    except json.JSONDecodeError:
                        pass

            # Fallback: store raw text
            if synthesis_data is None:
                logger.warning("[GrokEOD] Could not parse synthesis JSON - storing raw response")
                synthesis_data = {"raw_response": final_synthesis}

            result = {
                "date": date.today().isoformat(),
                "market_context": market_context,
                "lessons_relevance": lessons_context,
                "trade_analysis": trade_analysis,
                "synthesis": synthesis_data,
                "conversation_turns": len(self.conversation_history),
            }

            logger.info("[GrokEOD] Multi-prompt analysis complete")
            return result

        except Exception as e:
            logger.error(f"[GrokEOD] Analysis failed: {e}")
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

    learner = GrokEODLessonsLearnerV2()
    try:
        result = learner.analyze_eod(max_retries=2)

        print("\n" + "=" * 70)
        print("EOD ANALYSIS COMPLETE")
        print("=" * 70)
        print(f"\nMarket Context:\n{result['market_context'][:300]}...")
        print(f"\nLessons Relevance:\n{result['lessons_relevance'][:300]}...")
        print(f"\nTrade Analysis:\n{result['trade_analysis'][:300]}...")
        print(f"\nFinal Synthesis:\n{json.dumps(result['synthesis'], indent=2)[:500]}...")
        print(f"\nConversation turns: {result['conversation_turns']}")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        learner.close()
