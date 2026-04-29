"""
Agent State Manager - Manages conversation state and trading rules

Extracted from ClaudeTradingAgent monolith (Phase 1 refactor)
Manages: System prompt building, trading rules, conversation state, daily reset
"""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from utils.position_quantity import get_position_quantity

logger = logging.getLogger(__name__)


class AgentStateManager:
    """
    Manages conversation state and trading rules for ClaudeTradingAgent.

    Responsibilities:
    - Load and manage trading rules from file
    - Build system prompts with context
    - Track agent state (cash, positions, strategies, etc.)
    - Handle daily reset logic
    - Generate protected positions warnings
    """

    def __init__(
        self,
        rules_file: Path,
        ai_trader_data_path: Path,
        broker=None
    ):
        """
        Initialize agent state manager.

        Args:
            rules_file: Path to trading rules XML file
            ai_trader_data_path: Path to ai_trader_data directory
            broker: Trading broker instance (for position reconciliation)
        """
        self.rules_file = rules_file
        self.ai_trader_data_path = ai_trader_data_path
        self.broker = broker

        # Load trading rules
        self.rules_content = self._load_rules()

        # Load lessons once at startup (stable for the session - avoids DB hit on every Grok call)
        self.lessons_section = self._build_live_lessons_section()
        if self.lessons_section:
            lesson_count = self.lessons_section.count('[L')
            logger.info(f"Loaded {lesson_count} validated lesson(s) into startup context")
        else:
            logger.info("No validated lessons found at startup")

        # Agent state
        self.state = {
            "initialized": False,
            "autonomous_mode": True,
            "current_strategies": [],
            "risk_percent": 1.0,  # Gate 1 = Option A: 1% of total portfolio value
            "positions": [],
            "cash": 0.0,
            "account_value": 0.0
        }

        # Forbidden symbols (pre-existing positions)
        self.forbidden_symbols = set()

        # Agent-opened positions tracking
        self.agent_opened_positions = {}  # {symbol: quantity}
        self.agent_position_convictions = {}  # {symbol: conviction_score (1-10)}
        self.agent_position_entry_prices = {}  # {symbol: entry_price}
        self.agent_position_tp_targets = {}  # {symbol: take_profit_price}
        self.agent_position_sl_targets = {}  # {symbol: stop_loss_price}

        # Trading plan enforcement
        self.last_trading_plan_timestamp = None
        self.current_trading_plan = None

        # Last analysis summary (for dashboard Q&A)
        self.last_analysis_summary = ""

    def _load_rules(self) -> str:
        """
        Load trading rules from XML file.

        Returns:
            Rules content as string

        Raises:
            FileNotFoundError: If rules file doesn't exist
        """
        if not self.rules_file.exists():
            raise FileNotFoundError(f"Rules file not found: {self.rules_file}")

        with open(self.rules_file, 'r', encoding='utf-8') as f:
            return f.read()

    def _build_live_lessons_section(self) -> str:
        """
        Load live-eligible lessons from learning.db and format for system prompt.
        Only returns lessons that have passed resimulation threshold (live_eligible=1).
        Returns empty string if none exist yet.
        """
        try:
            from analytics.learning_database import LearningDatabase
            import sqlite3 as _sq
            db_path = str(self.ai_trader_data_path / "learning.db")
            db = LearningDatabase(db_path)
            lessons = db.get_live_eligible_lessons()
            if not lessons:
                return ""

            lines = [
                "\nVALIDATED TRADING LESSONS (proven in resimulation - apply these to trade decisions):"
            ]
            for lesson in lessons:
                resim_total = (lesson.get('resim_wins') or 0) + (lesson.get('resim_losses') or 0)
                live_total = (lesson.get('live_wins') or 0) + (lesson.get('live_losses') or 0)
                resim_wr = f"{lesson['resim_wins']*100//resim_total}%" if resim_total > 0 else "n/a"
                live_wr = f"{lesson['live_wins']*100//live_total}%" if live_total > 0 else "new"
                lines.append(
                    f"[L{lesson['id']}] {lesson['lesson_text']}\n"
                    f"  Action: {lesson.get('action', 'see lesson text')}\n"
                    f"  Resim: {lesson.get('resim_wins',0)}W/{lesson.get('resim_losses',0)}L ({resim_wr}) | "
                    f"Live: {lesson.get('live_wins',0)}W/{lesson.get('live_losses',0)}L ({live_wr})"
                )
            return "\n".join(lines) + "\n"
        except Exception as e:
            logger.debug(f"Could not load live lessons: {e}")
            return ""

    def build_system_prompt(self, protected_positions_warning: str = "") -> List[Dict]:
        """
        Build the system prompt that includes trading rules.

        Returns structured prompt for caching support.

        Args:
            protected_positions_warning: Warning text about protected positions

        Returns:
            List of content blocks with cache control for static content
        """
        # CACHEABLE: Trading rules + lessons (stable, doesn't change during session)
        cacheable_rules = {
            "type": "text",
            "text": self.rules_content + self.lessons_section,
            "cache_control": {"type": "ephemeral"}
        }

        # DYNAMIC: Guidelines and current state (changes frequently)
        dynamic_content = {
            "type": "text",
            "text": f"""You are an autonomous swing trading agent. Your behavior is governed by the trading rules provided above.

IMPORTANT GUIDELINES:
1. Always follow the rules exactly as specified in the XML document
2. Use the provided tools to fetch market data, place orders, and perform calculations
3. Make decisions autonomously when autonomous_mode is ON
4. Provide clear reasoning for all trading decisions
5. Track cash carefully and never exceed available capital
6. Focus on high-volatility individual stocks with strong catalysts (PEAD, upgrades, breakouts)
7. Hold positions 2-7 days (PEAD catalysts up to 10 days, H5 validated); exits managed by trailing stops and SwingExitEngine
8. Use proper risk management: initial stop 4% below entry, partial exit at +7%

PORTFOLIO MANAGEMENT APPROACH (CRITICAL - MANDATORY WORKFLOW):
**ABSOLUTE REQUIREMENT: You are FORBIDDEN from using 'place_order' tool without first using 'create_trading_plan'**

1. **ALWAYS use 'create_trading_plan' tool FIRST** - Any attempt to place_order without a trading plan will be rejected
2. **Identify 3-5 stocks** you want to trade based on your current strategy and market analysis
3. **Assign conviction scores 1-10** for each stock (10 = highest conviction/priority)
4. **Higher conviction = larger allocation** - Stock with conviction 8 gets 2x more capital than conviction 4
5. **Plan validates BUYING POWER** - Uses actual broker buying power, not just cash balance
6. **ONLY THEN place individual orders** using the EXACT planned position sizes from the trading plan

CONVICTION-BASED ALLOCATION RULES:
- Conviction 9-10: Premium setups, get largest allocations (could be 30-40% of trading capital)
- Conviction 7-8: Good setups, get moderate allocations (20-25% of trading capital)
- Conviction 5-6: Decent setups, get smaller allocations (10-15% of trading capital)
- Conviction 1-4: Weak setups, avoid or get minimal allocations (5-10% of trading capital)

RISK MANAGEMENT RULES (MANDATORY - SWING TRADING):
- Swing stops are set 4% below entry, managed by swing_exit_engine daily
- Do NOT exit based on intraday P&L movement alone (swings breathe 3-4% intraday)
- Partial exit at +7% to lock in gains; let remaining shares run with trailing stop
- Exit signals come from: swing_exit_engine trailing stop, RS breakdown, TIME_KILL_SWITCH, earnings proximity
- Never sell a swing position just because it is down intraday - wait for swing_exit_engine signal

**VIOLATION = SYSTEM BLOCK: Any place_order attempt without recent create_trading_plan will be automatically blocked**

STRATEGY TRACKING (IMPORTANT):
- Use the 'set_trading_strategy' tool to declare your current trading strategy at the start of each day and whenever you change strategies
- Common strategies: 'news_catalyst', 'bounce', 'momentum', 'breakout', 'mean_reversion', 'gap_fade', 'VWAP', 'opening_range_breakout', 'relative_strength'
- Provide clear reasons for each trade in the 'reason' parameter
- Strategy performance will be evaluated at end of day

HISTORICAL CHECK (MANDATORY BEFORE ANY ENTRY):
Before recommending ANY stock for entry, you MUST check the learning context provided:
1. Check if symbol appears in AVOID list - auto-skip if present
2. Check symbol_history for win rate - if <40% over 3+ trades, state "Historical caution: X% win rate on [SYMBOL]"
3. If symbol has negative all-time P&L in history, require extra conviction before entry

ALWAYS state your historical check result in your reasoning:
- "[SYMBOL]: No history" (new symbol - proceed with normal criteria)
- "[SYMBOL]: 6 trades, 67% win rate, +$450 total" (favorable - proceed)
- "[SYMBOL]: 4 trades, 25% win rate, -$320 total - SKIP per learning data" (unfavorable - skip)
- "[SYMBOL]: On AVOID list - SKIP" (auto-reject)

This check helps you learn from past mistakes and avoid repeating losing patterns.

ORDER PLACEMENT (CRITICAL - USE BRACKET ORDERS FOR ENTRIES):
- **ALWAYS use 'place_bracket_order' for BUY entries** - This guarantees TP/SL are in place atomically when entry fills
- 'place_bracket_order' requires: symbol, quantity, entry_price, take_profit_price, stop_loss_price
- Benefits: Single API call, no risk of fill without protection, broker enforces OCO on exits
- Only use 'place_order' for: SELL orders to close positions, or if bracket order fails
- Never place a BUY with 'place_order' then manually add TP/SL - use bracket orders instead

CRITICAL RESTRICTION - PROTECTED POSITIONS:
{protected_positions_warning}

Current agent state:
- Autonomous mode: {self.state['autonomous_mode']}
- Active strategies: {self.state['current_strategies']}
- Risk per trade: {self.state['risk_percent']}%
- Available cash: ${self.state['cash']:.2f}
- Account value: ${self.state['account_value']:.2f}

You have access to tools for market data, order placement, and calculations. Use them appropriately."""
        }

        # Return structured prompt for caching
        # Rules first (cacheable), then dynamic content
        return [cacheable_rules, dynamic_content]

    def start_new_day(
        self,
        initial_cash: float = 10000.0,
        broker_config: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Initialize a new trading day.

        Args:
            initial_cash: Starting cash for the day
            broker_config: Broker configuration dictionary

        Returns:
            Dict with initialization results
        """
        # PRESERVE positions we were already tracking (from saved state or previous session)
        previously_tracked = dict(self.agent_opened_positions)

        # Reset agent-opened positions tracker for new day
        self.agent_opened_positions = {}

        # Reset trading plan enforcement
        self.last_trading_plan_timestamp = None
        self.current_trading_plan = None

        # Check if forbidden symbols protection is enabled
        forbidden_protection_enabled = True  # Default to enabled for safety
        if broker_config and 'safety' in broker_config:
            forbidden_protection_enabled = broker_config['safety'].get(
                'forbidden_symbols_protection', {}
            ).get('enabled', True)

        # Get existing positions and determine which are forbidden vs agent-tracked
        self.forbidden_symbols = set()
        if self.broker and forbidden_protection_enabled:
            try:
                account_info = self.broker.get_account_info()
                if account_info and account_info.positions:
                    for pos in account_info.positions:
                        symbol = pos.symbol.upper()

                        # If we were already tracking this position, keep tracking it
                        if symbol in previously_tracked or symbol.lower() in previously_tracked:
                            qty = get_position_quantity(pos)
                            self.agent_opened_positions[symbol] = qty
                            logger.info(f"Restored tracked position: {symbol} ({qty} shares)")
                        else:
                            # Position exists but we weren't tracking it = pre-existing = forbidden
                            self.forbidden_symbols.add(symbol)

                    if self.forbidden_symbols:
                        logger.info(f"FORBIDDEN symbols (pre-existing): {', '.join(sorted(self.forbidden_symbols))}")
                    if self.agent_opened_positions:
                        logger.info(f"RESTORED agent positions: {list(self.agent_opened_positions.keys())}")
                else:
                    logger.info("No existing positions found - all symbols available for trading")
            except Exception as e:
                logger.error(f"Error fetching positions during start_new_day: {e}")
                self.agent_opened_positions = dict(previously_tracked)
                if self.agent_opened_positions:
                    logger.warning(
                        "Restored %d previously tracked position(s) after broker fetch failure: %s",
                        len(self.agent_opened_positions),
                        ', '.join(sorted(self.agent_opened_positions.keys()))
                    )
                # Continue without forbidden symbols protection
                logger.warning("Continuing without forbidden symbols protection due to error")
        else:
            if not forbidden_protection_enabled:
                logger.warning("Forbidden symbols protection is DISABLED")
            logger.info("No forbidden symbols - all symbols available for trading")

        # Update state
        self.state['initialized'] = True
        self.state['cash'] = initial_cash
        self.state['account_value'] = initial_cash

        return {
            "initialized": True,
            "cash": initial_cash,
            "forbidden_symbols": list(self.forbidden_symbols),
            "tracked_positions": list(self.agent_opened_positions.keys())
        }

    def get_state(self) -> Dict[str, Any]:
        """Get current agent state."""
        return self.state.copy()

    def update_state(self, **kwargs):
        """Update agent state with provided key-value pairs."""
        for key, value in kwargs.items():
            if key in self.state:
                self.state[key] = value
            else:
                logger.warning(f"Attempted to update unknown state key: {key}")

    def is_symbol_forbidden(self, symbol: str) -> bool:
        """
        Check if a symbol is forbidden (pre-existing position).

        Args:
            symbol: Stock symbol to check

        Returns:
            True if symbol is forbidden, False otherwise
        """
        return symbol.upper() in self.forbidden_symbols

    def is_symbol_tracked(self, symbol: str) -> bool:
        """
        Check if a symbol is tracked by the agent.

        Args:
            symbol: Stock symbol to check

        Returns:
            True if symbol is tracked, False otherwise
        """
        return symbol.upper() in self.agent_opened_positions

    def add_position(self, symbol: str, quantity: int, entry_price: float,
                     conviction: int = 5, take_profit: Optional[float] = None,
                     stop_loss: Optional[float] = None):
        """
        Add a position to agent tracking.

        Args:
            symbol: Stock symbol
            quantity: Number of shares
            entry_price: Entry price
            conviction: Conviction score 1-10
            take_profit: Take profit target price
            stop_loss: Stop loss price
        """
        symbol = symbol.upper()
        self.agent_opened_positions[symbol] = quantity
        self.agent_position_entry_prices[symbol] = entry_price
        self.agent_position_convictions[symbol] = conviction

        if take_profit:
            self.agent_position_tp_targets[symbol] = take_profit
        if stop_loss:
            self.agent_position_sl_targets[symbol] = stop_loss

        logger.info(f"Added position: {symbol} ({quantity} @ ${entry_price:.2f}, conviction={conviction})")

    def remove_position(self, symbol: str):
        """
        Remove a position from agent tracking.

        Args:
            symbol: Stock symbol
        """
        symbol = symbol.upper()
        if symbol in self.agent_opened_positions:
            del self.agent_opened_positions[symbol]
        if symbol in self.agent_position_entry_prices:
            del self.agent_position_entry_prices[symbol]
        if symbol in self.agent_position_convictions:
            del self.agent_position_convictions[symbol]
        if symbol in self.agent_position_tp_targets:
            del self.agent_position_tp_targets[symbol]
        if symbol in self.agent_position_sl_targets:
            del self.agent_position_sl_targets[symbol]

        logger.info(f"Removed position: {symbol}")

    def update_position_quantity(self, symbol: str, new_quantity: int):
        """
        Update position quantity.

        Args:
            symbol: Stock symbol
            new_quantity: New quantity (0 removes position)
        """
        symbol = symbol.upper()
        if new_quantity <= 0:
            self.remove_position(symbol)
        else:
            self.agent_opened_positions[symbol] = new_quantity
            logger.info(f"Updated position quantity: {symbol} -> {new_quantity} shares")

    def get_position_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get all info about a tracked position.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with position info, or None if not tracked
        """
        symbol = symbol.upper()
        if symbol not in self.agent_opened_positions:
            return None

        return {
            "symbol": symbol,
            "quantity": self.agent_opened_positions.get(symbol, 0),
            "entry_price": self.agent_position_entry_prices.get(symbol),
            "conviction": self.agent_position_convictions.get(symbol),
            "take_profit": self.agent_position_tp_targets.get(symbol),
            "stop_loss": self.agent_position_sl_targets.get(symbol)
        }

    def set_trading_plan(self, plan: Dict[str, Any]):
        """
        Set the current trading plan.

        Args:
            plan: Trading plan dict
        """
        import time
        self.current_trading_plan = plan
        self.last_trading_plan_timestamp = time.time()
        logger.info(f"Trading plan set with {len(plan.get('positions', []))} planned positions")

    def clear_trading_plan(self):
        """Clear the current trading plan."""
        self.current_trading_plan = None
        self.last_trading_plan_timestamp = None
        logger.info("Trading plan cleared")

    def is_trading_plan_valid(self, max_age_minutes: int = 30) -> bool:
        """
        Check if current trading plan is valid (not stale).

        Args:
            max_age_minutes: Maximum age of plan in minutes

        Returns:
            True if plan exists and is not stale
        """
        if not self.last_trading_plan_timestamp:
            return False

        import time
        current_time = time.time()
        plan_age_minutes = (current_time - self.last_trading_plan_timestamp) / 60

        return plan_age_minutes <= max_age_minutes

    def save_analysis_summary(self, summary: str):
        """
        Save analysis summary for dashboard Q&A.

        Args:
            summary: Analysis summary text
        """
        if len(summary) > 100:  # Only meaningful responses
            self.last_analysis_summary = summary[:2000]  # Cap at 2000 chars
            logger.debug("Analysis summary saved for dashboard Q&A")
