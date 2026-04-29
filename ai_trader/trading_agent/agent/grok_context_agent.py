"""
Grok Context Agent - Daily Context Summary System

Manages daily trading context from morning grok-4-0709 analysis and provides
it to fast model throughout the day for consistency and cost efficiency.

Architecture:
1. Morning: grok-4-0709 analyzes regime → generates Daily Context Summary
2. Intraday: grok-4-1-fast-reasoning gets summary prepended to all prompts
3. Result: Fast model knows morning strategy, constraints, and market thesis

Author: Claude Code + Grok outline
"""

import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class GrokContextAgent:
    """
    Manages daily trading context from morning analysis.

    Key Features:
    - Generates structured summary from morning grok-4-0709 regime analysis
    - Prepends context to all intraday fast model queries
    - Time-based model routing (morning vs intraday)
    - Caches summary for recovery after restarts
    """

    def __init__(self, agent, config: dict):
        """
        Initialize context agent.

        Args:
            agent: The main trading agent (ClaudeTradingAgent/GrokTradingAgent)
            config: Configuration dict with model names
        """
        self.agent = agent
        self.daily_summary: Optional[str] = None
        self.summary_timestamp: Optional[datetime] = None
        self.config = config

        # Model selection
        self.morning_model = config.get("thinking_model", "grok-4-0709")
        self.fast_model = config.get("fast_model", "grok-4-1-fast-reasoning")

        # Cache file for recovery
        cache_dir = Path(__file__).parent.parent.parent / "ai_trader_data"
        cache_dir.mkdir(exist_ok=True, parents=True)
        self.cache_file = cache_dir / "daily_context_cache.json"

        logger.info("GrokContextAgent initialized")
        logger.info(f"  Morning model: {self.morning_model}")
        logger.info(f"  Fast model: {self.fast_model}")
        logger.info(f"  Cache file: {self.cache_file}")

    def generate_morning_summary(self, regime_info: dict, market_news: str = None) -> str:
        """
        Generate Daily Context Summary after morning regime check.

        Uses grok-4-0709 to create structured Markdown summary with:
        - Market regime and reasoning
        - Trading strategy and thesis
        - Risk parameters
        - Trade criteria and constraints
        - Key market context

        Args:
            regime_info: Market regime data from regime filter

        Returns:
            Markdown-formatted daily context summary
        """
        logger.info("Generating daily context summary...")

        # Build prompt for summary generation
        summary_prompt = self._build_summary_generation_prompt(regime_info, market_news=market_news)

        # Try Ollama first for morning summary (free local inference)
        response = None
        ollama = getattr(self.agent, 'ollama_provider', None)
        if ollama and ollama.is_available():
            logger.info("Using Ollama for morning context summary")
            response = ollama.complete(
                summary_prompt,
                system="You are a professional trading analyst. Generate clear, structured daily context summaries in Markdown format."
            )
            if response:
                logger.info(f"Ollama morning summary generated ({len(response)} chars)")

        # Call grok-4-0709 to generate summary if Ollama not available/failed
        try:
            if not response:
                # Temporarily set context for model selection
                original_context = self.agent.current_context
                self.agent.current_context = "market_regime_analysis"  # Triggers thinking model

                # Generate summary
                response = self.agent.send_message(summary_prompt)

                # Restore context
                self.agent.current_context = original_context

            # Store summary
            self.daily_summary = response
            self.summary_timestamp = datetime.now()

            # Cache to file for recovery
            self._cache_summary()

            logger.info(f"Daily context summary generated ({len(self.daily_summary)} chars)")
            logger.debug(f"Summary preview: {self.daily_summary[:200]}...")

            return self.daily_summary

        except Exception as e:
            logger.error(f"Failed to generate morning summary: {e}")
            logger.info("Using fallback summary")

            # Fallback: Create basic summary from regime_info
            self.daily_summary = self._create_fallback_summary(regime_info)

            # Try to cache fallback
            try:
                self._cache_summary()
            except:
                pass

            return self.daily_summary

    def _build_summary_generation_prompt(self, regime_info: dict, market_news: str = None) -> str:
        """Build prompt asking grok-4-0709 to create daily summary."""

        regime = regime_info.get('regime', 'NEUTRAL')
        spy_change = regime_info.get('spy_change', 0)
        qqq_change = regime_info.get('qqq_change', 0)
        vix = regime_info.get('vix', 0)
        min_conviction = regime_info.get('min_conviction', 8.0)
        max_positions = regime_info.get('max_positions', 2)

        today = datetime.now().strftime('%Y-%m-%d')

        # Build market context section - use Perplexity data if available
        if market_news:
            market_context_section = (
                "## Market Context\n"
                "Pre-market intelligence (Perplexity, ~6:20 AM):\n"
                + market_news
            )
        else:
            market_context_section = (
                "## Market Context\n"
                "- Futures Pre-Market: [ES/NQ levels if known, else N/A]\n"
                "- Overnight News: [Major catalysts, earnings, geopolitical events?]\n"
                "- Economic Calendar: [Fed announcements, jobs data, CPI today?]\n"
                "- Technical Levels: [Key SPY/QQQ support/resistance to watch]\n"
                "- Sector Rotation: [What's in play? Tech, energy, biotech?]"
            )

        prompt = f"""You just analyzed the market regime and determined the following:

Market Regime: {regime}
SPY Change: {spy_change:+.2f}%
QQQ Change: {qqq_change:+.2f}%
VIX Level: {vix:.2f}
Min Conviction Required: {min_conviction}/10
Max Positions Allowed: {max_positions}

Now create a comprehensive Daily Context Summary that will guide ALL trading decisions today.
This summary will be prepended to every prompt sent to the fast model throughout the day.

Format as clear Markdown with these sections:

# Daily Trading Context - {today}

## Market Regime
- Regime: {regime}
- Reasoning: [Why this classification? Consider SPY/QQQ moves, VIX level, overnight action]
- Key Indicators: [What signals led to this conclusion?]
- Implications: [What does this mean for trading today?]

## Trading Strategy
- Primary Strategy: [momentum/mean_reversion/breakout/news_catalyst - pick ONE based on regime]
- Core Thesis: [1-2 sentence market outlook for the day]
- Focus Sectors: [Which sectors look strong? Which to avoid?]
- Best Setups: [What trade types work best in this regime?]

## Risk Parameters
- Max Positions: {max_positions}
- Min Conviction Required: {min_conviction}/10
- Position Size: [Standard or reduced based on regime?]
- Stop Loss Strategy: [Tighter or wider based on VIX/volatility?]
- Max Daily Loss: 5% (circuit breaker)

## Trade Criteria (DO NOT OVERRIDE THESE)
- Volume: >750K shares minimum
- Price Range: $5-$200 per share
- Catalyst Age: <2 days preferred (fresher is better)
- Relative Strength: Must outperform SPY/QQQ by +1.2% minimum
- VWAP Requirement: Within 1.0% for long entries
- Momentum Confirmation: Price making higher highs on 5-min chart
- Swing hold: 2-7 days (PEAD catalysts up to 10 days, H5 validated); hold overnight unless exit conditions met

## Key Constraints (NEVER VIOLATE)
- NEVER add to a losing position or average down
- NEVER exceed max positions ({max_positions} today)
- NEVER take trades below min conviction ({min_conviction}/10 today)
- NEVER trade without bracket orders (entry + TP + SL required)
- NEVER trade mega-caps (AAPL, MSFT, GOOGL, AMZN - too stable)
- NEVER chase stocks already up 15%+ (missed the move)
- NEVER fight the daily trend (align intraday with daily timeframe)

{market_context_section}

## Watchlist Focus
[Based on regime and strategy, what types of stocks should we scan for?]
- Example: "In STRONG_BULLISH regime, focus on momentum breakouts in growth tech with earnings beats"
- Example: "In BEARISH regime, focus on high-conviction oversold bounces in quality names only"

---

Be concise but complete. This summary will be the foundation for ALL trading decisions today.
Fast model needs clear guidance on strategy, risk, and constraints.
"""

        return prompt

    def _create_fallback_summary(self, regime_info: dict) -> str:
        """Create minimal summary if generation fails."""

        regime = regime_info.get('regime', 'NEUTRAL')
        min_conviction = regime_info.get('min_conviction', 8.0)
        max_positions = regime_info.get('max_positions', 2)
        today = datetime.now().strftime('%Y-%m-%d')

        return f"""# Daily Trading Context - {today}

## Market Regime
Regime: {regime}
Min Conviction: {min_conviction}/10
Max Positions: {max_positions}

## Risk Parameters
- Hold swing positions overnight; exit only on technical conditions (trailing stop, PEAD end, time kill)
- Use bracket orders only (entry + TP + SL)
- Volume >750K, Price $5-$200
- Outperform SPY/QQQ by +1.2% minimum

## Key Constraints
- Never add to a losing position
- Never exceed max positions
- Never trade below min conviction
- Never trade without stop loss

(Fallback summary - full summary generation failed)
"""

    def get_intraday_prompt(self, user_message: str, current_state: dict) -> str:
        """
        Build enriched prompt for fast model with daily context.

        Combines:
        1. Daily Context Summary (from morning grok-4-0709)
        2. Current State (positions, P&L, time, etc.)
        3. User Message (the actual task/question)

        Args:
            user_message: The trading task or question
            current_state: Dict with positions, account_value, daily_pnl, etc.

        Returns:
            Full prompt with context prepended
        """
        # Ensure we have a summary
        if not self.daily_summary:
            logger.warning("No daily summary available - attempting to load from cache")
            self._load_cached_summary()

            if not self.daily_summary:
                logger.warning("No cached summary found - using minimal context")
                self.daily_summary = "(No daily context summary available)"

        # Build current state section
        state_section = self._format_current_state(current_state)

        # Get current time
        current_time = datetime.now().strftime('%I:%M %p ET')

        # Combine: Daily Context + Current State + User Message
        full_prompt = f"""{self.daily_summary}

---

## Current State ({current_time})
{state_section}

---

## Task
{user_message}

**Remember**: Follow the strategy, risk parameters, and constraints from today's Daily Context above.
"""

        logger.debug(f"Built intraday prompt: {len(full_prompt)} chars total")

        return full_prompt

    def _format_current_state(self, state: dict) -> str:
        """Format current trading state for context."""

        positions = state.get('positions', {})
        account_value = state.get('account_value', 0)
        daily_pnl = state.get('daily_pnl', 0)
        daily_pnl_pct = state.get('daily_pnl_pct', 0)
        max_positions = state.get('max_positions', 2)

        # Build state summary
        state_lines = []
        state_lines.append(f"**Positions**: {len(positions)}/{max_positions}")
        state_lines.append(f"**Account Value**: ${account_value:,.2f}")
        state_lines.append(f"**Daily P&L**: ${daily_pnl:+,.2f} ({daily_pnl_pct:+.2f}%)")

        # Add position details if any
        if positions:
            state_lines.append("")
            state_lines.append("**Open Positions**:")

            for symbol, details in positions.items():
                if isinstance(details, dict):
                    qty = details.get('quantity', 0)
                    entry = details.get('entry_price', 0)
                    pnl = details.get('unrealized_pnl', 0)
                    state_lines.append(f"- {symbol}: {qty} shares @ ${entry:.2f}, P&L: ${pnl:+.2f}")
                else:
                    # Simple format (just quantity)
                    state_lines.append(f"- {symbol}: {details} shares")
        else:
            state_lines.append("")
            state_lines.append("**No open positions** - looking for new opportunities")

        return "\n".join(state_lines)

    def select_model_by_time(self) -> str:
        """
        Time-based model routing.

        Rules:
        - Before 9:30 AM ET: Use grok-4-0709 (morning analysis)
        - After 9:30 AM ET: Use grok-4-1-fast-reasoning (with context)

        Returns:
            Model name to use
        """
        now = datetime.now()

        # Market open time (9:30 AM ET)
        # Note: This assumes system timezone is ET, adjust if needed
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)

        if now < market_open:
            logger.debug("Before market open - using morning model (grok-4-0709)")
            return self.morning_model
        else:
            logger.debug("After market open - using fast model (grok-4-1-fast-reasoning)")
            return self.fast_model

    def query_model(self, prompt: str, model_name: Optional[str] = None) -> str:
        """
        Query the appropriate model with optional override.

        Handles:
        - Time-based model selection if no override
        - Temporary model switching
        - Error handling

        Args:
            prompt: The full prompt to send
            model_name: Optional model override (default: time-based selection)

        Returns:
            Model response text
        """
        # Select model
        selected_model = model_name or self.select_model_by_time()

        logger.info(f"Querying model: {selected_model}")

        # Save current agent model
        original_model = self.agent.model

        try:
            # Temporarily switch to selected model
            self.agent.model = selected_model

            # Send message
            response = self.agent.send_message(prompt)

            return response

        except Exception as e:
            logger.error(f"Error querying {selected_model}: {e}")

            # Retry once with fallback to fast model
            if selected_model != self.fast_model:
                logger.info(f"Retrying with fast model ({self.fast_model})")
                try:
                    self.agent.model = self.fast_model
                    response = self.agent.send_message(prompt)
                    return response
                except Exception as e2:
                    logger.error(f"Fallback query also failed: {e2}")
                    raise
            else:
                raise

        finally:
            # Restore original model
            self.agent.model = original_model

    def _cache_summary(self):
        """Save summary to file for recovery after restart."""
        try:
            cache_data = {
                "summary": self.daily_summary,
                "timestamp": self.summary_timestamp.isoformat() if self.summary_timestamp else None,
                "date": datetime.now().strftime('%Y-%m-%d'),
                "regime": "cached"  # Could extract from summary if needed
            }

            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)

            logger.debug(f"Cached daily summary to {self.cache_file}")

        except Exception as e:
            logger.warning(f"Failed to cache daily summary: {e}")

    def _load_cached_summary(self):
        """Load summary from cache if it exists and is from today."""
        try:
            if not self.cache_file.exists():
                logger.debug("No cache file found")
                return

            with open(self.cache_file, 'r') as f:
                cache_data = json.load(f)

            # Check if cache is from today
            cache_date = cache_data.get('date')
            today = datetime.now().strftime('%Y-%m-%d')

            if cache_date == today:
                self.daily_summary = cache_data.get('summary')
                timestamp_str = cache_data.get('timestamp')

                if timestamp_str:
                    self.summary_timestamp = datetime.fromisoformat(timestamp_str)

                logger.info("Loaded daily context summary from cache")
                logger.debug(f"Cache timestamp: {self.summary_timestamp}")
            else:
                logger.info(f"Cached summary is from {cache_date}, not today ({today}) - ignoring")

        except Exception as e:
            logger.warning(f"Failed to load cached summary: {e}")

    def is_summary_available(self) -> bool:
        """Check if daily summary is available."""
        return self.daily_summary is not None

    def get_summary_age(self) -> Optional[float]:
        """Get age of summary in hours."""
        if not self.summary_timestamp:
            return None

        age = datetime.now() - self.summary_timestamp
        return age.total_seconds() / 3600  # Convert to hours


if __name__ == "__main__":
    # Quick test
    print("GrokContextAgent module loaded successfully")
    print()
    print("Example usage:")
    print("  context_agent = GrokContextAgent(agent, config)")
    print("  summary = context_agent.generate_morning_summary(regime_info)")
    print("  prompt = context_agent.get_intraday_prompt('Analyze AAPL', current_state)")
    print("  response = context_agent.query_model(prompt)")
