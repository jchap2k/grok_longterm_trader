"""
Ollama Trade Evaluator - AI-Powered Trade Decision Making

Uses Ollama (local LLM) to make trade decisions by:
1. Reading lessons from learning database
2. Analyzing catalyst context
3. Deciding conviction score and whether to trade

This replaces hardcoded rules with AI reasoning that learns from experience.
"""

import logging
import json
import sqlite3
import requests
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger(__name__)

# CATALYST_RULES_v1.0 - Shared across all trade evaluation prompts (Grok 4.20 optimization)
# Extracted to reduce token duplication when used in multiple contexts
CATALYST_RULES_v1_0 = """
CATALYST_RULES_v1.0 (Shared across all entry-decision contexts):

CATALYST AGE PENALTY (apply when assigning conviction):
- Fresh (same-day or <24h old): No penalty, PRIORITIZE these
- 24-48h old AND stock moved >10%: Reduce conviction -1 point
- 48+ hours old AND stock moved >10%: Reduce conviction -2 points
REASONING: Big moves happen Day 1. Day 2-3 entries often catch exhaustion/pullback.

CATALYST TYPE WEIGHTING (adjust conviction based on quality):
BINARY CATALYSTS (more predictable follow-through):
  - FDA approval/rejection: +1.8 conviction points
  - M&A/acquisition announcement: +1.6 conviction points
  - Earnings beat (>10% revenue/EPS surprise): +1.5 conviction points
  - Guidance raise (>15% increase): +1.4 conviction points
MOMENTUM/TECHNICAL (less predictable):
  - Momentum breakout (no fundamental): +0.0 conviction points (neutral)
  - Technical pattern only: -0.2 conviction points
REASONING: Binary catalysts have clear information asymmetry, momentum fades faster.

GAP DIRECTION RULES:
- Gap UP >3% (strong move): Good candidate IF pullback occurs
- Gap DOWN <-2%: Reduce conviction -3 points or SKIP entirely
  Historical: Gap down trades avg -1.40% P&L, avoid
- Moved >7% from open WITHOUT pullback: Reduce conviction -3 points
  Reason: Missed the move, now chasing = negative expected value

VIX REGIME ADJUSTMENT:
- High-VIX (>25) or Bear regime: Reduce size 50-75% OR require +1 conviction point
- Normal/Bull regime: Standard sizing
- VIX 16-19 (optimal): 70.4% win rate - target this zone
- VIX 19-22 (elevated): 53.8% win rate - proceed with caution
"""


class OllamaTradeEvaluator:
    """
    Uses Ollama to evaluate trade opportunities and assign conviction scores.

    Replaces hardcoded conviction calculation with AI reasoning that:
    - Reads validated lessons from learning database
    - Considers market context (VIX, gap size, catalyst age)
    - Makes nuanced decisions based on pattern recognition
    """

    def __init__(self,
                 learning_db_path: Path,
                 ollama_base_url: str = "http://localhost:11434",
                 ollama_model: str = "deepseek-coder-v2:16b",
                 backend: str = "ollama",
                 rules_file: str = None,
                 skip_db_lessons: bool = False):
        """
        Initialize Ollama trade evaluator.

        Args:
            learning_db_path: Path to learning.db with lessons
            ollama_base_url: Ollama API endpoint
            ollama_model: Model to use (recommend 30B+ for good reasoning)
            backend: "ollama" (local) or "grok" (xAI API, matches production)
            rules_file: Path to active_rules.txt (matches production system prompt).
                        Defaults to absolute path derived from this file's location,
                        immune to working directory differences.
            skip_db_lessons: If True, load no lessons from the DB (baseline mode).
                             Grok still uses active_rules.txt; only learned lesson
                             adjustments are suppressed. Default False.
        """
        self.learning_db_path = Path(learning_db_path)
        self.skip_db_lessons = skip_db_lessons
        self.ollama_base_url = ollama_base_url
        self.ollama_model = ollama_model
        self.backend = backend.lower()
        # Resolve rules file to absolute path so it works regardless of CWD.
        # analytics/ -> trading_agent/ -> ai_trader/ -> project_root/
        if rules_file is None:
            rules_file = str(
                Path(__file__).parent.parent.parent.parent
                / 'ai_trader' / 'rules' / 'active_rules.txt'
            )
        self.rules_file = Path(rules_file)

        # Load production rules (matches live trading system prompt)
        self.rules_content = self._load_rules()

        # Load lessons once at init
        self.lessons = self._load_lessons()

        # Stores adjustments/lessons from last evaluate_trade() call (for lesson tracking)
        self.last_adjustments = []
        self.last_lessons_used = []  # [{id, text_excerpt, adjustment}]

        logger.info(f"OllamaTradeEvaluator initialized")
        logger.info(f"  Backend: {self.backend}")
        logger.info(f"  Model: {ollama_model if self.backend == 'ollama' else 'grok-4.3'}")
        logger.info(f"  Lessons loaded: {len(self.lessons)}")

    def _load_rules(self) -> str:
        """Load trading rules from active_rules.txt (matches production system prompt)."""
        try:
            if not self.rules_file.exists():
                logger.warning(f"Rules file not found: {self.rules_file}, using fallback CATALYST_RULES_v1_0")
                return CATALYST_RULES_v1_0

            with open(self.rules_file, 'r', encoding='utf-8') as f:
                rules_content = f.read()

            logger.info(f"Loaded trading rules from {self.rules_file} ({len(rules_content)} chars)")
            return rules_content
        except Exception as e:
            logger.warning(f"Failed to load rules: {e}, using fallback CATALYST_RULES_v1_0")
            return CATALYST_RULES_v1_0

    def _load_lessons(self) -> List[Dict[str, Any]]:
        """Load active lessons from learning database."""
        if self.skip_db_lessons:
            logger.info("  Lesson loading skipped (baseline mode - skip_db_lessons=True)")
            return []
        if not self.learning_db_path.exists():
            logger.warning(f"Learning database not found: {self.learning_db_path}")
            return []

        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                id,
                lesson_text,
                category,
                confidence,
                evidence_summary,
                condition,
                action,
                backtest_win_rate,
                backtest_sample_size,
                applicable_sectors
            FROM lessons
            WHERE is_active = 1
            ORDER BY confidence DESC
        """)

        lessons = []
        for row in cursor.fetchall():
            lessons.append({
                'id': row[0],
                'text': row[1],
                'category': row[2],
                'confidence': row[3],
                'evidence': row[4],
                'condition': row[5],
                'action': row[6],
                'win_rate': row[7],
                'sample_size': row[8],
                'applicable_sectors': row[9]  # JSON array or None
            })

        conn.close()
        return lessons

    def evaluate_trade(self, catalyst: Dict[str, Any]) -> Tuple[float, Optional[str]]:
        """
        Use Ollama to evaluate trade opportunity and return conviction score.

        Args:
            catalyst: Dict with keys: symbol, gap_pct, vix_level, catalyst_type, etc.

        Returns:
            (conviction_score, skip_reason)
            - conviction_score: 0-10 score (0 = don't trade)
            - skip_reason: Why trade was skipped (None if should trade)
        """

        # Clear lesson tracking at start of each evaluation
        self.last_adjustments = []
        self.last_lessons_used = []

        # Build prompt with catalyst context and lessons
        prompt = self._build_evaluation_prompt(catalyst)

        # Get LLM's decision (Ollama or Grok depending on backend)
        if self.backend == "grok":
            # Pass rules as system message (cached by xAI after setup_rules_context() call)
            response = self._call_grok(prompt, system=self.rules_content)
        else:
            response = self._call_ollama(prompt)

        # Parse response
        conviction, skip_reason = self._parse_ollama_response(response, catalyst)

        return conviction, skip_reason

    def _build_evaluation_prompt(self, catalyst: Dict[str, Any]) -> str:
        """Build prompt for Ollama with catalyst data and lessons."""

        # Filter lessons based on stock's sector
        import json
        stock_sector = catalyst.get('sector')
        gap_pct = catalyst.get('gap_pct', 0)
        applicable_lessons = []

        for lesson in self.lessons:
            sectors_json = lesson.get('applicable_sectors')

            # If lesson has no sector restriction (None), it applies to all
            if sectors_json is None:
                applicable_lessons.append(lesson)
            # If lesson has sector restrictions, check if stock's sector matches
            elif stock_sector:
                try:
                    applicable_sectors = json.loads(sectors_json)
                    if stock_sector in applicable_sectors:
                        applicable_lessons.append(lesson)
                except:
                    # If JSON parsing fails, include the lesson (safe default)
                    applicable_lessons.append(lesson)

        # Rank lessons by relevance to this specific trade, then cap at top 5.
        # This prevents conviction score stacking when multiple lessons say
        # similar things (e.g. 4 lessons all saying "positive gaps outperform").
        # Relevance score = confidence + gap-direction bonus.
        MAX_LESSONS = 5

        def lesson_relevance(lesson):
            score = lesson.get('confidence') or 0.5
            text = (lesson.get('text') or '').lower()
            # Boost lessons whose described direction matches this trade
            if gap_pct > 0 and ('positive' in text or 'gap-up' in text or 'gap up' in text):
                score += 0.2
            if gap_pct < 0 and ('negative' in text or 'gap-down' in text or 'gap down' in text):
                score += 0.2
            # Boost sector-specific lessons (they survived the sector filter above)
            if lesson.get('applicable_sectors'):
                score += 0.1
            return score

        applicable_lessons.sort(key=lesson_relevance, reverse=True)
        top_lessons = applicable_lessons[:MAX_LESSONS]

        if len(applicable_lessons) > MAX_LESSONS:
            logger.debug(
                f"Lesson cap: {len(applicable_lessons)} applicable -> top {MAX_LESSONS} by relevance"
            )

        lessons_text = "\n".join([
            f"[L{lesson['id']}] {lesson['text']}\n"
            f"   Action: {lesson['action']}\n"
            f"   Evidence: {lesson['evidence']}\n"
            f"   Win Rate: {(lesson['win_rate'] or 0)*100:.1f}% ({lesson['sample_size'] or 0} trades)\n"
            for lesson in top_lessons
        ])

        sector_info = f"\n- Sector: {catalyst.get('sector', 'Unknown')}" if catalyst.get('sector') else ""

        # Price direction lookback (30 min of 5-min bars from early market hours)
        # Mirrors production "PRICE DIRECTION" section - helps Grok see continuation vs fade
        price_dir_section = ""
        if catalyst.get('price_lookback'):
            price_dir_section = (
                f"\nPRICE DIRECTION (30-min, 5-min bars, sim approx):\n"
                f"{catalyst['price_lookback']}"
            )

        # NOTE: Trading rules are passed as system message (see setup_rules_context + evaluate_trade).
        # The user message contains only catalyst-specific data and lessons.
        # This enables xAI prompt caching across all calls in an iteration.

        # Validate required catalyst fields before prompt construction.
        # Silent defaults lead to wrong Grok scoring - log warnings so upstream gaps are visible.
        _sym = catalyst.get('symbol', 'N/A')
        if not catalyst.get('catalyst_type'):
            logger.warning(
                f"[EvalPrompt] {_sym}: catalyst_type missing/None - defaulting to 'earnings'. "
                f"Check candidate builder for this symbol."
            )
        if catalyst.get('catalyst_age_hours') is None:
            logger.warning(
                f"[EvalPrompt] {_sym}: catalyst_age_hours missing - defaulting to 24h. "
                f"Check that catalyst_date is populated upstream."
            )

        prompt = f"""Evaluate this trade opportunity using the trading rules and conviction scoring system in the system context.

CATALYST OPPORTUNITY:
- Symbol: {catalyst.get('symbol', 'N/A')}
- Type: {catalyst.get('catalyst_type', 'earnings')} announcement
- Gap: {catalyst.get('gap_pct', 0):.1f}%
- VIX: {catalyst.get('vix_level', 15):.1f}
- Catalyst Age: {catalyst.get('catalyst_age_hours', 24):.1f} hours{sector_info}{price_dir_section}

VALIDATED LESSONS FROM PAST TRADES:
{lessons_text if lessons_text else "No lessons available yet."}

---

YOUR TASK:
1. Apply CATALYST_RULES_v1.0 penalties and adjustments to base conviction
2. Use the lessons above to adjust conviction - they work in BOTH directions:
   ** Favorable lessons (high WR, matches this trade): +0.5 to +1.0 **
   ** Cautionary lessons (low WR, underperform, skip, avoid): -0.5 to -1.5 **
   ** CRITICAL: Multiple lessons pointing the same direction count as ONE adjustment **
   ** e.g. three lessons all saying "positive gaps win" = +0.5 to +1.0 total, NOT +3.0 **
   ** Cap total lesson adjustment at +1.5 max or -2.0 max regardless of lesson count **
   ** Lessons are SOFT GUIDANCE - do not hard-block trades based on lessons alone **
3. Use trading judgment as primary driver (gap size, VIX, catalyst freshness)
4. Assign final conviction score (0-10):
   - 0-6.4: SKIP (don't trade)
   - 6.5-7.9: MINIMUM opportunity floor (trade only if no better setups available)
   - 8.0-8.9: MEDIUM conviction (standard trade)
   - 9.0-10.0: HIGH conviction (high probability setup)

MINIMUM OPPORTUNITY FLOOR GUIDANCE:
If no conviction 8.0+ setups exist by 9:00 AM in a favorable regime (NEUTRAL, SMALL_GAP_UP, SMALL_GAP_DOWN):
- Can consider conviction 6.5-7.9 setups (but ONLY if they are A+ quality)
- A+ QUALITY means ALL of these must be true:
  * Fresh catalyst (<24h old, preferably <12h)
  * Volume surge (>1.5x average daily volume)
  * Multi-timeframe alignment (intraday AND daily both bullish/favorable)
  * Strong relative strength (>1.2% outperformance vs SPY/QQQ)
  * No correlation conflicts (>0.7 correlation with current holdings)
- Still apply all risk management rules (position sizing, max positions for regime, etc.)
- Log: "MINIMUM_OPPORTUNITY_FLOOR activated for [SYMBOL]" if you apply this floor

---

RESPOND WITH ONLY VALID JSON (no markdown, no other text):

{{
  "conviction": <float 0-10>,
  "skip_reason": <null if conviction >=6.5, otherwise string explaining why skip>,
  "reasoning": "<Clear explanation of conviction score, referencing rules and lessons>",
  "adjustments_applied": [
    {{"rule": "<rule name>", "adjustment": "<+X or -X points>"}},
    {{"rule": "Catalyst Age", "adjustment": "-1"}},
    {{"rule": "VIX Regime", "adjustment": "+0"}}
  ],
  "lessons_used": [
    {{"id": <lesson id integer>, "text_excerpt": "<first 60 chars of lesson text>", "adjustment": "<+X or -X points>"}}
  ],
  "base_conviction_before_adjustments": <float>,
  "final_conviction_after_adjustments": <float>
}}

CRITICAL REQUIREMENTS FOR VALID RESPONSE:
- Response must be valid JSON (no markdown code blocks, no text before/after)
- Required fields: conviction, skip_reason, reasoning, adjustments_applied
- lessons_used: include ONLY lessons you actually considered (use [L<id>] ids from above); empty list [] if no lessons applied
- conviction must be number 0-10 (not string)
- skip_reason must be null or string (not number)
- If response is invalid JSON, response will be rejected and trade will be skipped
"""

        return prompt

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API to get evaluation."""

        try:
            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.3,  # Lower for more consistent scoring
                    "num_predict": 500
                },
                timeout=180  # 3 minutes for 32B model
            )

            if response.status_code == 200:
                result = response.json()
                return result.get('response', '')
            else:
                logger.error(f"Ollama API error: {response.status_code}")
                return ""

        except requests.exceptions.Timeout:
            logger.error("Ollama request timed out")
            return ""
        except Exception as e:
            logger.error(f"Failed to call Ollama: {e}")
            return ""

    def setup_rules_context(self) -> None:
        """
        Prime the rules context at the start of an iteration (Grok backend only).

        Makes one API call with rules as system message to warm xAI's prompt cache.
        Subsequent evaluate_trade() calls pass the same system message and get cache hits,
        avoiding the TPM burst rate-limiting that occurs when 24 calls each send 41K chars.

        No-op for Ollama backend (no server-side prompt caching to warm).
        """
        if self.backend != "grok":
            return

        logger.info("[Setup] Priming Grok rules context for this iteration...")
        try:
            ack = self._call_grok(
                "Acknowledge receipt of the trading rules and confirm you are ready to evaluate trade opportunities. Reply in one sentence.",
                system=self.rules_content
            )
            logger.info(f"[Setup] Rules context primed. Grok: {(ack or '')[:100]}")
        except Exception as e:
            logger.warning(f"[Setup] Rules context priming failed (non-fatal): {e}")

    def _call_grok(self, prompt: str, system: Optional[str] = None) -> str:
        """Call Grok API using production pattern (OpenAI SDK + xAI endpoint)."""
        try:
            from ai_trader.trading_agent.analytics.grok_backend import call_grok
            return call_grok(prompt, system=system, temperature=0.3, max_tokens=500)
        except Exception as e:
            logger.error(f"Grok trade evaluation failed: {e}")
            return ""

    def _parse_ollama_response(self, response: str, catalyst: Dict[str, Any]) -> Tuple[float, Optional[str]]:
        """
        Parse LLM's JSON response into conviction and skip_reason.

        Grok 4.20 IMPROVEMENT: Enhanced JSON validation with required fields check.
        If response is malformed, log clearly and fall back to rules (don't attempt guessing).
        """

        try:
            # Clean up response: remove markdown code blocks
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()

            # Parse JSON
            data = json.loads(response)

            # VALIDATION: Check required fields (Grok 4.20 improvement)
            required_fields = ['conviction', 'skip_reason', 'reasoning']
            missing_fields = [f for f in required_fields if f not in data]

            if missing_fields:
                logger.warning(f"JSON validation failed: missing fields {missing_fields}")
                logger.debug(f"Response was: {response[:300]}")
                return self._fallback_evaluation(catalyst)

            # Extract and validate conviction (must be number 0-10)
            conviction_raw = data.get('conviction')
            if not isinstance(conviction_raw, (int, float)):
                logger.warning(f"Conviction field not a number: {type(conviction_raw)}")
                return self._fallback_evaluation(catalyst)

            conviction = float(conviction_raw)
            skip_reason = data.get('skip_reason', None)
            reasoning = data.get('reasoning', '')
            adjustments_applied = data.get('adjustments_applied', [])
            lessons_used = data.get('lessons_used', [])

            # Log LLM's reasoning with adjustments for audit trail
            adjustments_str = ", ".join([f"{adj['rule']}({adj['adjustment']})" for adj in adjustments_applied[:3]])
            lessons_str = ", ".join([f"L{lu.get('id','?')}({lu.get('adjustment','?')})" for lu in lessons_used[:3]]) if lessons_used else "none"
            logger.info(f"  LLM: conviction={conviction:.1f} - adjustments=[{adjustments_str}] - lessons=[{lessons_str}] - {reasoning[:60]}")

            # VERBOSE CONVICTION LOGGING (Grok 4.2 recommendation for diagnosis)
            # This format makes it easy to extract and analyze conviction distribution
            logger.info(f"CONVICTION_ANALYSIS | {catalyst.get('symbol','?')} | "
                       f"score={conviction:.1f}/10 | "
                       f"gap={catalyst.get('gap_pct','N/A'):.1f}% | "
                       f"catalyst={catalyst.get('catalyst_type','?')} | "
                       f"vix={catalyst.get('vix_level','N/A'):.1f} | "
                       f"reason_summary: {reasoning[:120]}")

            # Log full reasoning for ALL catalysts (not just first 5) for complete analysis
            # This was the key ask from Grok 4.2 - need full visibility
            if not hasattr(self, '_catalyst_count'):
                self._catalyst_count = 0
            self._catalyst_count += 1
            # Log all reasoning to stdout/logs (will be captured in test output)
            logger.debug(f"FULL_REASONING[{self._catalyst_count}] {catalyst.get('symbol','?')} (score={conviction:.1f}):\n{reasoning}")

            # Store for caller to inspect (lesson loss tracking)
            self.last_adjustments = adjustments_applied
            self.last_lessons_used = lessons_used  # [{id, text_excerpt, adjustment}]

            # Clamp conviction to valid range
            conviction = max(0.0, min(10.0, conviction))

            # Validate skip_reason logic: if conviction < 6.5 should have skip_reason
            if conviction < 6.5 and not skip_reason:
                logger.warning(f"Conviction {conviction:.1f} < 6.5 but no skip_reason provided")
                skip_reason = "Low conviction (no specific reason provided)"

            return conviction, skip_reason

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error at position {e.pos}: {e.msg}")
            logger.debug(f"Response was: {response[:300]}")
            return self._fallback_evaluation(catalyst)

        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Could not parse LLM response: {type(e).__name__}: {e}")
            logger.debug(f"Response was: {response[:300]}")
            return self._fallback_evaluation(catalyst)

    def _fallback_evaluation(self, catalyst: Dict[str, Any]) -> Tuple[float, Optional[str]]:
        """
        Fallback conviction calculation if Ollama fails.
        Uses simple rules as safety net.
        """
        conviction = 8.0  # Base conviction
        gap_pct = catalyst.get('gap_pct', 0)
        age_hours = catalyst.get('catalyst_age_hours', 24)

        # Rule: Skip gap downs
        if gap_pct < -2:
            return 0.0, "Gap down - avoid"

        # Rule: Penalize stale catalysts
        if age_hours > 48:
            conviction -= 2

        # Rule: Penalize huge gaps (priced in)
        if abs(gap_pct) > 15:
            conviction -= 2

        return conviction, None if conviction >= 6.5 else "Low conviction (fallback rules)"


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    evaluator = OllamaTradeEvaluator(
        learning_db_path=Path("ai_trader/ai_trader_data/learning.db"),
        ollama_model="qwen3:32b"
    )

    # Test catalyst
    test_catalyst = {
        'symbol': 'NVDA',
        'catalyst_type': 'earnings',
        'gap_pct': 12.5,
        'vix_level': 16.2,
        'catalyst_age_hours': 18.5
    }

    conviction, skip_reason = evaluator.evaluate_trade(test_catalyst)

    print(f"\nTest Evaluation:")
    print(f"  Conviction: {conviction:.1f}/10")
    print(f"  Decision: {'TRADE' if conviction >= 6.5 else 'SKIP'}")
    if skip_reason:
        print(f"  Reason: {skip_reason}")
