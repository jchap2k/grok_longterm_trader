"""
Claude Trading Agent - Refactored orchestrator using extracted components

This is the main agent orchestrator that coordinates between extracted components.
Reduced from 5,976 LOC monolith to ~800 LOC composition-based architecture.

Component Delegation:
- AnthropicLLMClient: All Claude API communication
- AgentStateManager: Conversation state and trading rules
- TradingLogic: Risk calculations and trading analysis
- PositionManager: Position tracking and monitoring
- OrderManager: Order execution and cooldowns
- ToolExecutor: Tool registry and execution

The agent now focuses purely on orchestration and high-level workflows.
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

AI_TRADER_DATA = Path(__file__).parent.parent.parent / "ai_trader_data"

logger = logging.getLogger(__name__)

# Import broker enums (relative import for compatibility)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from brokers.base_broker import OrderSide, OrderType
from risk.circuit_breaker import PortfolioCircuitBreaker
from analytics.learning_database import LearningDatabase
from utils.position_quantity import get_position_quantity

# Import extracted components (Phase 1 - standalone)
from .components import (
    PDTManager,
    CapitalManager,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_volume_profile,
    calculate_technical_indicators
)

# Import extracted components (Phase 2 - composition)
from .llm.anthropic_client import AnthropicLLMClient
from .state.state_manager import AgentStateManager
from .analysis.trading_logic import TradingLogic
from .positions.position_manager import PositionManager
from .orders.order_manager import OrderManager
from .tools.tool_executor import ToolExecutor

# Import base trading agent
from .base_trading_agent import BaseTradingAgent


class ClaudeTradingAgent(BaseTradingAgent):
    """
    Main trading agent orchestrator - coordinates between extracted components.

    Architecture:
    - LLM Client: Handles all Claude API communication
    - State Manager: Manages conversation state and trading rules
    - Trading Logic: Risk calculations and analysis
    - Position Manager: Position tracking and monitoring
    - Order Manager: Order execution and cooldowns
    - Tool Executor: Tool registry and execution

    The agent acts as the orchestrator, delegating to specialized components.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        rules_file: str = "rules/active_rules.txt",
        model: str = "claude-sonnet-4-20250514",
        data_model: str = "claude-3-5-haiku-20241022",
        thinking_model: Optional[str] = None,
        use_thinking_for_regime: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        broker = None,
        data_provider = None,
        news_provider = None,
        learning_db = None,
        ollama_provider = None
    ):
        """
        Initialize the Claude trading agent with component composition.

        Args:
            api_key: Anthropic API key (or set ANTHROPIC_API_KEY env var)
            rules_file: Path to XML rules file
            model: Claude model for strategic decisions (Sonnet)
            data_model: Claude model for data operations (Haiku)
            thinking_model: Optional deep reasoning model
            use_thinking_for_regime: Use thinking model for regime analysis
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
            broker: Trading broker instance
            data_provider: Market data provider instance
            news_provider: News provider instance
            learning_db: Learning database for trade journal
            ollama_provider: Local LLM for preprocessing
        """
        # Initialize base class
        super().__init__()

        # Set AI_TRADER_DATA path for base class state management
        self.ai_trader_data_path = AI_TRADER_DATA

        # Thread safety
        import threading
        self._state_lock = threading.Lock()

        # Validate API key
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set or passed as parameter")

        # Store dependencies
        self.broker = broker
        self.data_provider = data_provider
        self.news_provider = news_provider
        self.ollama_provider = ollama_provider

        # Initialize learning database
        try:
            self.learning_db = learning_db or LearningDatabase()
        except Exception as e:
            logger.warning(f"Could not initialize learning DB: {e}")
            self.learning_db = None

        # ==================== COMPONENT INITIALIZATION ====================

        # 1. LLM Client - Handles all Claude API communication
        self.llm_client = AnthropicLLMClient(
            api_key=api_key,
            model=model,
            data_model=data_model,
            thinking_model=thinking_model,
            use_thinking_for_regime=use_thinking_for_regime,
            max_tokens=max_tokens,
            temperature=temperature
        )

        # 2. State Manager - Manages conversation state and trading rules
        self.state_manager = AgentStateManager(
            rules_file=Path(rules_file),
            ai_trader_data_path=AI_TRADER_DATA,
            broker=broker
        )

        # 3. Trading Logic - Risk calculations and analysis
        self.trading_logic = TradingLogic(
            data_provider=data_provider
        )

        # 4. Position Manager - Position tracking and monitoring
        self.position_manager = PositionManager(
            broker=broker,
            data_provider=data_provider,
            forbidden_symbols=self.state_manager.forbidden_symbols
        )

        # 5. Order Manager - Order execution and cooldowns
        self.order_manager = OrderManager(
            broker=broker,
            learning_db=self.learning_db,
            pending_order_timeout_sec=300
        )

        # 6. Tool Executor - Tool registry and execution
        # Note: Needs parent_agent reference for methods not yet extracted
        self.tool_executor = ToolExecutor(
            broker=broker,
            data_provider=data_provider,
            news_provider=news_provider,
            learning_db=self.learning_db,
            position_manager=self.position_manager,
            order_manager=self.order_manager,
            state_manager=self.state_manager,
            trading_logic=self.trading_logic,
            parent_agent=self  # For methods not yet extracted
        )

        # ==================== ORCHESTRATION-LEVEL STATE ====================

        # Portfolio-level circuit breaker
        self.circuit_breaker = PortfolioCircuitBreaker()

        # PDT (Pattern Day Trader) manager
        self.pdt_manager = PDTManager()

        # Capital manager
        self.capital_manager = CapitalManager()
        self.high_water_mark = 25000.0

        # Protective MOC orders
        self.protective_moc_orders = {}  # {symbol: [order_ids]}

        # Daily loss tracking
        self.daily_pnl_percent = 0.0
        self.starting_portfolio_value = 0.0

        # State checkpointing
        self._checkpoint_count = 0
        self._last_checkpoint_time = datetime.now()

        # Strategy tracking
        self.strategy_log = []
        self.trade_log = []
        self.current_strategy = None
        self.price_snapshots = {}
        self.current_swing_scan_candidates = {}

        # Market regime filter
        self.market_regime = None
        self.min_conviction_threshold = 8.0
        self.max_positions_allowed = 3  # Swing: full=3, updated from regime at 6:45 AM open check

        # Context agent (Grok only, if thinking model configured)
        if thinking_model and use_thinking_for_regime:
            try:
                from .grok_context_agent import GrokContextAgent
                self.context_agent = GrokContextAgent(
                    agent=self,
                    config={
                        "thinking_model": thinking_model,
                        "fast_model": data_model,
                    }
                )
                logger.info("GrokContextAgent enabled for daily context management")
            except Exception as e:
                logger.warning(f"Failed to initialize GrokContextAgent: {e}")
                self.context_agent = None
        else:
            self.context_agent = None

        # Load persisted position state (for crash recovery)
        self._load_position_state()

        # Reconcile loaded state with actual broker positions
        if self.broker:
            self._reconcile_positions_with_broker()
            if self.position_manager.agent_opened_positions:
                logger.info(
                    f"Startup reconciliation: tracking {len(self.position_manager.agent_opened_positions)} "
                    f"positions: {list(self.position_manager.agent_opened_positions.keys())}"
                )

    # ==================== PROPERTY DELEGATES ====================
    # Expose component properties for backwards compatibility

    @property
    def agent_opened_positions(self):
        """Delegate to position_manager."""
        return self.position_manager.agent_opened_positions

    @property
    def agent_position_entry_prices(self):
        """Delegate to position_manager."""
        return self.position_manager.agent_position_entry_prices

    @property
    def agent_position_convictions(self):
        """Delegate to position_manager."""
        return self.position_manager.agent_position_convictions

    @property
    def agent_position_tp_targets(self):
        """Delegate to position_manager."""
        return self.position_manager.agent_position_tp_targets

    @property
    def agent_position_sl_targets(self):
        """Delegate to position_manager."""
        return self.position_manager.agent_position_sl_targets

    @property
    def forbidden_symbols(self):
        """Delegate to state_manager."""
        return self.state_manager.forbidden_symbols

    @property
    def conversation_history(self):
        """Delegate to llm_client."""
        return self.llm_client.conversation_history

    @property
    def state(self):
        """Delegate to state_manager."""
        return self.state_manager.state

    @property
    def tools(self):
        """Delegate to tool_executor."""
        return self.tool_executor.tools_schema

    # ==================== LLM COMMUNICATION (Delegate to llm_client) ====================

    def send_message(self, user_message: str, stream: bool = False, context: str = "general") -> str:
        """
        Send a message to Claude and get a response.

        Delegates to llm_client for API communication and handles tool execution loop.
        """
        # Build system prompt from state manager
        protected_positions_warning = self._generate_protected_positions_warning()
        system_prompt = self.state_manager.build_system_prompt(protected_positions_warning)

        # Send message via llm_client
        # Note: Tool execution loop needs to be handled here since tools need parent_agent
        return self._send_message_with_tools(user_message, system_prompt, context, stream)

    def _send_message_with_tools(
        self,
        user_message: str,
        system_prompt: Any,
        context: str = "general",
        stream: bool = False
    ) -> str:
        """
        Send message with full tool execution loop.

        Handles the tool use -> tool result -> response cycle.
        """
        # Add user message to history
        self.llm_client.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Select model based on context
        selected_model = self.llm_client._select_model(context)

        # Clear tool history when switching models
        self.llm_client._clear_tool_history_for_model_switch(selected_model)

        # Send to LLM
        try:
            response = self.llm_client.client.messages.create(
                model=selected_model,
                max_tokens=self.llm_client.max_tokens,
                temperature=self.llm_client.temperature,
                system=system_prompt,
                messages=self.llm_client.conversation_history,
                tools=self.tool_executor.tools_schema
            )
        except Exception as e:
            error_msg = f"Error communicating with Claude: {str(e)}"
            logger.error(error_msg)
            return error_msg

        # Track token usage
        self.llm_client.token_tracker.record_api_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=selected_model,
            context=context
        )

        # Process response and handle tool calls
        full_response = ""
        max_iterations = 50
        iteration = 0

        from anthropic.types import TextBlock, ToolUseBlock
        import time

        while response.stop_reason == "tool_use":
            iteration += 1
            if iteration > max_iterations:
                logger.error(f"Max tool iterations ({max_iterations}) exceeded")
                full_response += "\n[ERROR: Maximum tool iteration limit reached.]"
                break

            # Extract text and tool calls
            text_content = []
            tool_calls = []

            for block in response.content:
                if isinstance(block, TextBlock):
                    text_content.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append(block)

            # Add text to response
            if text_content:
                full_response += "\n".join(text_content) + "\n"

            # Add assistant message to history
            self.llm_client.conversation_history.append({
                "role": "assistant",
                "content": response.content
            })

            # Execute tools
            tool_results = []
            for tool_call in tool_calls:
                result = self.tool_executor.execute_tool(tool_call.name, tool_call.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
                })

            # Add tool results to history
            if tool_results:
                self.llm_client.conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })

                # Rate limiting
                time.sleep(1.5)

                # Get next response
                try:
                    response = self.llm_client.client.messages.create(
                        model=selected_model,
                        max_tokens=self.llm_client.max_tokens,
                        temperature=self.llm_client.temperature,
                        system=system_prompt,
                        messages=self.llm_client.conversation_history,
                        tools=self.tool_executor.tools_schema
                    )

                    # Track token usage
                    self.llm_client.token_tracker.record_api_call(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        model=selected_model,
                        context=f"{context}_tool_followup"
                    )
                except Exception as e:
                    logger.error(f"Tool followup error: {e}")
                    full_response += "\n[Error in tool followup - please retry]"
                    break
            else:
                break

        # Extract final text response
        for block in response.content:
            if isinstance(block, TextBlock):
                full_response += block.text

        # Add final assistant message to history
        self.llm_client.conversation_history.append({
            "role": "assistant",
            "content": response.content
        })

        # Prune conversation history
        self.llm_client.prune_conversation_history(max_messages=20)

        return full_response.strip()

    # ==================== STATE MANAGEMENT (Delegate to state_manager) ====================

    def start_new_day(self, initial_cash: float = 10000.0, broker_config: dict = None) -> Dict[str, Any]:
        """
        Initialize a new trading day.

        Delegates to state_manager for initialization logic.
        broker_config param accepted for scheduler compatibility but ignored -
        broker config is loaded internally via _load_broker_config().
        """
        # Load broker config
        broker_config = self._load_broker_config()

        # Delegate to state manager
        result = self.state_manager.start_new_day(
            initial_cash=initial_cash,
            broker_config=broker_config
        )

        # Sync position manager's forbidden symbols
        self.position_manager.forbidden_symbols = self.state_manager.forbidden_symbols

        # Sync position tracking from state manager to position manager
        for symbol, qty in self.state_manager.agent_opened_positions.items():
            self.position_manager.agent_opened_positions[symbol] = qty

        return result

    def _load_broker_config(self) -> Optional[Dict]:
        """Load broker configuration from file."""
        config_path = AI_TRADER_DATA / "broker_config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load broker config: {e}")
        return None

    # ==================== POSITION MONITORING (Delegate to position_manager) ====================

    def position_monitor_check(self) -> Dict[str, Any]:
        """
        Lightweight position monitoring check.

        Delegates to position_manager for watermark-based monitoring.
        """
        return self.position_manager.position_monitor_check(
            get_atr_stop_percent_callback=self.trading_logic.get_atr_stop_percent
        )

    def _get_validated_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get validated quote from multiple sources.

        Delegates to position_manager for multi-source validation.
        """
        return self.position_manager.get_validated_quote(symbol)

    # ==================== ORDER MANAGEMENT (Delegate to order_manager) ====================

    def _check_pending_orders(self) -> Dict[str, Any]:
        """
        Check all pending LIMIT orders for fills.

        Delegates to order_manager for pending order management.
        """
        return self.order_manager.check_pending_orders(
            agent_positions=self.position_manager.agent_opened_positions,
            agent_entry_prices=self.position_manager.agent_position_entry_prices,
            agent_tp_targets=self.position_manager.agent_position_tp_targets,
            agent_convictions=self.position_manager.agent_position_convictions
        )

    # ==================== UTILITY METHODS ====================

    def _generate_protected_positions_warning(self) -> str:
        """Generate warning text about protected positions."""
        if not self.state_manager.forbidden_symbols:
            return ""

        forbidden_list = ", ".join(sorted(self.state_manager.forbidden_symbols))
        return f"""
**PROTECTED POSITIONS - DO NOT TRADE:**
The following symbols existed before the agent started and are COMPLETELY OFF-LIMITS:
{forbidden_list}

You CANNOT buy or sell these symbols under any circumstances.
Any attempt to trade these symbols will be BLOCKED by the system.
"""

    def _reconcile_positions_with_broker(self):
        """
        Reconcile agent position tracking with actual broker positions.

        This handles restarts where broker has positions we need to track.
        """
        if not self.broker:
            return

        try:
            account_info = self.broker.get_account_info()
            if not account_info or not account_info.positions:
                return

            # Get previously tracked positions from state manager
            previously_tracked = dict(self.state_manager.agent_opened_positions)

            for pos in account_info.positions:
                symbol = pos.symbol.upper()
                qty = get_position_quantity(pos)

                # If we were tracking this position, restore to position manager
                if symbol in previously_tracked:
                    self.position_manager.add_position(
                        symbol=symbol,
                        quantity=qty,
                        entry_price=pos.avg_entry_price or 0,
                        conviction=self.state_manager.agent_position_convictions.get(symbol, 5)
                    )
                    logger.info(f"Reconciled position: {symbol} ({qty} shares)")
                else:
                    # Position exists but we lost track of it (e.g. path bug, missed fill event).
                    # ADOPT it - never leave an open position untracked and unprotected.
                    # Forbidden means "don't enter", not "ignore open risk".
                    entry_price = pos.avg_entry_price or 0
                    self.position_manager.add_position(
                        symbol=symbol,
                        quantity=qty,
                        entry_price=entry_price,
                        conviction=5  # default mid-conviction - original unknown
                    )
                    logger.warning(
                        f"Adopted untracked broker position: {symbol} "
                        f"({qty} shares @ {entry_price:.2f}) - "
                        f"position_state lost (restart/missed fill). conviction=5 default."
                    )

        except Exception as e:
            logger.error(f"Error reconciling positions: {e}")

    def _load_position_state(self):
        """
        Load persisted position state from disk.

        First tries position_manager.load_from_json() which restores full state
        (conviction, TP/SL, HWM, partial profits, momentum reversals).
        Falls back to legacy agent_state.json for backwards compatibility.
        """
        # Primary: load full state from position_manager's JSON
        loaded = self.position_manager.load_from_json()

        if loaded:
            # Sync state_manager to match (backwards compat - scheduler reads state_manager too)
            self.state_manager.agent_opened_positions = dict(
                self.position_manager.agent_opened_positions
            )
            return

        # Fallback: legacy agent_state.json (only has basic fields)
        state_file = self.ai_trader_data_path / "agent_state.json"
        if not state_file.exists():
            return

        try:
            with open(state_file, 'r') as f:
                saved_state = json.load(f)

            # Restore to state manager
            if 'agent_opened_positions' in saved_state:
                self.state_manager.agent_opened_positions = saved_state['agent_opened_positions']
                # Also populate position_manager
                for symbol, qty in saved_state['agent_opened_positions'].items():
                    self.position_manager.agent_opened_positions[symbol] = qty
            if 'agent_position_entry_prices' in saved_state:
                for symbol, price in saved_state['agent_position_entry_prices'].items():
                    self.position_manager.agent_position_entry_prices[symbol] = price
            if 'agent_position_convictions' in saved_state:
                for symbol, conviction in saved_state['agent_position_convictions'].items():
                    self.position_manager.agent_position_convictions[symbol] = conviction
            if 'agent_position_tp_targets' in saved_state:
                for symbol, tp in saved_state['agent_position_tp_targets'].items():
                    self.position_manager.agent_position_tp_targets[symbol] = tp
            if 'agent_position_sl_targets' in saved_state:
                for symbol, sl in saved_state['agent_position_sl_targets'].items():
                    self.position_manager.agent_position_sl_targets[symbol] = sl

            logger.info(f"Loaded position state (legacy): {len(self.state_manager.agent_opened_positions)} positions")

        except Exception as e:
            logger.warning(f"Could not load position state: {e}")

    def _save_position_state(self):
        """
        Save position state to disk.

        Delegates to position_manager to save state.
        """
        state_file = self.ai_trader_data_path / "agent_state.json"

        try:
            state_to_save = {
                'agent_opened_positions': dict(self.position_manager.agent_opened_positions),
                'agent_position_entry_prices': dict(self.position_manager.agent_position_entry_prices),
                'agent_position_convictions': dict(self.position_manager.agent_position_convictions),
                'agent_position_tp_targets': dict(self.position_manager.agent_position_tp_targets),
                'agent_position_sl_targets': dict(self.position_manager.agent_position_sl_targets),
                'timestamp': datetime.now().isoformat()
            }

            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, 'w') as f:
                json.dump(state_to_save, f, indent=2)

            logger.debug("Position state saved")

        except Exception as e:
            logger.error(f"Failed to save position state: {e}")

    # ==================== ABSTRACT METHOD IMPLEMENTATIONS ====================
    # These are required by BaseTradingAgent

    def initialize(self, initial_cash: float = 10000.0) -> str:
        """Initialize the agent for trading."""
        result = self.start_new_day(initial_cash=initial_cash)

        # Send initialization message to LLM
        init_message = f"""The trading day has started. Please analyze current market conditions and prepare your trading strategy.

Account Status:
- Initial Cash: ${initial_cash:,.2f}
- Forbidden Symbols: {', '.join(sorted(result['forbidden_symbols'])) if result['forbidden_symbols'] else 'None'}
- Tracked Positions: {', '.join(result['tracked_positions']) if result['tracked_positions'] else 'None'}

What is your trading plan for today?"""

        response = self.send_message(init_message, context="initialization")

        # Mark as initialized
        self.state_manager.state['initialized'] = True

        return response

    def update_trades(self) -> str:
        """Check positions and market conditions, make trading decisions."""
        # Check pending orders first
        pending_result = self._check_pending_orders()

        # Check position monitor watermarks
        monitor_result = self.position_monitor_check()

        # Build update message
        update_message = "Check current positions and market conditions. "

        if monitor_result.get('trigger_update'):
            update_message += f"ALERTS: {'; '.join(monitor_result['reasons'])}"
        else:
            update_message += "All positions within normal ranges."

        return self.send_message(update_message, context="trading_decision")

    def end_of_day(self) -> str:
        """Execute end-of-day procedures."""
        eod_message = """End of trading day approaching. Please:
1. Review all open swing positions
2. Review each open swing position for technical exit signals only - do NOT force liquidation overnight
3. Prepare end-of-day summary
4. Record lessons learned"""

        return self.send_message(eod_message, context="end_of_day")

    # ==================== ADDITIONAL ORCHESTRATION METHODS ====================

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status."""
        return {
            "initialized": self.state_manager.state.get('initialized', False),
            "autonomous_mode": self.state_manager.state.get('autonomous_mode', True),
            "positions_count": len(self.position_manager.agent_opened_positions),
            "pending_orders": self.order_manager.get_pending_count(),
            "forbidden_symbols": len(self.state_manager.forbidden_symbols),
            "conversation_length": len(self.llm_client.conversation_history),
            "current_model": self.llm_client.model,
            "data_model": self.llm_client.data_model
        }

    def clear_conversation(self):
        """Clear conversation history."""
        self.llm_client.clear_history()
        logger.info("Conversation history cleared")

    def force_save_state(self):
        """Force save position state to disk."""
        self._save_position_state()
        logger.info("Position state force-saved")


# ==================== BACKWARDS COMPATIBILITY ====================
# Expose component functionality through agent for existing code

def _get_atr_stop_percent(self, symbol: str, current_price: float, multiplier: float = 1.5) -> float:
    """Delegate to trading_logic."""
    return self.trading_logic.get_atr_stop_percent(symbol, current_price, multiplier)

ClaudeTradingAgent._get_atr_stop_percent = _get_atr_stop_percent
