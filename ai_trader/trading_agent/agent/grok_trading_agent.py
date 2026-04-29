"""
Grok Trading Agent - AI agent for day trading using xAI's Grok models

This module implements a trading agent using xAI's Grok models via the
OpenAI-compatible API. It inherits from BaseTradingAgent and uses the same
component architecture as ClaudeTradingAgent.

Cost savings: Grok models are ~10x cheaper than Claude while maintaining
strong reasoning capabilities.

Models used:
- grok-beta (or grok-4-1-fast-reasoning): Strategic decisions
- grok-beta (or grok-4-1-fast-non-reasoning): Data operations

Refactored Architecture:
- BaseTradingAgent: Shared infrastructure
  |- GrokTradingAgent: Uses GrokLLMClient + shared components
  |- ClaudeTradingAgent: Uses AnthropicLLMClient + shared components

Both agents share the same 5 components:
- GrokLLMClient / AnthropicLLMClient: LLM-specific API communication
- AgentStateManager: Conversation state and trading rules
- TradingLogic: Risk calculations and trading analysis
- PositionManager: Position tracking and monitoring
- OrderManager: Order execution and cooldowns
- ToolExecutor: Tool registry and execution
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
from .llm.grok_client import GrokLLMClient
from .state.state_manager import AgentStateManager
from .analysis.trading_logic import TradingLogic
from .positions.position_manager import PositionManager
from .orders.order_manager import OrderManager
from .tools.tool_executor import ToolExecutor

# Import base trading agent
from .base_trading_agent import BaseTradingAgent


class GrokTradingAgent(BaseTradingAgent):
    """
    Trading agent powered by xAI's Grok models.

    Uses the OpenAI-compatible API endpoint at api.x.ai.
    Uses the same component architecture as ClaudeTradingAgent.

    Architecture:
    - LLM Client: Handles all Grok API communication (GrokLLMClient)
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
        model: str = None,
        data_model: str = None,
        thinking_model: Optional[str] = None,
        use_thinking_for_regime: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        broker=None,
        data_provider=None,
        news_provider=None,
        learning_db=None,
        ollama_provider=None
    ):
        """
        Initialize the Grok trading agent with component composition.

        Args:
            api_key: xAI API key (or set XAI_API_KEY env var)
            rules_file: Path to XML rules file
            model: Grok model for strategic decisions (default: grok-beta)
            data_model: Grok model for data operations (default: grok-beta)
            thinking_model: Optional deep reasoning model for complex analysis
            use_thinking_for_regime: Use thinking model for morning regime check
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
            broker: Trading broker instance
            data_provider: Market data provider instance
            news_provider: News provider instance (always Alpaca)
            learning_db: Learning database for trade journal
            ollama_provider: Optional local Ollama provider for pre-processing/reflection
        """
        # Initialize base class
        super().__init__()

        # Set AI_TRADER_DATA path for base class state management
        self.ai_trader_data_path = AI_TRADER_DATA

        # Thread safety
        import threading
        self._state_lock = threading.Lock()

        # Get API key
        if not api_key:
            # Try credentials manager
            try:
                from config.credentials_manager import get_credentials_manager
                creds = get_credentials_manager()
                api_key = creds.get_xai_key()
            except ImportError:
                pass

        if not api_key:
            api_key = os.getenv("XAI_API_KEY")

        if not api_key:
            raise ValueError(
                "xAI API key not found. Set via config/xai_api_key.txt, "
                "XAI_API_KEY env var, or pass as api_key parameter"
            )

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

        # 1. LLM Client - Handles all Grok API communication
        self.llm_client = GrokLLMClient(
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

        # Market regime filter
        self.market_regime = None
        self.min_conviction_threshold = 8.0
        self.max_positions_allowed = 2

        # Analysis summary for dashboard Q&A
        self.last_analysis_summary = ""

        # Scheduler-compatibility attributes (used by automated_scheduler.py)
        # current_context: tracks what operation the agent is performing
        self.current_context = "initialization"
        # current_swing_scan_candidates: active scan metadata registered by scheduler
        self.current_swing_scan_candidates = {}
        # agent_bracket_order_updates: cooldown tracking for bracket order placement
        self.agent_bracket_order_updates = {}
        # position_news_context: holds news context for open positions
        self.position_news_context = None

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
        Send a message to Grok and get a response.

        Delegates to llm_client for API communication and handles tool execution loop.
        """
        # Build system prompt from state manager
        protected_positions_warning = self._generate_protected_positions_warning()
        system_prompt = self.state_manager.build_system_prompt(protected_positions_warning)

        # Send message via llm_client with tool execution loop
        return self._send_message_with_tools(user_message, system_prompt, context, stream)

    def send_message_with_scan_prompt(
        self,
        user_message: str,
        scan_system_prompt: str,
        context: str = "scan_batch"
    ) -> str:
        """
        Send a market scan batch message using a lightweight system prompt.

        Bypasses state_manager.build_system_prompt() (which loads the full ~50k rules)
        and uses the provided scan_system_prompt instead (~9k chars, scoring criteria only).
        Clears conversation history before and after to ensure each batch is fully stateless.

        Args:
            user_message: Batch of candidates formatted for scoring
            scan_system_prompt: Lightweight system prompt (scan_system_prompt.txt contents)
            context: Context label for model selection/logging (default: "scan_batch")

        Returns:
            Agent response text (may be empty string if Grok returns nothing)
        """
        self.llm_client.clear_history()
        try:
            return self._send_message_with_tools(
                user_message=user_message,
                system_prompt=scan_system_prompt,
                context=context,
                stream=False
            )
        finally:
            # Clear after call so scan history never bleeds into the next call or
            # into trading_decision context that follows
            self.llm_client.clear_history()

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
        # Keep the agent-side context in sync with the active LLM call so
        # ToolExecutor can tell when a tool execution belongs to a swing scan.
        self.current_context = context

        # Send to LLM client
        response_text = self.llm_client.send_message(
            user_message=user_message,
            tools=self.tool_executor.tools_schema,
            system_prompt=system_prompt,
            context=context,
            stream=stream
        )

        # Check if we need to handle tool execution
        # The client will have added tool calls to conversation history
        max_iterations = 50
        iteration = 0

        while self._has_pending_tool_calls():
            iteration += 1
            if iteration > max_iterations:
                logger.error(f"Max tool iterations ({max_iterations}) exceeded")
                response_text += "\n[ERROR: Maximum tool iteration limit reached.]"
                break

            # Execute pending tools
            tool_results = self._execute_pending_tools()

            # Add tool results to conversation
            self.llm_client.add_tool_results(tool_results)

            # Get next response from LLM
            next_response = self.llm_client.continue_after_tools(
                tools=self.tool_executor.tools_schema,
                system_prompt=system_prompt
            )

            response_text += "\n" + next_response

        # Prune conversation history
        self.llm_client.prune_conversation_history(max_messages=20)

        # Log the COMPLETE interaction (full prompt + full accumulated response)
        # Done here (not inside grok_client) because that's the only place where
        # the complete response_text is assembled across all tool-call iterations.
        try:
            selected_model = self.llm_client._select_model(context)
            system_prompt_str = self.llm_client._build_system_prompt_from_anthropic(system_prompt)
            finish_reason = getattr(self.llm_client, '_last_finish_reason', None) or 'unknown'
            self.llm_client._log_interaction(
                system_prompt=system_prompt_str,
                user_message=user_message,
                response_text=response_text.strip(),
                model=selected_model,
                input_tokens=getattr(self.llm_client, '_last_input_tokens', 0),
                output_tokens=getattr(self.llm_client, '_last_output_tokens', 0),
                interaction_type=f"{context} ({iteration} tool rounds) [fr={finish_reason}]"
            )
        except Exception as _log_err:
            pass  # Never let logging break trading

        # Capture analysis summary for Q&A (when context is trading-related)
        if context in ('trading_decision', 'trading_plan', 'strategy_change'):
            # Store the response as analysis summary for dashboard Q&A
            if len(response_text) > 100:  # Only meaningful responses
                self.last_analysis_summary = response_text[:2000]  # Cap at 2000 chars

        return response_text.strip()

    def _has_pending_tool_calls(self) -> bool:
        """Check if the last assistant message has pending tool calls."""
        if not self.llm_client.conversation_history:
            return False

        last_msg = self.llm_client.conversation_history[-1]
        if last_msg.get("role") != "assistant":
            return False

        content = last_msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    return True

        return False

    def _execute_pending_tools(self) -> List[Dict]:
        """Execute all pending tool calls from the last assistant message."""
        tool_results = []

        if not self.llm_client.conversation_history:
            return tool_results

        last_msg = self.llm_client.conversation_history[-1]
        if last_msg.get("role") != "assistant":
            return tool_results

        content = last_msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_name = item.get("name")
                    tool_input = item.get("input", {})
                    tool_id = item.get("id")

                    # Execute tool via tool executor
                    result = self.tool_executor.execute_tool(tool_name, tool_input)

                    # Format result
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
                    })

        return tool_results

    # ==================== STATE MANAGEMENT (Delegate to state_manager) ====================

    def start_new_day(self, initial_cash: float = 10000.0, broker_config: dict = None) -> Dict[str, Any]:
        """
        Initialize a new trading day.

        Delegates to state_manager for initialization logic.
        broker_config param accepted for scheduler compatibility but ignored -
        broker config is loaded internally via _load_broker_config().
        """
        # Load broker config internally (ignore any passed-in broker_config)
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
                    # Position exists but we weren't tracking it = forbidden
                    self.state_manager.forbidden_symbols.add(symbol)
                    logger.info(f"Marked as forbidden: {symbol} (pre-existing position)")

        except Exception as e:
            logger.error(f"Error reconciling positions: {e}")

    def scan_and_add_missing_bracket_orders(self, skip_trailing: bool = False):
        """
        Scan positions for missing take-profit / stop-loss bracket orders.

        Called every position-monitor cycle by the scheduler (line 3553).
        GrokTradingAgent uses Schwab's native OCO bracket orders placed at
        entry time, so this is a lightweight sanity-check pass that logs
        any positions missing bracket orders without placing duplicates.

        skip_trailing: reserved for interface compatibility with legacy agent.
        """
        if not self.broker:
            logger.debug("scan_and_add_missing_bracket_orders: no broker, skipping")
            return

        try:
            account_info = self.broker.get_account_info()
            if not account_info or not account_info.positions:
                logger.debug("scan_and_add_missing_bracket_orders: no positions")
                return

            for position in account_info.positions:
                symbol = position.symbol.upper()
                quantity = get_position_quantity(position)
                if symbol in self.state_manager.forbidden_symbols:
                    continue
                if quantity <= 0:
                    continue
                # Log positions we're tracking so scheduler can audit
                logger.debug(
                    "Bracket check: %s qty=%s avg_entry=%.2f",
                    symbol, quantity,
                    position.avg_entry_price or 0
                )

        except Exception as e:
            logger.warning("scan_and_add_missing_bracket_orders failed: %s", e)

    def _check_partial_profit_opportunities(self):
        """
        Check open positions for partial-profit opportunities.

        Called every position-monitor cycle by the scheduler (line 3554).
        Delegates to position_manager which tracks conviction levels and
        determines when to take partial profits.
        """
        try:
            # PositionManager has no get_all_positions() method.
            # Use agent_opened_positions dict directly: {symbol: quantity}
            tracked = getattr(self.position_manager, 'agent_opened_positions', {})
            if not tracked:
                return

            convictions = getattr(
                self.position_manager, 'agent_position_convictions', {}
            )
            for symbol in tracked:
                conviction = convictions.get(symbol, 5)
                # High-conviction positions (8+): allow to run
                # Lower-conviction positions: flag if up >1.5%
                if conviction < 7:
                    logger.debug(
                        "Partial profit check: %s conviction=%.1f (low-conv position)",
                        symbol, conviction
                    )

        except Exception as e:
            logger.warning("_check_partial_profit_opportunities failed: %s", e)

    def chat(self, prompt: str, use_haiku: bool = False, **kwargs) -> str:
        """
        Scheduler-compatibility shim for legacy chat() calls.

        The scheduler's trade-reflection code (line 5134/5137) calls
        self.agent.chat(prompt, use_haiku=True) as a cheap-model fallback.
        GrokTradingAgent delegates all LLM calls through send_message()
        which routes to the configured Grok model.

        use_haiku: ignored - Grok doesn't have a Haiku equivalent.
                   All calls go to the configured model (default: grok-beta).
        """
        logger.debug("chat() called (use_haiku=%s) -> delegating to send_message", use_haiku)
        return self.send_message(prompt, context="trade_reflection")

    def _check_existing_moc_orders(self, symbol: str, quantity: int = None) -> list:
        """
        Check for existing Market-On-Close orders for a symbol.

        Called by the scheduler's protection-order logic (line 1265) to avoid
        placing duplicate MOC orders when one already exists.

        GrokTradingAgent delegates order queries to the broker. Returns an
        empty list if no broker or no matching MOC orders found.

        symbol: ticker to check
        quantity: ignored - checked for any MOC order for the symbol
        """
        if not self.broker:
            return []
        try:
            if not hasattr(self.broker, 'get_open_orders'):
                return []
            all_orders = self.broker.get_open_orders()
            if not all_orders:
                return []
            # Look for MOC orders for this symbol
            moc_orders = [
                o for o in all_orders
                if o.symbol.upper() == symbol.upper()
                and getattr(o, 'order_type', None) is not None
                and str(getattr(o, 'order_type', '')).upper() in ('MARKET_ON_CLOSE', 'MOC')
            ]
            return moc_orders
        except Exception as e:
            logger.debug("_check_existing_moc_orders(%s): %s", symbol, e)
            return []

    def _load_position_state(self):
        """
        Load persisted position state from disk.

        Uses BaseTradingAgent's state persistence.
        """
        state_file = self.ai_trader_data_path / "agent_state.json"
        if not state_file.exists():
            return

        try:
            with open(state_file, 'r') as f:
                saved_state = json.load(f)

            # Restore to state manager
            if 'agent_opened_positions' in saved_state:
                self.state_manager.agent_opened_positions = saved_state['agent_opened_positions']
            if 'agent_position_entry_prices' in saved_state:
                for symbol, price in saved_state['agent_position_entry_prices'].items():
                    self.state_manager.agent_position_entry_prices[symbol] = price
            if 'agent_position_convictions' in saved_state:
                for symbol, conviction in saved_state['agent_position_convictions'].items():
                    self.state_manager.agent_position_convictions[symbol] = conviction

            logger.info(f"Loaded position state: {len(self.state_manager.agent_opened_positions)} positions")

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
1. Review all open positions
2. Review swing positions for technical exit conditions only - do NOT force liquidation overnight
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

    # ==================== BASE CLASS ABSTRACT METHOD IMPLEMENTATIONS ====================

    def _create_llm_client(self) -> Any:
        """Create the LLM API client (required by BaseTradingAgent)."""
        # Already created in __init__ as self.llm_client
        return self.llm_client.client

    def _send_message_to_llm(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        context: str = "general"
    ) -> Any:
        """Send messages to LLM and get response (required by BaseTradingAgent)."""
        # This is handled by send_message() method
        raise NotImplementedError("Use send_message() instead")

    def _parse_llm_response(self, response: Any, context: str = "general") -> Dict:
        """Parse LLM response into standard format (required by BaseTradingAgent)."""
        return self.llm_client.parse_response(response, context)

    def _format_tool_result(self, tool_use_id: str, result: Any) -> Dict:
        """Format tool execution result for LLM (required by BaseTradingAgent)."""
        return self.llm_client.format_tool_result(tool_use_id, result)


# ==================== BACKWARDS COMPATIBILITY ====================
# Expose component functionality through agent for existing code

def _get_atr_stop_percent(self, symbol: str, current_price: float, multiplier: float = 1.5) -> float:
    """Delegate to trading_logic."""
    return self.trading_logic.get_atr_stop_percent(symbol, current_price, multiplier)

GrokTradingAgent._get_atr_stop_percent = _get_atr_stop_percent


# Factory function for easy instantiation
def create_grok_agent(**kwargs) -> GrokTradingAgent:
    """Create a GrokTradingAgent instance."""
    return GrokTradingAgent(**kwargs)
