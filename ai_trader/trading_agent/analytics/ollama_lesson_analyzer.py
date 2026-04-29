"""
Ollama-Powered Lesson Analyzer

Analyzes simulated trades using Ollama to discover patterns and generate lessons.
This is the END-OF-DAY learning phase where we:
1. Read all simulated trades from database
2. Use Ollama to analyze patterns and correlations
3. Generate validated lessons with statistical backing
4. Add high-confidence lessons to learning database

This replaces hardcoded statistical analysis with AI-powered pattern discovery.
"""

import logging
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import requests

logger = logging.getLogger(__name__)


class OllamaLessonAnalyzer:
    """
    Uses Ollama to analyze trade results and generate lessons.

    End-of-day workflow:
    1. Fetch all simulated trades from database
    2. Group by different dimensions (symbol, gap size, catalyst age, etc.)
    3. Use Ollama to find statistically significant patterns
    4. Generate lessons with confidence scores and evidence
    5. Save to learning database
    """

    def __init__(self,
                 learning_db_path: Path,
                 ollama_base_url: str = "http://localhost:11434",
                 ollama_model: str = "deepseek-coder-v2:16b",
                 backend: str = "ollama"):
        """
        Initialize lesson analyzer.

        Args:
            learning_db_path: Path to learning.db
            ollama_base_url: Ollama API base URL
            ollama_model: Model to use for analysis (Ollama only)
            backend: "ollama" (local) or "grok" (xAI API - simulates production EOD behavior)
        """
        self.learning_db_path = learning_db_path
        self.ollama_base_url = ollama_base_url
        self.ollama_model = ollama_model
        self.backend = backend.lower()

        logger.info("OllamaLessonAnalyzer initialized")
        logger.info(f"  Backend: {self.backend}")
        logger.info(f"  Model: {ollama_model if self.backend == 'ollama' else 'grok-4-1-fast-reasoning'}")
        logger.info(f"  Database: {learning_db_path}")

    def analyze_simulated_trades(self, min_confidence: float = 0.70) -> List[Dict[str, Any]]:
        """
        Analyze all simulated trades and generate lessons.

        Args:
            min_confidence: Minimum confidence to save lesson to database

        Returns:
            List of generated lessons
        """
        # Fetch all simulated trades
        trades = self._fetch_simulated_trades()

        if not trades:
            logger.warning("No simulated trades found in database")
            return []

        logger.info(f"Analyzing {len(trades)} simulated trades...")

        # Build analysis prompt for Ollama
        prompt = self._build_analysis_prompt(trades)

        # Call LLM to analyze patterns (Ollama or Grok)
        if self.backend == "grok":
            response = self._call_grok(prompt)
        else:
            response = self._call_ollama(prompt)

        # Parse lessons from response
        lessons = self._parse_lessons(response, trades)

        # Save high-confidence lessons to database
        saved_count = 0
        for lesson in lessons:
            if lesson['confidence'] >= min_confidence:
                self._save_lesson(lesson)
                saved_count += 1

        logger.info(f"Generated {len(lessons)} lessons, saved {saved_count} to database")

        return lessons

    def _fetch_simulated_trades(self) -> List[Dict[str, Any]]:
        """Fetch all trades from simulated_trade_journal."""
        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                symbol,
                entry_time,
                exit_time,
                entry_price,
                exit_price,
                actual_pnl,
                catalyst,
                why_entered,
                exit_reason
            FROM simulated_trade_journal
            WHERE exit_time IS NOT NULL
            ORDER BY entry_time
        """)

        rows = cursor.fetchall()
        conn.close()

        trades = []
        for row in rows:
            entry_price = row[3] or 0
            exit_price = row[4] or 0
            pnl_percent = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

            trades.append({
                'symbol': row[0],
                'entry_date': row[1],
                'exit_date': row[2],
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl_dollars': row[5] or 0,
                'pnl_percent': pnl_percent,
                'catalyst_type': row[6] or 'unknown',
                'why_entered': row[7] or '',
                'why_exited': row[8] or ''
            })

        return trades

    def _build_analysis_prompt(self, trades: List[Dict[str, Any]]) -> str:
        """Build prompt for Ollama to analyze trades and discover patterns."""

        # Calculate summary statistics
        total = len(trades)
        winners = sum(1 for t in trades if t['pnl_dollars'] > 0)
        win_rate = winners / total * 100 if total > 0 else 0
        avg_pnl = sum(t['pnl_dollars'] for t in trades) / total if total > 0 else 0

        # Sample trades for context (show first 20)
        trades_sample = trades[:20]

        trades_text = "\n".join([
            f"{i+1}. {t['symbol']}: {t['pnl_percent']:+.2f}% "
            f"(entry: {t['entry_date'][:10]}, catalyst: {t['catalyst_type']})"
            for i, t in enumerate(trades_sample)
        ])

        if len(trades) > 20:
            trades_text += f"\n... and {len(trades) - 20} more trades"

        prompt = f"""You are an expert trading analyst. Analyze these simulated trades and discover actionable patterns.

TRADE SUMMARY:
- Total trades: {total}
- Winners: {winners} ({win_rate:.1f}% win rate)
- Average P&L: ${avg_pnl:+.2f}

SAMPLE TRADES:
{trades_text}

TASK: Identify 3-5 high-confidence trading lessons from this data.

For each lesson:
1. State the pattern clearly (e.g., "High conviction trades (9.5+) win more often")
2. Provide statistical evidence (win rates, sample sizes, effect sizes)
3. Make it actionable (what should the trader DO differently?)
4. Assign confidence (0.0-1.0) based on sample size and effect strength

RESPOND IN JSON:
{{
    "lessons": [
        {{
            "lesson_text": "High conviction (9.5+) trades show 55% win rate vs 42% for medium conviction",
            "confidence": 0.82,
            "evidence_summary": "Based on 23 high-conviction trades (13W-10L) vs 18 medium-conviction (8W-10L)",
            "actionable_rule": "Raise conviction threshold from 7.0 to 9.0 to improve win rate",
            "category": "conviction_threshold"
        }}
    ]
}}

Focus on patterns that:
- Have statistical significance (enough samples)
- Show meaningful effect sizes (>10% difference in win rate or P&L)
- Are actionable (trader can adjust rules based on this)

Analyze the data and respond with lessons in JSON format.
"""

        return prompt

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API to analyze trades."""
        try:
            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,  # Lower for more consistent analysis
                        "num_predict": 2000
                    }
                },
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                return result.get('response', '')
            else:
                logger.error(f"Ollama API error: {response.status_code}")
                return ""

        except Exception as e:
            logger.error(f"Failed to call Ollama: {e}")
            return ""

    def _call_grok(self, prompt: str) -> str:
        """Call Grok API for lesson generation - simulates production EOD behavior."""
        try:
            from ai_trader.trading_agent.analytics.grok_backend import call_grok
            return call_grok(prompt, temperature=0.3, max_tokens=2000)
        except Exception as e:
            logger.error(f"Grok lesson analysis failed: {e}")
            return ""

    def _parse_lessons(self, response: str, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse lessons from Ollama response."""
        try:
            # Try to extract JSON from response
            # Ollama sometimes includes text before/after JSON
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1

            if start_idx == -1 or end_idx == 0:
                logger.warning("No JSON found in Ollama response")
                return []

            json_str = response[start_idx:end_idx]
            data = json.loads(json_str)

            lessons = []
            for lesson_data in data.get('lessons', []):
                lessons.append({
                    'lesson_text': lesson_data.get('lesson_text', ''),
                    'confidence': float(lesson_data.get('confidence', 0.5)),
                    'evidence_summary': lesson_data.get('evidence_summary', ''),
                    'actionable_rule': lesson_data.get('actionable_rule', ''),
                    'category': lesson_data.get('category', 'general')
                })

            return lessons

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Ollama: {e}")
            logger.debug(f"Response: {response[:500]}")
            return []

    def _save_lesson(self, lesson: Dict[str, Any]):
        """Save lesson to learning database."""
        # Belt-and-suspenders: check allow_new_lessons flag before writing
        try:
            from ai_trader.trading_agent.analytics.learning_database import is_lesson_creation_allowed
            if not is_lesson_creation_allowed():
                logger.info("Lesson creation paused (allow_new_lessons=false) - skipping _save_lesson")
                return
        except Exception:
            pass  # If import fails, proceed (fail open - config can't silently block all lessons)

        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        # Build backtest statistics
        backtest_stats = {
            'confidence': lesson['confidence'],
            'evidence': lesson['evidence_summary']
        }

        try:
            import hashlib
            lesson_hash = hashlib.md5(lesson['lesson_text'].encode()).hexdigest()[:16]
            cursor.execute("""
                INSERT INTO lessons (
                    lesson_hash,
                    lesson_text,
                    confidence,
                    evidence_summary,
                    created_at,
                    category,
                    is_active,
                    source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lesson_hash,
                lesson['lesson_text'],
                lesson['confidence'],
                lesson['evidence_summary'],
                datetime.now().isoformat(),
                lesson.get('category', 'general'),
                1,
                'grok_simulation'
            ))

            conn.commit()
            logger.info(f"Saved lesson: {lesson['lesson_text'][:60]}...")

        except sqlite3.IntegrityError:
            logger.warning(f"Lesson already exists: {lesson['lesson_text'][:60]}")
        finally:
            conn.close()


def test_ollama_analyzer():
    """Test Ollama lesson analyzer."""
    print("Testing Ollama Lesson Analyzer...")

    learning_db_path = Path("ai_trader/ai_trader_data/learning.db")

    if not learning_db_path.exists():
        print(f"ERROR: Database not found at {learning_db_path}")
        return

    analyzer = OllamaLessonAnalyzer(
        learning_db_path=learning_db_path,
        ollama_model="qwen3:32b"
    )

    lessons = analyzer.analyze_simulated_trades(min_confidence=0.70)

    print(f"\n[OK] Generated {len(lessons)} lessons")

    for i, lesson in enumerate(lessons, 1):
        print(f"\n{i}. {lesson['lesson_text']}")
        print(f"   Confidence: {lesson['confidence']:.0%}")
        print(f"   Evidence: {lesson['evidence_summary'][:100]}...")
        print(f"   Action: {lesson.get('actionable_rule', 'N/A')[:100]}...")

    print("\nOllama Lesson Analyzer test complete!")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    test_ollama_analyzer()
