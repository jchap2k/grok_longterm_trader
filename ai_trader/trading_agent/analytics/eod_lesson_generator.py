"""
EOD Lesson Generator - Analyzes daily trades and generates learning database entries

Uses Grok AI to analyze completed trades, intraday price action, and exit reasons
to generate structured lessons for the learning database.

Strategy:
- Grok by default (fast, cheap, good enough for normal days)
- Claude fallback for "bad days" (approaching circuit breaker, multiple losses)

Usage:
    generator = EODLessonGenerator()
    lessons = generator.analyze_trades(date(2026, 2, 12), dry_run=True)
    print(lessons)  # Review before adding to database
"""

import os
import json
import logging
import sqlite3
import difflib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Import OpenAI client (xAI Grok uses OpenAI-compatible API)
try:
    from openai import OpenAI
except ImportError:
    logger.error("openai package not installed. Run: pip install openai")
    OpenAI = None

# Import Anthropic client for Claude fallback
try:
    from anthropic import Anthropic
except ImportError:
    logger.error("anthropic package not installed. Run: pip install anthropic")
    Anthropic = None

# Import learning database
from analytics.learning_database import LearningDatabase


@dataclass
class TradeContext:
    """Context for a single trade including entry/exit and intraday behavior."""
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    conviction: Optional[int]
    entry_rationale: Optional[str]
    stop_price: Optional[float]
    target_price: Optional[float]

    # Intraday behavior
    high_since_entry: Optional[float] = None
    low_since_entry: Optional[float] = None
    max_profit_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None

    # Triggers fired (from logs if available)
    triggers_fired: Optional[List[str]] = None

    # Entry context from trade_journal (FIX: Feb 15, 2026 - read from learning.db)
    why_entered: Optional[str] = None
    catalyst: Optional[str] = None
    market_context: Optional[str] = None
    setup_type: Optional[str] = None
    confidence_level: Optional[float] = None  # From trade_journal (may differ from conviction)


LESSON_GENERATOR_SYSTEM_PROMPT = """You are an expert day trading analyst reviewing completed trades.

Your job: Analyze trades to identify patterns, lessons, and behaviors that should be captured in a learning database for future reference.

VALIDATED PATTERN CONTEXT (Phase 2A):
You will receive a "VALIDATED PATTERNS" section showing patterns that have been statistically validated through real forward-tested trades (30+ trades, profit factor >= 1.5, p<0.05).

How to use this context:
1. **Higher confidence for confirming lessons** - If today's trades confirm a validated pattern, assign confidence 0.8-1.0
2. **Lower confidence for contradictory lessons** - If today's observation conflicts with validated pattern, flag as tentative (0.5-0.6) or explain the conflict
3. **Explicit reasoning** - Reference validated patterns in your reasoning: "This confirms pattern XYZ" or "This contradicts pattern ABC (may be regime shift)"
4. **Catch invalidations early** - If a VALIDATED pattern fails multiple times today, generate a lesson questioning its continued validity

IMPORTANT STRUCTURE:
1. First, provide a 1-paragraph meta-summary of today's overall themes:
   - Common entry/exit patterns across all trades
   - Sector/regime behavior (if relevant)
   - System issues (data quality, execution problems)
   - Overall trading psychology (overtrading, discipline, etc.)
   - How today's trades align with or contradict validated patterns

2. Then, analyze WINNING trades separately and generate positive lessons

3. Then, analyze LOSING trades separately and generate AVOID lessons:
   - What conditions should be avoided in the future?
   - What entry/exit timing patterns led to losses?
   - Use categories: "avoid_entry", "avoid_timing", or standard categories with negative framing
   - Be explicit about what NOT to do
   - Confidence 0.6-1.0 based on loss consistency

Focus areas:
1. **Symbol-specific behaviors** - How did this ticker behave? (fade patterns, support levels, short interest effects)
2. **Entry timing lessons** - Was the entry well-timed? Did pre-market data mislead?
3. **Exit timing lessons** - Was exit optimal? Profit given back? Stop loss hit prematurely?
4. **Data quality issues** - Stale quotes, low confidence, incorrect ATR? (CRITICAL - always check for system bugs)
5. **Risk management** - Should position size have been different? Stop too tight/loose?
6. **Re-entry rules** - If stopped out, should we re-enter same day?

Categories:
- symbol_behavior: Ticker-specific patterns (e.g., "CROX fades after earnings gaps >10%")
- entry_timing: When to enter/avoid (e.g., "Wait 30 min after open for SMCI")
- exit_timing: When to exit (e.g., "Take profits at +2% on low conviction trades")
- data_quality: Quote/data issues (e.g., "Pre-market quotes <0.5 confidence unreliable")
- risk_management: Position sizing, stops (e.g., "Use wider stops on earnings day")
- re_entry: Same-day re-entry rules (e.g., "Max 2 re-entries per symbol per day")
- avoid_entry: Conditions to avoid entering (e.g., "Avoid CROX momentum entries after 08:00")
- avoid_timing: Times/conditions to stay out (e.g., "Avoid chasing gaps >15% in first 10 min")

Output format (JSON):
{
  "meta_summary": "2 winning CGNX news trades (scalping worked well pre-market), 1 losing CROX momentum trade (fade pattern evident). Common theme: News catalysts outperformed momentum setups today. No system issues detected.",
  "lessons": [
    {
      "lesson_text": "CROX: Strong earnings gaps often fade by noon on high short interest",
      "category": "symbol_behavior",
      "confidence": 0.8,
      "symbols": ["CROX"],
      "sample_size": 1,
      "reasoning": "CROX gapped +8%, reached +10% pre-market, then faded to -1.2% by exit. Exit reason indicates momentum reversal."
    },
    {
      "lesson_text": "Avoid momentum entries on CROX after 08:00 - tends to fade intraday",
      "category": "avoid_entry",
      "confidence": 0.65,
      "symbols": ["CROX"],
      "sample_size": 1,
      "reasoning": "Entry at 08:13 led to immediate drawdown and manual exit at loss. No recovery attempts observed."
    }
  ],
  "summary": "2 winning trades, 1 loser. Key lesson: News catalysts worked, momentum faded."
}

Confidence scoring:
- 0.9-1.0: Multiple confirming instances, clear pattern
- 0.7-0.8: Single strong instance or multiple weak instances
- 0.5-0.6: Tentative pattern, needs more data
- <0.5: Too weak to be useful

Only generate lessons with confidence >= 0.6. Quality over quantity.
Generate at least one AVOID lesson if there were any losing trades.

DUPLICATE PREVENTION:
You will receive an "Existing Active Lessons" section in the user prompt.
Do not generate any lesson that substantially restates an existing one.
If today's observation merely confirms an existing lesson, note it in meta_summary
but do not create a new lesson for it.
Only generate NEW lessons that capture something not already expressed.

SKIPPED STOCKS ANALYSIS:
You will receive a "Stocks Considered But Not Traded" section.
- If a skipped stock had a large day-high move (+10% or more from open) that we missed,
  generate a lesson about what conditions made it worth entering OR why the skip was correct.
- If a skipped stock faded (EOD close below open), note the correct avoidance in
  meta_summary but do not create a redundant avoid lesson unless the pattern is novel.
- Correct avoidances that saved capital are positive signals - call them out explicitly.
"""


class EODLessonGenerator:
    """
    Generates trading lessons from completed trades using Grok AI.

    Analyzes trades from trading_performance.db and generates structured
    lessons for the learning database.
    """

    def __init__(self, config_path: Path = None):
        """
        Initialize the EOD lesson generator.

        Args:
            config_path: Path to broker_config.json (for API keys)
        """
        if config_path is None:
            script_dir = Path(__file__).parent
            config_path = script_dir.parent.parent / "ai_trader_data" / "broker_config.json"

        self.config_path = Path(config_path)
        self.data_dir = self.config_path.parent

        # Load config
        with open(self.config_path) as f:
            config = json.load(f)

        # xAI API key (stored in environment)
        self.xai_api_key = os.getenv('XAI_API_KEY')
        if not self.xai_api_key:
            logger.warning("XAI_API_KEY not set - will try Claude fallback")

        # Anthropic API key (stored in environment)
        self.anthropic_api_key = os.getenv('ANTHROPIC_API_KEY')

        # Initialize OpenAI client pointing to xAI endpoint (Grok)
        if OpenAI and self.xai_api_key:
            self.grok_client = OpenAI(
                api_key=self.xai_api_key,
                base_url="https://api.x.ai/v1"
            )
        else:
            self.grok_client = None

        # Initialize Anthropic client (Claude fallback)
        if Anthropic and self.anthropic_api_key:
            self.claude_client = Anthropic(api_key=self.anthropic_api_key)
        else:
            self.claude_client = None

        # Get models from config
        self.grok_model = config.get('ai_providers', {}).get('grok', {}).get('fast_model', 'grok-2-1212')
        self.claude_model = config.get('ai_providers', {}).get('claude', {}).get('fast_model', 'claude-3-5-haiku-20241022')

        # Database paths
        self.trades_db = self.data_dir.parent / "trading_performance.db"
        self.trading_db_path = str(self.trades_db)  # Phase 2A: for attribution_analyzer
        self.learning_db_path = self.data_dir / "learning.db"
        self.learning_db = LearningDatabase(self.learning_db_path)

    def analyze_trades(
        self,
        trade_date: date,
        dry_run: bool = False,
        force_claude: bool = False,
        regime_context: dict = None
    ) -> Dict[str, Any]:
        """
        Analyze all trades for a given date and generate lessons.

        Args:
            trade_date: Date to analyze (e.g., date(2026, 2, 12))
            dry_run: If True, print lessons instead of adding to database
            force_claude: If True, use Claude instead of Grok
            regime_context: Optional dict from get_swing_regime_detail() at EOD time.
                            Used to tag lessons with regime conditions so regime-stratified
                            retrieval can exclude bull-market lessons from bear-market decisions.
                            Expected keys: swing_score (int), vix_mode (str), qqq_signal (str),
                            spy_vs_iwm_signal (str), qqq_vs_iwm_signal (str).

        Returns:
            Dict with 'lessons', 'summary', 'trades_analyzed', 'ai_used'
        """
        # Respect allow_new_lessons flag before any API calls
        if not dry_run:
            try:
                from analytics.learning_database import is_lesson_creation_allowed
                if not is_lesson_creation_allowed():
                    logger.info(
                        "EOD lesson generation skipped: "
                        "allow_new_lessons=false in learning_config.json"
                    )
                    return {
                        'lessons': [],
                        'summary': 'Skipped - allow_new_lessons=false',
                        'trades_analyzed': 0,
                        'lessons_added': 0,
                    }
            except Exception:
                pass  # Fail open - if config unreadable, proceed

        # Get trades for date
        trade_contexts = self._get_trade_contexts(trade_date)

        if not trade_contexts:
            logger.info(f"No trades found for {trade_date}")
            return {
                'lessons': [],
                'summary': 'No trades to analyze',
                'trades_analyzed': 0
            }

        # Decide whether to use Grok or Claude
        use_claude = force_claude or self._should_use_claude(trade_contexts)

        if use_claude:
            logger.info("Using Claude for EOD analysis (bad day or forced)")
            return self._analyze_with_claude(trade_contexts, trade_date, dry_run)
        else:
            logger.info("Using Grok for EOD analysis (normal day)")
            return self._analyze_with_grok(trade_contexts, trade_date, dry_run)

    def _should_use_claude(self, trade_contexts: List[TradeContext]) -> bool:
        """
        Decide whether to use Claude (expensive, thorough) or Grok (cheap, fast).

        Use Claude for:
        - Multiple losing trades (3+ losses)
        - Large single loss (>2% portfolio drawdown)
        - Daily P&L approaching circuit breaker thresholds

        Returns:
            True if Claude should be used, False for Grok
        """
        if not self.claude_client:
            return False  # No Claude available, use Grok

        # Count losses
        losses = [t for t in trade_contexts if t.pnl < 0]
        loss_count = len(losses)

        # Sum total P&L percent (rough proxy for portfolio impact)
        total_pnl_pct = sum(t.pnl_pct for t in trade_contexts)

        # Use Claude if:
        # 1. Multiple losses (3+)
        if loss_count >= 3:
            logger.info(f"Using Claude: {loss_count} losses today")
            return True

        # 2. Daily P&L < -1.5% (approaching -2% warning threshold)
        if total_pnl_pct < -1.5:
            logger.info(f"Using Claude: Daily P&L {total_pnl_pct:.1f}% approaching circuit breaker")
            return True

        # 3. Any single loss > -2%
        if any(t.pnl_pct < -2.0 for t in losses):
            logger.info("Using Claude: Large single loss detected")
            return True

        # Otherwise, Grok is fine
        return False

    def _analyze_with_grok(
        self,
        trade_contexts: List[TradeContext],
        trade_date: date,
        dry_run: bool
    ) -> Dict[str, Any]:
        """Run analysis using Grok AI."""
        if not self.grok_client:
            return {
                'error': 'Grok client not initialized (check XAI_API_KEY)',
                'lessons': [],
                'trades_analyzed': 0
            }

        # Build prompt
        prompt = self._build_analysis_prompt(trade_contexts, trade_date)

        logger.info(f"Sending {len(trade_contexts)} trades to Grok for analysis...")

        try:
            # Call Grok API
            response = self.grok_client.chat.completions.create(
                model=self.grok_model,
                messages=[
                    {"role": "system", "content": LESSON_GENERATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,  # Some creativity, but not too much
                max_tokens=2000
            )

            # Parse response
            content = response.choices[0].message.content

            # Try to extract JSON from response (Grok sometimes wraps in markdown)
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()

            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"EOD generator: malformed LLM response - {e}. Content: {content[:200]}")
                result = {'lessons': [], 'summary': 'Failed to parse LLM response'}
            result['trades_analyzed'] = len(trade_contexts)
            result['trade_date'] = trade_date.isoformat()
            result['ai_used'] = 'grok'

            # Add lessons to database if not dry run
            if not dry_run:
                added_count = self._add_lessons_to_db(result.get('lessons', []), trade_date, regime_context=regime_context)
                result['lessons_added'] = added_count
                logger.info(f"Added {added_count} lessons to learning database")
            else:
                logger.info("DRY RUN - Lessons generated (not added to database):")
                logger.info(f"\nMeta-Summary: {result.get('meta_summary', 'N/A')}")
                logger.info(f"\nSummary: {result.get('summary', 'N/A')}\n")
                for i, lesson in enumerate(result.get('lessons', []), 1):
                    logger.info(f"Lesson {i}:")
                    logger.info(f"  Category: {lesson['category']}")
                    logger.info(f"  Confidence: {lesson['confidence']:.1f}")
                    logger.info(f"  Symbols: {', '.join(lesson.get('symbols', []))}")
                    logger.info(f"  Text: {lesson['lesson_text']}")
                    logger.info(f"  Reasoning: {lesson.get('reasoning', 'N/A')}")
                    logger.info("")

            return result

        except Exception as e:
            logger.error(f"Failed to generate lessons with Grok: {e}", exc_info=True)
            return {
                'error': str(e),
                'lessons': [],
                'trades_analyzed': len(trade_contexts)
            }

    def _analyze_with_claude(
        self,
        trade_contexts: List[TradeContext],
        trade_date: date,
        dry_run: bool
    ) -> Dict[str, Any]:
        """Run analysis using Claude AI (more expensive, more thorough)."""
        if not self.claude_client:
            logger.warning("Claude client not available, falling back to Grok")
            return self._analyze_with_grok(trade_contexts, trade_date, dry_run)

        # Build prompt
        prompt = self._build_analysis_prompt(trade_contexts, trade_date)

        logger.info(f"Sending {len(trade_contexts)} trades to Claude for deep analysis...")

        try:
            # Call Claude API
            response = self.claude_client.messages.create(
                model=self.claude_model,
                max_tokens=2000,
                temperature=0.7,
                system=LESSON_GENERATOR_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # Parse response
            content = response.content[0].text

            # Try to extract JSON from response
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()

            result = json.loads(content)
            result['trades_analyzed'] = len(trade_contexts)
            result['trade_date'] = trade_date.isoformat()
            result['ai_used'] = 'claude'

            # Add lessons to database if not dry run
            if not dry_run:
                added_count = self._add_lessons_to_db(result.get('lessons', []), trade_date, regime_context=regime_context)
                result['lessons_added'] = added_count
                logger.info(f"Added {added_count} lessons to learning database")
            else:
                logger.info("DRY RUN (Claude) - Lessons generated (not added to database):")
                logger.info(f"\nMeta-Summary: {result.get('meta_summary', 'N/A')}")
                logger.info(f"\nSummary: {result.get('summary', 'N/A')}\n")
                for i, lesson in enumerate(result.get('lessons', []), 1):
                    logger.info(f"Lesson {i}:")
                    logger.info(f"  Category: {lesson['category']}")
                    logger.info(f"  Confidence: {lesson['confidence']:.1f}")
                    logger.info(f"  Symbols: {', '.join(lesson.get('symbols', []))}")
                    logger.info(f"  Text: {lesson['lesson_text']}")
                    logger.info(f"  Reasoning: {lesson.get('reasoning', 'N/A')}")
                    logger.info("")

            return result

        except Exception as e:
            logger.error(f"Failed to generate lessons with Claude: {e}", exc_info=True)
            return {
                'error': str(e),
                'lessons': [],
                'trades_analyzed': len(trade_contexts)
            }

    def _find_semantic_duplicate(
        self,
        new_text: str,
        category: str,
        similarity_threshold: float = 0.82
    ) -> Tuple[bool, Optional[int], float]:
        """
        Check if a new lesson is semantically similar to any existing active lesson
        in the same category using fast string similarity (difflib SequenceMatcher).

        This catches paraphrases that hash differently but express the same rule.
        e.g. "Earnings gap > 1% win 85%" vs "Large earnings gaps show 85% win rate".

        Args:
            new_text: The new lesson text to check
            category: Lesson category (only compare within same category)
            similarity_threshold: 0.0-1.0; >= threshold = semantic duplicate

        Returns:
            (is_duplicate, similar_lesson_id, best_similarity_score)
        """
        try:
            existing = self.learning_db.get_lessons(
                category=category,
                active_only=True,
                limit=100,
                staleness_days=0  # Disable staleness filter for this check
            )

            if not existing:
                return False, None, 0.0

            new_normalized = new_text.lower().strip()
            best_ratio = 0.0
            best_id = None

            for lesson in existing:
                existing_text = (lesson.get('lesson_text') or '').lower().strip()
                if not existing_text:
                    continue

                ratio = difflib.SequenceMatcher(
                    None, new_normalized, existing_text
                ).ratio()

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_id = lesson.get('id')

            if best_ratio >= similarity_threshold:
                return True, best_id, best_ratio

            return False, None, best_ratio

        except Exception as e:
            logger.warning(f"Semantic duplicate check failed (skipping): {e}")
            return False, None, 0.0

    def _add_lessons_to_db(self, lessons: List[Dict], trade_date: date,
                           regime_context: dict = None) -> int:
        """
        Add generated lessons to the learning database.

        Applies two-layer deduplication before saving:
        1. Exact hash dedup (handled by learning_db.add_lesson internally)
        2. Semantic similarity check (difflib, threshold 0.82) to catch paraphrases

        Args:
            lessons: List of lesson dicts from AI response
            trade_date: Date these lessons were generated from

        Returns:
            Number of lessons successfully added
        """
        added_count = 0
        skipped_semantic = 0

        for lesson in lessons:
            try:
                # Extract primary symbol if available
                symbols = lesson.get('symbols', [])
                primary_symbol = symbols[0] if symbols else None
                lesson_text = lesson['lesson_text']
                category = lesson['category']

                # Layer 2: Semantic duplicate check (fast local, no API cost)
                is_dup, dup_id, sim_score = self._find_semantic_duplicate(
                    lesson_text, category
                )
                if is_dup:
                    logger.info(
                        f"[DEDUP] Skipping semantically similar lesson "
                        f"(similarity={sim_score:.2f}, matches lesson #{dup_id}): "
                        f"{lesson_text[:60]}"
                    )
                    skipped_semantic += 1
                    continue

                # Build notes with reasoning and metadata
                notes_parts = [lesson.get('reasoning', '')]
                if len(symbols) > 1:
                    notes_parts.append(f"Related symbols: {', '.join(symbols)}")
                notes_parts.append(f"Sample size: {lesson.get('sample_size', 1)}")
                notes = ' | '.join(filter(None, notes_parts))

                # Extract regime context fields for stratified retrieval
                _regime_score = None
                _vix_bucket = None
                _qqq_signal = None
                _spy_vs_iwm_signal = None
                _qqq_vs_iwm_signal = None
                if regime_context:
                    _regime_score = regime_context.get('swing_score')
                    _vix_bucket = regime_context.get('vix_mode')
                    _qqq_signal = regime_context.get('qqq_signal')
                    _spy_vs_iwm_signal = regime_context.get('spy_vs_iwm_signal')
                    _qqq_vs_iwm_signal = regime_context.get('qqq_vs_iwm_signal')

                # Add to database (Layer 1: exact hash dedup handled inside)
                self.learning_db.add_lesson(
                    lesson_text=lesson_text,
                    category=category,
                    confidence=lesson['confidence'],
                    symbol=primary_symbol,
                    source=f"eod_ai_{trade_date.isoformat()}",
                    notes=notes,
                    regime_score=_regime_score,
                    regime_vix_bucket=_vix_bucket,
                    qqq_signal=_qqq_signal,
                    spy_vs_iwm_signal=_spy_vs_iwm_signal,
                    qqq_vs_iwm_signal=_qqq_vs_iwm_signal,
                )
                added_count += 1

            except Exception as e:
                logger.warning(f"Failed to add lesson to database: {e}")

        if skipped_semantic > 0:
            logger.info(f"[DEDUP] Skipped {skipped_semantic} semantically duplicate lessons")

        return added_count

    def _get_trade_contexts(self, trade_date: date) -> List[TradeContext]:
        """
        Fetch all trades for a date from BOTH databases (FIX: Feb 15, 2026).

        Reads quantitative data from trading_performance.db
        AND qualitative context from learning.db/trade_journal.

        Returns:
            List of TradeContext objects with enriched data
        """
        if not self.trades_db.exists():
            logger.warning(f"Trading performance database not found: {self.trades_db}")
            return []

        trade_contexts = []

        try:
            # Step 1: Get quantitative trade data from trading_performance.db
            perf_conn = sqlite3.connect(self.trades_db)
            perf_conn.row_factory = sqlite3.Row
            perf_cursor = perf_conn.cursor()

            # Get trades for date (match on exit_time date)
            perf_cursor.execute("""
                SELECT
                    symbol, entry_time, exit_time, entry_price, exit_price,
                    quantity, realized_pnl, realized_pnl_percent, exit_reason,
                    stop_price, target_price, strategy
                FROM trades
                WHERE DATE(exit_time) = ?
                ORDER BY exit_time
            """, (trade_date.isoformat(),))

            trades = perf_cursor.fetchall()
            perf_conn.close()

            # Step 2: Get qualitative context from learning.db/trade_journal
            journal_entries = {}
            if self.learning_db_path.exists():
                try:
                    learn_conn = sqlite3.connect(self.learning_db_path)
                    learn_conn.row_factory = sqlite3.Row
                    learn_cursor = learn_conn.cursor()

                    # Get all journal entries for this date
                    learn_cursor.execute("""
                        SELECT
                            symbol, entry_time, why_entered, catalyst,
                            market_context, setup_type, confidence_level
                        FROM trade_journal
                        WHERE trade_date = ?
                    """, (trade_date.isoformat(),))

                    for row in learn_cursor.fetchall():
                        # Key by symbol + entry_time for matching
                        key = (row['symbol'], row['entry_time'])
                        journal_entries[key] = {
                            'why_entered': row['why_entered'],
                            'catalyst': row['catalyst'],
                            'market_context': row['market_context'],
                            'setup_type': row['setup_type'],
                            'confidence_level': row['confidence_level']
                        }

                    learn_conn.close()
                    logger.info(f"Loaded {len(journal_entries)} trade journal entries for {trade_date}")

                except Exception as e:
                    logger.warning(f"Failed to load trade journal entries: {e}")

            # Step 3: Merge quantitative + qualitative data
            for row in trades:
                # Calculate intraday metrics if we have stop/target data
                entry_price = row['entry_price']
                exit_price = row['exit_price']
                target = row['target_price']
                stop = row['stop_price']

                # Estimate max profit/drawdown based on exit reason
                max_profit_pct = None
                max_drawdown_pct = None

                if target and entry_price:
                    # If target was hit, max profit is at least target level
                    if 'target' in (row['exit_reason'] or '').lower():
                        max_profit_pct = ((target - entry_price) / entry_price) * 100
                    # Otherwise, exit price is max we know about
                    elif exit_price > entry_price:
                        max_profit_pct = ((exit_price - entry_price) / entry_price) * 100

                if stop and entry_price:
                    # If stop was hit, that's our max drawdown
                    if 'stop' in (row['exit_reason'] or '').lower():
                        max_drawdown_pct = ((stop - entry_price) / entry_price) * 100
                    # Otherwise, exit price is worst we know about (if negative)
                    elif exit_price < entry_price:
                        max_drawdown_pct = ((exit_price - entry_price) / entry_price) * 100

                # Look up journal entry for this trade
                # Match by symbol + entry_time (truncate to HH:MM:SS for matching)
                entry_time_str = row['entry_time']
                if 'T' in entry_time_str:
                    # ISO format: 2026-02-15T09:30:00
                    entry_time_only = entry_time_str.split('T')[1].split('.')[0]  # Get HH:MM:SS
                else:
                    entry_time_only = entry_time_str.split()[1] if ' ' in entry_time_str else entry_time_str

                journal_key = (row['symbol'], entry_time_only)
                journal_data = journal_entries.get(journal_key, {})

                # Create TradeContext with both quantitative + qualitative data
                context = TradeContext(
                    symbol=row['symbol'],
                    entry_time=row['entry_time'],
                    exit_time=row['exit_time'],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=row['quantity'],
                    pnl=row['realized_pnl'],
                    pnl_pct=row['realized_pnl_percent'],
                    exit_reason=row['exit_reason'] or 'unknown',
                    conviction=None,  # Not stored in trades table
                    entry_rationale=row['strategy'],  # strategy field has entry context
                    stop_price=stop,
                    target_price=target,
                    max_profit_pct=max_profit_pct,
                    max_drawdown_pct=max_drawdown_pct,
                    # NEW: Add context from trade_journal
                    why_entered=journal_data.get('why_entered'),
                    catalyst=journal_data.get('catalyst'),
                    market_context=journal_data.get('market_context'),
                    setup_type=journal_data.get('setup_type'),
                    confidence_level=journal_data.get('confidence_level')
                )

                trade_contexts.append(context)

            logger.info(f"Created {len(trade_contexts)} trade contexts for {trade_date}")
            if journal_entries:
                matched = sum(1 for t in trade_contexts if t.why_entered is not None)
                logger.info(f"Matched {matched}/{len(trade_contexts)} trades with journal entries")

        except Exception as e:
            logger.error(f"Failed to fetch trades for {trade_date}: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return trade_contexts

    def _get_pattern_context(
        self,
        trade_date: date,
        trade_contexts: List[TradeContext]
    ) -> str:
        """
        Get validated pattern context for EOD analysis.

        Phase 2A implementation - provides backtest validation context
        to lesson generator (Grok's CRITICAL #2 recommendation from EOD review).

        Returns:
            Formatted string with validated patterns or empty string if unavailable
        """
        try:
            # Import here to avoid circular dependency
            from analytics.attribution_analyzer import AttributionAnalyzer

            analyzer = AttributionAnalyzer(db_path=self.trading_db_path)

            # Get today's symbols and strategies for relevance filtering
            symbols_today = list(set(tc.symbol for tc in trade_contexts))

            # Get pattern context (top 10 patterns, filtered by relevance)
            context = analyzer.get_pattern_context_for_eod(
                today_symbols=symbols_today,
                top_n=10  # Grok's recommendation: limit for token budget
            )

            if context:
                return context
            else:
                return ""

        except Exception as e:
            # Pattern context is optional - don't break EOD if attribution system unavailable
            logger.warning(f"Could not fetch pattern context: {e}")
            return ""

    # ------------------------------------------------------------------
    # NEW: Enrichment methods for EOD overhaul (Feb 2026)
    # ------------------------------------------------------------------

    def _get_active_lessons_compact(self, category_filter: List[str] = None) -> List[Dict]:
        """
        Return active lessons from learning.db in compact format for duplicate suppression.
        If category_filter provided, only return lessons in those categories.
        Capped at 50 total. Returns list of {id, category, lesson_text}.
        """
        try:
            conn = sqlite3.connect(self.learning_db_path)
            c = conn.cursor()
            if category_filter:
                ph = ",".join("?" * len(category_filter))
                c.execute(
                    f"SELECT id, category, lesson_text FROM lessons "
                    f"WHERE is_active = 1 AND category IN ({ph}) "
                    f"ORDER BY id DESC LIMIT 50",
                    category_filter,
                )
            else:
                c.execute(
                    "SELECT id, category, lesson_text FROM lessons "
                    "WHERE is_active = 1 ORDER BY id DESC LIMIT 50"
                )
            rows = c.fetchall()
            conn.close()
            return [{"id": r[0], "category": r[1], "lesson_text": r[2]} for r in rows]
        except Exception as e:
            logger.warning(f"Could not fetch active lessons for dedup: {e}")
            return []

    def _get_brave_api_key(self) -> str:
        """Get Brave Search API key from broker_config.json or .mcp.json."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
            key = cfg.get('brave_search', {}).get('api_key', '')
            if key and key != 'YOUR_BRAVE_API_KEY_HERE':
                return key
            mcp_path = Path(self.config_path).parent.parent / '.mcp.json'
            if mcp_path.exists():
                with open(mcp_path) as f:
                    mcp = json.load(f)
                return (
                    mcp.get('mcpServers', {})
                       .get('brave-search', {})
                       .get('env', {})
                       .get('BRAVE_API_KEY', '')
                )
        except Exception:
            pass
        return ''

    def _brave_search(self, query: str, count: int = 3) -> List[Dict]:
        """Single Brave Search API call. Returns list of {title, description} dicts."""
        api_key = self._get_brave_api_key()
        if not api_key:
            return []
        try:
            import requests
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
                params={"q": query, "count": count, "freshness": "pd"},
                timeout=10,
            )
            if resp.status_code == 200:
                return [
                    {
                        "title": r.get("title", ""),
                        "description": r.get("description", ""),
                    }
                    for r in resp.json().get("web", {}).get("results", [])
                ]
            logger.warning(f"Brave search returned {resp.status_code} for: {query}")
        except Exception as e:
            logger.warning(f"Brave search failed ({query}): {e}")
        return []

    def _fetch_market_context(self, trade_date: date, symbols: List[str]) -> str:
        """
        Fetch brief market context via Brave Search for the EOD prompt.
        Returns formatted string; empty string if Brave unavailable.
        Searches: SPY overview, VIX, up to 3 individual symbols.
        """
        date_str = trade_date.strftime("%B %d %Y")
        parts = []

        spy_results = self._brave_search(
            f"SPY stock market {date_str} performance summary", count=2
        )
        if spy_results:
            parts.append("## Market Context (Brave Search)")
            parts.append(f"**Market on {date_str}:**")
            for r in spy_results[:2]:
                if r['description']:
                    parts.append(f"- {r['description'][:200]}")

        vix_results = self._brave_search(f"VIX volatility index {date_str}", count=1)
        if vix_results and vix_results[0]['description']:
            parts.append(f"**VIX:** {vix_results[0]['description'][:200]}")

        for sym in symbols[:3]:
            sym_results = self._brave_search(
                f"{sym} stock news {date_str}", count=2
            )
            sym_snippets = [
                r['description'][:150]
                for r in sym_results
                if r['description']
            ]
            if sym_snippets:
                parts.append(f"**{sym} news:** {' | '.join(sym_snippets[:2])}")

        return "\n".join(parts) if parts else ""

    def _fetch_alpaca_daily_bars(
        self, symbols: List[str], trade_date: date
    ) -> Dict[str, Dict]:
        """
        Fetch 1-day OHLCV bars from Alpaca for the given symbols and date.
        Returns {symbol: {open, high, low, close, volume}}. Silently skips failures.
        """
        if not symbols:
            return {}
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
            alpaca_key = (
                cfg.get('alpaca', {}).get('api_key')
                or os.getenv('APCA_API_KEY_ID')
            )
            alpaca_secret = (
                cfg.get('alpaca', {}).get('secret_key')
                or os.getenv('APCA_API_SECRET_KEY')
            )
            if not alpaca_key or not alpaca_secret:
                logger.warning("Alpaca keys not found - skipping intraday price fetch")
                return {}

            import requests
            headers = {
                "APCA-API-KEY-ID": alpaca_key,
                "APCA-API-SECRET-KEY": alpaca_secret,
            }
            start = trade_date.isoformat()
            end = (trade_date + timedelta(days=1)).isoformat()

            resp = requests.get(
                "https://data.alpaca.markets/v2/stocks/bars",
                headers=headers,
                params={
                    "symbols": ",".join(symbols[:30]),
                    "timeframe": "1Day",
                    "start": start,
                    "end": end,
                    "feed": "iex",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                result = {}
                for sym, bars in resp.json().get("bars", {}).items():
                    if bars:
                        b = bars[0]
                        result[sym] = {
                            "open": b.get("o"),
                            "high": b.get("h"),
                            "low": b.get("l"),
                            "close": b.get("c"),
                            "volume": b.get("v"),
                        }
                return result
            logger.warning(f"Alpaca bars fetch returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Alpaca daily bars fetch failed: {e}")
        return {}

    def _get_considered_stocks(self, trade_date: date) -> List[Dict]:
        """
        Return stocks from daily_candidate_snapshot that were NOT traded today.
        Enriches each with intraday price data from Alpaca.
        Post-hoc rejection reason inferred from gap_pct if DB column is NULL.

        Returns list of dicts: symbol, source, gap_pct, catalyst_headline,
        opening_price, day_high, eod_close, high_vs_open_pct, close_vs_open_pct,
        rejection_reason, conviction_score.
        """
        # Get traded symbols today
        traded_symbols: set = set()
        try:
            conn = sqlite3.connect(self.trades_db)
            c = conn.cursor()
            c.execute(
                "SELECT DISTINCT symbol FROM trades WHERE DATE(exit_time) = ?",
                (trade_date.isoformat(),),
            )
            traded_symbols = {row[0] for row in c.fetchall()}
            conn.close()
        except Exception as e:
            logger.warning(f"Could not fetch traded symbols for considered-stocks: {e}")

        # Get all snapshot candidates for the date
        rows = []
        try:
            conn = sqlite3.connect(self.trades_db)
            c = conn.cursor()
            c.execute(
                """
                SELECT symbol, source, gap_pct, catalyst_headline, opening_price,
                       conviction_score, rejection_reason
                FROM daily_candidate_snapshot
                WHERE trade_date = ?
                ORDER BY gap_pct DESC
                """,
                (trade_date.isoformat(),),
            )
            rows = c.fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"Could not fetch daily_candidate_snapshot: {e}")
            return []

        not_traded = [r for r in rows if r[0] not in traded_symbols]
        if not not_traded:
            return []

        # Enrich with Alpaca intraday prices
        price_data = self._fetch_alpaca_daily_bars(
            [r[0] for r in not_traded], trade_date
        )

        results = []
        for row in not_traded:
            sym, source, gap_pct, headline, open_px, conviction, reject_reason = row
            bars = price_data.get(sym, {})
            day_high = bars.get('high')
            eod_close = bars.get('close')

            # Infer post-hoc rejection reason if not stored
            if not reject_reason:
                if gap_pct is not None and gap_pct > 15:
                    reject_reason = (
                        f"Gap too large ({gap_pct:.1f}%) - pattern likely exhausted"
                    )
                elif gap_pct is not None and gap_pct < 1:
                    reject_reason = (
                        f"Gap too small ({gap_pct:.1f}%) - insufficient catalyst"
                    )
                else:
                    reject_reason = "Below conviction threshold"

            high_vs_open = None
            close_vs_open = None
            if open_px and open_px > 0:
                if day_high:
                    high_vs_open = ((day_high - open_px) / open_px) * 100
                if eod_close:
                    close_vs_open = ((eod_close - open_px) / open_px) * 100

            results.append({
                'symbol': sym,
                'source': source,
                'gap_pct': gap_pct,
                'catalyst_headline': headline,
                'opening_price': open_px,
                'day_high': day_high,
                'eod_close': eod_close,
                'high_vs_open_pct': high_vs_open,
                'close_vs_open_pct': close_vs_open,
                'rejection_reason': reject_reason,
                'conviction_score': conviction,
            })

        return results

    def _build_analysis_prompt(
        self,
        trade_contexts: List[TradeContext],
        trade_date: date
    ) -> str:
        """
        Build the enriched analysis prompt for Grok.
        Sections: validated patterns, active lessons (dedup), market context,
        traded stocks, considered-but-skipped stocks.
        """
        prompt_parts = [
            f"# Trading Day Analysis: {trade_date.strftime('%B %d, %Y')}",
            f"\nTotal trades: {len(trade_contexts)}",
        ]

        # SECTION 1: Validated pattern context (Phase 2A - existing)
        pattern_context = self._get_pattern_context(trade_date, trade_contexts)
        if pattern_context:
            prompt_parts.append("\n" + pattern_context)

        # SECTION 2: Active lessons for duplicate suppression
        # Filter to categories most likely to be generated today
        relevant_cats = [
            "symbol_behavior", "entry_timing", "exit_timing",
            "risk_management", "avoid_entry", "avoid_timing",
        ]
        active_lessons = self._get_active_lessons_compact(category_filter=relevant_cats)
        if active_lessons:
            prompt_parts.append("\n## Existing Active Lessons (DO NOT DUPLICATE):")
            prompt_parts.append(
                "Do not generate lessons that substantially overlap any of these:"
            )
            for lesson in active_lessons:
                prompt_parts.append(
                    f"- [L{lesson['id']}|{lesson['category']}] {lesson['lesson_text']}"
                )

        # SECTION 3: Market context from Brave Search
        all_symbols = list(set(t.symbol for t in trade_contexts))
        market_ctx = self._fetch_market_context(trade_date, all_symbols)
        if market_ctx:
            prompt_parts.append("\n" + market_ctx)

        # SECTION 4: Traded stocks
        prompt_parts.append("\n## Trades:\n")
        for i, trade in enumerate(trade_contexts, 1):
            prompt_parts.append(f"### Trade {i}: {trade.symbol}")
            prompt_parts.append(f"- Entry: {trade.entry_time} @ ${trade.entry_price:.2f}")
            prompt_parts.append(f"- Exit: {trade.exit_time} @ ${trade.exit_price:.2f}")
            prompt_parts.append(f"- P&L: ${trade.pnl:.2f} ({trade.pnl_pct:+.1f}%)")
            prompt_parts.append(f"- Exit reason: {trade.exit_reason}")
            if trade.why_entered:
                prompt_parts.append(f"- Why entered: {trade.why_entered}")
            if trade.catalyst:
                prompt_parts.append(f"- Catalyst: {trade.catalyst}")
            if trade.market_context:
                prompt_parts.append(f"- Market context: {trade.market_context}")
            if trade.setup_type:
                prompt_parts.append(f"- Setup type: {trade.setup_type}")
            if trade.confidence_level:
                prompt_parts.append(f"- Confidence at entry: {trade.confidence_level}/10")
            if trade.conviction:
                prompt_parts.append(f"- Conviction: {trade.conviction}/10")
            if trade.entry_rationale:
                prompt_parts.append(f"- Entry rationale: {trade.entry_rationale}")
            if trade.stop_price:
                prompt_parts.append(f"- Stop loss: ${trade.stop_price:.2f}")
            if trade.target_price:
                prompt_parts.append(f"- Take profit: ${trade.target_price:.2f}")
            if trade.max_profit_pct is not None:
                prompt_parts.append(f"- Max profit reached: {trade.max_profit_pct:+.1f}%")
            if trade.max_drawdown_pct is not None:
                prompt_parts.append(f"- Max drawdown: {trade.max_drawdown_pct:+.1f}%")
            prompt_parts.append("")

        # SECTION 5: Considered-but-skipped stocks
        considered = self._get_considered_stocks(trade_date)
        if considered:
            prompt_parts.append("\n## Stocks Considered But Not Traded:\n")
            prompt_parts.append(
                "These passed initial scans but were not entered. "
                "Use to generate lessons about correct avoidances OR missed opportunities."
            )
            for stock in considered:
                prompt_parts.append(f"\n### Skipped: {stock['symbol']}")
                prompt_parts.append(f"- Source: {stock['source']}")
                if stock['gap_pct'] is not None:
                    prompt_parts.append(f"- Gap at open: {stock['gap_pct']:+.1f}%")
                if stock['catalyst_headline']:
                    prompt_parts.append(
                        f"- Catalyst: {stock['catalyst_headline'][:150]}"
                    )
                if stock['opening_price']:
                    prompt_parts.append(
                        f"- Opening price: ${stock['opening_price']:.2f}"
                    )
                if stock['high_vs_open_pct'] is not None:
                    prompt_parts.append(
                        f"- Day high vs open: {stock['high_vs_open_pct']:+.1f}%"
                        f" (${stock['day_high']:.2f})"
                    )
                if stock['close_vs_open_pct'] is not None:
                    prompt_parts.append(
                        f"- EOD close vs open: {stock['close_vs_open_pct']:+.1f}%"
                        f" (${stock['eod_close']:.2f})"
                    )
                if stock['conviction_score'] is not None:
                    prompt_parts.append(
                        f"- Conviction score: {stock['conviction_score']}/10"
                    )
                prompt_parts.append(
                    f"- Reason not traded: {stock['rejection_reason']}"
                )
                prompt_parts.append("")

        prompt_parts.append("\n## Analysis Task:")
        prompt_parts.append(
            "Analyze trades AND skipped stocks. Generate 2-6 actionable lessons."
        )
        prompt_parts.append(
            "Focus on: trade patterns, correct avoidances, missed opportunities."
        )
        prompt_parts.append(
            "CRITICAL: Do not duplicate any lesson already listed in "
            "Existing Active Lessons above."
        )
        prompt_parts.append("Return results in JSON format as specified in the system prompt.")

        return "\n".join(prompt_parts)


def main():
    """CLI interface for testing the lesson generator."""
    import sys
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    parser = argparse.ArgumentParser(description='EOD lesson generator')
    parser.add_argument(
        'date', nargs='?',
        help='Trade date YYYY-MM-DD (default: today)'
    )
    parser.add_argument(
        '--live', action='store_true',
        help='Actually save lessons to DB (default: dry-run)'
    )
    parser.add_argument(
        '--show-prompt', action='store_true',
        help='Print the full constructed prompt and exit (no API call)'
    )
    parser.add_argument(
        '--force-claude', action='store_true',
        help='Use Claude instead of Grok for analysis'
    )
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.date) if args.date else date.today()
    dry_run = not args.live

    logger.info(f"Analyzing trades for {trade_date}")
    generator = EODLessonGenerator()

    if args.show_prompt:
        trade_contexts = generator._get_trade_contexts(trade_date)
        prompt = generator._build_analysis_prompt(trade_contexts, trade_date)
        print("\n" + "=" * 60)
        print("CONSTRUCTED PROMPT (--show-prompt mode)")
        print("=" * 60)
        print(prompt)
        print(f"\n[System prompt: {len(LESSON_GENERATOR_SYSTEM_PROMPT)} chars]")
        print(f"[User prompt: {len(prompt)} chars]")
        print(f"[Trades in context: {len(trade_contexts)}]")
        return

    result = generator.analyze_trades(
        trade_date, dry_run=dry_run, force_claude=args.force_claude
    )

    if 'error' in result:
        logger.error(f"Error: {result['error']}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("EOD LESSON GENERATION COMPLETE")
    print("=" * 60)
    print(f"Trades analyzed: {result['trades_analyzed']}")
    print(f"Lessons generated: {len(result.get('lessons', []))}")
    if not dry_run:
        print(f"Lessons saved to DB: {result.get('lessons_added', 0)}")
    print(f"\nSummary: {result.get('summary', 'N/A')}")
    print("=" * 60)


if __name__ == '__main__':
    main()
