"""
Claude Code Lesson Backend for EOD Analysis

Uses the Claude CLI (via membership, not API key) to analyze EOD simulation
results and generate trading lessons. Non-interactive, uses `-p` / `--print` flag.

Usage:
    backend = ClaudeCodeLessonBackend(model='sonnet')
    lessons = backend.generate_lessons(simulation_results)
"""

import subprocess
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ClaudeCodeLessonBackend:
    """Generate lessons using Claude CLI (membership-based, not API key)"""

    def __init__(self, model: str = 'sonnet', fallback_to_grok: bool = True):
        """
        Initialize the Claude backend.

        Args:
            model: Claude model to use
                - 'sonnet' (recommended for weekly deep-dive analysis)
                - 'opus' (recommended for heavy reasoning)
                - 'haiku' (fast, always works)
                - 'claude-opus-4-6' (specific version)
            fallback_to_grok: If True, fall back to Grok when Claude hits rate limits
                             (usage exceeded, out of access, etc). Only for lessons_learned mode.
        """
        self.model = model
        self.timeout = 300  # 5 min timeout
        self.fallback_to_grok = fallback_to_grok

    def generate_lessons(self, simulation_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Analyze simulation results and extract lessons using Claude CLI.

        Falls back to Grok if Claude hits rate limits (usage exceeded).

        Args:
            simulation_results: Dict with keys:
                - trades: list of trade dicts
                - win_rate: float (0-1)
                - avg_win: float ($)
                - avg_loss: float ($)
                - total_pnl: float ($)
                - summary_stats: dict with other metrics

        Returns:
            List of lesson dicts ready to insert into learning_db:
            [
                {
                    'lesson_text': 'specific actionable rule',
                    'category': 'entry_timing|position_sizing|...',
                    'confidence': 0.85,
                    'evidence_summary': 'why this works'
                },
                ...
            ]
        """
        prompt = self._build_prompt(simulation_results)

        logger.info(f"[Claude Lesson Backend] Using model: {self.model}")
        logger.info(f"[Claude Lesson Backend] Building analysis prompt ({len(prompt)} chars)")

        try:
            response = self._call_claude_cli(prompt)
            lessons = self._parse_lessons(response)
            logger.info(f"[Claude Lesson Backend] Generated {len(lessons)} lessons")
            return lessons

        except FileNotFoundError:
            logger.error(
                "[Claude Lesson Backend] Claude CLI not found. "
                "Install with: pip install claude-code"
            )
            return []
        except subprocess.TimeoutExpired:
            logger.error(
                f"[Claude Lesson Backend] Claude CLI timeout after {self.timeout}s"
            )
            return []
        except subprocess.CalledProcessError as e:
            # Likely rate limit or out of access
            if self.fallback_to_grok:
                logger.warning(
                    f"[Claude Lesson Backend] {self.model} unavailable (exit {e.returncode}) - "
                    "falling back to Grok for lesson generation"
                )
                return self._fallback_to_grok(simulation_results)
            else:
                logger.error(f"[Claude Lesson Backend] Error: {e}")
                return []
        except Exception as e:
            logger.error(f"[Claude Lesson Backend] Error: {e}")
            return []

    def _call_claude_cli(self, prompt: str) -> str:
        """
        Call Claude CLI non-interactively.

        Args:
            prompt: The prompt to send

        Returns:
            Claude's response as a string

        Raises:
            FileNotFoundError: if 'claude' command not found
            subprocess.TimeoutExpired: if timeout exceeded
            subprocess.CalledProcessError: if exit code != 0
        """
        import os

        cmd = [
            'claude',
            '--model', self.model,
            '-p',  # non-interactive print mode
            prompt
        ]

        logger.debug(f"[Claude Lesson Backend] Running: {' '.join(cmd[:3])}...")

        # Unset CLAUDECODE to allow nested CLI calls (we're inside Claude Code)
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=self.timeout,
            env=env  # Use modified environment
        )

        return result.stdout.strip()

    def _build_prompt(self, results: Dict[str, Any]) -> str:
        """
        Build the analysis prompt for Claude.

        Args:
            results: Simulation results dict

        Returns:
            Formatted prompt string
        """
        trades_text = self._format_trades(results.get('trades', []))

        prompt = f"""Analyze these EOD trading simulation results and extract 3-5 high-confidence lessons:

=== TODAY'S TRADES ===
{trades_text}

=== SUMMARY STATS ===
- Win Rate: {results.get('win_rate', 0):.1%}
- Avg Win: ${results.get('avg_win', 0):.2f}
- Avg Loss: ${results.get('avg_loss', 0):.2f}
- Total P&L: ${results.get('total_pnl', 0):.2f}
- Total Trades: {len(results.get('trades', []))}

=== YOUR TASK ===
For each lesson, respond ONLY with a JSON array (no other text or markdown):

[
  {{
    "lesson_text": "specific, actionable trading rule derived from today",
    "category": "entry_timing|position_sizing|risk_management|exit_strategy|catalyst_quality|market_regime",
    "confidence": 0.85,
    "evidence_summary": "why this lesson works based on today's results"
  }},
  {{
    "lesson_text": "another rule",
    "category": "...",
    "confidence": 0.80,
    "evidence_summary": "..."
  }}
]

IMPORTANT:
- Only return the JSON array, nothing else
- Lessons must be grounded in today's actual trade data
- Confidence: 0.5-0.9 (not 0.99)
- Each lesson should improve future trading"""

        return prompt

    def _format_trades(self, trades: List[Dict[str, Any]]) -> str:
        """
        Format trades into a readable summary.

        Args:
            trades: List of trade dicts

        Returns:
            Formatted trades text
        """
        if not trades:
            return "  (No trades today)"

        lines = []
        for i, t in enumerate(trades, 1):
            symbol = t.get('symbol', '?')
            entry = t.get('entry_price', 0)
            exit_p = t.get('exit_price', 0)
            pnl = t.get('pnl', 0)
            pnl_pct = t.get('pnl_pct', 0)
            setup = t.get('setup_type', 'unknown')
            conv = t.get('conviction', 0)

            lines.append(
                f"  {i}. {symbol:6} | entry ${entry:7.2f} → exit ${exit_p:7.2f} | "
                f"PnL {pnl:+8.2f} ({pnl_pct:+6.2%}) | {setup:15} | conv {conv}"
            )

        return '\n'.join(lines)

    def _parse_lessons(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse JSON lessons from Claude's response.

        Claude might wrap the JSON in markdown code blocks, so we handle that.

        Args:
            response: Claude's raw response

        Returns:
            List of lesson dicts, or [] if parsing fails
        """
        try:
            import re

            # Try to extract JSON from markdown code blocks
            json_match = re.search(
                r'```(?:json)?\s*(.*?)\s*```',
                response,
                re.DOTALL
            )

            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response

            # Parse as JSON array
            lessons = json.loads(json_str)

            # Validate structure
            if not isinstance(lessons, list):
                lessons = [lessons]

            # Validate each lesson
            validated = []
            for lesson in lessons:
                if self._validate_lesson(lesson):
                    validated.append(lesson)
                else:
                    logger.warning(f"Skipping invalid lesson: {lesson}")

            return validated

        except json.JSONDecodeError as e:
            logger.error(f"[Claude Lesson Backend] Failed to parse JSON: {e}")
            logger.debug(f"Response was: {response[:500]}")
            return []
        except Exception as e:
            logger.error(f"[Claude Lesson Backend] Parsing error: {e}")
            return []

    def _validate_lesson(self, lesson: Dict[str, Any]) -> bool:
        """
        Validate a lesson dict has required fields.

        Args:
            lesson: Lesson dict to validate

        Returns:
            True if valid, False otherwise
        """
        required = ['lesson_text', 'category', 'confidence', 'evidence_summary']

        for field in required:
            if field not in lesson:
                logger.warning(f"Missing field: {field}")
                return False

        # Confidence should be 0-1
        try:
            conf = float(lesson['confidence'])
            if not (0 <= conf <= 1):
                logger.warning(f"Confidence out of range: {conf}")
                return False
        except (ValueError, TypeError):
            logger.warning(f"Invalid confidence type: {lesson['confidence']}")
            return False

        # Category should be one of known types
        valid_categories = {
            'entry_timing', 'position_sizing', 'risk_management',
            'exit_strategy', 'catalyst_quality', 'market_regime',
            'general', 'technical', 'fundamental'
        }
        if lesson.get('category') not in valid_categories:
            logger.warning(f"Unknown category: {lesson.get('category')}")
            # Still valid, just log warning

        return True


    def _fallback_to_grok(self, simulation_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Fall back to Grok for lesson generation if Claude is unavailable.

        Only called when Claude hits rate limits/out of access and fallback_to_grok=True.

        Args:
            simulation_results: EOD sim results

        Returns:
            List of lessons from Grok, or [] if Grok backend not available
        """
        try:
            from ai_trader.trading_agent.analytics.grok_lesson_backend import GrokLessonBackend

            logger.info("[Claude Lesson Backend] Initializing Grok fallback...")
            grok_backend = GrokLessonBackend()
            lessons = grok_backend.generate_lessons(simulation_results)
            logger.info(f"[Claude Lesson Backend] Grok fallback generated {len(lessons)} lessons")
            return lessons

        except ImportError:
            logger.error(
                "[Claude Lesson Backend] Grok backend not available for fallback. "
                "Install grok-api or ensure grok_lesson_backend.py exists."
            )
            return []
        except Exception as e:
            logger.error(f"[Claude Lesson Backend] Grok fallback failed: {e}")
            return []


def compare_backends(simulation_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare lessons from Claude vs Grok on the same results.

    Useful for weekly deep-dive analysis to see if Claude catches patterns
    that Grok misses.

    Args:
        simulation_results: EOD sim results

    Returns:
        Dict with 'claude' and 'grok' lesson lists (if Grok is available)
    """
    output = {
        'timestamp': datetime.now().isoformat(),
        'claude': []
    }

    # Get Claude lessons
    claude_backend = ClaudeCodeLessonBackend(model='opus')
    output['claude'] = claude_backend.generate_lessons(simulation_results)
    logger.info(f"[Comparison] Claude generated {len(output['claude'])} lessons")

    # Try to get Grok lessons (if backend available)
    try:
        from .grok_lesson_backend import GrokLessonBackend
        grok_backend = GrokLessonBackend()
        output['grok'] = grok_backend.generate_lessons(simulation_results)
        logger.info(f"[Comparison] Grok generated {len(output['grok'])} lessons")
    except ImportError:
        logger.info("[Comparison] Grok backend not available")

    return output
