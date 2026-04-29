"""
GrokLessonBackend - EOD lesson generation using Grok via web membership.

Uses GrokWebClient (grok3api) to access Grok 4 through your grok.com
Premium+/SuperGrok subscription. No xAI API key needed, no per-token billing.

Interface matches ClaudeCodeLessonBackend so they are drop-in interchangeable.

Usage:
    backend = GrokLessonBackend()                    # grok-4 auto, falls back to grok-3
    backend = GrokLessonBackend(model="grok-3")      # force grok-3 (always available)
    lessons = backend.generate_lessons(simulation_results)

Requires:
    pip install grok3api setuptools
    Chrome installed and grok.com session active (Chrome opens on first call)
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

from ai_trader.trading_agent.analytics.grok_web_client import (
    GrokWebClient,
    GrokWebClientError,
)

logger = logging.getLogger(__name__)


class GrokLessonBackend:
    """
    Generate trading lessons via Grok web access (membership-based).

    Drop-in replacement for ClaudeCodeLessonBackend. Both classes expose
    a single generate_lessons(simulation_results) -> List[Dict] method.
    """

    def __init__(self, model: Optional[str] = None, timeout: int = 120):
        """
        Args:
            model: Grok model to use. None = auto (grok-4 -> grok-3 fallback).
                   Pass "grok-3" to skip grok-4 entirely (always available).
                   Pass "grok-4" to require grok-4 (raises on fallback failure).
            timeout: Chrome/response timeout in seconds.
        """
        self.model = model      # None = auto priority list in GrokWebClient
        self.timeout = timeout

    def generate_lessons(
        self, simulation_results: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Analyze EOD simulation results and extract trading lessons via Grok.

        Args:
            simulation_results: Dict with keys:
                - trades: list of trade dicts
                - win_rate: float (0-1)
                - avg_win: float ($)
                - avg_loss: float ($)
                - total_pnl: float ($)

        Returns:
            List of lesson dicts ready for learning_db:
            [
                {
                    "lesson_text": "specific actionable rule",
                    "category": "entry_timing|position_sizing|...",
                    "confidence": 0.85,
                    "evidence_summary": "why this works"
                },
                ...
            ]
        """
        prompt = self._build_prompt(simulation_results)
        num_trades = len(simulation_results.get("trades", []))

        logger.info(
            f"[GrokLessonBackend] Requesting lessons for {num_trades} trades "
            f"(model pref: {self.model or 'auto grok-4->grok-3'})"
        )

        try:
            client = GrokWebClient.get_instance(timeout=self.timeout)
            raw = client.ask(prompt, model=self.model)
            lessons = self._parse_lessons(raw)
            logger.info(f"[GrokLessonBackend] Generated {len(lessons)} lessons")
            return lessons

        except GrokWebClientError as exc:
            logger.error(f"[GrokLessonBackend] Grok web client failed: {exc}")
            return []
        except Exception as exc:
            logger.error(f"[GrokLessonBackend] Unexpected error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, results: Dict[str, Any]) -> str:
        trades_text = self._format_trades(results.get("trades", []))
        win_rate = results.get("win_rate", 0)
        avg_win = results.get("avg_win", 0)
        avg_loss = results.get("avg_loss", 0)
        total_pnl = results.get("total_pnl", 0)
        num_trades = len(results.get("trades", []))

        return f"""Analyze these EOD trading simulation results and extract 3-5 high-confidence lessons.

=== TODAY'S TRADES ===
{trades_text}

=== SUMMARY STATS ===
- Win Rate: {win_rate:.1%}
- Avg Win: ${avg_win:.2f}
- Avg Loss: ${avg_loss:.2f}
- Total P&L: ${total_pnl:.2f}
- Total Trades: {num_trades}

=== YOUR TASK ===
Return ONLY a JSON array - no markdown, no explanation, no code fences:

[
  {{
    "lesson_text": "specific, actionable trading rule derived from today",
    "category": "entry_timing|position_sizing|risk_management|exit_strategy|catalyst_quality|market_regime",
    "confidence": 0.85,
    "evidence_summary": "why this lesson works based on today's results"
  }}
]

Rules:
- Return only the JSON array, nothing else
- Each lesson must be grounded in the actual trade data above
- confidence range: 0.5-0.9 (not 0.99, not 1.0)
- Make lessons specific and actionable, not generic advice"""

    def _format_trades(self, trades: List[Dict[str, Any]]) -> str:
        if not trades:
            return "  (No trades today)"

        lines = []
        for i, t in enumerate(trades, 1):
            symbol = t.get("symbol", "?")
            entry = t.get("entry_price", 0)
            exit_p = t.get("exit_price", 0)
            pnl = t.get("pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            setup = t.get("setup_type", "unknown")
            conv = t.get("conviction", 0)
            lines.append(
                f"  {i}. {symbol:6} | entry ${entry:7.2f} -> exit ${exit_p:7.2f} | "
                f"PnL {pnl:+8.2f} ({pnl_pct:+6.2%}) | {setup:15} | conv {conv}"
            )
        return "\n".join(lines)

    def _parse_lessons(self, response: str) -> List[Dict[str, Any]]:
        # Strip markdown fences if model adds them despite instructions
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", response, re.DOTALL)
        json_str = fence_match.group(1) if fence_match else response.strip()

        try:
            lessons = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error(f"[GrokLessonBackend] JSON parse failed: {exc}")
            logger.debug(f"[GrokLessonBackend] Raw response: {response[:500]}")
            return []

        if not isinstance(lessons, list):
            lessons = [lessons]

        validated = []
        for lesson in lessons:
            if self._validate_lesson(lesson):
                validated.append(lesson)
            else:
                logger.warning(f"[GrokLessonBackend] Skipping invalid lesson: {lesson}")

        return validated

    def _validate_lesson(self, lesson: Dict[str, Any]) -> bool:
        required = ["lesson_text", "category", "confidence", "evidence_summary"]
        for field in required:
            if field not in lesson:
                logger.warning(f"[GrokLessonBackend] Missing field: {field}")
                return False

        try:
            conf = float(lesson["confidence"])
            if not (0 <= conf <= 1):
                logger.warning(f"[GrokLessonBackend] Confidence out of range: {conf}")
                return False
        except (ValueError, TypeError):
            logger.warning(
                f"[GrokLessonBackend] Invalid confidence: {lesson['confidence']}"
            )
            return False

        valid_categories = {
            "entry_timing", "position_sizing", "risk_management",
            "exit_strategy", "catalyst_quality", "market_regime",
            "general", "technical", "fundamental",
        }
        if lesson.get("category") not in valid_categories:
            logger.warning(
                f"[GrokLessonBackend] Unknown category: {lesson.get('category')} "
                "(still accepted)"
            )

        return True
