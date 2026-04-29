"""
Claude Trading Agent - Core AI agent for day trading automation

This module implements the main AI agent that interprets trading rules
and makes autonomous trading decisions using Claude API.
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

AI_TRADER_DATA = Path(__file__).parent.parent.parent / "ai_trader_data"

import anthropic
from anthropic import Anthropic
from anthropic.types import Message, TextBlock, ToolUseBlock

logger = logging.getLogger(__name__)

# Import token tracker
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from analytics.token_tracker import TokenTracker
from analytics.data_validator import DataValidator

# Import broker enums for use throughout the class
from brokers.base_broker import OrderSide, OrderType

# Import circuit breaker
from risk.circuit_breaker import PortfolioCircuitBreaker

# Import learning database for persistent rebuy blocks
from analytics.learning_database import LearningDatabase


class ClaudeTradingAgent:
    """
    Main trading agent powered by Claucde AI.

    Loads trading rules, manages conversation context, and makes
    autonomous trading decisions based on market conditions.
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
        import threading
        self._state_lock = threading.Lock()
        """
        Initialize the Claude trading agent.

        Args:
            api_key: Anthropic API key (or set ANTHROPIC_API_KEY env var)
            rules_file: Path to XML rules file
            model: Claude model to use for strategic decisions (default: Sonnet)
            data_model: Claude model to use for data fetching (default: Haiku, cost-effective)
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
            broker: Trading broker instance (for orders and account info)
            data_provider: Market data provider instance (for quotes and historical data)
            news_provider: News provider instance (always Alpaca for news API)
            learning_db: Learning database for trade journal (optional)
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set or passed as parameter")

        # Create Anthropic client with timeout to prevent hangs
        # Default timeout: 120 seconds for API calls
        self.client = Anthropic(
            api_key=self.api_key,
            timeout=120.0  # 2 minute timeout to prevent infinite hangs
        )
        self.model = model  # Sonnet for strategic decisions
        self.data_model = data_model  # Haiku for data
        self.thinking_model = thinking_model  # Optional deep reasoning model (Grok Thinking)
        self.use_thinking_for_regime = use_thinking_for_regime  # Use thinking for morning regime check
        self.max_tokens = max_tokens
        self.temperature = temperature

        # Validate models and set fallbacks
        self._validate_and_set_models()

        # Define which contexts require Sonnet (strategic reasoning)
        self.sonnet_contexts = {
            'trading_decision',
            'strategy_change',
            'risk_assessment',
            'news_analysis',
            'trading_plan',
            'error_recovery',
            'initialization',
            'end_of_day',
            'emergency_update'  # Triggered by position monitor watermarks
        }

        # Define which contexts can use thinking mode (if enabled)
        self.thinking_contexts = {
            'market_regime_analysis',  # Morning regime check - high leverage decision
        }

        # Broker and data provider connections
        self.broker = broker  # For trading (orders, positions, account)
        self.data_provider = data_provider  # For market data (quotes, historical, etc.)
        self.news_provider = news_provider  # For news (always Alpaca for news API)
        self.learning_db = learning_db  # For trade journal (entry reasoning at purchase time)
        self.ollama_provider = ollama_provider  # Local LLM for pre-processing/reflection

        # Token tracking
        self.token_tracker = TokenTracker()
        self.current_context = "initialization"  # Track what operation is happening

        # Load trading rules
        self.rules_file = Path(rules_file)
        self.rules_content = self._load_rules()

        # Conversation history
        self.conversation_history: List[Dict[str, Any]] = []

        # Agent state
        self.state = {
            "initialized": False,
            "autonomous_mode": True,
            "current_strategies": [],
            "risk_percent": 1.5,
            "positions": [],
            "cash": 0.0,
            "account_value": 0.0
        }

        # Track positions opened by the agent (only these can be sold)
        # Changed from set to dict to track quantities
        self.agent_opened_positions = {}  # Dict: {symbol: quantity}

        # Track conviction scores for positions (for rebalancing decisions)
        # When selling to rebalance, prefer selling low conviction positions first
        self.agent_position_convictions = {}  # Dict: {symbol: conviction_score (1-10)}

        # Track entry prices and TP targets for partial profit-taking
        self.agent_position_entry_prices = {}  # Dict: {symbol: entry_price}
        self.agent_position_tp_targets = {}    # Dict: {symbol: take_profit_price}
        self.agent_position_sl_targets = {}    # Dict: {symbol: stop_loss_price}
        self.agent_position_partial_profits = {}  # Dict: {symbol: {'taken': bool, 'qty_sold': int, 'price': float}}
        self.agent_position_high_water_marks = {}  # Dict: {symbol: highest_price_seen} - for trailing stops

        # Portfolio-level circuit breaker - pauses new trades after daily/weekly loss limits
        self.circuit_breaker = PortfolioCircuitBreaker()

        # Track bracket order updates to prevent churn (cooldown system)
        # Only update brackets if price moved >2% OR >30 min since last update
        self.agent_bracket_order_updates = {}  # Dict: {symbol: {'timestamp': datetime, 'price': float}}

        # Track pending (unfilled) LIMIT orders across cycles
        # Key: order_id, Value: {symbol, side, requested_qty, placed_at, entry_price, take_profit, conviction_score}
        # Checked each cycle to detect late fills; expired orders auto-cancelled
        self.pending_orders = {}  # Dict: {order_id: {...}}
        self.PENDING_ORDER_TIMEOUT_SEC = 300  # Cancel unfilled LIMIT orders after 5 minutes

        # Blacklist of symbols that existed before agent started (completely off-limits)
        self.forbidden_symbols = set()  # Symbols that cannot be traded at all

        # Track recent orders to prevent duplicates (60-second window)
        self.recent_orders = []  # List of (symbol, side, quantity, timestamp)

        # Track recently sold/bought symbols to prevent LLM hallucination duplicates (30-min cooldown)
        self.recently_sold_symbols = {}  # Dict: {symbol: timestamp}
        self.recently_bought_symbols = {}  # Dict: {symbol: timestamp}
        
        # Track recently closed losing positions (12-hour cooldown to prevent rebuying losers)
        self.recently_closed_losers = {}  # {symbol: {'timestamp': datetime, 'entry': float, 'exit': float, 'pnl_percent': float}}

        # Learning database - used for persistent rebuy blocks (survives restarts)
        try:
            self.learning_db = LearningDatabase()
            # Restore any active rebuy blocks from DB into in-memory dict
            active_blocks = self.learning_db.get_all_rebuy_blocks()
            for block in active_blocks:
                symbol = block["symbol"]
                if symbol not in self.recently_closed_losers:
                    self.recently_closed_losers[symbol] = {
                        'timestamp': datetime.fromisoformat(block["created_at"]),
                        'entry': block.get("entry_price"),
                        'exit': block.get("exit_price"),
                        'pnl_percent': block.get("loss_pct", 0)
                    }
            if active_blocks:
                logger.info(f"Restored {len(active_blocks)} rebuy block(s) from DB: {[b['symbol'] for b in active_blocks]}")
        except Exception as e:
            logger.warning(f"Could not initialize learning DB for rebuy blocks: {e}")
            self.learning_db = None

        # Momentum Reversal Tracker - Catches "dead cat bounces" before they turn back into losers
        # Detects pattern: Position drops → recovers toward break-even → weakens again → SELL before hitting stop
        self.momentum_reversals = {}  # Dict: {symbol: {'low_watermark': float, 'recovery_high': float, 'in_recovery': bool, 'last_update': datetime}}

        # Track trading plan usage (NEW - enforces mandatory trading plan workflow)
        self.last_trading_plan_timestamp = None
        self.current_trading_plan = None
        self.last_analysis_summary = ""  # Persists reasoning from last scan for Q&A

        # PDT (Pattern Day Trader) tracking for accounts under $25k
        self.pdt_day_trades = []  # List of {date, symbol, buy_time, sell_time}
        self.pdt_enabled = False
        self.pdt_max_trades = 3
        self.pdt_preferred_days = ["Tuesday", "Wednesday", "Thursday"]

        # Capital limits (fund $30k to avoid PDT, only trade with amount above base)
        self.capital_limits_enabled = False
        self.base_capital = 0  # Untouchable floor (e.g., $25k) - active = account - base
        self.high_water_mark = 25000.0  # Dynamic base tracking
        self.dynamic_base_enabled = True  # Use high_water * 0.8 as base

        # Track protective MOC orders (for automatic end-of-day position closing)
        self.protective_moc_orders = {}  # Dict: {symbol: [order_ids]}

        # Daily loss tracking for circuit breaker
        self.daily_pnl_percent = 0.0
        self.starting_portfolio_value = 0.0

        # State checkpointing
        self._checkpoint_count = 0
        self._last_checkpoint_time = datetime.now()

        # Strategy tracking for performance evaluation
        self.strategy_log = []  # List of {timestamp, strategy, reason, ...}
        self.trade_log = []  # List of trades with strategy attribution
        self.current_strategy = None  # Current active strategy
        self.price_snapshots = {}  # Dict: {symbol: [{timestamp, price, ...}]}
        
        # Market regime filter for adaptive thresholds
        self.market_regime = None  # Current market regime info
        self.min_conviction_threshold = 8.0  # Default neutral threshold
        self.max_positions_allowed = 2  # Default neutral limit

        # Context agent for daily summary (Grok only, if thinking model configured)
        if self.thinking_model and self.use_thinking_for_regime:
            try:
                from .grok_context_agent import GrokContextAgent
                self.context_agent = GrokContextAgent(
                    agent=self,
                    config={
                        "thinking_model": self.thinking_model,
                        "fast_model": self.data_model,
                    }
                )
                logger.info("GrokContextAgent enabled for daily context management")
            except Exception as e:
                logger.warning(f"Failed to initialize GrokContextAgent: {e}")
                self.context_agent = None
        else:
            self.context_agent = None
            logger.debug("GrokContextAgent not enabled (no thinking model configured)")

        # Load persisted position state (for crash recovery)
        self._load_position_state()

        # Reconcile loaded state with actual broker positions
        # This handles restarts where broker has positions we need to track
        if self.broker:
            self._reconcile_positions_with_broker()
            if self.agent_opened_positions:
                logger.info(f"Startup reconciliation: tracking {len(self.agent_opened_positions)} positions: {list(self.agent_opened_positions.keys())}")

        # Tool definitions for Claude
        self.tools = self._define_tools()

    def _validate_and_set_models(self):
        """
        Validate that the specified models are available and set fallbacks if needed.
        """
        # Test each model individually to properly configure dual model system
        available_models = {}
        
        # Test Sonnet model (strategic decisions)
        sonnet_models_to_test = [
            self.model,  # Primary Sonnet
            "claude-3-5-sonnet-20241022",  # Alternative Sonnet
            "claude-3-sonnet-20240229",  # Fallback Sonnet
        ]
        
        working_sonnet = None
        for model in sonnet_models_to_test:
            try:
                test_response = self.client.messages.create(
                    model=model,
                    max_tokens=5,
                    messages=[{"role": "user", "content": "Hi"}]
                )
                working_sonnet = model
                logger.info(f"Sonnet model {model} is available")
                break
            except Exception as e:
                if "404" in str(e):
                    logger.warning(f"Sonnet model {model} not available (404)")
                else:
                    logger.warning(f"Sonnet model {model} test failed: {e}")
                continue
        
        # Test Haiku model (data operations)
        haiku_models_to_test = [
            self.data_model,  # Primary Haiku
            "claude-3-haiku-20240307",  # Fallback Haiku
            "claude-3-5-haiku-20241022",  # Alternative Haiku (may not be available)
        ]
        
        working_haiku = None
        for model in haiku_models_to_test:
            try:
                test_response = self.client.messages.create(
                    model=model,
                    max_tokens=5,
                    messages=[{"role": "user", "content": "Hi"}]
                )
                working_haiku = model
                logger.info(f"Haiku model {model} is available")
                break
            except Exception as e:
                if "404" in str(e):
                    logger.warning(f"Haiku model {model} not available (404)")
                else:
                    logger.warning(f"Haiku model {model} test failed: {e}")
                continue
        
        # Configure models based on what's available
        original_sonnet = self.model
        original_haiku = self.data_model
        
        if working_sonnet and working_haiku:
            # IDEAL: Both models available - use dual model system
            self.model = working_sonnet  # Strategic decisions
            self.data_model = working_haiku  # Data operations
            logger.info(f"DUAL MODEL SYSTEM: Sonnet={self.model}, Haiku={self.data_model}")
            
        elif working_sonnet and not working_haiku:
            # Sonnet available but no Haiku - use Sonnet for both (expensive but functional)
            self.model = working_sonnet  # Strategic decisions
            self.data_model = working_sonnet  # Data operations (fallback)
            logger.warning(f"SONNET ONLY: Using {working_sonnet} for both strategic and data operations (Haiku unavailable)")
            logger.warning(f"This will increase costs - preferred Haiku model was: {original_haiku}")
            
        elif not working_sonnet and working_haiku:
            # Haiku available but no Sonnet - use Haiku for both (cheaper but less capable)
            self.model = working_haiku  # Strategic decisions (fallback)
            self.data_model = working_haiku  # Data operations
            logger.warning(f"HAIKU ONLY: Using {working_haiku} for both strategic and data operations (Sonnet unavailable)")
            logger.warning(f"Strategic decisions may be less sophisticated - preferred Sonnet model was: {original_sonnet}")
            
        else:
            # Neither model available - critical error
            raise ValueError(f"No Claude models are available with this API key. Tested: {sonnet_models_to_test + haiku_models_to_test}")
        
        # Log final configuration
        if self.model == self.data_model:
            logger.warning(f"SINGLE MODEL MODE: Using {self.model} for all operations")
        else:
            logger.info(f"DUAL MODEL MODE: Strategic={self.model}, Data={self.data_model}")

    def _load_rules(self) -> str:
        """Load trading rules from XML file."""
        if not self.rules_file.exists():
            raise FileNotFoundError(f"Rules file not found: {self.rules_file}")

        with open(self.rules_file, 'r', encoding='utf-8') as f:
            return f.read()

    def _get_state_file_path(self) -> Path:
        """Get the path to the agent state file."""
        return AI_TRADER_DATA / "agent_state.json"

    def _save_position_state(self):
        """Save comprehensive agent state to disk for crash recovery."""
        try:
            state_file = self._get_state_file_path()
            state_file.parent.mkdir(parents=True, exist_ok=True)

            # Build comprehensive state snapshot
            state_snapshot = {
                # Position tracking
                "agent_opened_positions": self.agent_opened_positions,
                "agent_position_convictions": self.agent_position_convictions,  # Conviction scores for rebalancing
                "agent_position_entry_prices": self.agent_position_entry_prices,  # Entry prices for partial profit tracking
                "agent_position_tp_targets": self.agent_position_tp_targets,  # TP targets for partial profit tracking
                "agent_position_partial_profits": self.agent_position_partial_profits,  # Partial profits taken
                "agent_bracket_order_updates": {k: {**v, 'timestamp': v['timestamp'].isoformat() if isinstance(v.get('timestamp'), datetime) else v.get('timestamp', '')} for k, v in self.agent_bracket_order_updates.items()},  # Bracket cooldown
                "forbidden_symbols": list(self.forbidden_symbols),
                "protective_moc_orders": self.protective_moc_orders,
                "recently_closed_losers": {k: {**v, 'timestamp': v['timestamp'].isoformat() if isinstance(v['timestamp'], datetime) else v['timestamp']} for k, v in self.recently_closed_losers.items()},  # 12h rebuy block
                "high_water_mark": self.high_water_mark,
        
                # Agent state
                "state": self.state,

                # Daily tracking
                "daily_pnl_percent": self.daily_pnl_percent,
                "starting_portfolio_value": self.starting_portfolio_value,

                # Strategy tracking
                "strategy_log": self.strategy_log,
                "trade_log": self.trade_log,
                "current_strategy": self.current_strategy,
                "price_snapshots": self.price_snapshots,

                # PDT tracking (persists across days for rolling 5-day window)
                "pdt_day_trades": self.pdt_day_trades,

                # Trading plan tracking (NEW)
                "last_trading_plan_timestamp": self.last_trading_plan_timestamp,
                "current_trading_plan": self.current_trading_plan,

                # Metadata
                "date": datetime.now().date().isoformat(),
                "timestamp": datetime.now().isoformat(),
                "checkpoint_count": getattr(self, '_checkpoint_count', 0) + 1
            }

            # Update checkpoint counter
            self._checkpoint_count = state_snapshot["checkpoint_count"]

            with open(state_file, 'w') as f:
                json.dump(state_snapshot, f, indent=2, default=str)  # default=str handles UUID, datetime, etc.

            logger.debug(f"Saved state checkpoint #{state_snapshot['checkpoint_count']}")

        except Exception as e:
            logger.error(f"Failed to save position state: {e}")

    def _load_position_state(self):
        """Load comprehensive agent state from disk if from same trading day."""
        try:
            state_file = self._get_state_file_path()
            if not state_file.exists():
                logger.debug("No previous state file found, starting fresh")
                return

            with open(state_file, 'r') as f:
                file_content = f.read().strip()
                
                # Check for empty or corrupted file
                if not file_content:
                    logger.warning("Empty agent state file, starting fresh")
                    state_file.unlink(missing_ok=True)  # Delete empty file
                    return
                    
                try:
                    saved_state = json.loads(file_content)
                except json.JSONDecodeError as e:
                    logger.error(f"Corrupted agent state file (JSON error: {e}), attempting to delete and starting fresh")
                    try:
                        state_file.unlink(missing_ok=True)  # Delete corrupted file
                        logger.info("Corrupted agent state file deleted successfully")
                    except PermissionError as pe:
                        logger.warning(f"Could not delete corrupted state file (file in use by another process): {pe}")
                        logger.warning("Continuing with fresh state - corrupted file will be overwritten on next save")
                    except Exception as delete_error:
                        logger.warning(f"Could not delete corrupted state file: {delete_error}")
                        logger.warning("Continuing with fresh state - corrupted file will be overwritten on next save")
                    return

            # Only load if same trading day
            state_date = saved_state.get("date", "")
            if state_date != datetime.now().date().isoformat():
                logger.info(f"State file is from {state_date}, not loading (today is {datetime.now().date().isoformat()})")
                return

            # Restore position tracking
            self.agent_opened_positions = saved_state.get("agent_opened_positions", {})
            self.agent_position_convictions = saved_state.get("agent_position_convictions", {})  # Conviction scores
            self.agent_position_entry_prices = saved_state.get("agent_position_entry_prices", {})  # Entry prices
            self.agent_position_tp_targets = saved_state.get("agent_position_tp_targets", {})  # TP targets
            self.agent_position_sl_targets = saved_state.get("agent_position_sl_targets", {})  # SL targets
            self.agent_position_partial_profits = saved_state.get("agent_position_partial_profits", {})  # Partial profits

            # Restore bracket order cooldown tracking - parse timestamps
            saved_bracket_updates = saved_state.get("agent_bracket_order_updates", {})
            self.agent_bracket_order_updates = {}
            for symbol, update_info in saved_bracket_updates.items():
                try:
                    timestamp_str = update_info.get('timestamp')
                    if timestamp_str:
                        self.agent_bracket_order_updates[symbol] = {
                            **update_info,
                            'timestamp': datetime.fromisoformat(timestamp_str)
                        }
                except (ValueError, TypeError):
                    pass  # Skip invalid entries

            self.protective_moc_orders = saved_state.get("protective_moc_orders", {})

            # Restore recently closed losers (12h rebuy block) - parse timestamps
            self.high_water_mark = saved_state.get("high_water_mark", 25000.0)

            saved_losers = saved_state.get("recently_closed_losers", {})
            self.recently_closed_losers = {}
            for symbol, loser_info in saved_losers.items():
                try:
                    timestamp_str = loser_info.get('timestamp')
                    if timestamp_str:
                        self.recently_closed_losers[symbol] = {
                            **loser_info,
                            'timestamp': datetime.fromisoformat(timestamp_str) if isinstance(timestamp_str, str) else timestamp_str
                        }
                except Exception as e:
                    logger.warning(f"Could not restore loser cooldown for {symbol}: {e}")

            # Restore agent state
            if "state" in saved_state:
                # Merge saved state with current state (preserve defaults for missing keys)
                for key, value in saved_state["state"].items():
                    self.state[key] = value

            # Restore daily tracking
            self.daily_pnl_percent = saved_state.get("daily_pnl_percent", 0.0)
            self.starting_portfolio_value = saved_state.get("starting_portfolio_value", 0.0)

            # Restore strategy tracking
            self.strategy_log = saved_state.get("strategy_log", [])
            self.trade_log = saved_state.get("trade_log", [])
            self.current_strategy = saved_state.get("current_strategy")
            self.price_snapshots = saved_state.get("price_snapshots", {})

            # Restore trading plan tracking (NEW)
            self.last_trading_plan_timestamp = saved_state.get("last_trading_plan_timestamp")
            self.current_trading_plan = saved_state.get("current_trading_plan")

            # Restore PDT tracking (always load - persists across days for rolling window)
            self.pdt_day_trades = saved_state.get("pdt_day_trades", [])
            # Clean up old trades (older than 5 business days)
            self._cleanup_old_pdt_trades()

            # Restore checkpoint counter
            self._checkpoint_count = saved_state.get("checkpoint_count", 0)

            logger.info(
                f"Restored state from checkpoint #{self._checkpoint_count}: "
                f"{len(self.agent_opened_positions)} positions, "
                f"{sum(len(v) for v in self.protective_moc_orders.values())} protective MOC orders, "
                f"daily P&L: {self.daily_pnl_percent:.2f}%, "
                f"strategy: {self.current_strategy}"
            )

        except Exception as e:
            logger.error(f"Failed to load position state: {e}", exc_info=True)

    def _prune_conversation_history(self):
        """Keep only last N messages to prevent token overflow."""
        max_messages = 20

        if len(self.conversation_history) <= max_messages:
            return

        # ENHANCED: More robust conversation history pruning to prevent tool_use_id mismatches
        # Strategy: Keep complete tool_use/tool_result pairs and validate tool_use_ids
        
        # First, identify all tool_use_ids in the conversation
        tool_use_ids = set()
        for message in self.conversation_history:
            if message.get("role") == "assistant" and isinstance(message.get("content"), list):
                for block in message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                        tool_use_ids.add(block["id"])
        
        # Start from the end and work backwards, keeping complete conversations
        pruned_history = []
        i = len(self.conversation_history) - 1
        
        while i >= 0 and len(pruned_history) < max_messages:
            message = self.conversation_history[i]
            
            # Check if this is a tool_result message
            if (message.get("role") == "user" and isinstance(message.get("content"), list)):
                tool_results = [item for item in message.get("content", []) 
                              if isinstance(item, dict) and item.get("type") == "tool_result"]
                
                if tool_results:
                    # This is a tool_result message - find matching tool_use
                    tool_result_ids = {tr.get("tool_use_id") for tr in tool_results if tr.get("tool_use_id")}
                    
                    # Look backwards for the matching assistant message with tool_use
                    found_matching_tool_use = False
                    j = i - 1
                    while j >= 0:
                        prev_message = self.conversation_history[j]
                        if prev_message.get("role") == "assistant" and isinstance(prev_message.get("content"), list):
                            # Check if this assistant message has matching tool_use blocks
                            tool_uses = [block for block in prev_message.get("content", [])
                                        if isinstance(block, dict) and block.get("type") == "tool_use"]
                            
                            if tool_uses:
                                tool_use_message_ids = {tu.get("id") for tu in tool_uses if tu.get("id")}
                                
                                # Check if any tool_result_ids match tool_use_ids in this message
                                if tool_result_ids.intersection(tool_use_message_ids):
                                    # Found matching tool_use message
                                    if prev_message not in pruned_history:
                                        pruned_history.insert(0, prev_message)
                                    found_matching_tool_use = True
                                    break
                        j -= 1
                    
                    # Only add tool_result if we found matching tool_use
                    if found_matching_tool_use:
                        pruned_history.insert(0, message)
                    else:
                        logger.warning(f"Dropping orphaned tool_result message with IDs: {tool_result_ids}")
                else:
                    # Regular user message
                    pruned_history.insert(0, message)
            else:
                # Assistant message or regular user message
                pruned_history.insert(0, message)
            
            i -= 1

        # Final validation: Remove any orphaned tool_results at the beginning
        while (len(pruned_history) > 0 and 
               pruned_history[0].get("role") == "user" and 
               isinstance(pruned_history[0].get("content"), list)):
            
            first_message = pruned_history[0]
            tool_results = [item for item in first_message.get("content", [])
                          if isinstance(item, dict) and item.get("type") == "tool_result"]
            
            if tool_results:
                # Check if there's a matching tool_use in the conversation
                tool_result_ids = {tr.get("tool_use_id") for tr in tool_results if tr.get("tool_use_id")}
                
                # Look for matching tool_use in remaining conversation
                has_matching_tool_use = False
                for msg in pruned_history[1:]:
                    if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                        tool_uses = [block for block in msg.get("content", [])
                                   if isinstance(block, dict) and block.get("type") == "tool_use"]
                        tool_use_message_ids = {tu.get("id") for tu in tool_uses if tu.get("id")}
                        
                        if tool_result_ids.intersection(tool_use_message_ids):
                            has_matching_tool_use = True
                            break
                
                if not has_matching_tool_use:
                    logger.warning(f"Removing orphaned tool_result at start with IDs: {tool_result_ids}")
                    pruned_history.pop(0)
                else:
                    break
            else:
                break

        # Ensure we don't exceed max_messages
        if len(pruned_history) > max_messages:
            # Remove from the beginning, but preserve tool_use/tool_result pairs
            excess = len(pruned_history) - max_messages
            for _ in range(excess):
                if len(pruned_history) <= max_messages:
                    break
                    
                # Check if removing first message would orphan a tool_result
                if (len(pruned_history) > 1 and 
                    pruned_history[1].get("role") == "user" and 
                    isinstance(pruned_history[1].get("content"), list)):
                    
                    tool_results = [item for item in pruned_history[1].get("content", [])
                                  if isinstance(item, dict) and item.get("type") == "tool_result"]
                    
                    if tool_results and pruned_history[0].get("role") == "assistant":
                        # Would orphan tool_result, remove both
                        pruned_history.pop(0)  # Remove assistant
                        if len(pruned_history) > 0:
                            pruned_history.pop(0)  # Remove tool_result
                    else:
                        pruned_history.pop(0)
                else:
                    pruned_history.pop(0)

        self.conversation_history = pruned_history
        logger.info(f"Pruned conversation history to {len(self.conversation_history)} messages")
        
        # Final validation log
        tool_use_count = 0
        tool_result_count = 0
        for msg in self.conversation_history:
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                tool_use_count += len([b for b in msg.get("content", []) 
                                     if isinstance(b, dict) and b.get("type") == "tool_use"])
            elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
                tool_result_count += len([b for b in msg.get("content", []) 
                                        if isinstance(b, dict) and b.get("type") == "tool_result"])
        
        logger.debug(f"Conversation validation: {tool_use_count} tool_use blocks, {tool_result_count} tool_result blocks")

    # ==================== PDT (Pattern Day Trader) Protection ====================

    def _cleanup_old_pdt_trades(self):
        """Remove day trades older than 5 business days from tracking."""
        if not self.pdt_day_trades:
            return

        from datetime import timedelta
        today = datetime.now().date()

        # Calculate date 5 business days ago (roughly 7 calendar days to be safe)
        cutoff_date = today - timedelta(days=7)

        original_count = len(self.pdt_day_trades)
        self.pdt_day_trades = [
            trade for trade in self.pdt_day_trades
            if datetime.fromisoformat(trade["date"]).date() > cutoff_date
        ]

        removed = original_count - len(self.pdt_day_trades)
        if removed > 0:
            logger.info(f"Cleaned up {removed} old PDT trades (older than 5 business days)")

    def _count_pdt_trades_in_window(self) -> int:
        """Count day trades in the rolling 5 business day window."""
        if not self.pdt_day_trades:
            return 0

        from datetime import timedelta
        today = datetime.now().date()

        # 5 business days is approximately 7 calendar days
        cutoff_date = today - timedelta(days=7)

        count = sum(
            1 for trade in self.pdt_day_trades
            if datetime.fromisoformat(trade["date"]).date() > cutoff_date
        )
        return count

    def _get_pdt_trades_remaining(self) -> int:
        """Get number of day trades remaining before hitting PDT limit."""
        if not self.pdt_enabled:
            return 999  # Unlimited

        used = self._count_pdt_trades_in_window()
        return max(0, self.pdt_max_trades - used)

    def _is_preferred_trading_day(self) -> bool:
        """Check if today is a preferred trading day for PDT-limited accounts."""
        day_name = datetime.now().strftime("%A")
        return day_name in self.pdt_preferred_days

    def _record_day_trade(self, symbol: str):
        """Record a completed day trade (buy + sell same day)."""
        trade_record = {
            "date": datetime.now().date().isoformat(),
            "symbol": symbol,
            "timestamp": datetime.now().isoformat()
        }
        self.pdt_day_trades.append(trade_record)
        self._save_position_state()  # Persist immediately

        remaining = self._get_pdt_trades_remaining()
        logger.info(f"PDT: Recorded day trade for {symbol}. {remaining} trades remaining in 5-day window.")

    def get_pdt_status(self) -> dict:
        """Get current PDT status for display/decision making."""
        if not self.pdt_enabled:
            return {
                "enabled": False,
                "message": "PDT protection disabled (account $25k+)"
            }

        trades_used = self._count_pdt_trades_in_window()
        trades_remaining = self._get_pdt_trades_remaining()
        is_preferred_day = self._is_preferred_trading_day()
        day_name = datetime.now().strftime("%A")

        return {
            "enabled": True,
            "trades_used": trades_used,
            "trades_remaining": trades_remaining,
            "max_trades": self.pdt_max_trades,
            "is_preferred_day": is_preferred_day,
            "today": day_name,
            "preferred_days": self.pdt_preferred_days,
            "can_trade": trades_remaining > 0,
            "should_trade": trades_remaining > 0 and is_preferred_day,
            "message": self._get_pdt_message(trades_remaining, is_preferred_day, day_name)
        }

    def _get_pdt_message(self, remaining: int, is_preferred: bool, day_name: str) -> str:
        """Generate human-readable PDT status message."""
        if remaining == 0:
            return f"PDT LIMIT REACHED: No day trades remaining. Wait for older trades to expire."

        if not is_preferred:
            return (
                f"PDT WARNING: Today is {day_name} (not a preferred trading day). "
                f"{remaining} trades remaining. Consider saving for Tue/Wed/Thu."
            )

        return f"PDT OK: {remaining} day trades remaining. {day_name} is a preferred trading day."

    def load_pdt_config(self, broker_config: dict):
        """Load PDT settings from broker config."""
        safety = broker_config.get("safety", {})
        pdt_config = safety.get("pdt_protection", {})

        self.pdt_enabled = pdt_config.get("enabled", False) and pdt_config.get("account_under_25k", False)
        self.pdt_max_trades = pdt_config.get("max_day_trades_per_5_days", 3)
        self.pdt_preferred_days = pdt_config.get("preferred_trading_days", ["Tuesday", "Wednesday", "Thursday"])

        if self.pdt_enabled:
            status = self.get_pdt_status()
            logger.info(f"PDT Protection enabled: {status['message']}")

    # ==================== End PDT Protection ====================

    # ==================== Capital Limits (trade with subset of account) ====================

    def load_capital_limits_config(self, broker_config: dict):
        """Load capital limits settings from broker config."""
        safety = broker_config.get("safety", {})
        capital_config = safety.get("capital_limits", {})

        self.capital_limits_enabled = capital_config.get("enabled", False)
        self.base_capital = capital_config.get("base_capital", 0)
        self.dynamic_base_enabled = capital_config.get("dynamic_base_enabled", True)

        if self.capital_limits_enabled:
            logger.info(
                f"Capital Limits enabled: Base=${self.base_capital:,.0f} (dynamic: {self.dynamic_base_enabled}). "
                f"Active capital = account_value - dynamic_base (high_water * 0.8)"
            )

    def get_active_capital(self, account_value: float) -> float:
        """
        Calculate active (tradeable) capital.

        Active capital = account_value - dynamic_base_capital (high_water_mark * 0.8)
        High water mark updated to max historical account value.

        Args:
            account_value: Current total account value

        Returns:
            Amount available for trading (0 if below base)
        """
        if not self.capital_limits_enabled:
            return account_value  # No limit, use full account

        with self._state_lock:
            self.high_water_mark = max(self.high_water_mark, account_value)
            dynamic_base = self.high_water_mark * 0.8 if self.dynamic_base_enabled else self.base_capital
            active_capital = max(0, account_value - dynamic_base)
            logger.debug(f"Dynamic capital: high_water=${self.high_water_mark:.0f}, base=${dynamic_base:.0f}, active=${active_capital:.0f}")
            return active_capital

    def get_available_trading_capital(self, account_value: float, current_positions_value: float = 0) -> float:
        """
        Get the amount of capital available for NEW trades.

        Available = active_capital - current_positions_value

        Args:
            account_value: Current total account value
            current_positions_value: Total value of currently held positions

        Returns:
            Available capital for new trades
        """
        if not self.capital_limits_enabled:
            return float('inf')  # No limit

        active = self.get_active_capital(account_value)
        return max(0, active - current_positions_value)

    def get_capital_limits_status(self, account_value: float, current_positions_value: float = 0) -> dict:
        """Get current capital limits status for display/decision making."""
        if not self.capital_limits_enabled:
            return {
                "enabled": False,
                "message": "Capital limits disabled - using full account"
            }

        active = self.get_active_capital(account_value)
        available = self.get_available_trading_capital(account_value, current_positions_value)
        used = current_positions_value
        utilization = (used / active * 100) if active > 0 else 0

        dynamic_base = self.high_water_mark * 0.8 if self.dynamic_base_enabled else self.base_capital

        return {
            "enabled": True,
            "base_capital": self.base_capital,
            "dynamic_base": dynamic_base,
            "high_water_mark": self.high_water_mark,
            "dynamic_enabled": self.dynamic_base_enabled,
            "account_value": account_value,
            "active_capital": active,
            "capital_in_positions": used,
            "capital_available": available,
            "utilization_percent": utilization,
            "message": (
                f"Account ${account_value:,.0f} - Dynamic Base ${dynamic_base:,.0f} (high_water ${self.high_water_mark:,.0f}) = "
                f"${active:,.0f} active. "
                f"${used:,.0f} used ({utilization:.1f}%), ${available:,.0f} avail"
            )
        }

    # ==================== End Capital Limits ====================

    # ==================== Market Regime Filter ====================

    def set_market_regime(self, regime_info: dict):
        """
        Set market regime info and update adaptive thresholds.
        
        Called by scheduler after regime check at market open.
        Updates conviction thresholds and position limits based on regime.
        
        Args:
            regime_info: Dict from MarketRegimeFilter.check_market_regime()
        """
        self.market_regime = regime_info
        self.min_conviction_threshold = regime_info.get('min_conviction', 8.0)
        self.max_positions_allowed = regime_info.get('max_positions', 2)
        
        regime_classification = regime_info.get('regime', 'NEUTRAL')
        spy_change = regime_info.get('spy_change_pct', 0)
        qqq_change = regime_info.get('qqq_change_pct', 0)
        
        logger.info(f"Market regime set: {regime_classification}")
        logger.info(f"  SPY: {spy_change:+.2f}%, QQQ: {qqq_change:+.2f}%")
        logger.info(f"  Min Conviction: {self.min_conviction_threshold}/10")
        logger.info(f"  Max Positions: {self.max_positions_allowed}")
        logger.info(f"  Strategy: {regime_info.get('strategy', 'N/A')}")
        
        # Save state with regime info
        self._save_position_state()

    def get_market_regime_status(self) -> dict:
        """Get current market regime status for display/decision making."""
        if not self.market_regime:
            return {
                "enabled": False,
                "message": "Market regime filter not active (using default thresholds)"
            }
        
        return {
            "enabled": True,
            "regime": self.market_regime.get('regime', 'UNKNOWN'),
            "spy_change": self.market_regime.get('spy_change_pct', 0),
            "qqq_change": self.market_regime.get('qqq_change_pct', 0),
            "min_conviction": self.min_conviction_threshold,
            "max_positions": self.max_positions_allowed,
            "strategy": self.market_regime.get('strategy', 'N/A'),
            "message": f"{self.market_regime.get('regime', 'UNKNOWN')}: Min conviction {self.min_conviction_threshold}/10, Max {self.max_positions_allowed} positions"
        }

    # ==================== End Market Regime Filter ====================

    def log_strategy_change(self, strategy: str, reason: str = ""):
        """
        Log a change in trading strategy.

        Args:
            strategy: Name of the strategy (e.g., "news_catalyst", "bounce", "momentum", "breakout")
            reason: Reason for the strategy change
        """
        timestamp = datetime.now().isoformat()

        # Handle None/null strategy values
        if strategy is None or strategy == "null" or strategy == "None":
            strategy = "unspecified"

        # Only log if strategy actually changed
        if strategy != self.current_strategy:
            strategy_entry = {
                "timestamp": timestamp,
                "strategy": strategy,
                "previous_strategy": self.current_strategy,
                "reason": reason
            }

            self.strategy_log.append(strategy_entry)
            self.current_strategy = strategy

            # Update state
            if "current_strategies" not in self.state:
                self.state["current_strategies"] = []
            if strategy not in self.state["current_strategies"]:
                self.state["current_strategies"].append(strategy)

            logger.info(f"Strategy changed to '{strategy}': {reason}")

            # Checkpoint state after strategy change
            self._save_position_state()

    def log_trade(self, symbol: str, side: str, quantity: int, price: float,
                  entry_price: float = None, stop_loss: float = None,
                  take_profit: float = None, reason: str = "",
                  conviction_score: int = None):
        """
        Log a trade with strategy attribution and R:R information.

        Args:
            symbol: Stock symbol
            side: "buy" or "sell"
            quantity: Number of shares
            price: Execution price
            entry_price: Entry price for position (for BUY orders)
            stop_loss: Stop loss price
            take_profit: Take profit / target price
            reason: Reason for the trade
            conviction_score: Original conviction score (1-10) for this position
        """
        timestamp = datetime.now().isoformat()

        # Calculate risk/reward ratio if applicable
        risk_reward = None
        if entry_price and stop_loss and take_profit:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            if risk > 0:
                risk_reward = reward / risk

        trade_entry = {
            "timestamp": timestamp,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_reward": risk_reward,
            "strategy": self.current_strategy,
            "reason": reason,
            "conviction_score": conviction_score  # Track conviction for later reference
        }

        self.trade_log.append(trade_entry)

        conviction_str = f", Conviction: {conviction_score}/10" if conviction_score else ""
        price_str = f"${price:.2f}" if price is not None else "N/A"
        rr_str = f"{risk_reward:.2f}" if risk_reward else "N/A"
        logger.info(
            f"Trade logged: {side.upper()} {quantity} {symbol} @ {price_str} "
            f"[Strategy: {self.current_strategy}, R:R: {rr_str}{conviction_str}]"
        )

    def _check_order_cooldown(self, symbol: str, side: str, cooldown_seconds: int = 1800) -> dict:
        """
        Check if an order was recently executed for this symbol/side.
        Includes position confirmation to catch LLM hallucinations.

        Args:
            symbol: Stock symbol
            side: "buy" or "sell"
            cooldown_seconds: Minimum seconds between orders (default: 30 minutes)

        Returns:
            Dict with 'allowed': True/False and 'reason' if blocked
        """
        import time
        current_time = time.time()
        symbol = symbol.upper()
        side = side.lower()

        # Use appropriate tracking dict
        if side == "sell":
            tracking_dict = self.recently_sold_symbols
        else:
            if not hasattr(self, 'recently_bought_symbols'):
                self.recently_bought_symbols = {}
            tracking_dict = self.recently_bought_symbols

        # Cleanup old entries (keep 30+ min history for cooldown check)
        for sym in list(tracking_dict.keys()):
            if current_time - tracking_dict[sym] > cooldown_seconds + 60:
                del tracking_dict[sym]

        # Check cooldown
        if symbol in tracking_dict:
            elapsed = current_time - tracking_dict[symbol]
            if elapsed < cooldown_seconds:
                mins_remaining = (cooldown_seconds - elapsed) / 60
                logger.warning(f"COOLDOWN BLOCK: {side.upper()} {symbol} blocked - last {side} was {elapsed:.0f}s ago ({mins_remaining:.1f} min remaining)")
                return {
                    'allowed': False,
                    'reason': f'cooldown_active',
                    'elapsed_seconds': elapsed,
                    'cooldown_seconds': cooldown_seconds,
                    'minutes_remaining': mins_remaining
                }

        # POSITION CONFIRMATION: Verify the action makes sense
        if side == "sell":
            # Confirm we actually have a position to sell
            agent_qty = self.agent_opened_positions.get(symbol, 0)
            if agent_qty <= 0:
                logger.warning(f"CONFIRMATION BLOCK: SELL {symbol} rejected - no position in agent tracking (qty={agent_qty})")
                return {
                    'allowed': False,
                    'reason': 'no_position_to_sell',
                    'agent_qty': agent_qty
                }

            # Double-check with broker
            if self.broker:
                try:
                    broker_qty = 0
                    account_info = self.broker.get_account_info()
                    if account_info and account_info.positions:
                        for pos in account_info.positions:
                            if pos.symbol.upper() == symbol:
                                broker_qty = pos.quantity
                                break
                    if broker_qty <= 0:
                        logger.warning(f"CONFIRMATION BLOCK: SELL {symbol} rejected - broker shows no position (broker_qty={broker_qty})")
                        return {
                            'allowed': False,
                            'reason': 'no_broker_position',
                            'broker_qty': broker_qty
                        }
                except Exception as e:
                    logger.warning(f"Could not confirm broker position for {symbol}: {e}")
                    # Continue anyway - agent tracking is our backup

        elif side == "buy":
            # Confirm we don't already have this position
            agent_qty = self.agent_opened_positions.get(symbol, 0)
            if agent_qty > 0:
                logger.warning(f"CONFIRMATION BLOCK: BUY {symbol} rejected - already holding {agent_qty} shares")
                return {
                    'allowed': False,
                    'reason': 'already_holding_position',
                    'agent_qty': agent_qty
                }

        return {'allowed': True}

    def _check_and_record_sell(self, symbol: str, cooldown_seconds: int = 15) -> bool:
        """
        Check if a sell is allowed (not on cooldown) and record it if allowed.
        
        This prevents double-selling the same position within the cooldown period.
        
        Args:
            symbol: Stock symbol to check
            cooldown_seconds: Cooldown period in seconds (default: 15)
            
        Returns:
            True if sell is allowed (not on cooldown), False if blocked
        """
        import time
        symbol = symbol.upper()
        current_time = time.time()
        
        # Check if symbol was recently sold
        if symbol in self.recently_sold_symbols:
            last_sell_time = self.recently_sold_symbols[symbol]
            elapsed = current_time - last_sell_time
            
            if elapsed < cooldown_seconds:
                # Still on cooldown
                return False
        
        # Allowed - record this sell
        self.recently_sold_symbols[symbol] = current_time
        return True
    
    def _record_order_executed(self, symbol: str, side: str):
        """Record that an order was successfully executed (for cooldown tracking)."""
        import time
        symbol = symbol.upper()
        side = side.lower()

        if side == "sell":
            self.recently_sold_symbols[symbol] = time.time()
            logger.info(f"Recorded SELL for {symbol} - 30-min cooldown started")
        else:
            if not hasattr(self, 'recently_bought_symbols'):
                self.recently_bought_symbols = {}
            self.recently_bought_symbols[symbol] = time.time()
            logger.info(f"Recorded BUY for {symbol} - 30-min cooldown started")

    def _get_atr_stop_percent(self, symbol: str, current_price: float, multiplier: float = 1.5) -> float:
        """
        Calculate ATR-based stop loss percentage for a symbol.

        Args:
            symbol: Stock symbol
            current_price: Current price of the stock
            multiplier: ATR multiplier (1.5 for SL orders, 2.0 for emergency rule)

        Returns:
            Stop loss percentage (e.g., 2.5 for 2.5%)
            Falls back to 1.5% if ATR cannot be calculated
        """
        try:
            if self.data_provider and hasattr(self.data_provider, 'calculate_atr'):
                atr = self.data_provider.calculate_atr(symbol, period=14)
                if atr and current_price > 0:
                    # Convert ATR to percentage of current price
                    atr_percent = (atr / current_price) * 100
                    # Apply multiplier
                    stop_percent = atr_percent * multiplier
                    # Enforce floor (1%) and ceiling (3%)
                    stop_percent = max(1.0, min(3.0, stop_percent))
                    logger.debug(f"{symbol}: ATR=${atr:.2f}, ATR%={atr_percent:.2f}%, Stop%={stop_percent:.2f}% (multiplier={multiplier})")
                    return stop_percent
        except Exception as e:
            logger.warning(f"Could not calculate ATR for {symbol}: {e}")

        # Fallback to default
        return 1.5  # Default 1.5% if ATR unavailable

    def record_price_snapshot(self, symbol: str, price: float, context: str = ""):
        """
        Record a price snapshot for chart generation.

        Args:
            symbol: Stock symbol
            price: Current price
            context: Context of the snapshot (e.g., "update", "entry", "exit")
        """
        timestamp = datetime.now().isoformat()

        if symbol not in self.price_snapshots:
            self.price_snapshots[symbol] = []

        snapshot = {
            "timestamp": timestamp,
            "price": price,
            "context": context
        }

        self.price_snapshots[symbol].append(snapshot)

        # Limit snapshots to last 200 per symbol to avoid memory bloat
        if len(self.price_snapshots[symbol]) > 200:
            self.price_snapshots[symbol] = self.price_snapshots[symbol][-200:]

    def _calculate_technical_indicators(self, symbol: str, bars: list) -> dict:
        """
        Calculate technical indicators for mean reversion and volume profile analysis.

        Args:
            symbol: Stock symbol
            bars: List of historical bars (OHLCV data)

        Returns:
            dict with technical indicators including RSI, Bollinger Bands, volume profile
        """
        try:
            if not bars or len(bars) < 20:
                return {"error": "Insufficient data for technical analysis (need 20+ bars)"}

            # Convert to numpy arrays for calculations
            import numpy as np

            closes = np.array([bar['close'] for bar in bars])
            highs = np.array([bar['high'] for bar in bars])
            lows = np.array([bar['low'] for bar in bars])
            volumes = np.array([bar['volume'] for bar in bars])

            current_price = closes[-1]

            # Calculate RSI (14-period)
            rsi = self._calculate_rsi(closes, period=14)

            # Calculate Bollinger Bands (20-period, 2 std dev)
            bb_middle, bb_upper, bb_lower = self._calculate_bollinger_bands(closes, period=20, std_dev=2.0)

            # Calculate Volume Profile (find high-volume price levels)
            volume_profile = self._calculate_volume_profile(bars)

            # Determine mean reversion setups
            mean_reversion_signal = None
            mean_reversion_confidence = 0

            if rsi < 30 and current_price < bb_lower:
                mean_reversion_signal = "OVERSOLD_BOUNCE"
                mean_reversion_confidence = 8
            elif rsi > 70 and current_price > bb_upper:
                mean_reversion_signal = "OVERBOUGHT_FADE"
                mean_reversion_confidence = 7
            elif 30 <= rsi <= 40 and current_price <= bb_middle:
                mean_reversion_signal = "OVERSOLD_MILD"
                mean_reversion_confidence = 6
            elif 60 <= rsi <= 70 and current_price >= bb_middle:
                mean_reversion_signal = "OVERBOUGHT_MILD"
                mean_reversion_confidence = 5

            return {
                "symbol": symbol,
                "current_price": float(current_price),
                "rsi": float(rsi),
                "bollinger_bands": {
                    "upper": float(bb_upper),
                    "middle": float(bb_middle),
                    "lower": float(bb_lower),
                    "position": "above" if current_price > bb_upper else "below" if current_price < bb_lower else "inside"
                },
                "volume_profile": volume_profile,
                "mean_reversion_signal": mean_reversion_signal,
                "mean_reversion_confidence": mean_reversion_confidence
            }

        except Exception as e:
            logger.error(f"Error calculating technical indicators for {symbol}: {e}")
            return {"error": str(e)}

    def _calculate_rsi(self, prices: 'np.ndarray', period: int = 14) -> float:
        """Calculate Relative Strength Index."""
        import numpy as np

        if len(prices) < period + 1:
            return 50.0  # Neutral if not enough data

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0  # Maximum RSI if no losses

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_bollinger_bands(self, prices: 'np.ndarray', period: int = 20, std_dev: float = 2.0) -> tuple:
        """Calculate Bollinger Bands (middle, upper, lower)."""
        import numpy as np

        if len(prices) < period:
            # Not enough data - return current price for all bands
            current = prices[-1]
            return current, current, current

        # Use last 'period' prices for calculation
        recent_prices = prices[-period:]

        middle = np.mean(recent_prices)
        std = np.std(recent_prices)

        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)

        return middle, upper, lower

    def _calculate_volume_profile(self, bars: list, num_buckets: int = 20) -> dict:
        """
        Calculate volume profile to find high-volume price levels.

        Returns:
            dict with POC (point of control), value area, and support/resistance levels
        """
        import numpy as np

        if len(bars) < 10:
            return {"error": "Not enough bars for volume profile"}

        # Extract prices and volumes
        prices = [bar['close'] for bar in bars]
        volumes = [bar['volume'] for bar in bars]

        min_price = min(prices)
        max_price = max(prices)

        # Create price buckets
        bucket_size = (max_price - min_price) / num_buckets
        if bucket_size == 0:
            bucket_size = max_price * 0.01  # 1% if price range is zero

        volume_by_bucket = [0] * num_buckets

        # Accumulate volume in each price bucket
        for i, bar in enumerate(bars):
            price = bar['close']
            volume = bar['volume']

            bucket_idx = int((price - min_price) / bucket_size) if bucket_size > 0 else 0
            bucket_idx = min(bucket_idx, num_buckets - 1)  # Clamp to last bucket

            volume_by_bucket[bucket_idx] += volume

        # Find Point of Control (POC) - price level with highest volume
        poc_bucket_idx = volume_by_bucket.index(max(volume_by_bucket))
        poc_price = min_price + (poc_bucket_idx * bucket_size) + (bucket_size / 2)

        # Find Value Area (70% of total volume)
        total_volume = sum(volume_by_bucket)
        value_area_volume = total_volume * 0.70

        # Sort buckets by volume (descending)
        sorted_buckets = sorted(enumerate(volume_by_bucket), key=lambda x: x[1], reverse=True)

        accumulated_volume = 0
        value_area_buckets = []

        for bucket_idx, bucket_volume in sorted_buckets:
            accumulated_volume += bucket_volume
            value_area_buckets.append(bucket_idx)
            if accumulated_volume >= value_area_volume:
                break

        # Calculate value area high/low
        value_area_buckets.sort()
        vah_bucket = value_area_buckets[-1] if value_area_buckets else poc_bucket_idx
        val_bucket = value_area_buckets[0] if value_area_buckets else poc_bucket_idx

        vah_price = min_price + (vah_bucket * bucket_size) + bucket_size
        val_price = min_price + (val_bucket * bucket_size)

        return {
            "poc": float(poc_price),  # Point of Control (highest volume price)
            "value_area_high": float(vah_price),  # Top of value area
            "value_area_low": float(val_price),  # Bottom of value area
            "current_vs_poc": "above" if prices[-1] > poc_price else "below",
            "total_volume": int(total_volume)
        }

    def _define_tools(self) -> List[Dict[str, Any]]:
        """
        Define tools that Claude can use during trading.

        These tools allow the agent to interact with brokers,
        fetch market data, and perform calculations.
        """
        return [
            {
                "name": "get_market_data",
                "description": "Fetch current market price and data for a stock symbol",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock ticker symbol (e.g., AAPL, TSLA)"
                        },
                        "data_type": {
                            "type": "string",
                            "enum": ["quote", "intraday", "historical"],
                            "description": "Type of market data to fetch"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "place_order",
                "description": "Place a trading order through the connected broker",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "side": {
                            "type": "string",
                            "enum": ["buy", "sell"],
                            "description": "Order side"
                        },
                        "quantity": {
                            "type": "integer",
                            "description": "Number of shares"
                        },
                        "order_type": {
                            "type": "string",
                            "enum": ["market", "limit", "stop", "moc"],
                            "description": "Order type"
                        },
                        "limit_price": {
                            "type": "number",
                            "description": "Limit price (required for limit orders)"
                        },
                        "stop_price": {
                            "type": "number",
                            "description": "Stop price (required for stop orders, also used for R:R calculation)"
                        },
                        "take_profit": {
                            "type": "number",
                            "description": "Take profit / target price (used for R:R calculation)"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for trade (e.g., 'stop_loss', 'take_profit', 'partial_profit', 'market_close', 'entry_signal', 'breakout')"
                        }
                    },
                    "required": ["symbol", "side", "quantity", "order_type"]
                }
            },
            {
                "name": "place_bracket_order",
                "description": "Place a bracket order (entry + take profit + stop loss all in one). PREFERRED for buy entries - guarantees TP/SL are in place when entry fills. Entry is a limit order at entry_price, TP and SL are automatically linked as OCO.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Stock symbol"},
                        "quantity": {"type": "integer", "description": "Number of shares to buy"},
                        "entry_price": {"type": "number", "description": "Limit price for entry order"},
                        "take_profit_price": {"type": "number", "description": "Limit price for take profit exit"},
                        "stop_loss_price": {"type": "number", "description": "Stop price for stop loss exit"},
                        "reason": {"type": "string", "description": "Reason for trade (e.g., 'breakout', 'momentum', 'dip_buy')"}
                    },
                    "required": ["symbol", "quantity", "entry_price", "take_profit_price", "stop_loss_price"]
                }
            },
            {
                "name": "get_account_info",
                "description": "Get current account balance, positions, and buying power",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "create_trading_plan",
                "description": "MANDATORY FIRST STEP: Create a complete trading plan with multiple stocks before placing ANY orders. This prevents overbuying by allocating available capital across all planned positions using conviction-based weighting.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "trading_candidates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "entry_price": {"type": "number"},
                                    "stop_price": {"type": "number"},
                                    "conviction_score": {"type": "number", "description": "1-10 scale of confidence in this trade (higher score = larger allocation)"},
                                    "strategy": {"type": "string", "description": "Trading strategy for this position"},
                                    "take_profit": {"type": "number", "description": "Target profit price (optional)"}
                                },
                                "required": ["symbol", "entry_price", "stop_price", "conviction_score", "strategy"]
                            },
                            "description": "List of stocks you want to trade today with their entry/stop levels and conviction scores"
                        },
                        "risk_percent": {"type": "number", "description": "Total risk percentage across all positions (default 1.5%)"}
                    },
                    "required": ["trading_candidates"]
                }
            },
            {
                "name": "calculate_position_size",
                "description": "DEPRECATED: Single position sizing that leads to overbuying. Use create_trading_plan instead.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_value": {"type": "number"},
                        "risk_percent": {"type": "number"},
                        "entry_price": {"type": "number"},
                        "stop_price": {"type": "number"}
                    },
                    "required": ["account_value", "risk_percent", "entry_price", "stop_price"]
                }
            },
            {
                "name": "get_market_time_info",
                "description": "Get current market time, status (open/closed), and minutes until open/close",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "search_market_news",
                "description": "Search for recent market news and catalysts",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g., 'tech sector news', 'NVDA earnings')"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "set_trading_strategy",
                "description": "Declare the current trading strategy being used. Call this when you change strategies or at start of day to declare initial strategy.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "strategy": {
                            "type": "string",
                            "description": "Name of the strategy (e.g., 'news_catalyst', 'bounce', 'momentum', 'breakout', 'mean_reversion', 'gap_fade', 'VWAP', 'opening_range_breakout', 'relative_strength')"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for using this strategy or switching to it"
                        }
                    },
                    "required": ["strategy", "reason"]
                }
            },
            {
                "name": "get_market_regime",
                "description": "Analyze current market regime (volatility and trend). Returns VIX level, trend direction, recommended strategies, and position size adjustment. Use this at start of day or when market conditions change significantly.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "analyze_multi_timeframe",
                "description": "Analyze a symbol across multiple timeframes (daily and intraday). Returns daily trend, key support/resistance levels, and trade direction bias. Use before entering a position to align with higher timeframe trends.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol to analyze"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "check_correlation_risk",
                "description": "Check if adding a new position would create correlation risk with existing positions. Use before placing an order to avoid over-concentration.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to check"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "get_strategy_performance",
                "description": "Get performance metrics for trading strategies (win rate, confidence score, R:R ratio). Use this to identify which strategies are working best and should be prioritized.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "calculate_dynamic_position_size",
                "description": "Calculate optimal position size using Kelly Criterion and multiple adjustment factors (strategy performance, volatility, streak, correlation). More sophisticated than basic calculate_position_size.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to size position for"
                        },
                        "entry_price": {
                            "type": "number",
                            "description": "Planned entry price"
                        },
                        "stop_price": {
                            "type": "number",
                            "description": "Stop loss price"
                        },
                        "strategy": {
                            "type": "string",
                            "description": "Strategy being used (for performance lookup)"
                        }
                    },
                    "required": ["symbol", "entry_price", "stop_price"]
                }
            },
            {
                "name": "analyze_technical_indicators",
                "description": "Analyze technical indicators for mean reversion setups and volume profile. Returns RSI, Bollinger Bands, volume profile (POC/value area), and mean reversion signals (OVERSOLD_BOUNCE, OVERBOUGHT_FADE, etc.). Use this to identify bounce opportunities at oversold levels or fade opportunities at overbought levels.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol to analyze"
                        },
                        "timeframe": {
                            "type": "string",
                            "enum": ["intraday", "daily"],
                            "description": "Timeframe for analysis (intraday=15min bars for day trading, daily=daily bars for swing)"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "extend_take_profit",
                "description": "Extend the take-profit target for an open position mid-trade. Use when conviction remains high AND price momentum is strong and the original TP is about to be hit. Only extends upward - never lowers TP. This replaces the OCO bracket atomically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock ticker symbol of the open position"
                        },
                        "new_take_profit": {
                            "type": "number",
                            "description": "New take-profit price (must be higher than current TP)"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why extending TP (e.g., 'momentum accelerating', 'volume surge confirms breakout')"
                        }
                    },
                    "required": ["symbol", "new_take_profit", "reason"]
                }
            },
            {
                "name": "update_position_conviction",
                "description": "Update your conviction score for an open position after reassessment. IMPORTANT: If conviction drops below the entry threshold (typically 8), this will trigger an immediate exit - the thesis is broken. Call this whenever your view on a held position changes materially.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock ticker symbol of the open position"
                        },
                        "conviction": {
                            "type": "number",
                            "description": "Updated conviction score 1-10"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why conviction changed (e.g., 'momentum fading', 'news catalyst resolved', 'thesis intact')"
                        }
                    },
                    "required": ["symbol", "conviction", "reason"]
                }
            }
        ]

    def _get_protected_positions_warning(self) -> str:
        """Get warning message about position restrictions."""
        if not self.forbidden_symbols:
            return "No restrictions - account was empty at start. You can trade any symbol."

        symbols = ', '.join(sorted(self.forbidden_symbols))
        return f"""FORBIDDEN SYMBOLS (DO NOT TRADE): {symbols}

These symbols had existing positions when trading started. You CANNOT:
- Buy these symbols (would mix with existing positions)
- Sell these symbols (they're long-term holdings)

You CAN trade ANY OTHER symbols not in this list. When you buy a new symbol, you can add to it or sell it later."""

    def _select_model(self, context: str) -> str:
        """
        Select the appropriate model based on operation context.

        Args:
            context: The current operation context

        Returns:
            Model name to use (Thinking for regime, Sonnet for strategic, Haiku for data)
        """
        # OVERRIDE: Force thinking model if Ctrl+G was pressed (manual override)
        if getattr(self, '_force_thinking_model', False) and self.thinking_model:
            logger.info(f"FORCED THINKING MODEL (Ctrl+G) for context: {context}")
            return self.thinking_model

        # Use Thinking model for high-leverage decisions (if configured)
        if (self.thinking_model and
            self.use_thinking_for_regime and
            context in self.thinking_contexts):
            logger.info(f"Using Thinking model for context: {context}")
            return self.thinking_model

        # Use Sonnet for strategic decision-making contexts
        if context in self.sonnet_contexts:
            logger.debug(f"Using Sonnet for context: {context}")
            return self.model

        # Use Haiku for all other contexts (data fetching, monitoring, etc.)
        logger.debug(f"Using Haiku for context: {context}")
        return self.data_model

    def _get_model_display_name(self, model_name: str) -> str:
        """
        Get a display name for the model based on its type.

        Args:
            model_name: The model name being used

        Returns:
            Display name (e.g., "Sonnet", "Haiku", "Reasoning", "Fast")
        """
        if not model_name:
            return "Unknown"

        model_lower = model_name.lower()

        # Grok models
        if "grok" in model_lower:
            if "reasoning" in model_lower:
                return "Reasoning"
            elif "fast" in model_lower:
                return "Fast"
            else:
                return "Grok"

        # Claude models
        elif "claude" in model_lower:
            if "sonnet" in model_lower:
                return "Sonnet"
            elif "haiku" in model_lower:
                return "Haiku"
            else:
                return "Claude"

        # Default
        return "AI"

    def _clear_tool_history_for_model_switch(self, new_model: str):
        """
        Clear tool_use/tool_result pairs from conversation history when switching models.

        Claude API rejects messages with tool_use_ids that don't exist in the current
        conversation context. When switching between Sonnet and Haiku, we need to
        strip out tool_use/tool_result pairs to prevent 400 errors.
        """
        if not hasattr(self, '_last_used_model'):
            self._last_used_model = None

        # Check if we're switching models
        if self._last_used_model and self._last_used_model != new_model:
            logger.info(f"Model switch detected: {self._last_used_model} -> {new_model}")
            logger.info("Clearing tool history for model switch (preserving current user message)")

            # AGGRESSIVE CLEANUP: On model switch, clear ALL old conversation history
            # BUT preserve the current user message (last message in history if it's a user message)
            # This is the safest approach - start fresh with new model
            # The prefetch data in the prompt provides context anyway
            preserved_message = None
            if self.conversation_history:
                last_msg = self.conversation_history[-1]
                if last_msg.get("role") == "user" and isinstance(last_msg.get("content"), str):
                    preserved_message = last_msg
                    logger.info("Preserving current user message for new model context")

            self.conversation_history = []
            if preserved_message:
                self.conversation_history.append(preserved_message)
                logger.info("Conversation history CLEARED, current user message preserved")
            else:
                logger.info("Conversation history CLEARED for model switch (starting fresh)")

        self._last_used_model = new_model
    
    def _reconcile_positions_with_broker(self):
        """
        Reconcile agent position tracking with actual broker positions.
        
        This ensures agent_opened_positions dict always matches broker reality.
        Called after every trade to keep tracking accurate.
        """
        try:
            if not self.broker:
                logger.warning("No broker connected - cannot reconcile positions")
                return
                
            account_info = self.broker.get_account_info()
            if not account_info or not account_info.positions:
                # No positions at broker - clear agent tracking
                if self.agent_opened_positions:
                    logger.info(f"Reconciliation: Broker has no positions, clearing agent tracking of {len(self.agent_opened_positions)} positions")
                    self.agent_opened_positions.clear()
                return
            
            # Build actual broker positions dict
            broker_positions = {}
            for pos in account_info.positions:
                if pos.quantity > 0:  # Only track positive quantities
                    broker_positions[pos.symbol.upper()] = pos.quantity
            
            # Compare and sync
            changes_made = False
            
            # Check for positions in agent dict that don't exist at broker
            for symbol in list(self.agent_opened_positions.keys()):
                if symbol.upper() not in broker_positions:
                    logger.warning(f"Reconciliation: {symbol} in agent dict but NOT at broker - removing from tracking")
                    del self.agent_opened_positions[symbol]
                    changes_made = True
                else:
                    # Position exists at broker - sync quantity
                    agent_qty = self.agent_opened_positions[symbol]
                    broker_qty = broker_positions[symbol.upper()]
                    
                    if agent_qty != broker_qty:
                        logger.warning(f"Reconciliation: {symbol} quantity mismatch - Agent={agent_qty}, Broker={broker_qty} - UPDATING to broker value")
                        self.agent_opened_positions[symbol] = broker_qty
                        changes_made = True
            
            # Check for positions at broker that aren't in agent dict (shouldn't happen, but handle it)
            for symbol, qty in broker_positions.items():
                if symbol not in self.agent_opened_positions:
                    logger.warning(f"Reconciliation: {symbol} at broker but NOT in agent dict - adding to tracking")
                    self.agent_opened_positions[symbol] = qty
                    changes_made = True
            
            if changes_made:
                logger.info(f"Reconciliation complete: Agent dict now matches broker positions")
                logger.info(f"Current agent positions: {self.agent_opened_positions}")
                # Save state after reconciliation
                self._save_position_state()
            else:
                logger.debug(f"Reconciliation: All positions match (agent={len(self.agent_opened_positions)}, broker={len(broker_positions)})")
                
        except Exception as e:
            logger.error(f"Position reconciliation failed: {e}", exc_info=True)

    def _build_system_prompt(self):
        """
        Build the system prompt that includes trading rules.
        
        Returns structured prompt for caching support.
        
        Returns:
            List of content blocks with cache control for static content
        """
        # CACHEABLE: Trading rules (static, doesn't change during trading day)
        cacheable_rules = {
            "type": "text",
            "text": self.rules_content,
            "cache_control": {"type": "ephemeral"}
        }
        
        # DYNAMIC: Guidelines and current state (changes frequently)
        dynamic_content = {
            "type": "text",
            "text": f"""You are an autonomous day trading agent. Your behavior is governed by the trading rules provided above.

IMPORTANT GUIDELINES:
1. Always follow the rules exactly as specified in the XML document
2. Use the provided tools to fetch market data, place orders, and perform calculations
3. Make decisions autonomously when autonomous_mode is ON
4. Provide clear reasoning for all trading decisions
5. Track cash carefully and never exceed available capital
6. Focus on high-volatility individual stocks (avoid index funds)
7. Close all positions by end of day (4:00 PM ET)
8. Use proper risk management with 1.5-2% risk per trade

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

RISK MANAGEMENT RULES (MANDATORY):
- SELL immediately if position drops -1.0% or more (don't wait for -1.5% stop loss)
- SELL positions down less than -1% if no conviction of recovery (weak momentum, bad market conditions, etc.)
- Stop losses at -1.5% are emergency backstop only
- Take profits at +3% to +5% depending on conviction
- Never hold losing positions hoping for recovery
- Cut losses quickly and let winners run

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
{self._get_protected_positions_warning()}

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

    def start_new_day(self, initial_cash: float = 10000.0, broker_config: dict = None) -> str:
        """
        Initialize a new trading day.

        Args:
            initial_cash: Starting cash for the day
            broker_config: Broker configuration dictionary

        Returns:
            Agent's response with strategy recommendations
        """
        # PRESERVE positions we were already tracking (from saved state or previous session)
        # This allows proper restart behavior - don't lose track of positions we opened
        previously_tracked = dict(self.agent_opened_positions)

        # Reset agent-opened positions tracker for new day
        self.agent_opened_positions = {}

        # Reset trading plan enforcement
        self.last_trading_plan_timestamp = None
        self.current_trading_plan = None

        # Check if forbidden symbols protection is enabled
        forbidden_protection_enabled = True  # Default to enabled for safety
        if broker_config and 'safety' in broker_config:
            forbidden_protection_enabled = broker_config['safety'].get('forbidden_symbols_protection', {}).get('enabled', True)

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
                            self.agent_opened_positions[symbol] = pos.quantity
                            logger.info(f"Restored tracked position: {symbol} ({pos.quantity} shares)")
                        else:
                            # Position exists but we weren't tracking it = pre-existing = forbidden
                            self.forbidden_symbols.add(symbol)

                    if self.forbidden_symbols:
                        logger.info(f"FORBIDDEN symbols (pre-existing positions - NO TRADING ALLOWED): {', '.join(sorted(self.forbidden_symbols))}")
                    if self.agent_opened_positions:
                        logger.info(f"RESTORED agent positions: {list(self.agent_opened_positions.keys())}")
                else:
                    logger.info("No existing positions found - agent can trade any symbol")
            except Exception as e:
                logger.warning(f"Could not check for existing positions: {e}")
                # On error, restore previously tracked positions to avoid losing track
                self.agent_opened_positions = previously_tracked
        elif not forbidden_protection_enabled:
            logger.info("Forbidden symbols protection disabled - agent can trade any symbol")
            # All positions are considered agent-opened since protection is disabled
            if self.broker:
                try:
                    account_info = self.broker.get_account_info()
                    if account_info and account_info.positions:
                        for pos in account_info.positions:
                            self.agent_opened_positions[pos.symbol.upper()] = pos.quantity
                            logger.info(f"Tracking position (protection disabled): {pos.symbol} ({pos.quantity} shares)")
                except Exception as e:
                    logger.warning(f"Could not reconcile positions: {e}")
                    self.agent_opened_positions = previously_tracked

        # Reset state for new day
        self.state = {
            "initialized": True,
            "autonomous_mode": True,
            "current_strategies": [],
            "risk_percent": 1.5,
            "positions": [],
            "cash": initial_cash,
            "account_value": initial_cash
        }

        # Track starting value for daily loss limit
        self.starting_portfolio_value = initial_cash
        self.daily_pnl_percent = 0.0

        # Start conversation
        forbidden_msg = ""
        if self.forbidden_symbols:
            forbidden_msg = f"\n\nCRITICAL: These symbols are FORBIDDEN (existing long-term positions - do NOT trade them): {', '.join(sorted(self.forbidden_symbols))}\nOnly trade symbols NOT in this list."

        user_message = f"""new day - starting with ${initial_cash:.2f} cash{forbidden_msg}

MANDATORY WORKFLOW: 
1. Use 'set_trading_strategy' tool to declare your strategy
2. Use 'create_trading_plan' tool to plan ALL positions before placing ANY orders
3. Only then use 'place_order' tool with the exact planned quantities

Do NOT place any orders without first creating a complete trading plan."""

        # Set context for Sonnet usage (strategic initialization)
        self.current_context = "initialization"
        return self.send_message(user_message)

    def send_message(self, user_message: str, stream: bool = False) -> str:
        """
        Send a message to Claude and get a response.

        Args:
            user_message: Message from user
            stream: Whether to stream the response

        Returns:
            Agent's text response
        """
        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Create the API request
        try:
            if stream:
                return self._send_streaming_message()
            else:
                return self._send_standard_message()
        except Exception as e:
            error_msg = f"Error communicating with Claude: {str(e)}"
            print(error_msg)
            return error_msg

    def _send_standard_message(self) -> str:
        """Send a standard (non-streaming) message."""
        # Select appropriate model based on current context
        selected_model = self._select_model(self.current_context)

        # CRITICAL: Clear tool history when switching between models to prevent API errors
        self._clear_tool_history_for_model_switch(selected_model)

        # Log what we're asking Claude (for better log visibility)
        if self.conversation_history:
            latest_user_msg = None
            # Find the latest user message (not tool results)
            for msg in reversed(self.conversation_history):
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    latest_user_msg = msg["content"]
                    break

            if latest_user_msg:
                # Truncate very long messages for readability
                if len(latest_user_msg) > 200:
                    preview = latest_user_msg[:197] + "..."
                else:
                    preview = latest_user_msg
                provider_name = self._get_ai_provider_name(selected_model)
                model_name = self._get_model_display_name(selected_model)
                logger.info(f"[{self.current_context.upper()}] Asking {provider_name} ({model_name}): {preview}")

        # Log the API call with context
        provider_name = self._get_ai_provider_name(selected_model)
        model_name = self._get_model_display_name(selected_model)
        logger.info(f"[{self.current_context.upper()}] API CALL: {provider_name} {model_name} - {selected_model}")

        try:
            response = self.client.messages.create(
                model=selected_model,  # Use dynamically selected model
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self._build_system_prompt(),
                messages=self.conversation_history,
                tools=self.tools
            )
        except Exception as api_error:
            error_str = str(api_error)
            # Check for tool_use/tool_result mismatch error
            if "tool_use" in error_str and "tool_result" in error_str and "400" in error_str:
                logger.error(f"Conversation history corrupted (tool_use/tool_result mismatch). Clearing and retrying...")
                # Clear conversation history and retry
                self.conversation_history = []
                # Re-add the latest user message if we can reconstruct it
                response = self.client.messages.create(
                    model=selected_model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=self._build_system_prompt(),
                    messages=[{"role": "user", "content": "Continue trading - check positions and market conditions."}],
                    tools=self.tools
                )
                # Rebuild history with fresh start
                self.conversation_history = [{"role": "user", "content": "Continue trading - check positions and market conditions."}]
                logger.info("Conversation history cleared and recovered from error")
            else:
                raise  # Re-raise other errors

        # Track token usage with actual model used
        self.token_tracker.record_api_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=selected_model,  # Track actual model used
            context=self.current_context
        )

        # Process response and handle tool calls
        full_response = ""

        # Add iteration tracking to prevent infinite loops
        max_iterations = 50
        iteration = 0

        while response.stop_reason == "tool_use":
            iteration += 1
            if iteration > max_iterations:
                logger.error(f"Max tool iterations ({max_iterations}) exceeded. Stopping to prevent infinite loop.")
                full_response += "\n[ERROR: Maximum tool iteration limit reached. Stopping execution for safety.]"
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
            self.conversation_history.append({
                "role": "assistant",
                "content": response.content
            })

            # Execute tools and add results
            tool_results = []
            for tool_call in tool_calls:
                result = self._execute_tool(tool_call.name, tool_call.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": json.dumps(result, default=str)
                })

            # Add tool results to history
            if tool_results:
                self.conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })

                # Add delay to prevent rate limiting (Claude API has strict rate limits)
                import time
                time.sleep(1.5)  # 1.5 second delay between successive API calls
                
                # Get next response (continue using same model for consistency in tool loops)
                try:
                    response = self.client.messages.create(
                        model=selected_model,  # Use same model for tool followups
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        system=self._build_system_prompt(),
                        messages=self.conversation_history,
                        tools=self.tools
                    )
                except Exception as api_error:
                    error_str = str(api_error)
                    if "tool_use" in error_str and "tool_result" in error_str and "400" in error_str:
                        logger.error(f"Tool followup: conversation corrupted. Breaking out of tool loop.")
                        full_response += "\n[Recovered from conversation error - please retry]"
                        break
                    else:
                        raise

                # Track token usage for follow-up call
                self.token_tracker.record_api_call(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=selected_model,  # Track actual model used
                    context=f"{self.current_context}_tool_followup"
                )
            else:
                break

        # Extract final text response
        for block in response.content:
            if isinstance(block, TextBlock):
                full_response += block.text

        # Add final assistant message to history
        self.conversation_history.append({
            "role": "assistant",
            "content": response.content
        })

        # Prune conversation history to prevent token overflow
        self._prune_conversation_history()

        # Capture analysis summary for Q&A (when context is trading-related)
        if self.current_context in ('trading_decision', 'trading_plan', 'strategy_change'):
            # Store the response as analysis summary for dashboard Q&A
            if len(full_response) > 100:  # Only meaningful responses
                self.last_analysis_summary = full_response[:2000]  # Cap at 2000 chars

        return full_response.strip()

    def _send_streaming_message(self) -> str:
        """Send a streaming message (for real-time CLI output)."""
        full_response = ""

        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=self._build_system_prompt(),
            messages=self.conversation_history,
            tools=self.tools
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                full_response += text

        print()  # New line after streaming

        # Add response to history
        self.conversation_history.append({
            "role": "assistant",
            "content": full_response
        })

        return full_response

    def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool call from Claude.

        This is where the agent's decisions get translated into actual
        actions (fetching data, placing orders, etc.)

        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool

        Returns:
            Tool execution result
        """
        # Wrap all tool execution in comprehensive error handling
        try:
            return self._execute_tool_internal(tool_name, tool_input)
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}", exc_info=True)
            return {
                "error": f"Tool execution failed: {str(e)}",
                "tool": tool_name,
                "input": str(tool_input)
            }

    def _check_trading_plan_required(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """
        Check if a trading plan is required before executing certain tools.
        
        Returns error dict if trading plan is required but missing, None otherwise.
        """
        if tool_name != "place_order":
            return None
            
        # Check if we have a recent trading plan (within last 30 minutes)
        if not self.last_trading_plan_timestamp:
            return {
                "error": "BLOCKED: You must use 'create_trading_plan' tool first before placing ANY orders. This prevents overbuying.",
                "blocked": True,
                "reason": "no_trading_plan",
                "required_action": "Use create_trading_plan tool first"
            }
            
        # Check if trading plan is stale (older than 30 minutes)
        import time
        current_time = time.time()
        plan_age_minutes = (current_time - self.last_trading_plan_timestamp) / 60
        
        if plan_age_minutes > 30:
            return {
                "error": f"BLOCKED: Trading plan is stale ({plan_age_minutes:.1f} minutes old). Create a new trading plan first.",
                "blocked": True,
                "reason": "stale_trading_plan",
                "required_action": "Use create_trading_plan tool to refresh your plan"
            }
            
        return None

    def _execute_tool_internal(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Internal tool execution (wrapped by _execute_tool for error handling)."""
        
        # Check if trading plan is required
        plan_check = self._check_trading_plan_required(tool_name)
        if plan_check:
            logger.error(f"Trading plan enforcement: {plan_check['error']}")
            return plan_check

        if tool_name == "get_market_data":
            symbol = tool_input["symbol"]
            data_type = tool_input.get("data_type", "quote")

            if not self.data_provider:
                return {"error": "Market data provider not connected"}

            try:
                if data_type == "quote":
                    # Get validated quote from multiple sources
                    validated_quote = self._get_validated_quote(symbol)

                    # Return format compatible with existing code
                    return {
                        "symbol": symbol,
                        "price": validated_quote["consensus_price"],
                        "confidence": validated_quote["confidence"],
                        "sources_used": validated_quote["sources_used"],
                        "validation_status": validated_quote["validation_status"],
                        "warnings": validated_quote.get("warnings", []),
                        "timestamp": validated_quote["timestamp"]
                    }

                elif data_type == "intraday":
                    # Get intraday bars (last 1 day, 15-min bars)
                    bars = self.data_provider.get_historical_data(
                        symbol=symbol,
                        days_back=1,
                        timeframe="15Min"
                    )
                    return {"symbol": symbol, "bars": bars, "timeframe": "15Min"}

                elif data_type == "historical":
                    # Get daily historical data (last 30 days)
                    bars = self.data_provider.get_historical_data(
                        symbol=symbol,
                        days_back=30,
                        timeframe="1D"
                    )
                    return {"symbol": symbol, "bars": bars, "timeframe": "1D"}

                else:
                    return {"error": f"Unknown data_type: {data_type}"}

            except Exception as e:
                return {"error": f"Failed to fetch market data: {str(e)}"}

        elif tool_name == "place_order":
            if not self.broker:
                return {"error": "Broker not connected"}

            try:
                # Normalize symbol to uppercase and strip whitespace
                symbol = tool_input["symbol"].strip().upper()

                # Validate order parameters
                if tool_input["quantity"] <= 0:
                    return {"error": "Quantity must be positive", "blocked": True}

                if tool_input.get("limit_price") and tool_input["limit_price"] <= 0:
                    return {"error": "Limit price must be positive", "blocked": True}

                # Check for duplicate orders within last 60 seconds
                import time
                current_time = time.time()
                order_signature = (symbol, tool_input["side"], tool_input["quantity"])

                for recent_order in self.recent_orders:
                    recent_symbol = recent_order[0]
                    recent_side = recent_order[1]
                    recent_time = recent_order[3]

                    # AGGRESSIVE CHECK: Block ANY sell for same symbol within 10 seconds (prevents double-sell)
                    if recent_symbol == symbol and recent_side == tool_input["side"] and recent_side.lower() == "sell":
                        if (current_time - recent_time) < 10:
                            error_msg = f"BLOCKED: Sell order for {symbol} too soon after previous sell ({current_time - recent_time:.1f}s ago)"
                            logger.warning(error_msg)
                            return {"error": error_msg, "blocked": True, "reason": "duplicate_sell"}

                    # Standard check: Exact same order within 60 seconds
                    recent_sig = (recent_order[0], recent_order[1], recent_order[2])
                    if recent_sig == order_signature and (current_time - recent_time) < 60:
                        error_msg = f"BLOCKED: Duplicate order detected for {symbol} within last 60 seconds"
                        logger.warning(error_msg)
                        return {"error": error_msg, "blocked": True, "reason": "duplicate_order"}

                # Add current order to tracking
                self.recent_orders.append((symbol, tool_input["side"], tool_input["quantity"], current_time))

                # Cleanup old orders (>60 seconds)
                self.recent_orders = [o for o in self.recent_orders if (current_time - o[3]) < 60]

                # Map string side to enum FIRST (needed for capital protection check)
                side = OrderSide.BUY if tool_input["side"].lower() == "buy" else OrderSide.SELL

                # CRITICAL: Check $25k minimum protection BEFORE allowing ANY new BUY orders
                try:
                    current_account = self.broker.get_account_info()
                    current_value = current_account.portfolio_value
                    available_cash = current_account.cash  # CRITICAL: Use actual cash, not buying power (no margin!)

                    # ALWAYS enforce $25,000 minimum account protection
                    MINIMUM_ACCOUNT_VALUE = 25000.0  # Hard-coded $25k minimum

                    # Log capital status for debugging
                    logger.info(f"CAPITAL CHECK: Account=${current_value:.2f}, Cash=${available_cash:.2f}, Order={symbol} qty={tool_input['quantity']}")

                    # EMERGENCY CHECK: If account is already below $25k, BLOCK ALL BUY orders immediately
                    if current_value < MINIMUM_ACCOUNT_VALUE:
                        deficit = MINIMUM_ACCOUNT_VALUE - current_value
                        error_msg = f"EMERGENCY BLOCK: Account value ${current_value:.2f} is BELOW $25k minimum by ${deficit:.2f}. ALL BUY orders blocked until account recovers above $25,000."
                        logger.error(error_msg)
                        logger.error("CRITICAL PDT VIOLATION: Account must be restored above $25,000 immediately!")
                        return {"error": error_msg, "blocked": True, "reason": "account_below_25k_minimum", "current_value": current_value, "minimum_required": MINIMUM_ACCOUNT_VALUE, "deficit": deficit}

                    if side == OrderSide.BUY:
                        # Get VALIDATED quote price for accurate cost calculation (cross-source validation)
                        limit_price = tool_input.get("limit_price", 0)
                        if not limit_price or limit_price <= 0:
                            try:
                                # Use validated quote instead of raw broker quote for critical calculations
                                validated_quote = self._get_validated_quote(symbol)
                                limit_price = validated_quote["consensus_price"]
                                confidence = validated_quote["confidence"]

                                # Warn if confidence is low (potential data quality issue)
                                if confidence < 0.7:
                                    logger.warning(f"LOW CONFIDENCE quote for {symbol}: {confidence:.1f} - proceeding but monitoring required")
                            except Exception as qe:
                                logger.warning(f"Could not get validated quote for {symbol}: {qe}")
                                limit_price = 100  # Conservative fallback only if validation fails

                        # Calculate actual order cost
                        order_cost = tool_input["quantity"] * limit_price
                        logger.info(f"ORDER COST CHECK: {tool_input['quantity']} shares @ ${limit_price:.2f} = ${order_cost:.2f}")

                        # CRITICAL CHECK #0: Block buying MORE of a LOSING position
                        # This prevents the terrible behavior of averaging down on losers
                        if symbol in self.agent_opened_positions and self.agent_opened_positions[symbol] > 0:
                            # We already have this position - check if it's losing
                            try:
                                entry_price_existing = self.agent_position_entry_prices.get(symbol)
                                if entry_price_existing and limit_price < entry_price_existing:
                                    loss_pct = ((limit_price - entry_price_existing) / entry_price_existing) * 100
                                    error_msg = f"BLOCKED: Cannot buy MORE of {symbol} while position is LOSING ({loss_pct:.1f}%). Current price ${limit_price:.2f} < entry ${entry_price_existing:.2f}. Sell the loser first or wait for recovery."
                                    logger.error(error_msg)
                                    return {"error": error_msg, "blocked": True, "reason": "buying_more_of_loser", "current_price": limit_price, "entry_price": entry_price_existing, "loss_percent": loss_pct}
                            except Exception as e:
                                logger.warning(f"Could not check existing position P&L for {symbol}: {e}")

                        # CRITICAL CHECK #1: Order cost exceeds available CASH (no margin allowed!)
                        if order_cost > available_cash:
                            error_msg = f"BLOCKED: Order cost ${order_cost:.2f} exceeds available cash ${available_cash:.2f}. This is a CASH account - no margin!"
                            logger.error(error_msg)
                            return {"error": error_msg, "blocked": True, "reason": "exceeds_available_cash", "order_cost": order_cost, "available_cash": available_cash}

                        # CRITICAL CHECK #2: Order would bring account below $25k minimum
                        projected_value_after_order = current_value - order_cost
                        if projected_value_after_order < MINIMUM_ACCOUNT_VALUE:
                            shortage = MINIMUM_ACCOUNT_VALUE - projected_value_after_order
                            error_msg = f"BLOCKED: BUY order would bring account below $25k minimum. Current: ${current_value:.2f}, Order cost: ${order_cost:.2f}, After: ${projected_value_after_order:.2f}"
                            logger.error(error_msg)
                            return {"error": error_msg, "blocked": True, "reason": "would_violate_25k_minimum"}

                        # CRITICAL CHECK #3: Order exceeds safe trading limit (cash above $25k)
                        safe_trading_limit = max(0, current_value - MINIMUM_ACCOUNT_VALUE)
                        if order_cost > safe_trading_limit:
                            error_msg = f"BLOCKED: Order cost ${order_cost:.2f} exceeds safe trading limit ${safe_trading_limit:.2f} (keeping $25k reserve)"
                            logger.error(error_msg)
                            return {"error": error_msg, "blocked": True, "reason": "exceeds_safe_limit", "order_cost": order_cost, "safe_limit": safe_trading_limit}

                        # Additional check: If account is already close to $25k, be extra cautious
                        buffer_amount = current_value - MINIMUM_ACCOUNT_VALUE
                        if buffer_amount < 5000:  # Less than $5k buffer
                            logger.warning(f"CAPITAL WARNING: Only ${buffer_amount:.2f} buffer above $25k minimum")
                            if order_cost > buffer_amount * 0.25:  # Order uses more than 25% of buffer
                                error_msg = f"BLOCKED: Order too large for current buffer. Buffer: ${buffer_amount:.2f}, Order cost: ${order_cost:.2f}. Need larger buffer above $25k minimum."
                                logger.error(error_msg)
                                return {"error": error_msg, "blocked": True, "reason": "insufficient_buffer"}

                        logger.info(f"CAPITAL CHECK PASSED: Order ${order_cost:.2f} within limits (cash=${available_cash:.2f}, safe_limit=${safe_trading_limit:.2f})")

                    # Check daily loss limit
                    if self.starting_portfolio_value > 0:
                        self.daily_pnl_percent = ((current_value - self.starting_portfolio_value) / self.starting_portfolio_value) * 100
                        max_daily_loss = -3.0  # 3% daily loss limit (portfolio-level hard stop)
                        warn_daily_loss = -2.0  # Warn at -2% so there's advance notice
                        if self.daily_pnl_percent <= max_daily_loss:
                            loss_amount = self.starting_portfolio_value - current_value
                            error_msg = f"BLOCKED: Daily loss limit reached ({self.daily_pnl_percent:.2f}%, ${loss_amount:.2f}). Trading halted for safety."
                            logger.error(error_msg)
                            return {"error": error_msg, "blocked": True, "reason": "daily_loss_limit"}
                        elif self.daily_pnl_percent <= warn_daily_loss:
                            loss_amount = self.starting_portfolio_value - current_value
                            logger.warning(f"DAILY LOSS WARNING: Portfolio down {self.daily_pnl_percent:.2f}% (${loss_amount:.2f}) - approaching {max_daily_loss}% hard stop")
                except Exception as e:
                    logger.error(f"CRITICAL: Capital protection check failed with exception: {e}")
                    logger.error(f"BLOCKING ORDER as safety measure - cannot verify capital limits")
                    import traceback
                    logger.error(traceback.format_exc())
                    return {"error": f"Capital protection check failed: {e}. Order blocked for safety.", "blocked": True, "reason": "capital_check_failed"}

                # Map string side to enum
                side = OrderSide.BUY if tool_input["side"].lower() == "buy" else OrderSide.SELL

                # PDT (Pattern Day Trader) Protection: Block new BUY orders if at limit
                if side == OrderSide.BUY and self.pdt_enabled:
                    pdt_status = self.get_pdt_status()
                    trades_remaining = pdt_status["trades_remaining"]

                    if trades_remaining == 0:
                        error_msg = f"BLOCKED (PDT): No day trades remaining in 5-day window. Cannot open new position in {symbol}."
                        logger.error(error_msg)
                        return {"error": error_msg, "blocked": True, "reason": "pdt_limit_reached", "pdt_status": pdt_status}

                    # Warn (but allow) if not a preferred trading day and trades are limited
                    if not pdt_status["is_preferred_day"] and trades_remaining <= 2:
                        logger.warning(
                            f"PDT WARNING: Opening position on {pdt_status['today']} (not preferred). "
                            f"Only {trades_remaining} trades remaining. Consider saving for Tue/Wed/Thu."
                        )

                # CRITICAL PROTECTION #0.5: Circuit breaker - pause new entries after daily/weekly loss limit
                if side == OrderSide.BUY and not self.circuit_breaker.is_trading_allowed():
                    pause_reason = self.circuit_breaker.get_pause_reason()
                    error_msg = f"BLOCKED: Circuit breaker active - {pause_reason}"
                    logger.error(error_msg)
                    return {"error": error_msg, "blocked": True, "reason": "circuit_breaker"}

                # CRITICAL PROTECTION #1: Never allow trading forbidden symbols (existed at start)
                if symbol in self.forbidden_symbols:
                    error_msg = f"BLOCKED: {symbol} is a FORBIDDEN symbol (existed in account before trading started). Cannot buy or sell this symbol."
                    logger.error(error_msg)
                    return {"error": error_msg, "blocked": True, "reason": "forbidden_symbol"}

                # CRITICAL PROTECTION #1.5: Don't rebuy losers within 12 hours (prevents revenge trading)
                if side == OrderSide.BUY and symbol in self.recently_closed_losers:
                    loser_info = self.recently_closed_losers[symbol]
                    hours_since_close = (datetime.now() - loser_info['timestamp']).total_seconds() / 3600
                    
                    if hours_since_close < 12:
                        error_msg = f"BLOCKED: {symbol} was closed at {loser_info['pnl_percent']:.1f}% loss {hours_since_close:.1f}h ago. Cannot rebuy for 12 hours (prevents revenge trading/averaging down on losers)."
                        logger.error(error_msg)
                        return {
                            "error": error_msg,
                            "blocked": True,
                            "reason": "recently_closed_loser",
                            "loss_percent": loser_info['pnl_percent'],
                            "hours_ago": hours_since_close,
                            "cooldown_remaining_hours": 12 - hours_since_close
                        }

                # CRITICAL PROTECTION #1.5: 30-minute cooldown + position confirmation
                # Prevents LLM hallucinations from causing duplicate orders
                cooldown_check = self._check_order_cooldown(symbol, tool_input["side"], cooldown_seconds=1800)
                if not cooldown_check.get('allowed', True):
                    reason = cooldown_check.get('reason', 'cooldown_blocked')
                    if reason == 'cooldown_active':
                        mins_remaining = cooldown_check.get('minutes_remaining', 0)
                        error_msg = f"BLOCKED: {tool_input['side'].upper()} {symbol} on cooldown - {mins_remaining:.1f} minutes remaining"
                    elif reason == 'no_position_to_sell':
                        error_msg = f"BLOCKED: Cannot SELL {symbol} - no position exists (LLM hallucination?)"
                    elif reason == 'no_broker_position':
                        error_msg = f"BLOCKED: Cannot SELL {symbol} - broker confirms no position"
                    elif reason == 'already_holding_position':
                        error_msg = f"BLOCKED: Cannot BUY {symbol} - already holding position (LLM hallucination?)"
                    else:
                        error_msg = f"BLOCKED: Order for {symbol} rejected - {reason}"
                    logger.warning(error_msg)
                    return {"error": error_msg, "blocked": True, "reason": reason, "details": cooldown_check}

                # CRITICAL PROTECTION #2: Verify ACTUAL broker position and auto-adjust if needed
                if side == OrderSide.SELL:
                    # Step 1: Check agent tracking (quick pre-check)
                    agent_position_qty = self.agent_opened_positions.get(symbol, 0)
                    if agent_position_qty == 0:
                        error_msg = f"BLOCKED: Cannot sell {symbol} - not in agent tracking (you didn't open this position today)"
                        logger.error(error_msg)
                        return {"error": error_msg, "blocked": True, "reason": "not_agent_opened"}

                    # Step 2: Get ACTUAL position from broker (source of truth)
                    actual_broker_qty = 0
                    try:
                        account_info = self.broker.get_account_info()
                        if account_info and account_info.positions:
                            for pos in account_info.positions:
                                if pos.symbol.upper() == symbol.upper():
                                    actual_broker_qty = pos.quantity
                                    break
                    except Exception as e:
                        logger.error(f"Could not verify broker position for {symbol}: {e}")
                        return {"error": "Cannot verify position with broker - blocking sell for safety", "blocked": True}

                    # Step 3: If no position at broker, block the sell
                    if actual_broker_qty <= 0:
                        logger.error(f"BLOCKED: No position in {symbol} at broker (qty: {actual_broker_qty})")
                        return {"error": f"Cannot sell {symbol} - no position at broker", "blocked": True, "reason": "no_broker_position"}

                    # Step 4: If requesting more than we have, AUTO-ADJUST to actual amount
                    requested_qty = tool_input["quantity"]
                    quantity_adjusted = False
                    
                    if requested_qty > actual_broker_qty:
                        logger.warning(f"QUANTITY ADJUSTED: Requested {requested_qty} shares of {symbol}, but only {actual_broker_qty} available at broker")
                        logger.info(f"Auto-adjusting sell quantity from {requested_qty} to {actual_broker_qty}")
                        tool_input["quantity"] = actual_broker_qty  # Adjust instead of blocking
                        quantity_adjusted = True
                        
                        # Sync agent tracking with reality
                        if symbol in self.agent_opened_positions:
                            self.agent_opened_positions[symbol] = actual_broker_qty
                            logger.info(f"Synced agent tracking: {symbol} now shows {actual_broker_qty} shares")

                # Map string order_type to enum
                order_type_map = {
                    "market": OrderType.MARKET,
                    "limit": OrderType.LIMIT,
                    "stop": OrderType.STOP,
                    "moc": OrderType.MOC
                }
                order_type = order_type_map.get(tool_input["order_type"].lower(), OrderType.MARKET)

                # Validate order (buying power, cash, etc.) before placing - ONLY FOR BUY ORDERS
                # Sell orders don't need cash validation - they free up cash!
                if side == OrderSide.BUY:
                    entry_price = tool_input.get("limit_price", 0)
                    if entry_price == 0:
                        # For market orders, estimate price from current quote
                        try:
                            quote = self.broker.get_quote(symbol)
                            entry_price = quote.price
                        except:
                            entry_price = 0  # Will let broker validate

                    validation = self.broker.validate_order(
                        symbol=symbol,
                        quantity=tool_input["quantity"],
                        price=entry_price if entry_price > 0 else 100  # Conservative fallback
                    )

                    if not validation.get("valid", False):
                        error_msg = f"Order validation failed: {', '.join(validation.get('errors', ['Unknown error']))}"
                        logger.error(error_msg)
                        return {"error": error_msg, "blocked": True}

                # CRITICAL: Get FRESH quote immediately before order placement for BUY LIMIT orders.
                # Uses ask price (not last trade) for limit price - prevents fills being missed
                # when bid/ask spread is wide. Also computes adaptive buffer from spread.
                limit_price = tool_input.get("limit_price")
                if side == OrderSide.BUY and order_type == OrderType.LIMIT:
                    try:
                        logger.info(f"Fetching FRESH quote for {symbol} before order placement...")
                        fresh_quote = self._get_validated_quote(symbol)

                        if fresh_quote['confidence'] < 0.7:
                            error_msg = f"Cannot place order - fresh quote has low confidence ({fresh_quote['confidence']:.2f})"
                            logger.error(error_msg)
                            return {"error": error_msg, "blocked": True, "reason": "low_confidence_fresh_quote"}

                        fresh_price = fresh_quote['consensus_price']

                        # Check if price moved significantly since initial analysis quote
                        if limit_price and limit_price > 0:
                            price_diff_pct = abs((fresh_price - limit_price) / limit_price) * 100
                            if price_diff_pct > 2.0:
                                logger.warning(f"Price moved {price_diff_pct:.1f}% since analysis: ${limit_price:.2f} -> ${fresh_price:.2f}")

                        # Use ASK price as base for buy limit (not last trade).
                        # Last trade can be at bid on a downtick - ask is the actual cost to buy now.
                        ask_price = fresh_quote.get('ask') or fresh_price
                        bid_price = fresh_quote.get('bid') or fresh_price

                        # Compute adaptive buffer: max(config default, 1.2x spread_pct)
                        # Wide-spread or pre-market stocks need more buffer to fill
                        # Min floor of 0.2% prevents zero/negative buffers on fractional-cent spreads
                        config_buffer_pct = self.broker_config.get("order_execution", {}).get("limit_price_buffer_percent", 0.4)
                        MIN_BUFFER_PCT = 0.2
                        if ask_price > 0 and bid_price > 0 and ask_price > bid_price:
                            spread_pct = (ask_price - bid_price) / ask_price * 100
                            spread_bps = spread_pct * 100
                            adaptive_buffer_pct = max(config_buffer_pct, MIN_BUFFER_PCT, spread_pct * 1.2)
                        else:
                            spread_pct = 0.0
                            spread_bps = 0.0
                            adaptive_buffer_pct = max(config_buffer_pct, MIN_BUFFER_PCT)

                        # Set limit at ask + adaptive buffer
                        limit_price = round(ask_price * (1 + adaptive_buffer_pct / 100), 2)
                        logger.info(f"Limit price: ask=${ask_price:.2f}, spread={spread_bps:.0f}bps, "
                                    f"buffer={adaptive_buffer_pct:.2f}% -> limit=${limit_price:.2f}")

                    except Exception as e:
                        error_msg = f"Failed to get fresh quote before order placement: {e}"
                        logger.error(error_msg)
                        return {"error": error_msg, "blocked": True, "reason": "fresh_quote_failed"}
                elif side == OrderSide.BUY and order_type == OrderType.LIMIT and limit_price:
                    # Fallback: no fresh-quote path (should rarely hit) - apply config buffer to analysis price
                    buffer_percent = self.broker_config.get("order_execution", {}).get("limit_price_buffer_percent", 0)
                    if buffer_percent > 0:
                        original_limit = limit_price
                        limit_price = limit_price * (1 + buffer_percent / 100)
                        logger.info(f"Applied fallback {buffer_percent}% limit buffer: ${original_limit:.2f} -> ${limit_price:.2f}")

                # SIMPLIFIED ORDER PLACEMENT: Always place simple entry order
                # TP/SL handled by scan_and_add_missing_bracket_orders after fill
                # Removes broker-specific bracket complexity
                order = self.broker.place_order(
                    symbol=symbol,
                    side=side,
                    quantity=tool_input["quantity"],
                    order_type=order_type,
                    limit_price=limit_price,
                    stop_price=tool_input.get("stop_price")
                )
                
                logger.info(f"SIMPLE ORDER PLACEMENT: {side.value} {tool_input['quantity']} {symbol} ({order_type.value})")
                logger.info(f"TP/SL will be auto-added by position monitor after fill")

                # Wait for order to fill - retry a few times to catch full fill
                import time
                requested_qty = tool_input["quantity"]
                filled_qty = 0
                filled_price = None
                order_cancelled_no_fill = False

                # Retry loop to wait for fill (market orders usually fill quickly, limits may not)
                for attempt in range(5):  # Up to 5 attempts over ~2.5 seconds
                    time.sleep(0.5)
                    try:
                        updated_order = self.broker.get_order_status(order.order_id)
                        filled_qty = updated_order.filled_quantity if hasattr(updated_order, 'filled_quantity') else 0
                        filled_price = updated_order.filled_price if hasattr(updated_order, 'filled_price') else None

                        # Check order status - handle both string and enum
                        status = updated_order.status if hasattr(updated_order, 'status') else None
                        status_str = status.name.lower() if hasattr(status, 'name') else str(status).lower()

                        # Detect explicit failure/cancellation
                        if 'cancelled' in status_str or 'canceled' in status_str or 'rejected' in status_str:
                            logger.warning(f"Order {order.order_id} was {status_str} by broker")
                            order_cancelled_no_fill = True
                            break

                        # If fully filled or order is complete, stop waiting
                        if filled_qty >= requested_qty or 'filled' in status_str or 'complete' in status_str:
                            break

                        logger.debug(f"Order {order.order_id} still processing: {filled_qty}/{requested_qty} filled (attempt {attempt+1})")

                    except Exception as e:
                        logger.warning(f"Could not check order status (attempt {attempt+1}): {e}")

                # Log final fill status - never silently assume full fill on zero-fill response
                if order_cancelled_no_fill:
                    logger.warning(f"Order {order.order_id} rejected/cancelled - no position recorded for {symbol}")
                    return {"error": f"Order was rejected or cancelled by broker", "order_id": order.order_id, "symbol": symbol, "filled": 0}
                elif filled_qty == 0:
                    # Zero fill after all retries - order may be pending (LIMIT) or truly unfilled
                    if order_type == OrderType.LIMIT:
                        # Track as pending - may fill later; do NOT assume full fill
                        MAX_PENDING_ORDERS = 5  # Safety cap: prevent runaway accumulation
                        if len(self.pending_orders) >= MAX_PENDING_ORDERS:
                            logger.warning(f"Pending order cap ({MAX_PENDING_ORDERS}) reached - cancelling new unfilled LIMIT order for {symbol}")
                            try:
                                self.broker.cancel_order(order.order_id)
                            except Exception as e:
                                logger.warning(f"Could not cancel excess pending order: {e}")
                            return {"error": f"Too many pending orders ({MAX_PENDING_ORDERS}) - order cancelled",
                                    "order_id": order.order_id, "symbol": symbol, "filled": 0}
                        logger.info(f"LIMIT order {order.order_id} not yet filled - tracking as pending for {symbol} ({len(self.pending_orders)+1}/{MAX_PENDING_ORDERS})")
                        self.pending_orders[order.order_id] = {
                            'symbol': symbol,
                            'side': side.value if hasattr(side, 'value') else str(side),
                            'requested_qty': requested_qty,
                            'order_type': 'limit',
                            'placed_at': time.time(),
                            'entry_price': entry_price,
                            'take_profit': take_profit,
                            'conviction_score': None  # Will be set below if found in trading plan
                        }
                        # Return early - do not record position until fill confirmed
                        return {"status": "pending", "order_id": order.order_id, "symbol": symbol,
                                "message": f"LIMIT order placed, awaiting fill. Tracking as pending."}
                    else:
                        # MARKET order with zero fill - something is wrong, cancel and abort
                        logger.error(f"MARKET order {order.order_id} returned 0 fill after {5 * 0.5:.1f}s - cancelling to avoid phantom position")
                        try:
                            self.broker.cancel_order(order.order_id)
                        except Exception as cancel_err:
                            logger.warning(f"Could not cancel unfilled MARKET order: {cancel_err}")
                        return {"error": "Market order returned zero fill - cancelled to prevent phantom position",
                                "order_id": order.order_id, "symbol": symbol, "filled": 0}
                elif filled_qty < requested_qty:
                    logger.warning(
                        f"PARTIAL FILL: Requested {requested_qty} shares of {symbol}, "
                        f"only {filled_qty} filled ({(filled_qty/requested_qty)*100:.1f}%)" +
                        (f" at ${filled_price:.2f}" if filled_price else "")
                    )
                elif filled_price:
                    logger.info(f"Order filled: {filled_qty} shares of {symbol} at ${filled_price:.2f}")

                # Get sell reason if provided
                sell_reason = tool_input.get("reason", "unspecified")

                # Track agent-opened positions with ACTUAL filled quantities (for sell validation later)
                quantity = filled_qty  # Use actual filled quantity, not requested
                if side == OrderSide.BUY:
                    current_qty = self.agent_opened_positions.get(symbol, 0)
                    self.agent_opened_positions[symbol] = current_qty + quantity

                    # Look up conviction score from trading plan (for rebalancing decisions later)
                    conviction_score = None
                    if self.current_trading_plan:
                        for planned_pos in self.current_trading_plan:
                            if planned_pos.get("symbol", "").upper() == symbol.upper():
                                conviction_score = planned_pos.get("conviction_score")
                                break
                    if conviction_score:
                        self.agent_position_convictions[symbol] = conviction_score
                        logger.debug(f"Tracking conviction score {conviction_score}/10 for {symbol}")

                    # Track entry price and TP target for partial profit-taking
                    actual_entry = filled_price if filled_price else entry_price
                    if actual_entry:
                        self.agent_position_entry_prices[symbol] = actual_entry
                    if take_profit:
                        self.agent_position_tp_targets[symbol] = take_profit
                        logger.debug(f"Tracking TP target ${take_profit:.2f} for {symbol} (entry ${actual_entry:.2f if actual_entry else 'N/A'})")

                    reason_str = f" (reason: {sell_reason})" if sell_reason != "unspecified" else ""
                    conviction_str = f" [conviction: {conviction_score}/10]" if conviction_score else ""
                    logger.info(f"Agent opened/added to position in {symbol}: {quantity} shares at ${filled_price:.2f} (total: {self.agent_opened_positions[symbol]}){reason_str}{conviction_str}" if filled_price else f"Agent opened/added to position in {symbol}: {quantity} shares (total: {self.agent_opened_positions[symbol]}){reason_str}{conviction_str}")

                    # Record BUY for 30-minute cooldown (prevents LLM duplicate buys)
                    self._record_order_executed(symbol, "buy")

                # AUTOMATIC PROTECTIVE MOC ORDER: Only place if we DON'T have bracket orders
                # If we have TP/SL orders, they provide risk management, so MOC is redundant
                should_place_moc = (
                    quantity > 0 and  # Only place MOC if we actually have shares
                    not is_bracket_order  # Don't place MOC if we have bracket orders
                )

                if should_place_moc:
                    # This ensures the position WILL be closed by end of day, even if agent misses the close
                    try:
                        # Add small delay to avoid wash trade detection
                        import time
                        time.sleep(0.1)  # 100ms delay

                        moc_order = self.broker.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=quantity,
                            order_type=OrderType.MOC
                        )
                        logger.info(f"[OK] Protective MOC order placed: Will sell {quantity} shares of {symbol} at market close (Order ID: {moc_order.order_id})")

                        # Store MOC order ID for tracking (could cancel if we manually close position earlier)
                        if not hasattr(self, 'protective_moc_orders'):
                            self.protective_moc_orders = {}  # Dict: {symbol: [order_ids]}
                        if symbol not in self.protective_moc_orders:
                            self.protective_moc_orders[symbol] = []
                        self.protective_moc_orders[symbol].append(moc_order.order_id)

                    except Exception as e:
                        error_str = str(e).lower()
                        # Check if this is a wash trade detection error (expected/common with some brokers)
                        if "wash trade" in error_str or "opposite side" in error_str or "40310000" in str(e):
                            logger.warning(f"MOC order blocked by broker (wash trade detection): {symbol}")
                            logger.info(f"NOTE: {symbol} position opened without protective MOC due to broker restrictions")
                            logger.info("    Agent will need to manually close position before market close")
                        else:
                            logger.error(f"FAILED to place protective MOC order for {symbol}: {e}")
                            logger.error("WARNING: Position opened without protective MOC - agent must manually close before market close!")
                elif is_bracket_order:
                    logger.info(f"[OK] Bracket orders provide risk management - skipping MOC order for {symbol}")
                else:
                    logger.warning(f"Skipping MOC order for {symbol}: No shares were filled ({quantity} shares)")

                if side == OrderSide.SELL:
                    # CRITICAL: Only update position tracking AFTER confirming the sell actually executed
                    # This prevents the bug where agent thinks position was sold but broker still has it

                    # Record SELL for 30-minute cooldown (prevents LLM duplicate sells)
                    self._record_order_executed(symbol, "sell")

                    # Check if this was a losing position - record it to prevent rebuy within 24h
                    entry_price = self.agent_position_entry_prices.get(symbol)
                    if entry_price and filled_price:
                        pnl_percent = ((filled_price - entry_price) / entry_price) * 100

                        # Record all closed trades to circuit breaker (wins + losses)
                        # Partial sells use their proportional P&L
                        self.circuit_breaker.record_trade_pnl(pnl_percent, symbol=symbol)

                        if filled_price < entry_price:
                            # Record as recently closed loser (12-hour cooldown)
                            self.recently_closed_losers[symbol] = {
                                'timestamp': datetime.now(),
                                'entry': entry_price,
                                'exit': filled_price,
                                'pnl_percent': pnl_percent
                            }
                            logger.warning(f"LOSER RECORDED: {symbol} closed at {pnl_percent:.1f}% loss - BLOCKED from rebuying for 12 hours")
                            # Also persist to DB so rebuy block survives restarts
                            if self.learning_db:
                                try:
                                    self.learning_db.add_rebuy_block(
                                        symbol=symbol, hours=12,
                                        entry_price=entry_price, exit_price=filled_price,
                                        loss_pct=pnl_percent
                                    )
                                except Exception as db_err:
                                    logger.warning(f"Could not persist rebuy block to DB for {symbol}: {db_err}")

                    # Update position after sell - but only if we actually have the position tracked
                    if symbol in self.agent_opened_positions:
                        self.agent_opened_positions[symbol] -= quantity

                        # If we're manually closing the position (or reducing it), cancel protective MOC orders
                        if hasattr(self, 'protective_moc_orders') and symbol in self.protective_moc_orders:
                            remaining_qty = self.agent_opened_positions.get(symbol, 0)

                            # If position fully closed, cancel ALL MOC orders for this symbol
                            if remaining_qty <= 0:
                                for moc_order_id in self.protective_moc_orders[symbol]:
                                    try:
                                        self.broker.cancel_order(moc_order_id)
                                        logger.info(f"[OK] Cancelled protective MOC order {moc_order_id} (position closed manually)")
                                    except Exception as e:
                                        logger.warning(f"Could not cancel MOC order {moc_order_id}: {e}")

                                # Clear MOC tracking for this symbol
                                del self.protective_moc_orders[symbol]

                            # If position partially reduced, try to reduce MOC orders proportionally
                            else:
                                # For simplicity, cancel all MOC orders and let agent place new ones if needed
                                for moc_order_id in self.protective_moc_orders[symbol]:
                                    try:
                                        self.broker.cancel_order(moc_order_id)
                                        logger.info(f"[OK] Cancelled protective MOC order {moc_order_id} (position reduced)")
                                    except Exception as e:
                                        logger.warning(f"Could not cancel MOC order {moc_order_id}: {e}")

                                # Place new MOC for remaining quantity
                                try:
                                    new_moc_order = self.broker.place_order(
                                        symbol=symbol,
                                        side=OrderSide.SELL,
                                        quantity=remaining_qty,
                                        order_type=OrderType.MOC
                                    )
                                    self.protective_moc_orders[symbol] = [new_moc_order.order_id]
                                    logger.info(f"[OK] New protective MOC order placed for remaining {remaining_qty} shares")
                                except Exception as e:
                                    logger.error(f"Failed to place new protective MOC order: {e}")

                        if self.agent_opened_positions.get(symbol, 0) <= 0:
                            if symbol in self.agent_opened_positions:
                                del self.agent_opened_positions[symbol]
                            # Also clean up conviction tracking for closed position
                            if symbol in self.agent_position_convictions:
                                del self.agent_position_convictions[symbol]
                            # Clean up partial profit tracking for closed position
                            if symbol in self.agent_position_entry_prices:
                                del self.agent_position_entry_prices[symbol]
                            if symbol in self.agent_position_tp_targets:
                                del self.agent_position_tp_targets[symbol]
                            if symbol in self.agent_position_sl_targets:
                                del self.agent_position_sl_targets[symbol]
                            if symbol in self.agent_position_partial_profits:
                                del self.agent_position_partial_profits[symbol]
                            logger.info(f"Agent CLOSED position in {symbol}: {quantity} shares at ${filled_price:.2f} - REASON: {sell_reason}" if filled_price else f"Agent CLOSED position in {symbol}: {quantity} shares - REASON: {sell_reason}")

                            # Record day trade for PDT tracking (position fully closed same day = day trade)
                            if self.pdt_enabled:
                                self._record_day_trade(symbol)
                        else:
                            logger.info(f"Agent REDUCED position in {symbol}: sold {quantity} shares at ${filled_price:.2f} (remaining: {self.agent_opened_positions[symbol]}) - REASON: {sell_reason}" if filled_price else f"Agent REDUCED position in {symbol}: sold {quantity} shares (remaining: {self.agent_opened_positions[symbol]}) - REASON: {sell_reason}")
                    else:
                        logger.warning(f"SELL ORDER EXECUTED for {symbol} but position not in agent tracking - possible reconciliation issue")

                # Log trade with strategy attribution and R:R
                entry_price = filled_price if side == OrderSide.BUY else None
                stop_loss = tool_input.get("stop_price")
                take_profit = tool_input.get("take_profit")

                self.log_trade(
                    symbol=symbol,
                    side=tool_input["side"],
                    quantity=filled_qty,
                    price=filled_price or 0,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reason=sell_reason
                )

                # Persist position state to disk for crash recovery
                self._save_position_state()

                # Build response with fill info
                response = {
                    "status": order.status.value,
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity_requested": tool_input["quantity"],
                    "quantity_filled": filled_qty,
                    "order_type": order.order_type.value,
                    "limit_price": order.limit_price or None,
                    "filled_price": filled_price,
                    "created_at": order.created_at.isoformat()
                }

                # Add adjustment info if quantity was auto-adjusted
                if side == OrderSide.SELL and quantity_adjusted:
                    response["quantity_adjusted"] = True
                    response["adjustment_reason"] = f"Broker only had {actual_broker_qty} shares available"
                    response["adjustment_note"] = "Quantity auto-adjusted to match actual broker position to prevent short selling"

                # Add warning if partial fill occurred
                if filled_qty < tool_input["quantity"]:
                    response["warning"] = f"PARTIAL FILL: Only {filled_qty} of {tool_input['quantity']} shares filled"
                    if filled_price:
                        response["warning"] += f" at ${filled_price:.2f}"

                return response

            except Exception as e:
                return {"error": f"Failed to place order: {str(e)}"}

        elif tool_name == "place_bracket_order":
            # BRACKET ORDER: Entry + Take Profit + Stop Loss all in one
            # This guarantees TP/SL are in place when entry fills
            if not self.broker:
                return {"error": "Broker not connected"}

            symbol = tool_input.get("symbol", "").upper()
            quantity = tool_input.get("quantity", 0)
            entry_price = tool_input.get("entry_price", 0)
            take_profit_price = tool_input.get("take_profit_price", 0)
            stop_loss_price = tool_input.get("stop_loss_price", 0)
            reason = tool_input.get("reason", "bracket_entry")

            # Validate inputs
            if not symbol or quantity <= 0:
                return {"error": "Invalid symbol or quantity"}
            if entry_price <= 0 or take_profit_price <= 0 or stop_loss_price <= 0:
                return {"error": "Entry, take profit, and stop loss prices must be positive"}
            if take_profit_price <= entry_price:
                return {"error": f"Take profit ({take_profit_price}) must be above entry ({entry_price})"}
            if stop_loss_price >= entry_price:
                return {"error": f"Stop loss ({stop_loss_price}) must be below entry ({entry_price})"}

            # STALE PRICE GUARD: Verify entry_price is close to current market price.
            # If stale, auto-recalculate using current price while preserving stop distance
            # and R:R ratio - so the trade still executes with correct bracket levels.
            MAX_ENTRY_DEVIATION_PCT = 5.0  # Warn/recalculate if entry is >5% away from market price
            try:
                if self.data_provider:
                    quote = self.data_provider.get_quote(symbol)
                    current_price = quote.get('last') or quote.get('bid') or quote.get('ask') if quote else None
                    if current_price and current_price > 0:
                        deviation_pct = abs(entry_price - current_price) / current_price * 100
                        if deviation_pct > MAX_ENTRY_DEVIATION_PCT:
                            # AUTO-RECALCULATE: Preserve stop distance and R:R, use current price as entry.
                            old_entry = entry_price
                            stop_distance = entry_price - stop_loss_price  # dollar distance to stop
                            tp_distance = take_profit_price - entry_price   # dollar distance to TP
                            entry_price = current_price
                            stop_loss_price = round(max(0.01, entry_price - stop_distance), 2)
                            take_profit_price = round(entry_price + tp_distance, 2)
                            logger.warning(
                                f"BRACKET RECALCULATED - {symbol}: stale entry ${old_entry:.2f} -> "
                                f"current ${entry_price:.2f} ({deviation_pct:.1f}% drift). "
                                f"New bracket: entry=${entry_price:.2f} TP=${take_profit_price:.2f} SL=${stop_loss_price:.2f}"
                            )
                            # Validate recalculated prices are still sane
                            if take_profit_price <= entry_price or stop_loss_price >= entry_price:
                                logger.warning(
                                    f"BRACKET BLOCKED - {symbol}: recalculated prices invalid "
                                    f"(entry=${entry_price:.2f} TP=${take_profit_price:.2f} SL=${stop_loss_price:.2f})"
                                )
                                return {
                                    "error": (
                                        f"STALE PRICES: entry_price ${old_entry:.2f} was {deviation_pct:.1f}% "
                                        f"away from current ${entry_price:.2f}. Recalculated bracket invalid - "
                                        f"please re-evaluate the setup."
                                    ),
                                    "current_price": current_price,
                                    "entry_submitted": old_entry,
                                    "deviation_pct": round(deviation_pct, 1)
                                }
                        elif deviation_pct > 2.0:
                            logger.warning(
                                f"BRACKET WARNING - {symbol}: entry_price ${entry_price:.2f} is "
                                f"{deviation_pct:.1f}% from current ${current_price:.2f} - proceeding but verify."
                            )
            except Exception as e:
                logger.debug(f"Could not validate entry price for {symbol}: {e}")

            # ATR STOP VALIDATION: Ensure stop loss is not tighter than 1x ATR.
            # Prevents stops that are too tight relative to normal volatility,
            # especially on gap-up stocks where intraday swings are larger.
            # If stop is tighter than 1x ATR, widen to 1.5x ATR.
            try:
                if self.data_provider:
                    atr_stop_pct = self._get_atr_stop_percent(symbol, entry_price, multiplier=1.0)
                    if atr_stop_pct and atr_stop_pct > 0:
                        atr_min_stop = round(entry_price * (1 - atr_stop_pct / 100), 2)
                        if stop_loss_price > atr_min_stop:
                            # Grok's stop is tighter than 1x ATR - widen to 1.5x ATR
                            atr_wide_pct = self._get_atr_stop_percent(symbol, entry_price, multiplier=1.5)
                            atr_wide_stop = round(entry_price * (1 - atr_wide_pct / 100), 2)
                            logger.warning(
                                f"ATR STOP WIDENED - {symbol}: Grok stop ${stop_loss_price:.2f} is tighter than "
                                f"1x ATR (${atr_min_stop:.2f}, {atr_stop_pct:.1f}%). "
                                f"Widening to 1.5x ATR: ${atr_wide_stop:.2f} ({atr_wide_pct:.1f}%)"
                            )
                            stop_loss_price = atr_wide_stop
            except Exception as e:
                logger.debug(f"ATR stop validation skipped for {symbol}: {e}")

            # Check forbidden symbols (positions that existed before agent started)
            if symbol in self.forbidden_symbols:
                return {"error": f"BLOCKED: {symbol} is a forbidden symbol (existed before agent started)"}

            # Check if symbol was a recent loser (12h cooldown)
            if symbol in self.recently_closed_losers:
                loser_info = self.recently_closed_losers[symbol]
                hours_since_loss = (datetime.now() - loser_info['timestamp']).total_seconds() / 3600
                if hours_since_loss < 12:
                    hours_remaining = 12 - hours_since_loss
                    return {
                        "error": f"BLOCKED: {symbol} was closed at a loss {hours_since_loss:.1f}h ago. Wait {hours_remaining:.1f}h before rebuying.",
                        "blocked": True
                    }

            # Check position limits (use market regime adaptive limit)
            current_positions = len([s for s, q in self.agent_opened_positions.items() if q > 0])
            max_allowed = self.max_positions_allowed  # Adaptive: 0-3 based on market regime
            regime_info = f" ({self.market_regime.get('regime', 'UNKNOWN')} market)" if self.market_regime else ""

            # DETAILED LOGGING: Position limit check
            logger.info(f"POSITION LIMIT CHECK: Current={current_positions}, Max={max_allowed}{regime_info}, Symbol={symbol}")

            if current_positions >= max_allowed and symbol not in self.agent_opened_positions:
                logger.warning(f"POSITION LIMIT: BLOCKED {symbol} - Already at max {max_allowed} positions{regime_info}")
                return {
                    "error": f"BLOCKED: Already at max {max_allowed} positions{regime_info}. Close existing before opening new.",
                    "current_positions": list(self.agent_opened_positions.keys()),
                    "max_positions_allowed": max_allowed,
                    "market_regime": self.market_regime.get('regime') if self.market_regime else None,
                    "blocked": True
                }
            else:
                logger.info(f"POSITION LIMIT: PASSED {symbol} - {current_positions}/{max_allowed} positions{regime_info}")

            # Check if broker supports bracket orders
            if not hasattr(self.broker, 'place_bracket_order'):
                return {"error": "Broker does not support bracket orders"}

            try:
                logger.info(f"Placing BRACKET order: {symbol} x{quantity} @ ${entry_price:.2f} (TP: ${take_profit_price:.2f}, SL: ${stop_loss_price:.2f})")

                # Calculate R:R ratio for logging
                risk = entry_price - stop_loss_price
                reward = take_profit_price - entry_price
                rr_ratio = reward / risk if risk > 0 else 0

                order = self.broker.place_bracket_order(
                    symbol=symbol,
                    entry_price=entry_price,
                    take_profit_price=take_profit_price,
                    stop_loss_price=stop_loss_price,
                    quantity=quantity
                )

                # Track the new position (assumes entry will fill)
                if symbol not in self.agent_opened_positions:
                    self.agent_opened_positions[symbol] = 0
                self.agent_opened_positions[symbol] += quantity

                # Track entry price, TP target, and SL target
                self.agent_position_entry_prices[symbol] = entry_price
                self.agent_position_tp_targets[symbol] = take_profit_price
                self.agent_position_sl_targets[symbol] = stop_loss_price

                # Track conviction score from trading plan so websocket/monitor
                # prompts have the original entry conviction as context.
                # Always overwrite from current_trading_plan on a new bracket entry -
                # stale values from a previous exit (e.g. 2.0 from panic sell) must not persist.
                conviction_score = None
                if self.current_trading_plan:
                    for planned_pos in self.current_trading_plan:
                        if planned_pos.get("symbol", "").upper() == symbol.upper():
                            conviction_score = planned_pos.get("conviction_score")
                            break
                if conviction_score:
                    self.agent_position_convictions[symbol] = conviction_score
                    logger.debug(f"Tracking entry conviction {conviction_score}/10 for {symbol}")

                # Record BUY for cooldown tracking
                self._record_order_executed(symbol, "buy")

                # Log to trade log
                self.trade_log.append({
                    "timestamp": datetime.now().isoformat(),
                    "symbol": symbol,
                    "side": "buy",
                    "quantity": quantity,
                    "order_type": "bracket",
                    "entry_price": entry_price,
                    "take_profit": take_profit_price,
                    "stop_loss": stop_loss_price,
                    "reason": reason,
                    "order_id": str(order.order_id) if order.order_id else None,
                    "rr_ratio": round(rr_ratio, 2)
                })

                # Fetch recent news for the symbol to record as catalyst (with URLs for dashboard links)
                news_catalyst = None
                if self.news_provider and hasattr(self.news_provider, 'get_news'):
                    try:
                        news_articles = self.news_provider.get_news(symbols=[symbol], limit=3, hours_back=6)
                        if news_articles and not any('error' in str(a).lower() for a in news_articles):
                            valid = [a for a in news_articles[:3] if a.get('title')]
                            if valid:
                                headlines = [a.get('title', '') for a in valid]
                                news_links = [
                                    {'headline': a.get('title', ''), 'url': a.get('url', '') or a.get('article_url', '')}
                                    for a in valid if a.get('url') or a.get('article_url')
                                ]
                                import json as _json
                                if news_links:
                                    # Store as JSON so dashboard can render clickable links
                                    news_catalyst = _json.dumps({
                                        'text': " | ".join(headlines),
                                        'news_links': news_links
                                    })
                                else:
                                    news_catalyst = " | ".join(headlines)
                                logger.debug(f"News catalyst for {symbol}: {headlines[0][:80]}...")
                    except Exception as e:
                        logger.debug(f"Could not fetch news for {symbol}: {e}")

                # Record entry reasoning in trade journal (for EOD reflection)
                # This captures the "why" at purchase time, since LLM may not remember morning reasoning at EOD
                if self.learning_db:
                    try:
                        self.learning_db.record_trade_entry(
                            symbol=symbol,
                            entry_price=entry_price,
                            why_entered=reason or "No reason provided",
                            shares=quantity,
                            expected_target=take_profit_price,
                            expected_stop=stop_loss_price,
                            setup_type=tool_input.get("setup_type", "bracket_order"),
                            catalyst=news_catalyst,
                            order_id=str(order.order_id) if order.order_id else None  # Convert UUID to string
                        )
                        logger.debug(f"Recorded trade journal entry for {symbol}")
                    except Exception as e:
                        logger.warning(f"Failed to record trade journal for {symbol}: {e}")

                logger.info(f"[OK] BRACKET order placed for {symbol}: Entry@${entry_price:.2f}, TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f} (R:R = {rr_ratio:.1f})")

                return {
                    "success": True,
                    "order_id": str(order.order_id) if order.order_id else None,
                    "symbol": symbol,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "take_profit_price": take_profit_price,
                    "stop_loss_price": stop_loss_price,
                    "rr_ratio": round(rr_ratio, 2),
                    "order_type": "bracket",
                    "message": f"Bracket order placed - TP and SL will activate when entry fills"
                }

            except Exception as e:
                logger.error(f"Bracket order failed for {symbol}: {e}")
                return {"error": f"Failed to place bracket order: {str(e)}"}

        elif tool_name == "get_account_info":
            if not self.broker:
                return {"error": "Broker not connected"}

            try:
                account_info = self.broker.get_account_info()

                # Convert positions to dict format
                positions = []
                for pos in account_info.positions:
                    positions.append({
                        "symbol": pos.symbol,
                        "quantity": pos.quantity,
                        "avg_entry_price": pos.avg_entry_price,
                        "current_price": pos.current_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "unrealized_pnl_percent": pos.unrealized_pnl_percent
                    })

                # Update agent state
                self.state["cash"] = account_info.cash
                self.state["account_value"] = account_info.portfolio_value
                self.state["positions"] = positions

                # Build response with account info
                response = {
                    "cash": account_info.cash,
                    "account_value": account_info.portfolio_value,
                    "buying_power": account_info.buying_power,
                    "positions": positions
                }

                # Add PDT status if enabled (so Claude knows how many trades remain)
                if self.pdt_enabled:
                    response["pdt_status"] = self.get_pdt_status()

                return response

            except Exception as e:
                return {"error": f"Failed to get account info: {str(e)}"}

        elif tool_name == "create_trading_plan":
            # Create a complete trading plan with balanced position sizing
            candidates = tool_input["trading_candidates"]
            risk_percent = tool_input.get("risk_percent", 1.5)

            if not candidates:
                return {"error": "No trading candidates provided"}

            # MARKET REGIME FILTER: Enforce conviction threshold
            # Filter out candidates that don't meet the regime's minimum conviction requirement
            original_count = len(candidates)
            min_conviction_required = self.min_conviction_threshold  # Adaptive: 7-10 based on market regime

            # DETAILED LOGGING: Show what we received
            regime_name = self.market_regime.get('regime', 'UNKNOWN') if self.market_regime else 'UNKNOWN'
            logger.info(f"=" * 60)
            logger.info(f"MARKET REGIME FILTER - CONVICTION ENFORCEMENT")
            logger.info(f"Regime: {regime_name} | Min Conviction Required: {min_conviction_required}/10")
            logger.info(f"Received {original_count} candidate(s) for filtering:")
            for idx, candidate in enumerate(candidates, 1):
                symbol = candidate.get("symbol", "UNKNOWN")
                conviction = candidate.get("conviction_score", 0)
                logger.info(f"  {idx}. {symbol}: conviction={conviction}/10")
            logger.info(f"=" * 60)

            filtered_candidates = []
            rejected_candidates = []

            for candidate in candidates:
                conviction = candidate.get("conviction_score", 0)
                symbol = candidate.get("symbol", "UNKNOWN")

                if conviction < min_conviction_required:
                    reason = f"Conviction {conviction}/10 below {min_conviction_required}/10 required for {regime_name} market"
                    rejected_candidates.append({"symbol": symbol, "conviction": conviction, "reason": reason})
                    logger.warning(f"REGIME FILTER: REJECTED {symbol} - {reason}")
                else:
                    logger.info(f"REGIME FILTER: PASSED {symbol} - Conviction {conviction}/10 meets {min_conviction_required}/10 requirement")
                    filtered_candidates.append(candidate)

            # Log filtering results
            if rejected_candidates:
                regime_name = self.market_regime.get('regime', 'UNKNOWN') if self.market_regime else 'UNKNOWN'
                logger.info(f"MARKET REGIME FILTER ({regime_name}): {original_count} candidates -> {len(filtered_candidates)} passed (min conviction: {min_conviction_required}/10)")
                logger.info(f"REJECTED: {[r['symbol'] for r in rejected_candidates]}")

            # Replace candidates with filtered list
            candidates = filtered_candidates

            if not candidates:
                regime_name = self.market_regime.get('regime', 'UNKNOWN') if self.market_regime else 'UNKNOWN'
                return {
                    "error": f"All candidates rejected by market regime filter ({regime_name} market requires {min_conviction_required}/10 conviction)",
                    "rejected_candidates": rejected_candidates,
                    "min_conviction_required": min_conviction_required,
                    "market_regime": regime_name
                }

            # Get available capital - CASH ACCOUNT (NO MARGIN) - Use actual cash only
            if self.broker:
                try:
                    account_info = self.broker.get_account_info()
                    total_account_value = account_info.portfolio_value
                    available_cash = account_info.cash
                    
                    # CRITICAL FIX: ALWAYS enforce $25,000 minimum account protection
                    # This prevents PDT violations and protects base capital regardless of config
                    MINIMUM_ACCOUNT_VALUE = 25000.0  # Hard-coded $25k minimum
                    
                    if self.capital_limits_enabled and self.base_capital > 0:
                        # Use configured base capital if enabled
                        effective_base_capital = self.base_capital
                        logger.info(f"CONFIGURED CAPITAL PROTECTION: Base=${effective_base_capital:.2f}")
                    else:
                        # Always enforce $25k minimum even if capital limits disabled
                        effective_base_capital = MINIMUM_ACCOUNT_VALUE
                        logger.info(f"DEFAULT CAPITAL PROTECTION: Enforcing ${effective_base_capital:.2f} minimum (PDT protection)")
                    
                    # Calculate maximum safe usage to never dip below base capital
                    # Available for trading = min(cash, account_value - base_capital)
                    max_safe_usage = max(0, total_account_value - effective_base_capital)
                    max_tradeable_cash = min(available_cash, max_safe_usage)
                    
                    logger.info(f"ACCOUNT STATUS: Total=${total_account_value:.2f}, Cash=${available_cash:.2f}, Base=${effective_base_capital:.2f}")
                    logger.info(f"SAFE TRADING LIMIT: ${max_tradeable_cash:.2f} (ensures account stays above ${effective_base_capital:.2f})")
                    
                    # Additional safety check - if we're already below the minimum, block all trading
                    if total_account_value <= effective_base_capital:
                        logger.error(f"CRITICAL: Account value ${total_account_value:.2f} is AT OR BELOW minimum ${effective_base_capital:.2f}")
                        logger.error(f"TRADING BLOCKED: Cannot place any orders until account recovers above ${effective_base_capital:.2f}")
                        return {
                            "error": f"TRADING BLOCKED: Account value ${total_account_value:.2f} is at/below minimum ${effective_base_capital:.2f}. No trading allowed until account recovers.",
                            "blocked": True,
                            "reason": "below_minimum_capital",
                            "current_value": total_account_value,
                            "minimum_required": effective_base_capital,
                            "deficit": effective_base_capital - total_account_value
                        }
                        
                except Exception as e:
                    logger.warning(f"Could not get account info: {e}")
                    max_tradeable_cash = 1000.0  # Conservative fallback
            else:
                max_tradeable_cash = 1000.0  # Conservative fallback

            # Calculate total conviction score
            total_conviction = sum(candidate["conviction_score"] for candidate in candidates)
            
            if total_conviction == 0:
                return {"error": "All candidates have zero conviction score"}

            # CASH ACCOUNT: Reserve conservative safety buffer to ensure we NEVER dip below base capital
            # This prevents any cash overdraw and protects the $25,000 base capital requirement
            if self.capital_limits_enabled and self.base_capital > 0:
                # Additional safety: Reserve 10% buffer above the base capital protection
                safety_buffer = max(max_tradeable_cash * 0.15, 1000.0)  # 15% buffer or $1000 minimum
                available_for_new_positions = max(0, max_tradeable_cash - safety_buffer)
                
                logger.info(f"CAPITAL PROTECTION MODE: Account=${total_account_value:.2f}, Base=${self.base_capital:.2f}")
                logger.info(f"SAFE ALLOCATION: MaxTradeable=${max_tradeable_cash:.2f}, SafetyBuffer=${safety_buffer:.2f}, Available=${available_for_new_positions:.2f}")
                logger.info(f"PROTECTION: Ensures account never drops below ${self.base_capital:.2f} + ${safety_buffer:.2f} buffer")
            else:
                # No capital limits - use standard 20% cash account safety buffer
                safety_buffer = max_tradeable_cash * 0.20  # 20% safety buffer for cash account
                available_for_new_positions = max_tradeable_cash - safety_buffer
                
                logger.info(f"STANDARD CASH ACCOUNT: MaxTradeable=${max_tradeable_cash:.2f}, SafetyBuffer=${safety_buffer:.2f}, Available=${available_for_new_positions:.2f}")
                logger.info(f"CASH ACCOUNT SAFETY: Using 80% of available cash to prevent overdraw")

            # Calculate position sizes based on conviction weighting + Kelly/volatility factors
            trading_plan = []
            total_allocated = 0

            # FIX: risk_percent is TOTAL portfolio risk budget, not per-position.
            # Split total risk budget across candidates by conviction weight.
            # With 2 positions and 1.5% total risk, each position risks ~0.75% (not 1.5% each).
            total_risk_dollar = available_for_new_positions * (risk_percent / 100.0)
            logger.info(f"Total risk budget: ${total_risk_dollar:.2f} ({risk_percent}% of ${available_for_new_positions:.2f})")

            MIN_POSITION_DOLLAR = 500  # Never open a position smaller than this

            # MERGE: Get volatility regime and strategy metrics once for all candidates
            # so create_trading_plan uses Kelly Criterion + volatility + streaks (not just conviction)
            try:
                from analytics.dynamic_sizing import DynamicPositionSizer
                from analytics.strategy_analytics import StrategyAnalytics
                from analytics.market_regime import get_current_market_regime
                regime_data = get_current_market_regime(self.data_provider)
                volatility_regime = regime_data.get('volatility_regime', 'medium')
                all_strategy_metrics = StrategyAnalytics().analyze_strategy_performance(self.trade_log)
                dynamic_sizer_available = True
            except Exception as e:
                logger.warning(f"Dynamic sizer unavailable, using conviction-only sizing: {e}")
                volatility_regime = 'medium'
                all_strategy_metrics = {}
                dynamic_sizer_available = False

            for candidate in candidates:
                symbol = candidate["symbol"].upper()
                entry_price = candidate["entry_price"]
                stop_price = candidate["stop_price"]
                conviction = candidate["conviction_score"]
                strategy = candidate.get("strategy", "unspecified")

                # FRESH PRICE: Refresh entry price from live market before sizing.
                # Grok may have decided on a price seconds/minutes ago - use current
                # price so position size, risk-per-share, and bracket levels are accurate.
                if self.data_provider:
                    try:
                        quote = self.data_provider.get_quote(symbol)
                        current_price = quote.get('last') or quote.get('bid') or quote.get('ask') if quote else None
                        if current_price and current_price > 0:
                            old_entry = entry_price
                            stop_distance = entry_price - stop_price  # preserve dollar stop distance
                            entry_price = current_price
                            stop_price = max(0.01, entry_price - stop_distance)
                            if abs(old_entry - entry_price) / old_entry > 0.005:  # log if >0.5% drift
                                logger.info(
                                    f"{symbol}: entry price refreshed ${old_entry:.2f} -> ${entry_price:.2f} "
                                    f"(stop adjusted to ${stop_price:.2f})"
                                )
                    except Exception as e:
                        logger.debug(f"{symbol}: price refresh failed, using Grok price ${entry_price:.2f}: {e}")

                # Validate candidate
                if entry_price <= stop_price:
                    logger.warning(f"Invalid prices for {symbol}: entry ${entry_price:.2f} <= stop ${stop_price:.2f}")
                    continue

                # Calculate allocation based on conviction weighting
                allocation_percent = (conviction / total_conviction)
                allocated_cash = available_for_new_positions * allocation_percent

                # Calculate risk-based position size
                # risk_budget = this candidate's share of total portfolio risk budget
                risk_per_share = entry_price - stop_price
                risk_budget = total_risk_dollar * allocation_percent  # conviction-weighted slice of total risk

                shares = int(risk_budget / risk_per_share)
                actual_cost = shares * entry_price

                # MERGE: Apply Kelly + volatility + streak adjustments via DynamicPositionSizer
                # Use the lower of conviction-weighted shares vs Kelly-optimal shares
                if dynamic_sizer_available:
                    try:
                        strategy_perf = all_strategy_metrics.get(strategy)
                        # Base risk percent for this candidate = its conviction-weighted slice
                        per_candidate_risk_pct = risk_percent * allocation_percent
                        sizer = DynamicPositionSizer(base_risk_percent=per_candidate_risk_pct)
                        kelly_result = sizer.calculate_position_size(
                            account_value=available_for_new_positions,
                            entry_price=entry_price,
                            stop_price=stop_price,
                            strategy_performance=strategy_perf,
                            volatility_regime=volatility_regime,
                            trade_history=self.trade_log[-10:] if self.trade_log else None
                        )
                        kelly_shares = kelly_result.get('shares', shares)
                        # Take the more conservative (smaller) of the two estimates
                        if kelly_shares < shares:
                            logger.info(
                                f"{symbol}: Kelly/vol sizing reduced shares {shares} -> {kelly_shares} "
                                f"(adjustments: {kelly_result.get('adjustments_applied', [])})"
                            )
                            shares = kelly_shares
                            actual_cost = shares * entry_price
                    except Exception as e:
                        logger.warning(f"Dynamic sizing failed for {symbol}, using conviction sizing: {e}")

                # Hard cap: never exceed 90% of this candidate's conviction-weighted cash slice
                if actual_cost > allocated_cash * 0.90:
                    shares = int(allocated_cash * 0.90 / entry_price)
                    actual_cost = shares * entry_price

                # Skip if position is too small (uneconomic trade)
                if shares <= 0 or actual_cost < MIN_POSITION_DOLLAR:
                    logger.warning(f"Position size too small for {symbol}: {shares} shares (${actual_cost:.0f}) - minimum ${MIN_POSITION_DOLLAR}")
                    continue

                position_plan = {
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "shares": shares,
                    "cost": actual_cost,
                    "conviction_score": conviction,
                    "allocation_percent": allocation_percent * 100,
                    "risk_per_share": risk_per_share,
                    "total_risk": shares * risk_per_share,
                    "strategy": strategy,
                    "risk_reward_ratio": None  # Will calculate if take_profit provided
                }

                # Calculate R:R if take_profit available
                # Scale take_profit to preserve the original R:R ratio from Grok's plan
                if "take_profit" in candidate and candidate["take_profit"] > candidate["entry_price"]:
                    original_tp_distance = candidate["take_profit"] - candidate["entry_price"]
                    original_stop_distance = candidate["entry_price"] - candidate.get("stop_price", stop_price)
                    # Preserve R:R ratio: scale TP distance by same ratio as price change
                    if original_stop_distance > 0:
                        rr_ratio_original = original_tp_distance / original_stop_distance
                        fresh_tp = entry_price + (risk_per_share * rr_ratio_original)
                    else:
                        fresh_tp = entry_price + original_tp_distance
                    reward_per_share = fresh_tp - entry_price
                    position_plan["risk_reward_ratio"] = reward_per_share / risk_per_share
                    position_plan["take_profit"] = round(fresh_tp, 2)

                trading_plan.append(position_plan)
                total_allocated += actual_cost

            # Sort by conviction score (highest first)
            trading_plan.sort(key=lambda x: x["conviction_score"], reverse=True)

            # Calculate utilization
            utilization = (total_allocated / max_tradeable_cash) * 100 if max_tradeable_cash > 0 else 0

            # Store trading plan and timestamp for enforcement
            import time
            self.current_trading_plan = trading_plan
            self.last_trading_plan_timestamp = time.time()
            
            logger.info(f"[OK] Trading plan created: {len(trading_plan)} positions, ${total_allocated:.2f} allocated ({utilization:.1f}% utilization)")

            # Log decision to decision journal for learning/reflection
            if self.learning_db:
                try:
                    # Build candidate info for the journal
                    plan_symbols = {p['symbol'] for p in trading_plan}
                    candidates_info = []
                    for c in candidates:
                        candidates_info.append({
                            'symbol': c['symbol'],
                            'conviction': c.get('conviction_score', 0),
                            'entry_price': c.get('entry_price', 0),
                            'in_plan': c['symbol'] in plan_symbols
                        })

                    # Determine decision type
                    if len(trading_plan) == 0:
                        decision_type = 'NOPOSITIONS'
                        decision_reason = f"0/{len(candidates)} candidates viable - all filtered due to position sizing constraints"
                    elif len(trading_plan) < len(candidates):
                        decision_type = 'PARTIAL'
                        decision_reason = f"{len(trading_plan)}/{len(candidates)} candidates viable"
                    else:
                        decision_type = 'FULL'
                        decision_reason = f"All {len(candidates)} candidates included in plan"

                    # Get market context
                    market_ctx = f"Cash: ${max_tradeable_cash:.2f}, Safety buffer: ${safety_buffer:.2f}"

                    self.learning_db.record_decision(
                        decision_type=decision_type,
                        candidates_considered=candidates_info,
                        decision_reason=decision_reason,
                        agent_reasoning=getattr(self, 'last_analysis_summary', ''),
                        market_context=market_ctx
                    )
                except Exception as e:
                    logger.warning(f"Failed to record decision to journal: {e}")

            return {
                "trading_plan": trading_plan,
                "summary": {
                    "total_candidates": len(candidates),
                    "viable_positions": len(trading_plan),
                    "available_cash": max_tradeable_cash,
                    "allocated_cash": total_allocated,
                    "remaining_cash": max_tradeable_cash - total_allocated,
                    "utilization_percent": utilization,
                    "safety_buffer": safety_buffer,
                    "risk_percent": risk_percent
                }
            }

        elif tool_name == "calculate_position_size":
            # DEPRECATED: Single position sizing (use create_trading_plan instead)
            logger.error("calculate_position_size is DEPRECATED and leads to overbuying - use create_trading_plan instead!")
            return {
                "error": "DEPRECATED TOOL: calculate_position_size leads to overbuying. Use create_trading_plan instead for balanced portfolio allocation.",
                "blocked": True,
                "reason": "deprecated_tool",
                "required_action": "Use create_trading_plan tool instead"
            }

        elif tool_name == "get_market_time_info":
            if not self.data_provider:
                # Fallback if no data provider
                return {
                    "current_time": datetime.now().strftime("%Y-%m-%d %I:%M %p ET"),
                    "is_open": True,
                    "minutes_to_close": 180
                }

            try:
                return self.data_provider.get_market_time_info()
            except Exception as e:
                return {"error": f"Failed to get market time info: {str(e)}"}

        elif tool_name == "search_market_news":
            query = tool_input.get("query", "")
            limit = tool_input.get("limit", 10)

            # Extract symbol from query if it looks like a ticker
            symbols = None
            query_parts = query.upper().split()
            potential_symbols = [p for p in query_parts if p.isalpha() and 1 <= len(p) <= 5]

            # If query contains potential ticker symbols, use them
            if potential_symbols and any(len(s) <= 4 for s in potential_symbols):
                symbols = [s for s in potential_symbols if len(s) <= 4][:3]  # Max 3 symbols

            try:
                # Try to get news from news_provider first (always Alpaca), then broker, then data_provider
                news_source = None
                if self.news_provider and hasattr(self.news_provider, 'get_news'):
                    news_source = self.news_provider
                elif self.broker and hasattr(self.broker, 'get_news'):
                    news_source = self.broker
                elif self.data_provider and hasattr(self.data_provider, 'get_news'):
                    news_source = self.data_provider

                articles = []
                if news_source:
                    articles = news_source.get_news(
                        symbols=symbols,
                        query=query.lower() if not symbols else None,
                        limit=limit,
                        hours_back=48  # Last 48 hours for more results
                    )

                    if articles and not any("error" in a for a in articles):
                        # Use full NewsAnalyzer for sophisticated sentiment analysis
                        try:
                            from analytics.news_analyzer import NewsAnalyzer
                            news_analyzer = NewsAnalyzer()
                        except ImportError:
                            news_analyzer = None

                        analyzed_articles = []
                        for article in articles[:limit]:
                            title = article.get("title", "")
                            summary = article.get("summary", "")
                            text = f"{title} {summary}".lower()

                            analyzed = {
                                "title": title,
                                "summary": summary,
                                "source": article.get("source", "Unknown"),
                                "timestamp": article.get("timestamp", ""),
                                "symbols": article.get("symbols", []),
                                "url": article.get("url", "")
                            }

                            # Use NewsAnalyzer for sentiment if available
                            if news_analyzer:
                                # Classify impact using NewsAnalyzer keywords
                                if any(kw in text for kw in news_analyzer.CRITICAL_KEYWORDS):
                                    analyzed["impact"] = "critical"
                                elif any(kw in text for kw in news_analyzer.HIGH_IMPACT_KEYWORDS):
                                    analyzed["impact"] = "high"
                                else:
                                    analyzed["impact"] = "low"

                                # Classify sentiment using NewsAnalyzer keywords
                                bullish_count = sum(1 for kw in news_analyzer.BULLISH_KEYWORDS if kw in text)
                                bearish_count = sum(1 for kw in news_analyzer.BEARISH_KEYWORDS if kw in text)

                                if bullish_count >= 3 and bearish_count == 0:
                                    analyzed["sentiment"] = "very_bullish"
                                elif bullish_count > bearish_count:
                                    analyzed["sentiment"] = "bullish"
                                elif bearish_count >= 3 and bullish_count == 0:
                                    analyzed["sentiment"] = "very_bearish"
                                elif bearish_count > bullish_count:
                                    analyzed["sentiment"] = "bearish"
                                else:
                                    analyzed["sentiment"] = "neutral"

                                # Generate trading signal
                                if analyzed["impact"] == "critical":
                                    if analyzed["sentiment"] in ["very_bullish", "bullish"]:
                                        analyzed["trading_signal"] = "STRONG BUY - Critical positive catalyst"
                                    elif analyzed["sentiment"] in ["very_bearish", "bearish"]:
                                        analyzed["trading_signal"] = "AVOID/SHORT - Critical negative catalyst"
                                    else:
                                        analyzed["trading_signal"] = "WAIT - Critical news, unclear direction"
                                elif analyzed["impact"] == "high":
                                    if analyzed["sentiment"] in ["very_bullish", "bullish"]:
                                        analyzed["trading_signal"] = "BUY SIGNAL - Positive high-impact news"
                                    elif analyzed["sentiment"] in ["very_bearish", "bearish"]:
                                        analyzed["trading_signal"] = "CAUTION - Negative news"
                                    else:
                                        analyzed["trading_signal"] = "NEUTRAL - Watch for direction"
                                else:
                                    analyzed["trading_signal"] = "LOW IMPACT - Monitor only"
                            else:
                                # Fallback: basic sentiment detection
                                if any(w in text for w in ["surge", "soar", "jump", "beat", "record", "breakout", "rally"]):
                                    analyzed["sentiment"] = "bullish"
                                    analyzed["impact"] = "high"
                                elif any(w in text for w in ["drop", "fall", "miss", "warn", "cut", "crash", "plunge"]):
                                    analyzed["sentiment"] = "bearish"
                                    analyzed["impact"] = "high"
                                elif any(w in text for w in ["earnings", "fda", "merger", "acquisition", "buyout"]):
                                    analyzed["sentiment"] = "neutral"
                                    analyzed["impact"] = "critical"
                                else:
                                    analyzed["sentiment"] = "neutral"
                                    analyzed["impact"] = "low"
                                analyzed["trading_signal"] = None

                            analyzed_articles.append(analyzed)

                        # Use Ollama to summarize news if available
                        ollama_summary = None
                        if self.ollama_provider and self.ollama_provider.is_available():
                            headlines = [a.get("title", "") for a in analyzed_articles if a.get("title")]
                            primary_symbol = symbols[0] if symbols else query
                            ollama_summary = self.ollama_provider.summarize_news(primary_symbol, headlines)

                        result = {
                            "query": query,
                            "symbols_searched": symbols,
                            "count": len(analyzed_articles),
                            "results": analyzed_articles,
                            "source": f"{type(news_source).__name__} News API"
                        }
                        if ollama_summary:
                            result["ai_summary"] = ollama_summary
                        return result

                # Fallback: return empty results if no news available
                return {
                    "query": query,
                    "count": 0,
                    "results": [],
                    "message": "No news available - broker news API not connected"
                }

            except Exception as e:
                return {
                    "query": query,
                    "error": f"Failed to fetch news: {str(e)}",
                    "results": []
                }

        elif tool_name == "set_trading_strategy":
            # Log strategy change
            strategy = tool_input["strategy"]
            reason = tool_input.get("reason", "")

            self.log_strategy_change(strategy, reason)

            return {
                "status": "success",
                "current_strategy": strategy,
                "previous_strategy": self.strategy_log[-2]["strategy"] if len(self.strategy_log) > 1 else None,
                "message": f"Strategy set to '{strategy}'"
            }

        elif tool_name == "get_market_regime":
            # Analyze current market regime
            try:
                from analytics.market_regime import get_current_market_regime
                regime = get_current_market_regime(self.data_provider)
                return regime
            except Exception as e:
                logger.error(f"Error getting market regime: {e}")
                return {"error": str(e)}

        elif tool_name == "analyze_multi_timeframe":
            # Analyze symbol across multiple timeframes
            try:
                from analytics.multi_timeframe import analyze_multi_timeframe
                symbol = tool_input["symbol"]
                analysis = analyze_multi_timeframe(symbol, self.data_provider)
                return analysis
            except Exception as e:
                logger.error(f"Error analyzing {tool_input.get('symbol')}: {e}")
                return {"error": str(e)}

        elif tool_name == "check_correlation_risk":
            # Check correlation risk for new position
            try:
                from analytics.correlation_tracker import CorrelationTracker
                tracker = CorrelationTracker(self.data_provider)
                symbol = tool_input["symbol"]
                result = tracker.check_new_position_correlation(symbol, self.agent_opened_positions)
                return result
            except Exception as e:
                logger.error(f"Error checking correlation: {e}")
                return {"error": str(e)}

        elif tool_name == "analyze_technical_indicators":
            # Analyze technical indicators for mean reversion and volume profile
            symbol = tool_input["symbol"]
            timeframe = tool_input.get("timeframe", "intraday")

            if not self.data_provider:
                return {"error": "Market data provider not connected"}

            try:
                # Fetch historical data based on timeframe
                if timeframe == "daily":
                    # Get 30 days of daily bars
                    bars = self.data_provider.get_historical_data(
                        symbol=symbol,
                        days_back=30,
                        timeframe="1D"
                    )
                else:
                    # Get 1 day of 15-minute bars for intraday
                    bars = self.data_provider.get_historical_data(
                        symbol=symbol,
                        days_back=1,
                        timeframe="15Min"
                    )

                if not bars or len(bars) < 20:
                    return {"error": f"Insufficient data for {symbol} (need 20+ bars, got {len(bars) if bars else 0})"}

                # Calculate technical indicators
                analysis = self._calculate_technical_indicators(symbol, bars)
                return analysis

            except Exception as e:
                logger.error(f"Error analyzing technical indicators for {symbol}: {e}")
                return {"error": str(e)}

        elif tool_name == "get_strategy_performance":
            # Get performance metrics for strategies
            try:
                from analytics.strategy_analytics import StrategyAnalytics
                analytics = StrategyAnalytics()
                metrics = analytics.analyze_strategy_performance(self.trade_log)
                recommendations = analytics.get_strategy_recommendations(metrics)
                return {
                    "strategy_metrics": metrics,
                    "recommendations": recommendations
                }
            except Exception as e:
                logger.error(f"Error getting strategy performance: {e}")
                return {"error": str(e)}

        elif tool_name == "calculate_dynamic_position_size":
            # Calculate optimal position size with Kelly Criterion
            try:
                from analytics.dynamic_sizing import calculate_optimal_position_size
                from analytics.strategy_analytics import StrategyAnalytics
                from analytics.market_regime import get_current_market_regime
                from analytics.correlation_tracker import CorrelationTracker

                symbol = tool_input["symbol"]
                entry_price = tool_input["entry_price"]
                stop_price = tool_input["stop_price"]
                strategy_name = tool_input.get("strategy", self.current_strategy)

                # Get account value and convert to active capital
                account_info = self.broker.get_account_info() if self.broker else None
                total_value = account_info.portfolio_value if account_info else 10000
                account_value = self.get_active_capital(total_value)  # Use active capital, not total

                # Get strategy performance
                strategy_performance = None
                if strategy_name:
                    analytics = StrategyAnalytics()
                    all_metrics = analytics.analyze_strategy_performance(self.trade_log)
                    strategy_performance = all_metrics.get(strategy_name)

                # Get market regime for volatility
                regime = get_current_market_regime(self.data_provider)
                volatility_regime = regime.get('volatility_regime', 'medium')

                # Check correlation risk
                tracker = CorrelationTracker(self.data_provider)
                max_correlation = 0.0
                for existing_symbol in self.agent_opened_positions.keys():
                    correlation = tracker._get_correlation(symbol, existing_symbol)
                    max_correlation = max(max_correlation, correlation)

                # Calculate optimal size (pass recent trade history for streak detection)
                result = calculate_optimal_position_size(
                    account_value=account_value,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    strategy_performance=strategy_performance,
                    volatility_regime=volatility_regime,
                    correlation_risk=max_correlation,
                    trade_history=self.trade_log[-10:] if self.trade_log else None
                )

                return result
            except Exception as e:
                logger.error(f"Error calculating dynamic position size: {e}")
                return {"error": str(e)}

        elif tool_name == "extend_take_profit":
            symbol = tool_input["symbol"].upper()
            new_tp = float(tool_input["new_take_profit"])
            reason = tool_input.get("reason", "no reason given")

            if symbol not in self.agent_opened_positions or not self.broker:
                return {"error": f"No tracked position for {symbol}"}

            current_tp = self.agent_position_tp_targets.get(symbol)
            current_sl = self.agent_position_sl_targets.get(symbol)
            qty = self.agent_opened_positions.get(symbol, 0)

            # Only allow upward extension
            if current_tp and new_tp <= current_tp:
                return {
                    "error": f"New TP ${new_tp:.2f} must be higher than current TP ${current_tp:.2f}",
                    "current_tp": current_tp,
                    "rejected": True
                }

            if not current_sl or current_sl <= 0:
                return {"error": f"No stop loss tracked for {symbol} - cannot safely extend TP"}

            if qty <= 0:
                return {"error": f"No shares tracked for {symbol}"}

            try:
                self.broker.update_oco_order(
                    symbol=symbol,
                    quantity=int(qty),
                    take_profit_price=new_tp,
                    stop_loss_price=current_sl
                )
                self.agent_position_tp_targets[symbol] = new_tp
                logger.info(
                    f"TP extended for {symbol}: ${current_tp:.2f} -> ${new_tp:.2f} "
                    f"({reason}) | SL unchanged at ${current_sl:.2f}"
                )
                return {
                    "status": "tp_extended",
                    "symbol": symbol,
                    "old_tp": current_tp,
                    "new_tp": new_tp,
                    "stop_loss": current_sl,
                    "quantity": qty,
                    "reason": reason
                }
            except Exception as e:
                logger.error(f"Failed to extend TP for {symbol}: {e}")
                return {"error": f"TP extension failed: {e}"}

        elif tool_name == "update_position_conviction":
            symbol = tool_input["symbol"].upper()
            new_conviction = float(tool_input["conviction"])
            reason = tool_input.get("reason", "no reason given")

            old_conviction = self.agent_position_convictions.get(symbol, "unknown")

            # STOP-LOSS CONTEXT GUARD: During emergency_stop_loss evaluation, Grok is being
            # asked "hold or sell now" - not to reassess conviction. Allowing conviction updates
            # here causes automatic exits via the conviction threshold, bypassing the explicit
            # hold/sell decision. In this context Grok must use place_order to sell or do nothing.
            if getattr(self, 'current_context', '') == 'emergency_stop_loss':
                logger.info(
                    f"CONVICTION UPDATE BLOCKED - {symbol}: cannot reassess conviction during "
                    f"emergency_stop_loss evaluation (attempted {new_conviction}/10, "
                    f"current={old_conviction}/10). Use place_order to sell or do nothing to hold."
                )
                return {
                    "status": "blocked",
                    "symbol": symbol,
                    "conviction": old_conviction,
                    "note": (
                        f"Conviction updates are not allowed during stop-loss evaluation. "
                        f"To exit: use place_order(symbol='{symbol}', side='sell', ...). "
                        f"To hold: do nothing - the bracket stop is still active."
                    )
                }

            self.agent_position_convictions[symbol] = new_conviction
            logger.info(f"Conviction update: {symbol} {old_conviction} -> {new_conviction}/10 ({reason})")

            # MINIMUM HOLD TIME: Suppress conviction exits for the first 10 minutes after entry.
            # Prevents whipsawing out of positions on normal open volatility before the trade
            # has had time to develop. The bracket stop loss handles catastrophic moves.
            MIN_HOLD_MINUTES = 10
            minutes_held = None
            try:
                for t in reversed(self.trade_log):
                    if t.get('symbol', '').upper() == symbol and t.get('side', '').lower() == 'buy':
                        entry_dt = datetime.fromisoformat(t['timestamp'])
                        minutes_held = (datetime.now() - entry_dt).total_seconds() / 60
                        break
            except Exception:
                pass

            if minutes_held is not None and minutes_held < MIN_HOLD_MINUTES:
                logger.info(
                    f"CONVICTION EXIT SUPPRESSED - {symbol}: only held {minutes_held:.1f} min "
                    f"(min {MIN_HOLD_MINUTES} min). Conviction={new_conviction}/10. "
                    f"Bracket stop at ${self.agent_position_sl_targets.get(symbol, 0):.2f} still active."
                )
                return {
                    "status": "updated",
                    "symbol": symbol,
                    "conviction": new_conviction,
                    "note": (
                        f"Conviction noted ({new_conviction}/10) but exit suppressed - "
                        f"position only {minutes_held:.1f} min old (min {MIN_HOLD_MINUTES} min). "
                        f"Bracket stop is still protecting the position."
                    )
                }

            # Conviction-based graduated response:
            #   conviction < FULL_EXIT_THRESHOLD  -> full exit (thesis completely broken)
            #   conviction in reduce zone          -> reduce to 50% (uncertain, de-risk)
            #   conviction >= REDUCE_THRESHOLD     -> hold normally
            #
            # TIME-OF-DAY ADJUSTMENT: In the last 60 minutes of trading, be more aggressive.
            # Less time to recover means we should cut losers faster and protect winners harder.
            FULL_EXIT_THRESHOLD = 6.0
            REDUCE_THRESHOLD = self.min_conviction_threshold  # default 8.0, regime-adjusted

            try:
                from zoneinfo import ZoneInfo
                now_et = datetime.now(ZoneInfo("America/New_York"))
                from datetime import time as dt_time
                market_close_et = dt_time(16, 0)
                now_time = now_et.time()
                minutes_to_close = (
                    (market_close_et.hour * 60 + market_close_et.minute) -
                    (now_time.hour * 60 + now_time.minute)
                )
                if minutes_to_close < 60:
                    # Last hour: lower both thresholds by 1.0 to exit earlier
                    FULL_EXIT_THRESHOLD += 1.0   # e.g. 6.0 -> 7.0
                    REDUCE_THRESHOLD = max(REDUCE_THRESHOLD - 1.0, FULL_EXIT_THRESHOLD + 0.1)
                    logger.info(
                        f"Late-day conviction adjustment ({minutes_to_close:.0f} min to close): "
                        f"FULL_EXIT>={FULL_EXIT_THRESHOLD:.1f}, REDUCE>={REDUCE_THRESHOLD:.1f}"
                    )
            except Exception:
                pass  # Timezone check failed - use defaults

            if new_conviction < FULL_EXIT_THRESHOLD or new_conviction < REDUCE_THRESHOLD:
                if symbol not in self.agent_opened_positions or not self.broker:
                    return {
                        "status": "updated",
                        "symbol": symbol,
                        "conviction": new_conviction,
                        "warning": f"Below threshold but no tracked position"
                    }
                try:
                    account_info = self.broker.get_account_info()
                    position = next(
                        (p for p in (account_info.positions or []) if p.symbol.upper() == symbol),
                        None
                    )
                    if not position or position.quantity <= 0:
                        return {
                            "status": "updated",
                            "symbol": symbol,
                            "conviction": new_conviction,
                            "note": "Below threshold but no open position found at broker"
                        }

                    current_qty = int(position.quantity)

                    if new_conviction < FULL_EXIT_THRESHOLD:
                        # Full exit - thesis completely broken
                        sell_qty = current_qty
                        action_label = "FULL EXIT"
                        logger.warning(
                            f"CONVICTION FULL EXIT: {symbol} conviction={new_conviction} < {FULL_EXIT_THRESHOLD} "
                            f"- thesis broken, selling all {sell_qty} shares"
                        )
                    else:
                        # Reduce to 50% - uncertain but not broken
                        target_qty = max(1, round(current_qty * 0.5))
                        sell_qty = current_qty - target_qty
                        action_label = "REDUCE 50%"
                        logger.warning(
                            f"CONVICTION REDUCTION: {symbol} conviction={new_conviction} in reduce zone "
                            f"({FULL_EXIT_THRESHOLD}-{REDUCE_THRESHOLD}) - selling {sell_qty} of {current_qty} shares (keeping {target_qty})"
                        )

                    if sell_qty <= 0:
                        return {"status": "updated", "symbol": symbol, "conviction": new_conviction, "note": "sell_qty=0, nothing to sell"}

                    # Cancel existing bracket orders (TP/SL) before selling
                    # Bracket orders lock all shares - must cancel before any sell
                    cancelled_brackets = []
                    try:
                        open_orders = self.broker.get_open_orders()
                        for o in open_orders:
                            o_symbol = getattr(o, 'symbol', '') or ''
                            o_side = str(getattr(o, 'side', '')).lower()
                            if o_symbol.upper() == symbol and 'sell' in o_side:
                                try:
                                    self.broker.cancel_order(o.order_id)
                                    cancelled_brackets.append(o.order_id)
                                    logger.info(f"Cancelled bracket order {o.order_id} for {symbol} (conviction {action_label})")
                                except Exception as ce:
                                    logger.warning(f"Could not cancel bracket {o.order_id} for {symbol}: {ce}")
                    except Exception as e:
                        logger.warning(f"Could not fetch open orders before conviction sell for {symbol}: {e}")

                    order = self.broker.place_order(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=sell_qty,
                        order_type=OrderType.MARKET
                    )
                    logger.info(f"Conviction {action_label} order placed for {symbol}: {sell_qty} shares")

                    # Re-add bracket protection for remaining shares (partial reduce only)
                    remaining_qty = current_qty - sell_qty
                    if remaining_qty > 0 and cancelled_brackets:
                        try:
                            import time as _time
                            _time.sleep(2)  # Brief pause for sell to process
                            self.scan_and_add_missing_bracket_orders(skip_trailing=True)
                            logger.info(f"Re-added bracket protection for {remaining_qty} remaining {symbol} shares")
                        except Exception as e:
                            logger.warning(f"Could not re-add bracket for {symbol} after conviction reduce: {e}")

                    return {
                        "status": "conviction_action_triggered",
                        "action": action_label,
                        "symbol": symbol,
                        "old_conviction": old_conviction,
                        "new_conviction": new_conviction,
                        "reason": reason,
                        "sold_qty": sell_qty,
                        "remaining_qty": remaining_qty,
                        "order_id": order.order_id if order else None
                    }
                except Exception as e:
                    logger.error(f"Error placing conviction action order for {symbol}: {e}")
                    return {"error": f"Conviction action failed: {e}", "conviction_stored": new_conviction}
            else:
                return {
                    "status": "updated",
                    "symbol": symbol,
                    "old_conviction": old_conviction,
                    "new_conviction": new_conviction,
                    "reason": reason
                }

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    def _check_pending_orders(self):
        """
        Check all pending LIMIT orders for fills. Called each scan cycle.

        - If filled: record position, set up tracking, queue bracket addition
        - If expired (>5 min): cancel the order, remove from pending dict
        - If cancelled/rejected by broker: remove from pending dict
        """
        if not self.pending_orders:
            return

        import time as _time
        now = _time.time()
        to_remove = []

        for order_id, data in list(self.pending_orders.items()):
            symbol = data['symbol']
            try:
                updated = self.broker.get_order_status(order_id)
                filled_qty = updated.filled_quantity if hasattr(updated, 'filled_quantity') else 0
                filled_price = updated.filled_price if hasattr(updated, 'filled_price') else None
                status = updated.status if hasattr(updated, 'status') else None
                status_str = status.name.lower() if hasattr(status, 'name') else str(status).lower()

                if 'cancelled' in status_str or 'canceled' in status_str or 'rejected' in status_str:
                    logger.info(f"Pending order {order_id} for {symbol} was {status_str} - removing from pending")
                    to_remove.append(order_id)
                    continue

                if filled_qty > 0:
                    # Order (fully or partially) filled - record the position
                    logger.info(f"Pending LIMIT order filled: {symbol} {filled_qty} shares" +
                                (f" at ${filled_price:.2f}" if filled_price else ""))
                    current_qty = self.agent_opened_positions.get(symbol, 0)
                    self.agent_opened_positions[symbol] = current_qty + filled_qty

                    actual_entry = filled_price if filled_price else data.get('entry_price')
                    if actual_entry:
                        self.agent_position_entry_prices[symbol] = actual_entry

                    take_profit = data.get('take_profit')
                    if take_profit:
                        self.agent_position_tp_targets[symbol] = take_profit

                    conviction = data.get('conviction_score')
                    if conviction:
                        self.agent_position_convictions[symbol] = conviction

                    if filled_qty >= data['requested_qty']:
                        to_remove.append(order_id)
                    else:
                        # Partial fill - update pending qty remaining
                        remaining = data['requested_qty'] - filled_qty
                        self.pending_orders[order_id]['requested_qty'] = remaining
                        logger.info(f"Partial fill on pending order {order_id}: {filled_qty}/{data['requested_qty']} {symbol} filled, {remaining} remaining")
                    continue

                # Check for timeout - cancel stale unfilled LIMIT orders
                age_sec = now - data.get('placed_at', now)
                if age_sec > self.PENDING_ORDER_TIMEOUT_SEC:
                    logger.warning(f"Pending LIMIT order {order_id} for {symbol} timed out after {age_sec:.0f}s (limit={self.PENDING_ORDER_TIMEOUT_SEC}s) - cancelling")
                    try:
                        self.broker.cancel_order(order_id)
                    except Exception as e:
                        logger.warning(f"Could not cancel timed-out pending order {order_id}: {e}")
                    to_remove.append(order_id)

            except Exception as e:
                logger.warning(f"Could not check pending order {order_id} for {symbol}: {e}")

        for order_id in to_remove:
            self.pending_orders.pop(order_id, None)

        if to_remove:
            logger.debug(f"Pending order cleanup: removed {len(to_remove)} order(s), {len(self.pending_orders)} still pending")

    def scan_and_add_missing_bracket_orders(self, skip_trailing: bool = False):
        """
        Scan existing positions and add missing take profit/stop loss orders.
        Also checks for capital protection violations and rebalances if needed.

        This ensures all positions have proper risk management, even if they were
        opened manually or in previous sessions without bracket orders.

        Args:
            skip_trailing: If True, skip _update_trailing_stops() at the end.
                           Set to True when called from update_trades(), which runs
                           trailing stops explicitly AFTER partial profits to avoid
                           placing an OCO that partial profits immediately cancels.
        """
        if not self.broker:
            logger.warning("No broker connected - cannot scan positions")
            return

        # FIRST: Reconcile agent dict with broker positions
        # This ensures we're working with accurate data
        self._reconcile_positions_with_broker()

        try:
            # Get current positions
            account_info = self.broker.get_account_info()
            if not account_info or not account_info.positions:
                logger.debug("No positions to scan for bracket orders")
                return

            # CRITICAL: Check if portfolio violates $25k protection rule and rebalance if needed
            self._check_and_rebalance_capital_protection(account_info)

            logger.info("Scanning positions for 1% loss rule and missing bracket orders...")

            for position in account_info.positions:
                symbol = position.symbol.upper()  # Normalize case for consistent tracking
                quantity = position.quantity

                # Skip if this is a forbidden symbol (long-term holdings)
                if symbol in self.forbidden_symbols:
                    logger.debug(f"Skipping {symbol} - forbidden symbol (pre-existing position)")
                    continue

                # Skip if position is being closed (negative quantity after sell)
                if quantity <= 0:
                    logger.debug(f"Skipping {symbol} - zero/negative quantity: {quantity}")
                    continue

                # CRITICAL: Check 1% loss rule for ALL positions (not just agent-opened)
                # This protects against manual positions or reconciliation issues
                logger.info(f"Checking 1% loss rule for {symbol}: {quantity} shares")

                # Get validated current price for 1% loss rule
                try:
                    validated_quote = self._get_validated_quote(symbol)
                    current_price = validated_quote["consensus_price"]
                    confidence = validated_quote["confidence"]

                    if confidence < 0.5:
                        logger.warning(f"Skipping 1% loss check for {symbol} - low confidence quote ({confidence:.1f})")
                        continue

                    # Use our tracked entry price first, fall back to broker's avg_entry_price
                    avg_entry = self.agent_position_entry_prices.get(symbol)
                    if not avg_entry:
                        avg_entry = position.avg_entry_price if hasattr(position, 'avg_entry_price') and position.avg_entry_price else None

                    # Skip P&L check if we don't have a valid entry price (just bought, not yet recorded)
                    if not avg_entry or avg_entry <= 0:
                        logger.debug(f"{symbol}: No entry price available yet, skipping P&L check")
                        continue

                    # Calculate current P&L percentage
                    pnl_percent = ((current_price - avg_entry) / avg_entry) * 100

                    # Calculate ATR-based emergency stop threshold (2x ATR, min 1%, max 3%)
                    emergency_stop_percent = self._get_atr_stop_percent(symbol, current_price, multiplier=2.0)

                    logger.info(f"{symbol}: Current=${current_price:.2f}, Entry=${avg_entry:.2f}, P&L={pnl_percent:.2f}%, Emergency Stop={emergency_stop_percent:.1f}%")

                    # ATR-BASED LOSS RULE - Cut losses based on stock volatility
                    if pnl_percent <= -emergency_stop_percent:
                        # DOUBLE-SELL PROTECTION: Check if we recently sold this symbol
                        if not self._check_and_record_sell(symbol, cooldown_seconds=15):
                            logger.warning(f"Skipping emergency sell for {symbol} - recently sold")
                            continue

                        logger.warning(f"LOSS PROTECTION: {symbol} down {pnl_percent:.2f}% (>= -{emergency_stop_percent:.1f}% ATR threshold) - executing protective sell")

                        # Step 1: Cancel ALL existing orders FIRST to free up shares
                        logger.info(f"Cancelling existing orders for {symbol} to free up shares for protective sell")
                        self._cancel_all_orders_for_symbol(symbol)
                        
                        # Wait for cancellations to process
                        import time
                        time.sleep(1.5)  # 1.5 second delay for order cancellations
                        
                        # CRITICAL: Verify actual position size before emergency sell
                        actual_position_qty = 0
                        try:
                            # Get fresh account info to verify actual position size
                            fresh_account = self.broker.get_account_info()
                            if fresh_account and fresh_account.positions:
                                for pos in fresh_account.positions:
                                    if pos.symbol.upper() == symbol.upper():
                                        actual_position_qty = pos.quantity
                                        break
                                        
                            if actual_position_qty <= 0:
                                logger.error(f"CRITICAL: Cannot emergency sell {symbol} - no position found (actual qty: {actual_position_qty})")
                                logger.error(f"Position may have been already sold or never existed - skipping emergency sell")
                                continue
                                
                            if quantity > actual_position_qty:
                                logger.warning(f"POSITION MISMATCH: Requested sell {quantity} shares of {symbol}, but only own {actual_position_qty}")
                                quantity = actual_position_qty  # Use actual position size
                                logger.info(f"Adjusted emergency sell quantity to {actual_position_qty} shares")
                                
                        except Exception as e:
                            logger.error(f"CRITICAL: Could not verify position size for {symbol}: {e}")
                            logger.error(f"Proceeding with emergency sell but this may create overselling!")

                        # Now place immediate market sell order (shares should be available)
                        first_order_id = None  # Track first order for double-sell prevention
                        try:
                            emergency_sell = self.broker.place_order(
                                symbol=symbol,
                                side=OrderSide.SELL,
                                quantity=quantity,
                                order_type=OrderType.MARKET
                            )
                            first_order_id = emergency_sell.order_id

                            # Check if order was successful by getting order status
                            # Wait longer for market orders - they can take 1-2 seconds to fill
                            import time
                            time.sleep(1.5)  # Increased from 0.5s - market orders need time
                            order_status = self.broker.get_order_status(emergency_sell.order_id)

                            # Check if order was filled - handle both string and enum status
                            status_val = order_status.status if hasattr(order_status, 'status') else None
                            status_str = status_val.name.lower() if hasattr(status_val, 'name') else str(status_val).lower()
                            is_filled = 'filled' in status_str or 'complete' in status_str or 'executed' in status_str

                            if is_filled:
                                logger.warning(f"[EMERGENCY SELL EXECUTED] {symbol}: {quantity} shares at ${current_price:.2f} (1% loss rule)")

                                # CRITICAL: Record as loser to prevent rebuy within 12 hours
                                loss_pct = pnl_percent
                                self.recently_closed_losers[symbol] = {
                                    'timestamp': datetime.now(),
                                    'entry': avg_entry,
                                    'exit': current_price,
                                    'pnl_percent': loss_pct
                                }
                                logger.warning(f"LOSER RECORDED: {symbol} closed at {loss_pct:.1f}% loss - BLOCKED from rebuying for 12 hours")

                                # Update tracking (whether it was agent-opened or not)
                                if symbol in self.agent_opened_positions:
                                    del self.agent_opened_positions[symbol]
                                    logger.info(f"Removed {symbol} from agent positions tracking")

                                # Record trade
                                self.log_trade(
                                    symbol=symbol,
                                    side="sell",
                                    quantity=quantity,
                                    price=current_price,
                                    reason="1_percent_loss_rule_emergency"
                                )

                                # Save state immediately
                                self._save_position_state()

                                continue  # Skip bracket order logic since we're selling
                            else:
                                # Order placed but not filled yet - wait a bit more before considering it failed
                                logger.warning(f"EMERGENCY SELL ORDER PENDING: {emergency_sell.order_id} - Status: {status_str}")
                                logger.info(f"Waiting additional 2 seconds for order to fill...")
                                time.sleep(2.0)

                                # Re-check order status
                                order_status = self.broker.get_order_status(emergency_sell.order_id)
                                status_val = order_status.status if hasattr(order_status, 'status') else None
                                status_str = status_val.name.lower() if hasattr(status_val, 'name') else str(status_val).lower()
                                is_filled = 'filled' in status_str or 'complete' in status_str or 'executed' in status_str

                                if is_filled:
                                    logger.warning(f"[EMERGENCY SELL EXECUTED AFTER WAIT] {symbol}: {quantity} shares at ${current_price:.2f}")

                                    loss_pct = pnl_percent
                                    self.recently_closed_losers[symbol] = {
                                        'timestamp': datetime.now(),
                                        'entry': avg_entry,
                                        'exit': current_price,
                                        'pnl_percent': loss_pct
                                    }
                                    logger.warning(f"LOSER RECORDED: {symbol} closed at {loss_pct:.1f}% loss - BLOCKED from rebuying for 12 hours")

                                    if symbol in self.agent_opened_positions:
                                        del self.agent_opened_positions[symbol]
                                        logger.info(f"Removed {symbol} from agent positions tracking")

                                    self.log_trade(
                                        symbol=symbol,
                                        side="sell",
                                        quantity=quantity,
                                        price=current_price,
                                        reason="1_percent_loss_rule_emergency"
                                    )
                                    self._save_position_state()
                                    continue
                                else:
                                    # Still pending after 3.5 seconds total - this is unusual
                                    logger.error(f"EMERGENCY SELL STILL PENDING after 3.5s: {emergency_sell.order_id}")
                                    raise Exception(f"Emergency sell order stuck pending - Status: {status_str}")

                        except Exception as e:
                            logger.error(f"CRITICAL FAILURE: Could not execute 1% loss sell for {symbol} after cancelling orders: {e}")
                            logger.error(f"Position {symbol} is down {pnl_percent:.2f}% but emergency sell failed even after freeing shares!")

                            # CRITICAL: Before placing another order, check if first order actually filled
                            if first_order_id:
                                logger.info(f"Checking if first order {first_order_id} eventually filled...")
                                try:
                                    order_status = self.broker.get_order_status(first_order_id)
                                    status_val = order_status.status if hasattr(order_status, 'status') else None
                                    status_str = status_val.name.lower() if hasattr(status_val, 'name') else str(status_val).lower()
                                    if 'filled' in status_str or 'complete' in status_str or 'executed' in status_str:
                                        logger.warning(f"First order {first_order_id} DID fill - NOT placing duplicate order!")

                                        loss_pct = pnl_percent
                                        self.recently_closed_losers[symbol] = {
                                            'timestamp': datetime.now(),
                                            'entry': avg_entry,
                                            'exit': current_price,
                                            'pnl_percent': loss_pct
                                        }
                                        if symbol in self.agent_opened_positions:
                                            del self.agent_opened_positions[symbol]
                                        self.log_trade(symbol=symbol, side="sell", quantity=quantity, price=current_price, reason="1_percent_loss_rule_emergency")
                                        self._save_position_state()
                                        continue  # First order filled, skip retry
                                except Exception as check_err:
                                    logger.warning(f"Could not verify first order status: {check_err}")

                            # Check if we still have shares before placing another order
                            try:
                                positions = self.broker.get_positions()
                                current_position = None
                                for p in positions:
                                    if p.symbol == symbol:
                                        current_position = p
                                        break

                                if current_position is None or current_position.quantity <= 0:
                                    logger.warning(f"No {symbol} position found - first order likely filled, NOT placing duplicate!")
                                    if symbol in self.agent_opened_positions:
                                        del self.agent_opened_positions[symbol]
                                    self._save_position_state()
                                    continue

                                # Update quantity to actual remaining shares
                                remaining_qty = int(current_position.quantity)
                                logger.info(f"Still have {remaining_qty} shares of {symbol}, proceeding with retry")
                                quantity = remaining_qty

                            except Exception as pos_err:
                                logger.warning(f"Could not verify position: {pos_err} - skipping retry to prevent double-sell")
                                continue

                            # Only retry if we confirmed we still have shares
                            try:
                                logger.warning(f"Final attempt: emergency market sell for {symbol} - {quantity} shares")
                                emergency_sell_retry = self.broker.place_order(
                                    symbol=symbol,
                                    side=OrderSide.SELL,
                                    quantity=quantity,
                                    order_type=OrderType.MARKET
                                )
                                logger.error(f"[EMERGENCY SELL FINAL ATTEMPT] {symbol}: {quantity} shares - Order ID: {emergency_sell_retry.order_id}")

                                # Update tracking regardless - we placed the sell order
                                if symbol in self.agent_opened_positions:
                                    del self.agent_opened_positions[symbol]
                                    logger.info(f"Removed {symbol} from agent positions tracking (emergency order placed)")

                                # Record trade attempt
                                self.log_trade(
                                    symbol=symbol,
                                    side="sell",
                                    quantity=quantity,
                                    price=current_price,
                                    reason="1_percent_loss_rule_emergency_retry"
                                )

                                # Save state
                                self._save_position_state()

                                continue  # Skip bracket order logic

                            except Exception as retry_error:
                                logger.error(f"ABSOLUTE FAILURE: Final emergency sell attempt failed for {symbol}: {retry_error}")
                                logger.error(f"CRITICAL: {symbol} position still exists and is down {pnl_percent:.2f}% - MANUAL INTERVENTION REQUIRED")
                    else:
                        logger.debug(f"{symbol} P&L {pnl_percent:.2f}% - within acceptable range")
                    
                except Exception as e:
                    logger.error(f"CRITICAL: Could not get quote/price for {symbol}: {e}")
                    logger.error(f"Cannot check 1% loss rule for {symbol} - price data unavailable!")
                    continue

                # Only proceed with bracket order logic if position wasn't sold
                # Check if this position was opened by the agent
                if symbol not in self.agent_opened_positions:
                    logger.debug(f"Position {symbol} not opened by agent - skipping bracket order management")
                    continue

                # BRACKET ORDER COOLDOWN: Prevent excessive cancel/replace cycles
                # Only update brackets if: price moved >2% OR >30 min since last update
                BRACKET_COOLDOWN_MINUTES = 30
                BRACKET_PRICE_CHANGE_THRESHOLD = 0.02  # 2%

                if symbol in self.agent_bracket_order_updates:
                    last_update = self.agent_bracket_order_updates[symbol]
                    last_timestamp = last_update.get('timestamp')
                    last_price = last_update.get('price', 0)

                    if last_timestamp and last_price > 0:
                        minutes_since = (datetime.now() - last_timestamp).total_seconds() / 60
                        price_change_pct = abs(current_price - last_price) / last_price

                        # Skip if within cooldown period AND price hasn't moved significantly
                        if minutes_since < BRACKET_COOLDOWN_MINUTES and price_change_pct < BRACKET_PRICE_CHANGE_THRESHOLD:
                            logger.debug(f"Position {symbol}: Bracket cooldown active ({minutes_since:.1f} min, {price_change_pct*100:.2f}% price change)")
                            continue

                # Check for existing take profit and stop loss orders
                existing_tp_sl = self._check_existing_bracket_orders(symbol, quantity)

                if existing_tp_sl['has_take_profit'] and existing_tp_sl['has_stop_loss']:
                    logger.debug(f"Position {symbol}: Complete bracket orders already exist")
                    continue

                # FIXED: Only cancel orders if we have NO bracket orders at all
                # If we have at least one (SL or TP), preserve it - don't cancel working orders
                # This prevents the cancel-and-replace cycle that was happening every scan
                has_any_protection = existing_tp_sl['has_stop_loss'] or existing_tp_sl['has_take_profit']

                if not has_any_protection:
                    # No bracket orders exist - cancel any stray sell orders before placing new ones
                    try:
                        if hasattr(self.broker, 'get_open_orders'):
                            try:
                                all_orders = self.broker.get_open_orders()
                                symbol_orders = [order for order in all_orders if order.symbol.upper() == symbol.upper()]
                            except Exception as e:
                                logger.warning(f"Failed to get open orders: {e}")
                                symbol_orders = []
                        else:
                            try:
                                all_orders = self.broker.get_orders(symbol=symbol)
                                def is_open_order(order):
                                    if not hasattr(order, 'status'):
                                        return False
                                    status = order.status
                                    status_str = status.name.lower() if hasattr(status, 'name') else str(status).lower()
                                    return 'open' in status_str or 'pending' in status_str or 'partial' in status_str
                                symbol_orders = [order for order in all_orders if is_open_order(order)]
                            except Exception as e:
                                logger.warning(f"Failed to get orders for {symbol}: {e}")
                                symbol_orders = []

                        sell_orders = [order for order in symbol_orders if order.side == OrderSide.SELL]

                        if sell_orders:
                            logger.info(f"Position {symbol}: No bracket orders - canceling {len(sell_orders)} stray SELL orders")
                            for order in sell_orders:
                                try:
                                    self.broker.cancel_order(order.order_id)
                                    logger.info(f"[OK] Canceled stray order {order.order_id} for {symbol}")
                                except Exception as e:
                                    logger.warning(f"Could not cancel order {order.order_id}: {e}")
                            import time
                            time.sleep(1.0)

                    except Exception as e:
                        logger.error(f"Error canceling existing orders for {symbol}: {e}")
                else:
                    # Has at least one bracket order - preserve it, just log what's missing
                    missing = []
                    if not existing_tp_sl['has_stop_loss']:
                        missing.append("SL")
                    if not existing_tp_sl['has_take_profit']:
                        missing.append("TP")
                    logger.debug(f"Position {symbol}: Has protection, missing {', '.join(missing)} - will try to add without canceling")

                # BROKER LIMITATION FIX: Many brokers don't allow multiple sell orders for same shares
                # Strategy: Only place ONE protective order at a time, prioritize stop loss for risk management
                logger.info(f"Position {symbol}: Adding protective orders (broker-compatible approach)")

                # Calculate ATR-based TP/SL levels
                atr_stop_percent = self._get_atr_stop_percent(symbol, current_price, multiplier=1.5)
                take_profit_price = current_price * 1.03  # 3% profit target
                stop_loss_price = current_price * (1 - atr_stop_percent / 100)  # ATR-based stop loss

                # Round to 2 decimal places
                take_profit_price = round(take_profit_price, 2)
                stop_loss_price = round(stop_loss_price, 2)

                logger.info(f"Calculated levels for {symbol}: TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f} (ATR stop: {atr_stop_percent:.1f}%)")

                missing_orders = []

                if not existing_tp_sl['has_stop_loss'] or not existing_tp_sl['has_take_profit']:
                    # Place both as an OCO order - atomic pair, avoids "shares held by existing orders" error
                    try:
                        logger.info(f"Placing OCO bracket for {symbol}: qty={quantity}, TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f}")
                        oco_order = self.broker.place_oco_order(
                            symbol=symbol,
                            quantity=quantity,
                            take_profit_price=take_profit_price,
                            stop_loss_price=stop_loss_price
                        )
                        missing_orders.append(f"OCO: {oco_order.order_id}")
                        logger.info(f"[OK] Added OCO bracket order: {oco_order.order_id} (TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f})")

                    except Exception as e:
                        error_msg = str(e).lower()
                        if "insufficient qty" in error_msg or "40310000" in str(e):
                            logger.warning(f"BROKER LIMITATION: Cannot place OCO bracket for {symbol} - shares held by existing orders")
                            logger.info(f"SOLUTION: 1% loss rule will handle emergency exits")
                        else:
                            logger.error(f"CRITICAL: Failed to add OCO bracket order for {symbol}: {e}")
                else:
                    logger.info(f"Position {symbol}: Already has both TP and SL orders")

                # FALLBACK PROTECTION: If no orders could be placed, ensure 1% loss rule will protect
                if not missing_orders and not existing_tp_sl['has_stop_loss'] and not existing_tp_sl['has_take_profit']:
                    logger.warning(f"FALLBACK PROTECTION: {symbol} has no protective orders due to broker limitations")
                    logger.warning(f"RELYING ON: 1% loss rule for emergency protection (will sell if position drops 1%)")

                if missing_orders:
                    logger.info(f"Position {symbol}: Added protective orders - {', '.join(missing_orders)}")
                    # Record bracket order update to prevent churn (cooldown tracking)
                    self.agent_bracket_order_updates[symbol] = {
                        'timestamp': datetime.now(),
                        'price': current_price,
                        'orders_added': missing_orders
                    }

        except Exception as e:
            logger.error(f"Error scanning positions for bracket orders: {e}")

        # Trailing stops run AFTER partial profits (in update_trades) to avoid placing an OCO
        # that partial profits immediately cancels. Callers that need trailing stops here
        # (e.g. standalone scan not going through update_trades) pass skip_trailing=False.
        if not skip_trailing:
            self._update_trailing_stops()

    def _cancel_all_orders_for_symbol(self, symbol: str):
        """Cancel all open orders for a given symbol."""
        try:
            if hasattr(self.broker, 'get_open_orders'):
                try:
                    all_orders = self.broker.get_open_orders()
                    symbol_orders = [order for order in all_orders if order.symbol.upper() == symbol.upper()]
                except Exception as e:
                    logger.warning(f"Failed to get open orders for {symbol}: {e}")
                    return
            else:
                try:
                    symbol_orders = self.broker.get_orders(symbol=symbol)
                    # Filter for open orders - handle both string and enum status
                    def is_open_order(order):
                        if not hasattr(order, 'status'):
                            return False
                        status = order.status
                        status_str = status.name.lower() if hasattr(status, 'name') else str(status).lower()
                        return 'open' in status_str or 'pending' in status_str or 'partial' in status_str
                    symbol_orders = [order for order in symbol_orders if is_open_order(order)]
                except Exception as e:
                    logger.warning(f"Failed to get orders for {symbol}: {e}")
                    return

            if symbol_orders:
                logger.info(f"Cancelling {len(symbol_orders)} open orders for {symbol}")
                for order in symbol_orders:
                    try:
                        self.broker.cancel_order(order.order_id)
                        logger.info(f"Cancelled order {order.order_id} for {symbol}")
                    except Exception as e:
                        logger.warning(f"Could not cancel order {order.order_id}: {e}")
            else:
                logger.debug(f"No open orders to cancel for {symbol}")
                
        except Exception as e:
            logger.error(f"Error cancelling orders for {symbol}: {e}")

    def _check_existing_bracket_orders(self, symbol: str, quantity: int) -> dict:
        """
        Check if a position already has take profit and stop loss orders.

        Returns:
            dict with 'has_take_profit' and 'has_stop_loss' boolean flags
        """

        has_take_profit = False
        has_stop_loss = False

        try:
            # Try to get open orders from broker
            # This is broker-specific, so we'll try a generic approach

            if hasattr(self.broker, 'get_open_orders'):
                # If broker has get_open_orders method, use it
                try:
                    open_orders = self.broker.get_open_orders()
                except:
                    open_orders = []
            else:
                # Fallback: try to get orders by symbol if method exists
                try:
                    open_orders = self.broker.get_orders(symbol=symbol, status='open')
                except:
                    open_orders = []

            # Filter orders for this symbol
            symbol_orders = [order for order in open_orders if order.symbol.upper() == symbol.upper()]

            # Check for take profit (LIMIT SELL) and stop loss (STOP SELL) orders
            # Be more flexible with quantity matching - allow orders with similar quantities
            # (in case of partial fills or rounding differences)
            for order in symbol_orders:
                if order.side == OrderSide.SELL:
                    # Allow quantity to be within 10% of expected quantity
                    quantity_match = abs(order.quantity - quantity) / max(quantity, 1) <= 0.1

                    if quantity_match:
                        if order.order_type == OrderType.LIMIT:
                            has_take_profit = True
                        elif order.order_type == OrderType.STOP:
                            has_stop_loss = True

            logger.debug(f"Found {len(symbol_orders)} open orders for {symbol}: TP={has_take_profit}, SL={has_stop_loss}")

        except Exception as e:
            logger.warning(f"Could not check existing bracket orders for {symbol}: {e}")
            # Return False to be safe - assume orders are missing
            return {
                'has_take_profit': False,
                'has_stop_loss': False
            }

        return {
            'has_take_profit': has_take_profit,
            'has_stop_loss': has_stop_loss
        }

    def _check_partial_profit_opportunities(self):
        """
        Check positions for partial profit-taking opportunities based on conviction.

        Strategy:
        - High conviction (8-10): Take 25% profit at 75% of way to TP (more patient)
        - Medium conviction (5-7): Take 33% profit at 50% of way to TP
        - Lower conviction (1-4): Take 50% profit at 40% of way to TP (more aggressive)

        This prevents "watching a winner turn into a loser" scenario.
        """
        if not self.broker:
            return

        # Ensure position tracking is synced with broker before checking
        self._reconcile_positions_with_broker()

        try:
            account_info = self.broker.get_account_info()
            if not account_info or not account_info.positions:
                return

            logger.info("Checking positions for partial profit opportunities...")

            for position in account_info.positions:
                symbol = position.symbol.upper()
                quantity = position.quantity

                # Skip forbidden symbols and zero positions
                if symbol in self.forbidden_symbols or quantity <= 0:
                    continue

                # Skip if not tracked by agent
                if symbol not in self.agent_opened_positions:
                    continue

                # Check cooldown for partial profits (15 minutes between takes)
                PARTIAL_PROFIT_COOLDOWN_MINUTES = 15  # Reduced from 30 to take profits more frequently
                RAPID_GAINER_THRESHOLD = 0.03  # 3% gain bypasses cooldown (rapid gainer)

                # Pre-check: Get current price to detect rapid gainers
                try:
                    entry_price_check = self.agent_position_entry_prices.get(symbol)
                    if entry_price_check:
                        validated_quote_check = self._get_validated_quote(symbol)
                        current_price_check = validated_quote_check["consensus_price"]
                        quick_pnl = (current_price_check - entry_price_check) / entry_price_check
                        is_rapid_gainer = quick_pnl >= RAPID_GAINER_THRESHOLD
                    else:
                        is_rapid_gainer = False
                except Exception:
                    is_rapid_gainer = False

                if symbol in self.agent_position_partial_profits:
                    pp_info = self.agent_position_partial_profits[symbol]
                    last_taken_str = pp_info.get('timestamp')
                    if last_taken_str:
                        try:
                            last_taken = datetime.fromisoformat(last_taken_str)
                            minutes_since = (datetime.now() - last_taken).total_seconds() / 60
                            if minutes_since < PARTIAL_PROFIT_COOLDOWN_MINUTES:
                                # RAPID GAINER BYPASS: Skip cooldown for fast movers
                                if is_rapid_gainer:
                                    logger.info(f"{symbol}: RAPID GAINER (+{quick_pnl*100:.1f}%) - bypassing cooldown to lock in profits!")
                                else:
                                    logger.debug(f"{symbol}: Partial profit cooldown active ({minutes_since:.1f} min since last take)")
                                    continue
                            else:
                                logger.info(f"{symbol}: Partial profit cooldown expired ({minutes_since:.1f} min), can take profits again")
                        except (ValueError, TypeError):
                            pass  # Invalid timestamp, continue with check

                # Get entry price and TP target
                entry_price = self.agent_position_entry_prices.get(symbol)
                tp_target = self.agent_position_tp_targets.get(symbol)

                if not entry_price or not tp_target:
                    # Try to get from position average entry
                    if hasattr(position, 'avg_entry_price') and position.avg_entry_price:
                        entry_price = position.avg_entry_price
                        # Estimate TP as 3% above entry if not tracked
                        if not tp_target:
                            tp_target = entry_price * 1.03
                    else:
                        logger.debug(f"{symbol}: No entry/TP data for partial profit check")
                        continue

                # Get validated current price for partial profit calculations
                try:
                    validated_quote = self._get_validated_quote(symbol)
                    current_price = validated_quote["consensus_price"]
                    confidence = validated_quote["confidence"]

                    # Skip if confidence is too low for profit-taking decisions
                    if confidence < 0.6:
                        logger.debug(f"Skipping partial profit check for {symbol} - low confidence ({confidence:.1f})")
                        continue
                except Exception as e:
                    logger.warning(f"Could not get validated quote for {symbol}: {e}")
                    continue

                # Calculate progress toward TP (0% = entry, 100% = TP target)
                price_range = tp_target - entry_price
                if price_range <= 0:
                    continue  # Invalid TP (below entry)

                current_profit = current_price - entry_price

                # CRITICAL: Skip if position is actually losing (below entry)
                # This prevents false triggers when TP target is corrupted
                if current_profit <= 0:
                    continue  # Position is losing, no partial profit to take

                # Additional safety check: ensure P&L percentage is positive
                pnl_percent = (current_profit / entry_price) * 100
                if pnl_percent <= 0:
                    continue  # Position is losing, no partial profit to take
                
                progress_percent = (current_profit / price_range) * 100

                if progress_percent <= 0:
                    continue  # Not in profit

                # Get conviction score (default to 5 if unknown)
                conviction = self.agent_position_convictions.get(symbol, 5)

                # DYNAMIC TIERED PROFIT TAKING
                # Tiers are conviction- and regime-aware:
                #   High conviction (>= 8.5): let winners run - take less, later
                #   Low conviction (<= 6.5) or high-VIX: trim fast - take more, sooner
                #   Default: balanced tiers
                pnl_pct = ((current_price - entry_price) / entry_price) * 100

                # Determine regime volatility for tier selection
                regime_vix = 'medium'
                if self.market_regime:
                    regime_vix = self.market_regime.get('volatility_regime', 'medium')
                    regime_name = self.market_regime.get('regime', '')
                    if regime_name in ('large_gap_down', 'large_gap_up'):
                        regime_vix = 'high'

                # Select tier set based on conviction + regime
                if conviction >= 8.5 and regime_vix not in ('high', 'extreme'):
                    # High conviction, calm market - let it run
                    tier_thresholds = [(5.5, 0.40, "TIER3_5.5PCT"), (3.5, 0.25, "TIER2_3.5PCT"), (2.0, 0.15, "TIER1_2PCT")]
                elif conviction <= 6.5 or regime_vix in ('high', 'extreme'):
                    # Low conviction or high volatility - trim fast, protect gains
                    tier_thresholds = [(3.5, 0.60, "TIER3_3.5PCT"), (2.0, 0.40, "TIER2_2PCT"), (1.0, 0.30, "TIER1_1PCT")]
                else:
                    # Default balanced tiers
                    tier_thresholds = [(4.0, 0.50, "TIER3_4PCT"), (2.5, 0.30, "TIER2_2.5PCT"), (1.5, 0.20, "TIER1_1.5PCT")]

                # Find which tier fires (highest threshold first)
                sell_percentage = 0.0
                tier_label = ""
                for threshold, pct, label in tier_thresholds:
                    if pnl_pct >= threshold:
                        sell_percentage = pct
                        tier_label = label
                        break

                if not tier_label:
                    continue  # Not at profit taking threshold

                # PARTIAL PROFIT TRIGGER - we already passed the threshold check above
                logger.info(f"PARTIAL PROFIT TRIGGER for {symbol}: "
                           f"P&L {pnl_pct:.2f}% - {tier_label} "
                           f"(selling {sell_percentage*100:.0f}%)")

                # Calculate quantity to sell
                qty_to_sell = int(quantity * sell_percentage)
                if qty_to_sell < 1:
                    qty_to_sell = 1  # Sell at least 1 share

                # Don't sell everything - keep at least 1 share
                remaining_qty = quantity - qty_to_sell
                if remaining_qty < 1:
                    qty_to_sell = quantity - 1
                    remaining_qty = 1

                if qty_to_sell < 1:
                    logger.debug(f"{symbol}: Only 1 share, skipping partial profit")
                    continue

                logger.info(f"Taking partial profits on {symbol}: Selling {qty_to_sell} of {quantity} shares "
                           f"({sell_percentage*100:.0f}%) at ${current_price:.2f}")

                # Execute partial profit sale
                try:
                    # First cancel existing TP/SL orders (they're for full quantity)
                    self._cancel_all_orders_for_symbol(symbol)

                    import time
                    time.sleep(0.5)  # Wait for cancellations

                    # Place partial profit sell order
                    partial_sell = self.broker.place_order(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=qty_to_sell,
                        order_type=OrderType.MARKET
                    )

                    logger.info(f"[PARTIAL PROFIT SOLD] {symbol}: {qty_to_sell} shares at ~${current_price:.2f} "
                               f"(Order ID: {partial_sell.order_id})")

                    # Record the partial profit
                    self.agent_position_partial_profits[symbol] = {
                        'taken': True,
                        'qty_sold': qty_to_sell,
                        'price': current_price,
                        'timestamp': datetime.now().isoformat(),
                        'progress_at_trigger': progress_percent,
                        'conviction': conviction
                    }

                    # Update position tracking
                    self.agent_opened_positions[symbol] = remaining_qty

                    # Log the trade
                    self.log_trade(
                        symbol=symbol,
                        side="sell",
                        quantity=qty_to_sell,
                        price=current_price,
                        reason=f"partial_profit_{tier_label.lower()}"
                    )

                    # [GROK CRITICAL FIX #1] Record partial exit to learning database
                    if self.learning_db:
                        try:
                            partial_pnl = (current_price - entry_price) * qty_to_sell
                            self.learning_db.record_partial_exit(
                                symbol=symbol,
                                exit_price=current_price,
                                exit_reason=f"partial_profit_{tier_label.lower()}",
                                qty_sold=qty_to_sell,
                                partial_pnl=partial_pnl,
                                remaining_qty=remaining_qty
                            )
                            logger.info(f"[AUDIT] Partial exit logged: {symbol} {qty_to_sell}@${current_price:.2f} P&L ${partial_pnl:.2f}")
                        except Exception as e:
                            logger.error(f"Failed to log partial exit for {symbol}: {e}")

                    # Place new bracket orders for remaining shares
                    time.sleep(0.5)  # Wait before placing new orders

                    # CRITICAL FIX: Reset stop to protect the gain already locked in.
                    # Old behavior: stop anchored to entry price, remaining shares could give back
                    # the entire move before the emergency stop fired.
                    # New behavior: after partial, reset HWM to partial price and set stop at
                    # partial_price - 1.5x ATR (tighter initial trail to protect the gain).
                    atr_sl_percent = self._get_atr_stop_percent(symbol, current_price, multiplier=1.5)
                    # Stop = partial sell price minus 1.5x ATR - protects at least some gain
                    partial_protect_stop = round(current_price * (1 - atr_sl_percent / 100), 2)
                    # Never set stop below entry (don't risk a loss on what was a winner)
                    stop_loss_price = max(partial_protect_stop, round(entry_price * 1.001, 2))
                    # Update high water mark to at least the partial sell price so trailing stop
                    # starts from here, not from entry
                    current_hwm = self.agent_position_high_water_marks.get(symbol, entry_price)
                    self.agent_position_high_water_marks[symbol] = max(current_hwm, current_price)
                    # Update tracked stop price so _update_trailing_stops sees the new floor
                    self.agent_position_sl_targets[symbol] = stop_loss_price
                    logger.info(
                        f"Post-partial stop reset for {symbol}: ${stop_loss_price:.2f} "
                        f"(partial at ${current_price:.2f}, ATR trail={atr_sl_percent:.1f}%)"
                    )

                    try:
                        # Place new OCO bracket for remaining shares (cancel+replace existing orders atomically)
                        # Using separate LIMIT + STOP orders fails: Schwab holds shares for first order,
                        # blocking the second with "shares held by existing orders" error.
                        if (tp_target is not None and tp_target > 0 and
                                stop_loss_price is not None and stop_loss_price > 0):
                            logger.info(f"Placing replacement OCO bracket for {remaining_qty} remaining shares: TP@${tp_target:.2f}, SL@${stop_loss_price:.2f}")
                            new_oco_order = self.broker.update_oco_order(
                                symbol=symbol,
                                quantity=remaining_qty,
                                take_profit_price=tp_target,
                                stop_loss_price=stop_loss_price
                            )
                            logger.info(f"[OK] Replacement OCO bracket placed: {new_oco_order.order_id}")
                        elif tp_target is not None and tp_target > 0:
                            logger.warning(f"No SL price for {symbol} - placing TP-only order for remaining {remaining_qty} shares")
                            self.broker.place_order(
                                symbol=symbol,
                                side=OrderSide.SELL,
                                quantity=remaining_qty,
                                order_type=OrderType.LIMIT,
                                limit_price=tp_target
                            )
                        else:
                            logger.warning(f"Skipping new bracket for {symbol}: tp_target={tp_target}, stop_loss_price={stop_loss_price}")

                    except Exception as bracket_error:
                        logger.warning(f"Could not place replacement OCO bracket for {symbol}: {bracket_error}")

                    # Save state
                    self._save_position_state()

                except Exception as e:
                    logger.error(f"Failed to take partial profits on {symbol}: {e}")

        except Exception as e:
            logger.error(f"Error checking partial profit opportunities: {e}")

    def _update_trailing_stops(self):
        """
        Update trailing stops for all open positions based on high water mark.

        After a position moves in our favor, trail the stop up so gains are protected.
        Stop only moves UP (tighter) - never loosens.

        Strategy: trail stop at 2x ATR below the highest price seen since entry.
        Called from scan_and_add_missing_bracket_orders() each cycle.
        """
        if not self.broker:
            return

        try:
            account_info = self.broker.get_account_info()
            if not account_info or not account_info.positions:
                return

            for position in account_info.positions:
                symbol = position.symbol.upper()

                # Only manage positions we opened
                if symbol not in self.agent_opened_positions:
                    continue

                entry_price = self.agent_position_entry_prices.get(symbol)
                if not entry_price:
                    continue

                # Get current price from position data
                current_price = getattr(position, 'market_value', None)
                if current_price:
                    qty = position.quantity or 1
                    current_price = current_price / qty if qty else None
                if not current_price:
                    current_price = getattr(position, 'last_price', None)
                if not current_price or current_price <= 0:
                    continue

                # Update high water mark
                hwm = self.agent_position_high_water_marks.get(symbol, entry_price)
                hwm = max(hwm, current_price)
                self.agent_position_high_water_marks[symbol] = hwm

                # Only trail if position is meaningfully in profit (>1% above entry)
                # Avoid tightening stop on minor fluctuations right after entry
                gain_pct = (hwm - entry_price) / entry_price * 100
                if gain_pct < 1.0:
                    continue

                # Calculate trailing stop price (2x ATR below high water mark)
                try:
                    atr_pct = self._get_atr_stop_percent(symbol, current_price, multiplier=2.0)
                    trail_distance = hwm * (atr_pct / 100)
                    new_stop = round(hwm - trail_distance, 2)
                except Exception:
                    # Fallback: 1.5% trail below HWM
                    new_stop = round(hwm * 0.985, 2)

                # Never trail below entry (don't introduce a loss stop where none existed)
                new_stop = max(new_stop, round(entry_price * 0.99, 2))

                # Only update if new stop is tighter than current stop
                current_stop = self.agent_position_sl_targets.get(symbol, 0)
                if new_stop <= current_stop:
                    continue

                # Check if there's actually a meaningful improvement (>$0.05)
                if new_stop - current_stop < 0.05:
                    continue

                logger.info(
                    f"Trailing stop update for {symbol}: ${current_stop:.2f} -> ${new_stop:.2f} "
                    f"(HWM=${hwm:.2f}, gain={gain_pct:.1f}%)"
                )

                # Place replacement OCO with updated stop
                try:
                    tp_target = self.agent_position_tp_targets.get(symbol)
                    qty = self.agent_opened_positions.get(symbol, position.quantity)

                    if tp_target and tp_target > 0 and qty > 0:
                        self.broker.update_oco_order(
                            symbol=symbol,
                            quantity=int(qty),
                            take_profit_price=tp_target,
                            stop_loss_price=new_stop
                        )
                        self.agent_position_sl_targets[symbol] = new_stop
                        logger.info(f"[OK] Trailing stop placed for {symbol}: SL=${new_stop:.2f}, TP=${tp_target:.2f}")
                    else:
                        logger.debug(f"Skipping trailing stop for {symbol}: tp_target={tp_target}, qty={qty}")

                except Exception as e:
                    logger.warning(f"Failed to place trailing stop for {symbol}: {e}")

        except Exception as e:
            logger.error(f"Error updating trailing stops: {e}")

    def _check_existing_moc_orders(self, symbol: str, quantity: int) -> list:
        """
        Check if a position has existing MOC (Market on Close) orders.

        Returns:
            List of MOC order IDs that should be cancelled
        """

        moc_order_ids = []

        try:
            # Try to get open orders from broker
            if hasattr(self.broker, 'get_open_orders'):
                try:
                    open_orders = self.broker.get_open_orders()
                    logger.debug(f"Retrieved {len(open_orders)} open orders from broker")
                except Exception as e:
                    logger.warning(f"Failed to get open orders: {e}")
                    open_orders = []
            else:
                # Fallback: try to get orders by symbol if method exists
                try:
                    open_orders = self.broker.get_orders(symbol=symbol, status='open')
                    logger.debug(f"Retrieved {len(open_orders)} orders for {symbol}")
                except Exception as e:
                    logger.warning(f"Failed to get orders for {symbol}: {e}")
                    open_orders = []

            # Filter orders for this symbol
            symbol_orders = [order for order in open_orders if order.symbol.upper() == symbol.upper()]
            logger.debug(f"Found {len(symbol_orders)} orders for symbol {symbol}")

            # Check for MOC orders - be more permissive with quantity matching
            for order in symbol_orders:
                logger.debug(f"Checking order {order.order_id}: side={order.side}, type={order.order_type}, qty={order.quantity}")

                # Check if this is a MOC order (more flexible matching)
                is_moc = False
                if hasattr(order, 'order_type'):
                    # Try different ways to identify MOC orders
                    if order.order_type == OrderType.MOC:
                        is_moc = True
                    elif str(order.order_type).upper() in ['MOC', 'MARKET_ON_CLOSE']:
                        is_moc = True
                    elif hasattr(order, 'time_in_force') and str(order.time_in_force).upper() == 'CLS':
                        is_moc = True

                if (order.side == OrderSide.SELL and is_moc):
                    # For MOC orders, quantity might not match exactly due to partial fills
                    # Include any MOC sell order for this symbol
                    moc_order_ids.append(order.order_id)
                    logger.debug(f"Found MOC order: {order.order_id} (qty: {order.quantity})")

            if moc_order_ids:
                logger.info(f"Found {len(moc_order_ids)} MOC orders for {symbol}: {moc_order_ids}")
            else:
                logger.debug(f"No MOC orders found for {symbol}")

        except Exception as e:
            logger.error(f"Could not check existing MOC orders for {symbol}: {e}")

        return moc_order_ids

    def _check_and_rebalance_capital_protection(self, account_info):
        """
        Check if portfolio violates $25k protection rule and rebalance if needed.
        
        IMPROVEMENT: Market regime awareness to avoid selling at bottoms.
        In capitulation (high VIX + downtrend), hold high-conviction positions longer.
        """
        try:
            total_account_value = account_info.portfolio_value
            
            # CRITICAL: Always enforce $25,000 minimum account protection
            MINIMUM_ACCOUNT_VALUE = 25000.0  # Hard-coded $25k minimum
            
            if self.capital_limits_enabled and self.base_capital > 0:
                # Use configured base capital if enabled
                effective_base_capital = self.base_capital
                logger.debug(f"Using configured base capital: ${effective_base_capital:.2f}")
            else:
                # Always enforce $25k minimum even if capital limits disabled
                effective_base_capital = MINIMUM_ACCOUNT_VALUE
                logger.debug(f"Using default minimum capital: ${effective_base_capital:.2f}")
            
            # Calculate how close we are to the limit
            buffer_amount = total_account_value - effective_base_capital
            buffer_percent = (buffer_amount / effective_base_capital) * 100 if effective_base_capital > 0 else 0
            
            logger.info(f"CAPITAL PROTECTION CHECK: Account=${total_account_value:.2f}, Base=${effective_base_capital:.2f}, Buffer=${buffer_amount:.2f} ({buffer_percent:.1f}%)")
            
            # Get current market regime for liquidation strategy adjustment
            try:
                from analytics.market_regime import get_current_market_regime
                regime = get_current_market_regime(self.data_provider)
                regime_str = f"VIX={regime.get('vix', 'N/A')}, Vol={regime.get('volatility_regime', 'unknown')}, Trend={regime.get('trend_regime', 'unknown')}"
                logger.info(f"Market regime for liquidation: {regime_str}")
                
                # Capitulation bottom: high/extreme vol + downtrend = hold high-conviction longer
                is_capitulation_bottom = (
                    regime.get('volatility_regime') in ['high', 'extreme'] and
                    regime.get('trend_regime') == 'trending_down'
                )
            except Exception as e:
                regime = {}
                regime_str = "unknown (regime fetch failed)"
                is_capitulation_bottom = False
                logger.warning(f"Could not get market regime: {e}")
            
            # CRITICAL: If account is ALREADY below minimum, IMMEDIATE emergency liquidation
            if total_account_value <= effective_base_capital:
                deficit = effective_base_capital - total_account_value
                logger.error(f"CRITICAL PDT VIOLATION: Account ${total_account_value:.2f} is BELOW minimum ${effective_base_capital:.2f} by ${deficit:.2f}")
                logger.error(f"IMMEDIATE EMERGENCY LIQUIDATION REQUIRED - PDT RULE VIOLATED!")
                
                # Get sellable positions with regime-aware filtering
                sellable_positions = []
                if account_info.positions:
                    for pos in account_info.positions:
                        pos_symbol = pos.symbol.upper()
                        if pos_symbol in self.agent_opened_positions and pos.quantity > 0:
                            position_value = pos.quantity * pos.current_price if hasattr(pos, 'current_price') else 0
                            pnl_pct = getattr(pos, 'unrealized_pnl_percent', 0)
                            conviction = self.agent_position_convictions.get(pos_symbol, 5)
                            
                            # Regime-aware filtering to avoid selling at bottoms
                            should_sell = True
                            if is_capitulation_bottom:
                                # Hold high-conviction positions unless deep losses
                                if conviction >= 7 and pnl_pct > -10:
                                    logger.info(f"HOLDING {pos_symbol} during capitulation bottom (conviction {conviction}, P&L {pnl_pct:+.1f}%)")
                                    should_sell = False
                            
                            if should_sell:
                                sellable_positions.append({
                                    'symbol': pos_symbol,
                                    'quantity': pos.quantity,
                                    'current_price': getattr(pos, 'current_price', 0),
                                    'value': position_value,
                                    'unrealized_pnl': getattr(pos, 'unrealized_pnl', 0),
                                    'unrealized_pnl_percent': pnl_pct,
                                    'conviction': conviction
                                })
                
                if not sellable_positions:
                    # FALLBACK: If agent_opened_positions is empty/corrupted but positions exist at broker,
                    # sell ALL broker positions (except forbidden) as emergency measure
                    logger.error("CRITICAL: No positions in agent_opened_positions - checking broker positions as fallback!")

                    if account_info.positions:
                        for pos in account_info.positions:
                            # Skip forbidden symbols (pre-existing long-term holdings)
                            if pos.symbol in self.forbidden_symbols:
                                logger.warning(f"Skipping {pos.symbol} - forbidden symbol (won't sell)")
                                continue
                            if pos.quantity > 0:
                                position_value = pos.quantity * pos.current_price if hasattr(pos, 'current_price') else 0
                                conviction = self.agent_position_convictions.get(pos.symbol, 5)  # Default 5 if unknown
                                sellable_positions.append({
                                    'symbol': pos.symbol,
                                    'quantity': pos.quantity,
                                    'current_price': getattr(pos, 'current_price', 0),
                                    'value': position_value,
                                    'unrealized_pnl': getattr(pos, 'unrealized_pnl', 0),
                                    'conviction': conviction
                                })

                        if sellable_positions:
                            logger.error(f"FALLBACK: Found {len(sellable_positions)} broker positions to liquidate (not in agent tracking)")
                            # Also add these to agent tracking so subsequent sells work
                            for pos_info in sellable_positions:
                                self.agent_opened_positions[pos_info['symbol']] = pos_info['quantity']
                                logger.warning(f"Added {pos_info['symbol']} to agent tracking for emergency sell")

                    if not sellable_positions:
                        logger.error("CRITICAL: No sellable positions found at broker either - account below $25k with no way to recover!")
                        logger.error("MANUAL INTERVENTION REQUIRED IMMEDIATELY")
                        return

                # Sort by: 1) P&L ascending (losers first), 2) conviction ascending (low conviction first as tie-breaker)
                # This sells the worst performing AND lowest conviction positions first
                sellable_positions.sort(key=lambda x: (x['unrealized_pnl'], x.get('conviction', 5)))
                
                logger.error(f"EMERGENCY LIQUIDATION: Found {len(sellable_positions)} sellable positions")
                logger.error(f"SELLING ALL POSITIONS to restore account above ${effective_base_capital:.2f}")
                
                # Calculate target: Get back above minimum + 10% safety buffer
                target_value = effective_base_capital * 1.10  # 10% above minimum
                amount_needed = target_value - total_account_value
                
                logger.error(f"Target account value: ${target_value:.2f}, Need to raise: ${amount_needed:.2f}")
                
                # Sell ALL positions to restore capital protection
                amount_raised = 0
                positions_sold = 0
                
                for position in sellable_positions:
                    symbol = position['symbol']
                    quantity = position['quantity']

                    # DOUBLE-SELL PROTECTION
                    if not self._check_and_record_sell(symbol, cooldown_seconds=15):
                        logger.warning(f"Skipping emergency sell for {symbol} - recently sold")
                        continue

                    logger.error(f"EMERGENCY SELL: {symbol} - {quantity} shares (P&L: ${position['unrealized_pnl']:.2f})")

                    try:
                        # Cancel all existing orders for this symbol first
                        self._cancel_all_orders_for_symbol(symbol)
                        
                        # Wait for cancellations
                        import time
                        time.sleep(1.0)
                        
                        # Place emergency market sell order
                        emergency_order = self.broker.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=quantity,
                            order_type=OrderType.MARKET
                        )
                        
                        logger.warning(f"[EMERGENCY SELL EXECUTED] {symbol}: {quantity} shares - Order ID: {emergency_order.order_id}")
                        
                        # Update tracking
                        if symbol in self.agent_opened_positions:
                            del self.agent_opened_positions[symbol]
                        
                        # Record emergency trade
                        self.log_trade(
                            symbol=symbol,
                            side="sell",
                            quantity=quantity,
                            price=position['current_price'],
                            reason="emergency_pdt_violation_liquidation"
                        )
                        
                        amount_raised += position['value']
                        positions_sold += 1
                        
                        logger.error(f"Emergency liquidation progress: ${amount_raised:.2f} raised, {positions_sold} positions sold")
                        
                    except Exception as e:
                        logger.error(f"CRITICAL: Emergency sell failed for {symbol}: {e}")
                        continue
                
                # Save state after emergency liquidation
                self._save_position_state()
                
                if positions_sold > 0:
                    logger.error(f"EMERGENCY LIQUIDATION COMPLETE: Sold {positions_sold} positions, raised ~${amount_raised:.2f}")
                    logger.error(f"Account should now be restored above ${effective_base_capital:.2f} minimum")
                    logger.error(f"PDT VIOLATION RESOLVED - All positions liquidated to protect capital")
                else:
                    logger.error(f"EMERGENCY LIQUIDATION FAILED: Could not sell any positions")
                    logger.error(f"CRITICAL: Account remains below ${effective_base_capital:.2f} - MANUAL INTERVENTION REQUIRED")
                
                return  # Exit after handling critical violation
            
            # If we're within 5% of the minimum, start regime-aware emergency liquidation
            EMERGENCY_THRESHOLD_PERCENT = 5.0  # 5% buffer above minimum
            
            if buffer_percent <= EMERGENCY_THRESHOLD_PERCENT:
                logger.error(f"EMERGENCY: Account value ${total_account_value:.2f} is within {EMERGENCY_THRESHOLD_PERCENT}% of minimum ${effective_base_capital:.2f}")
                logger.error(f"Regime-aware liquidation: {regime_str} - { 'Partial hold strategy' if is_capitulation_bottom else 'Full liquidation'}")
                
                # Get sellable positions with regime-aware filtering
                sellable_positions = []
                if account_info.positions:
                    for pos in account_info.positions:
                        pos_symbol = pos.symbol.upper()
                        if pos_symbol in self.agent_opened_positions and pos.quantity > 0:
                            position_value = pos.quantity * pos.current_price if hasattr(pos, 'current_price') else 0
                            pnl_pct = getattr(pos, 'unrealized_pnl_percent', 0)
                            conviction = self.agent_position_convictions.get(pos_symbol, 5)
                            
                            # Regime-aware filtering
                            should_sell = True
                            if is_capitulation_bottom:
                                # In capitulation, hold high-conviction unless deep losses
                                if conviction >= 7 and pnl_pct > -8:
                                    logger.info(f"HOLDING {pos_symbol} during capitulation warning (conviction {conviction}, P&L {pnl_pct:.1f}%)")
                                    should_sell = False
                            
                            if should_sell:
                                sellable_positions.append({
                                    'symbol': pos_symbol,
                                    'quantity': pos.quantity,
                                    'current_price': getattr(pos, 'current_price', 0),
                                    'value': position_value,
                                    'unrealized_pnl': getattr(pos, 'unrealized_pnl', 0),
                                    'unrealized_pnl_percent': pnl_pct,
                                    'conviction': conviction
                                })

                if not sellable_positions:
                    # FALLBACK: Check all broker positions if agent tracking is empty
                    logger.warning("No positions in agent_opened_positions - checking broker positions as fallback!")

                    if account_info.positions:
                        for pos in account_info.positions:
                            if pos.symbol in self.forbidden_symbols:
                                continue
                            if pos.quantity > 0:
                                position_value = pos.quantity * pos.current_price if hasattr(pos, 'current_price') else 0
                                conviction = self.agent_position_convictions.get(pos.symbol, 5)
                                sellable_positions.append({
                                    'symbol': pos.symbol,
                                    'quantity': pos.quantity,
                                    'current_price': getattr(pos, 'current_price', 0),
                                    'value': position_value,
                                    'unrealized_pnl': getattr(pos, 'unrealized_pnl', 0),
                                    'conviction': conviction
                                })
                        if sellable_positions:
                            for pos_info in sellable_positions:
                                self.agent_opened_positions[pos_info['symbol']] = pos_info['quantity']

                    if not sellable_positions:
                        logger.warning("No sellable positions found for emergency liquidation")
                        return

                # Sort by: 1) P&L ascending (losers first), 2) conviction ascending (low conviction first as tie-breaker)
                sellable_positions.sort(key=lambda x: (x['unrealized_pnl'], x.get('conviction', 5)))

                logger.error(f"EMERGENCY LIQUIDATION: Found {len(sellable_positions)} sellable positions")

                # Calculate how much we need to raise to get back above minimum + safety buffer
                target_buffer = effective_base_capital * 0.10  # 10% safety buffer
                amount_needed = (effective_base_capital + target_buffer) - total_account_value
                
                logger.error(f"Need to raise ${amount_needed:.2f} to restore safety buffer")
                
                # Sell positions starting with biggest losers until we raise enough
                amount_raised = 0
                positions_sold = 0
                
                for position in sellable_positions:
                    if amount_raised >= amount_needed:
                        break

                    symbol = position['symbol']
                    quantity = position['quantity']

                    # DOUBLE-SELL PROTECTION
                    if not self._check_and_record_sell(symbol, cooldown_seconds=15):
                        logger.warning(f"Skipping emergency sell for {symbol} - recently sold")
                        continue

                    logger.error(f"EMERGENCY SELL: {symbol} - {quantity} shares (P&L: ${position['unrealized_pnl']:.2f})")

                    try:
                        # Cancel all existing orders for this symbol first
                        self._cancel_all_orders_for_symbol(symbol)

                        # Wait for cancellations
                        import time
                        time.sleep(1.0)

                        # Place emergency market sell order
                        emergency_order = self.broker.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=quantity,
                            order_type=OrderType.MARKET
                        )
                        
                        logger.error(f"[EMERGENCY SELL EXECUTED] {symbol}: {quantity} shares - Order ID: {emergency_order.order_id}")
                        
                        # Update tracking
                        if symbol in self.agent_opened_positions:
                            del self.agent_opened_positions[symbol]
                        
                        # Record emergency trade
                        self.log_trade(
                            symbol=symbol,
                            side="sell",
                            quantity=quantity,
                            price=position['current_price'],
                            reason="emergency_capital_protection"
                        )
                        
                        amount_raised += position['value']
                        positions_sold += 1
                        
                        logger.error(f"Emergency liquidation progress: ${amount_raised:.2f} raised, {positions_sold} positions sold")
                        
                    except Exception as e:
                        logger.error(f"CRITICAL: Emergency sell failed for {symbol}: {e}")
                        continue
                
                # Save state after emergency liquidation
                self._save_position_state()
                
                if positions_sold > 0:
                    logger.error(f"EMERGENCY LIQUIDATION COMPLETE: Sold {positions_sold} positions, raised ~${amount_raised:.2f}")
                    logger.error(f"Account should now be protected above ${effective_base_capital:.2f} minimum")
                else:
                    logger.error(f"EMERGENCY LIQUIDATION FAILED: Could not sell any positions")
                    logger.error(f"MANUAL INTERVENTION REQUIRED - Account at risk of dropping below ${effective_base_capital:.2f}")
                    
            elif buffer_percent <= 10.0:
                # Warning zone - close to minimum but not emergency yet
                logger.warning(f"CAPITAL WARNING: Account buffer is only {buffer_percent:.1f}% above minimum")
                logger.warning(f"Consider reducing position sizes or taking profits to increase buffer")
                
            else:
                # Safe zone
                logger.debug(f"Capital protection OK: {buffer_percent:.1f}% buffer above minimum")
                
        except Exception as e:
            logger.error(f"Error in capital protection check: {e}", exc_info=True)

    def _prefetch_market_data(self) -> str:
        """
        Pre-fetch market data directly (without Claude) to reduce token usage.

        This gathers account info, positions, and quotes that would otherwise
        require Sonnet tool calls. By including this data in the prompt,
        we reduce the number of tool calls Sonnet needs to make.

        Returns:
            Formatted string with current market data
        """
        try:
            lines = []

            # Get account info
            if self.broker:
                try:
                    account = self.broker.get_account_info()
                    if account:
                        lines.append(f"Account Value: ${account.portfolio_value:,.2f}")
                        lines.append(f"Cash Available: ${account.cash:,.2f}")
                        lines.append(f"Buying Power: ${account.buying_power:,.2f}")

                        if self.capital_limits_enabled and self.base_capital > 0:
                            active_capital = max(0, account.portfolio_value - self.base_capital)
                            lines.append(f"Active Capital (above ${self.base_capital:,.0f} base): ${active_capital:,.2f}")

                        # Get positions with current prices
                        if account.positions:
                            lines.append(f"\nPositions ({len(account.positions)}):")
                            for pos in account.positions:
                                try:
                                    # Use validated quote instead of raw broker quote
                                    validated_quote = self._get_validated_quote(pos.symbol)
                                    current_price = validated_quote["consensus_price"]
                                    confidence = validated_quote["confidence"]
                                    sources_used = validated_quote["sources_used"]

                                    entry_price = pos.avg_entry_price if hasattr(pos, 'avg_entry_price') else 0
                                    pnl = (current_price - entry_price) * pos.quantity if entry_price else 0
                                    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0

                                    conviction = self.agent_position_convictions.get(pos.symbol, "N/A")
                                    partial_taken = "Yes" if pos.symbol in self.agent_position_partial_profits else "No"

                                    if confidence < 0.5:
                                        # Low confidence quote - suppress P&L to prevent Grok from
                                        # acting on stale/unreliable price data (e.g. pre-market Schwab gaps)
                                        lines.append(f"  {pos.symbol}: {pos.quantity} shares @ ${entry_price:.2f} -> ${current_price:.2f} "
                                                   f"(P&L: UNRELIABLE - low confidence {confidence:.1f}, do NOT exit based on this) "
                                                   f"[conviction: {conviction}, partial profit taken: {partial_taken}, sources: {sources_used}]")
                                    else:
                                        lines.append(f"  {pos.symbol}: {pos.quantity} shares @ ${entry_price:.2f} -> ${current_price:.2f} "
                                                   f"(P&L: ${pnl:+.2f} / {pnl_pct:+.1f}%) [conviction: {conviction}, partial profit taken: {partial_taken}, "
                                                   f"confidence: {confidence:.1f}, sources: {sources_used}]")
                                except Exception as e:
                                    lines.append(f"  {pos.symbol}: {pos.quantity} shares (price error: {e})")
                        else:
                            lines.append("\nPositions: None (all cash)")

                except Exception as e:
                    lines.append(f"Account data error: {e}")

            # Get market regime if available
            try:
                from analytics.market_regime import MarketRegimeDetector
                regime_detector = MarketRegimeDetector(self.data_provider)
                regime = regime_detector.get_current_regime()
                if regime:
                    lines.append(f"\nMarket Regime: {regime.get('regime', 'Unknown')}")
                    lines.append(f"  VIX: {regime.get('vix', 'N/A')}")
                    lines.append(f"  Recommended strategies: {regime.get('recommended_strategies', [])}")
            except Exception as e:
                logger.debug(f"Could not get market regime: {e}")

            # PDT status
            if self.pdt_enabled:
                day_trades_used = len(self.pdt_day_trades)
                remaining = max(0, self.pdt_max_trades - day_trades_used)
                lines.append(f"\nPDT Status: {day_trades_used}/{self.pdt_max_trades} day trades used ({remaining} remaining)")

            # Recently closed losers (12-hour rebuy block)
            if self.recently_closed_losers:
                lines.append(f"\nRECENTLY CLOSED LOSERS (12-hour rebuy block):")
                for symbol, loser_info in self.recently_closed_losers.items():
                    hours_since = (datetime.now() - loser_info['timestamp']).total_seconds() / 3600
                    if hours_since < 12:  # Only show active blocks
                        hours_remaining = 12 - hours_since
                        lines.append(f"  {symbol}: Closed at {loser_info['pnl_percent']:.1f}% loss {hours_since:.1f}h ago (blocked for {hours_remaining:.1f}h more)")
                        lines.append(f"    DO NOT suggest {symbol} - will be automatically blocked until cooldown expires")

            return "\n".join(lines) if lines else "No market data available"

        except Exception as e:
            logger.error(f"Error prefetching market data: {e}")
            return f"Error fetching market data: {e}"

    def _get_validated_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get validated quote from multiple data sources using DataValidator.

        Args:
            symbol: Stock symbol to validate

        Returns:
            Dict with consensus price, confidence, and validation metadata
        """
        sources = {}

        # Get quote from data_provider if available
        if self.data_provider:
            try:
                provider_quote = self.data_provider.get_quote(symbol)
                if provider_quote:
                    sources['data_provider'] = provider_quote
            except Exception as e:
                logger.warning(f"Failed to get quote from data_provider for {symbol}: {e}")

        # Get quote from broker if available
        if self.broker:
            try:
                broker_quote = self.broker.get_quote(symbol)
                if broker_quote:
                    sources['broker'] = broker_quote
            except Exception as e:
                logger.warning(f"Failed to get quote from broker for {symbol}: {e}")

        # Special handling for VIX
        if symbol.upper() == 'VIX':
            try:
                validator = DataValidator()
                result = validator.validate_vix(sources)
                logger.info(f"VIX validation: ${result['consensus_price']:.2f} (confidence: {result['confidence']:.2f}, sources: {result['sources_used']}/{result['total_sources']})")
                return result
            except Exception as e:
                logger.error(f"VIX validation failed for {symbol}: {e}")
                # Fallback to any available source
                if sources:
                    fallback_source = next(iter(sources.values()))
                    return {
                        'symbol': symbol,
                        'consensus_price': fallback_source.get('price', fallback_source.get('last', 0)),
                        'confidence': 0.1,  # Very low confidence
                        'sources_used': 1,
                        'total_sources': len(sources),
                        'validation_status': 'fallback_single_source',
                        'warnings': ['VIX validation failed - using fallback']
                    }
                else:
                    raise Exception(f"No VIX data available from any source")

        # Validate regular symbols
        try:
            from analytics.data_validator import validate_market_data
            result = validate_market_data(symbol, sources)
            return result
        except Exception as e:
            logger.warning(f"Quote validation failed for {symbol}: {e}")
            # Fallback: return data from best available source
            if sources:
                # Try to find the most recent/best source
                best_source = None
                best_price = None

                for source_name, quote_data in sources.items():
                    price = quote_data.get('price') or quote_data.get('last') or quote_data.get('lastPrice')
                    if price and price > 0:
                        if best_price is None or source_name == 'broker':  # Prefer broker data as fallback
                            best_price = price
                            best_source = source_name

                if best_price:
                    logger.warning(f"Using fallback price from {best_source} for {symbol}: ${best_price:.2f}")
                    return {
                        'symbol': symbol,
                        'consensus_price': best_price,
                        'confidence': 0.3,  # Low confidence for fallback
                        'sources_used': 1,
                        'total_sources': len(sources),
                        'validation_status': 'fallback_single_source',
                        'warnings': ['Validation failed - using single source fallback']
                    }

                # No data available at all
                raise Exception(f"No quote data available for {symbol} from any source")

    def position_monitor_check(self) -> dict:
        """
        Lightweight position monitoring check (no Claude API call).

        Checks positions against watermarks to determine if action is needed.
        Watermarks are ALIGNED with action thresholds to ensure triggers lead to action.

        Watermarks (aligned with action points):
        - Position down > 0.75% from entry -> triggers before -1% stop loss rule
        - Position at partial profit threshold -> triggers AT action point (not before)
        - Position up > 3% from entry -> triggers SL/TP adjustment review (rocketing stock)
        - Account value dropped near $25k PDT threshold

        Returns:
            dict with keys:
            - trigger_update: bool - whether to call update_trades()
            - reasons: list - why update is triggered
            - positions_checked: int - number of positions monitored
        """
        result = {
            "trigger_update": False,
            "reasons": [],
            "positions_checked": 0,
            "timestamp": datetime.now().isoformat()
        }

        try:
            account_info = self.broker.get_account_info()
            if not account_info:
                logger.warning("Position monitor: Could not get account info")
                return result

            # Check 1: Account value near PDT threshold
            MINIMUM_ACCOUNT = 25000.0
            ACCOUNT_WARNING_BUFFER = 500.0  # Trigger if within $500 of $25k
            if account_info.portfolio_value < (MINIMUM_ACCOUNT + ACCOUNT_WARNING_BUFFER):
                result["trigger_update"] = True
                result["reasons"].append(f"ACCOUNT WARNING: ${account_info.portfolio_value:.2f} near $25k PDT threshold")

            # Check 2: Idle portfolio detection - trigger opportunity scan when 0 positions
            if not account_info.positions or len(account_info.positions) == 0:
                # Check if we have significant cash available for trading
                MINIMUM_CASH_FOR_SCAN = 1000.0  # Only scan if we have at least $1k to deploy
                if account_info.cash >= MINIMUM_CASH_FOR_SCAN:
                    result["trigger_update"] = True
                    result["reasons"].append(f"PORTFOLIO IDLE: 0 positions, ${account_info.cash:.2f} cash available - scanning for opportunities")
                    logger.info(f"Portfolio idle (0 positions) with ${account_info.cash:.2f} cash - triggering opportunity scan")
                else:
                    logger.info(f"Position monitor: 0 positions but only ${account_info.cash:.2f} cash - skipping scan")
                return result

            positions_to_check = []
            for pos in account_info.positions:
                symbol = pos.symbol.upper()
                # Only monitor positions we opened (not forbidden symbols)
                if symbol not in self.forbidden_symbols and symbol in self.agent_opened_positions:
                    positions_to_check.append(pos)

            result["positions_checked"] = len(positions_to_check)

            for pos in positions_to_check:
                symbol = pos.symbol.upper()
                quantity = pos.quantity

                if quantity <= 0:
                    continue

                # Get entry price and current price
                entry_price = self.agent_position_entry_prices.get(symbol)
                if not entry_price:
                    # Try to use position's avg_entry_price
                    if hasattr(pos, 'avg_entry_price') and pos.avg_entry_price:
                        entry_price = pos.avg_entry_price
                    else:
                        continue  # Can't monitor without entry price

                # Get validated current quote for monitoring
                try:
                    validated_quote = self._get_validated_quote(symbol)
                    current_price = validated_quote["consensus_price"]
                    confidence = validated_quote["confidence"]

                    # Skip monitoring if confidence is too low (data quality issue)
                    if confidence < 0.5:
                        logger.warning(f"Position monitor: Skipping {symbol} - low confidence quote ({confidence:.1f})")
                        continue
                except Exception as e:
                    logger.warning(f"Position monitor: Could not get validated quote for {symbol}: {e}")
                    continue

                # Calculate P&L percentage
                pnl_percent = ((current_price - entry_price) / entry_price) * 100

                # Watermark 1: Position down > 0.75% (gives time before -1% stop loss action)
                # Action: Agent sells at -1%, so trigger at -0.75% to catch it in time
                if pnl_percent < -0.75:
                    result["trigger_update"] = True
                    result["reasons"].append(f"{symbol} DOWN {pnl_percent:.1f}% - approaching -1% stop loss")

                # EMERGENCY WATERMARK: Position down > 2% - IMMEDIATE ACTION REQUIRED
                # This catches cases where ATR-based stops are too wide (like CELH at -3%)
                if pnl_percent < -2.0:
                    result["trigger_update"] = True
                    result["reasons"].append(f"{symbol} EMERGENCY: DOWN {pnl_percent:.1f}% - exceeds 2% safety threshold")

                # Watermark 2: Position rocketing up > 3% (consider tightening SL/raising TP)
                # For high conviction positions doing very well, may want to lock in gains
                if pnl_percent > 3.0:
                    conviction = self.agent_position_convictions.get(symbol, 5)
                    if conviction >= 7:  # Only for medium-high conviction
                        result["trigger_update"] = True
                        result["reasons"].append(f"{symbol} ROCKETING +{pnl_percent:.1f}% - consider SL/TP adjustment")

                # Watermark 3: Position at partial profit threshold (ALIGNED with action)
                # Trigger AT the action threshold so _check_partial_profit_opportunities() will act
                tp_target = self.agent_position_tp_targets.get(symbol)
                if tp_target and tp_target > entry_price:
                    # Check cooldown - partial profits can repeat after 30 minutes
                    PARTIAL_PROFIT_COOLDOWN_MINUTES = 15  # Reduced from 30 to take profits more frequently
                    if symbol in self.agent_position_partial_profits:
                        pp_info = self.agent_position_partial_profits[symbol]
                        last_taken_str = pp_info.get('timestamp')
                        if last_taken_str:
                            try:
                                last_taken = datetime.fromisoformat(last_taken_str)
                                minutes_since = (datetime.now() - last_taken).total_seconds() / 60
                                if minutes_since < PARTIAL_PROFIT_COOLDOWN_MINUTES:
                                    continue  # Still on cooldown
                            except (ValueError, TypeError):
                                pass

                    # Calculate progress toward TP
                    price_range = tp_target - entry_price
                    current_profit = current_price - entry_price
                    progress_percent = (current_profit / price_range) * 100

                    # Get conviction and determine threshold - ALIGNED with action thresholds
                    conviction = self.agent_position_convictions.get(symbol, 5)
                    if conviction >= 8:
                        trigger_threshold = 75  # High conviction: action at 75%
                    elif conviction >= 5:
                        trigger_threshold = 50  # Medium conviction: action at 50%
                    else:
                        trigger_threshold = 40  # Low conviction: action at 40%

                    if progress_percent >= trigger_threshold:
                        result["trigger_update"] = True
                        result["reasons"].append(f"{symbol} PARTIAL PROFIT +{pnl_percent:.1f}% - at {progress_percent:.0f}% to TP (threshold: {trigger_threshold}%)")

                # Watermark 4: ATR EMERGENCY STOP - Preemptive sell before stop loss to reduce losses
                # Triggers at wider ATR level (2.0x) to cut losses BEFORE they reach stop loss point
                emergency_atr_percent = self._get_atr_stop_percent(symbol, entry_price, multiplier=2.0)
                emergency_stop_price = round(entry_price * (1 - emergency_atr_percent / 100), 2)

                if current_price < emergency_stop_price:
                    result["trigger_update"] = True
                    result["reasons"].append(f"{symbol} ATR EMERGENCY STOP: Current ${current_price:.2f} < Emergency ${emergency_stop_price:.2f} ({emergency_atr_percent:.1f}% ATR) - cutting losses early")

                # Watermark 5: STOP LOSS ORDER VERIFICATION - Backup for when broker stop orders fail
                # Only triggers if price falls below the normal stop loss level (1.5x ATR)
                stop_price = self.agent_position_entry_prices.get(symbol)
                if stop_price and stop_price > 0:
                    # Calculate normal ATR-based stop loss (same logic as used in bracket orders)
                    atr_sl_percent = self._get_atr_stop_percent(symbol, entry_price, multiplier=1.5)
                    calculated_stop_price = round(entry_price * (1 - atr_sl_percent / 100), 2)

                    # Check if current price is below the calculated stop price (stop order should have executed)
                    if current_price < calculated_stop_price:
                        result["trigger_update"] = True
                        result["reasons"].append(f"{symbol} STOP LOSS FAILURE: Current ${current_price:.2f} < Stop ${calculated_stop_price:.2f} - broker stop order failed to execute")

                # Watermark 6: MOMENTUM REVERSAL TRACKER - "Dead cat bounce" detection
                # Catches pattern: Position drops → recovers toward break-even → weakens again → SELL before hitting stop
                # Configuration
                DECLINE_THRESHOLD = -1.5        # Track positions down > 1.5%
                RECOVERY_ZONE_LOW = -0.5        # Recovery zone: -0.5% to +0.5%
                RECOVERY_ZONE_HIGH = 0.5
                FAILED_RECOVERY_DROP = 0.3      # Sell if drops 0.3% from recovery high
                COOLDOWN_SECONDS = 300          # 5 min cooldown between checks

                # Initialize or get existing state
                if symbol not in self.momentum_reversals:
                    self.momentum_reversals[symbol] = {
                        'low_watermark': pnl_percent,
                        'recovery_high': pnl_percent,
                        'in_recovery': False,
                        'failed_recovery_triggered': False,
                        'last_update': datetime.now()
                    }

                reversal_state = self.momentum_reversals[symbol]

                # Check cooldown
                time_since_update = (datetime.now() - reversal_state['last_update']).total_seconds()
                if time_since_update < COOLDOWN_SECONDS:
                    continue  # Skip this position (still on cooldown)

                # Update state based on current P&L
                # 1. Update low watermark if position drops further
                if pnl_percent < reversal_state['low_watermark']:
                    reversal_state['low_watermark'] = pnl_percent
                    reversal_state['in_recovery'] = False
                    reversal_state['last_update'] = datetime.now()
                    logger.debug(f"{symbol}: New low watermark {pnl_percent:.1f}% (was {reversal_state['low_watermark']:.1f}%)")

                # 2. Detect recovery attempt (back near break-even after decline)
                elif (reversal_state['low_watermark'] < DECLINE_THRESHOLD and 
                      not reversal_state['in_recovery'] and 
                      RECOVERY_ZONE_LOW <= pnl_percent <= RECOVERY_ZONE_HIGH):
                    reversal_state['in_recovery'] = True
                    reversal_state['recovery_high'] = pnl_percent
                    reversal_state['last_update'] = datetime.now()
                    logger.info(f"{symbol}: RECOVERY DETECTED - bounced from {reversal_state['low_watermark']:.1f}% to {pnl_percent:.1f}%")

                # 3. Track recovery high (highest point during recovery)
                elif reversal_state['in_recovery'] and pnl_percent > reversal_state['recovery_high']:
                    reversal_state['recovery_high'] = pnl_percent
                    reversal_state['last_update'] = datetime.now()
                    logger.debug(f"{symbol}: New recovery high {pnl_percent:.1f}%")

                # 4. FAILED RECOVERY - Recovery stalled and price weakening
                elif (reversal_state['in_recovery'] and 
                      not reversal_state['failed_recovery_triggered'] and
                      (reversal_state['recovery_high'] - pnl_percent) >= FAILED_RECOVERY_DROP):
                    
                    # TRIGGER IMMEDIATE SELL
                    result["trigger_update"] = True
                    result["reasons"].append(
                        f"{symbol} FAILED RECOVERY: Peaked at {reversal_state['recovery_high']:+.1f}%, "
                        f"now {pnl_percent:+.1f}% (dropped {reversal_state['recovery_high'] - pnl_percent:.1f}% from recovery high) - "
                        f"SELL before returning to low of {reversal_state['low_watermark']:.1f}%"
                    )
                    
                    # Mark as triggered to prevent duplicate sells
                    reversal_state['failed_recovery_triggered'] = True
                    reversal_state['last_update'] = datetime.now()
                    
                    logger.warning(f"MOMENTUM REVERSAL TRIGGERED: {symbol} - Dead cat bounce detected!")

            # Log result
            if result["trigger_update"]:
                logger.info(f"POSITION MONITOR: Triggering update - {', '.join(result['reasons'])}")
            else:
                logger.debug(f"Position monitor: {result['positions_checked']} positions OK, no triggers")

            return result

        except Exception as e:
            logger.error(f"Position monitor error: {e}")
            result["reasons"].append(f"Monitor error: {e}")
            return result

    def update_trades(self) -> str:
        """
        Trigger a trade update cycle.

        This is the main command that causes the agent to:
        - Fetch fresh prices
        - Evaluate positions
        - Make autonomous trading decisions
        - Adjust strategies and risk
        """
        # Check for late-filling LIMIT orders from previous cycles (must run before scan).
        # This ensures pending orders that filled between cycles get position-tracked
        # and receive brackets before the rest of update_trades logic runs.
        self._check_pending_orders()

        # First, scan positions and add missing bracket orders (includes 1% loss rule).
        # skip_trailing=True: trailing stops run below AFTER partials, not inside scan.
        self.scan_and_add_missing_bracket_orders(skip_trailing=True)

        # SEQUENCE: Partials first (qty changes), then trailing stops (on updated qty).
        # This prevents trailing stop placing an OCO that partial immediately cancels.

        # Check for partial profit opportunities based on conviction
        # This prevents "watching a winner turn into a loser" scenarios
        self._check_partial_profit_opportunities()

        # Update trailing stops AFTER partials - trailing stop now works on correct remaining qty
        self._update_trailing_stops()

        # Pre-fetch market data to reduce Sonnet tool calls
        # This data is gathered directly without Claude to save tokens
        prefetched_data = self._prefetch_market_data()

        # Set context for Sonnet usage (strategic trading decisions)
        self.current_context = "trading_decision"

        # Include fresh position news if available (fetched by scheduler before this call)
        position_news_context = getattr(self, 'position_news_context', None)
        news_section = ""
        if position_news_context:
            news_lines = ["\nFRESH NEWS FOR OPEN POSITIONS (fetched this update):"]
            for symbol, summary in position_news_context.items():
                news_lines.append(f"  {symbol}: {summary}")
            news_section = "\n".join(news_lines)
            # Clear after reading so stale news doesn't persist to next update
            self.position_news_context = None

        # Include prefetched data in prompt to reduce tool calls
        prompt = f"""Update trades - check for new opportunities and manage existing positions.

CURRENT MARKET DATA (pre-fetched):
{prefetched_data}{news_section}

Based on this data, decide what actions to take. Remember: SELL any position down -1% or more immediately. EXCEPTION: If a position shows "P&L: UNRELIABLE", do NOT exit - the price data is low-confidence (pre-market gap or stale feed). Wait for reliable data before any exit decision.

CONVICTION REASSESSMENT: For each open position, reassess your conviction (1-10). If conviction has changed from entry, call update_position_conviction. If conviction drops below {self.min_conviction_threshold} (entry threshold), the thesis is broken and the tool will trigger an immediate exit. Do NOT drop conviction based on unreliable P&L data - only on news/technicals."""

        return self.send_message(prompt)

    def end_day(self) -> str:
        """
        Generate end-of-day reports (pure Python - no LLM call).

        Position closure is now handled by scheduler's _close_all_positions().
        This method only generates reports and saves logs.
        """
        logger.info("Generating end-of-day reports...")

        # Generate and save strategy performance report
        strategy_report = self.generate_strategy_performance_report()
        print("\n" + strategy_report)
        logger.info("Strategy performance report generated")

        # Save strategy report to file
        strategy_log_file = self.save_strategy_report()
        print(f"\nStrategy performance log saved to: {strategy_log_file}")
        logger.info(f"Strategy log saved: {strategy_log_file}")

        # Generate visual performance charts
        try:
            from analytics.performance_charts import generate_performance_report
            chart_report = generate_performance_report(
                trade_log=self.trade_log,
                strategy_log=self.strategy_log,
                data_provider=self.data_provider,
                price_snapshots=self.price_snapshots
            )
            print(f"Visual performance report saved to: {chart_report}")
            logger.info(f"Performance charts saved: {chart_report}")
        except Exception as e:
            logger.warning(f"Could not generate performance charts: {e}")

        # Save token usage log for the day
        log_file = self.token_tracker.save_session_log()
        print(f"Token usage log saved to: {log_file}")
        logger.info(f"Token usage log saved: {log_file}")

        # Print token summary
        self.token_tracker.print_summary()

        # Clear day's state for next trading day
        self.trade_log = []
        self.strategy_log = []
        self.price_snapshots = {}
        self.agent_opened_positions = {}
        self.agent_position_convictions = {}
        self.agent_position_entry_prices = {}
        self.agent_position_tp_targets = {}
        self.agent_position_sl_targets = {}
        self.agent_position_partial_profits = {}
        self.agent_bracket_order_updates = {}  # Clear bracket cooldown tracking
        self.protective_moc_orders = {}
        logger.info("Agent state cleared for next trading day")

        return "End of day reports generated successfully (pure Python)"

    def export_state(self, file_path: Optional[str] = None) -> str:
        """
        Export current portfolio state to JSON.

        Args:
            file_path: Optional path to save state (legacy support)
        """
        try:
            # Get fresh account information from broker
            account_info = None
            if self.broker:
                try:
                    account_info = self.broker.get_account_info()
                    logger.debug("Fetched fresh account info for export")
                except Exception as e:
                    logger.warning(f"Could not fetch account info for export: {e}")

            # Build portfolio state snapshot
            portfolio_state = {
                "export_timestamp": datetime.now().isoformat(),
                "trading_day": datetime.now().date().isoformat(),
                "agent_state": self.state,
                "positions": self.agent_opened_positions,
                "forbidden_symbols": list(self.forbidden_symbols),
                "protective_moc_orders": self.protective_moc_orders,
                "daily_pnl_percent": self.daily_pnl_percent,
                "starting_portfolio_value": self.starting_portfolio_value,
                "current_strategy": self.current_strategy,
                "strategy_log": self.strategy_log[-10:] if len(self.strategy_log) > 10 else self.strategy_log,  # Last 10 strategy changes
                "recent_trades": self.trade_log[-20:] if len(self.trade_log) > 20 else self.trade_log,  # Last 20 trades
                "price_snapshots": self.price_snapshots,
                "pdt_status": self.get_pdt_status() if hasattr(self, 'pdt_enabled') and self.pdt_enabled else None,
                "capital_limits": self.get_capital_limits_status(
                    self.starting_portfolio_value, 0
                ) if hasattr(self, 'capital_limits_enabled') and self.capital_limits_enabled else None
            }

            # Add fresh broker account data if available
            if account_info:
                portfolio_state["broker_account"] = {
                    "cash": account_info.cash,
                    "buying_power": account_info.buying_power,
                    "portfolio_value": account_info.portfolio_value,
                    "positions": [
                        {
                            "symbol": pos.symbol,
                            "quantity": pos.quantity,
                            "avg_entry_price": pos.avg_entry_price,
                            "current_price": pos.current_price,
                            "unrealized_pnl": pos.unrealized_pnl,
                            "unrealized_pnl_percent": pos.unrealized_pnl_percent
                        }
                        for pos in account_info.positions
                    ] if account_info.positions else []
                }
            else:
                portfolio_state["broker_account"] = {
                    "error": "Could not fetch broker account information",
                    "cash": 0.0,
                    "buying_power": 0.0,
                    "portfolio_value": 0.0,
                    "positions": []
                }

            # Save to file if path provided
            if file_path:
                # Ensure directory exists
                Path(file_path).parent.mkdir(parents=True, exist_ok=True)

                with open(file_path, 'w') as f:
                    json.dump(portfolio_state, f, indent=2, default=str)

                logger.info(f"Portfolio state exported to: {file_path}")
                return f"Portfolio state exported to {file_path}"

            # If no file path, return JSON string
            return json.dumps(portfolio_state, indent=2, default=str)

        except Exception as e:
            error_msg = f"Failed to export portfolio state: {e}"
            logger.error(error_msg)
            return error_msg

    def checkpoint_if_needed(self, force: bool = False, interval_minutes: int = 5):
        """
        Perform periodic state checkpoint if enough time has elapsed.

        This provides additional safety beyond post-trade checkpoints by
        saving state periodically even if no trades occur.

        Args:
            force: Force checkpoint regardless of time elapsed
            interval_minutes: Minimum minutes between automatic checkpoints

        Returns:
            True if checkpoint was performed
        """
        from datetime import timedelta

        time_since_last = datetime.now() - self._last_checkpoint_time
        should_checkpoint = force or time_since_last >= timedelta(minutes=interval_minutes)

        if should_checkpoint:
            self._save_position_state()
            self._last_checkpoint_time = datetime.now()
            return True

        return False

    def get_token_summary(self) -> Dict[str, Any]:
        """Get current session's token usage summary."""
        return self.token_tracker.get_session_summary()

    def get_token_breakdown(self) -> Dict[str, Dict[str, Any]]:
        """Get token usage breakdown by context."""
        return self.token_tracker.get_breakdown_by_context()

    def set_risk(self, risk_percent: float) -> str:
        """
        Set risk percentage per trade.

        Args:
            risk_percent: Risk as percentage (0.75 - 2.0)
        """
        risk_percent = max(0.75, min(2.0, risk_percent))
        self.state["risk_percent"] = risk_percent
        return self.send_message(f"set risk to {risk_percent}%")

    def toggle_autonomous(self, enabled: bool) -> str:
        """Toggle autonomous mode on/off."""
        self.state["autonomous_mode"] = enabled
        mode = "on" if enabled else "off"
        return self.send_message(f"autonomous {mode}")

    def get_state(self) -> Dict[str, Any]:
        """Get current agent state."""
        return self.state.copy()

    def generate_strategy_performance_report(self) -> str:
        """
        Generate a comprehensive strategy performance report.

        Returns:
            Formatted string with strategy statistics
        """
        if not self.trade_log:
            return "No trades executed today."

        # Group trades by strategy
        strategy_stats = {}

        for trade in self.trade_log:
            strategy = trade.get("strategy") or "unspecified"

            if strategy not in strategy_stats:
                strategy_stats[strategy] = {
                    "trades": [],
                    "buy_count": 0,
                    "sell_count": 0,
                    "total_shares_bought": 0,
                    "total_shares_sold": 0,
                    "avg_rr": [],
                    "symbols": set()
                }

            stats = strategy_stats[strategy]
            stats["trades"].append(trade)
            stats["symbols"].add(trade["symbol"])

            if trade["side"].lower() == "buy":
                stats["buy_count"] += 1
                stats["total_shares_bought"] += trade["quantity"]

                # Track R:R ratios
                if trade.get("risk_reward"):
                    stats["avg_rr"].append(trade["risk_reward"])
            else:
                stats["sell_count"] += 1
                stats["total_shares_sold"] += trade["quantity"]

        # Build report
        report_lines = [
            "=" * 80,
            "STRATEGY PERFORMANCE REPORT",
            "=" * 80,
            ""
        ]

        # Strategy changes log
        if self.strategy_log:
            report_lines.append("Strategy Changes During Day:")
            report_lines.append("-" * 80)
            for entry in self.strategy_log:
                timestamp = entry["timestamp"].split("T")[1][:8]  # Just time
                report_lines.append(
                    f"  {timestamp} - Changed to '{entry['strategy']}' "
                    f"(from '{entry['previous_strategy'] or 'none'}'): {entry['reason']}"
                )
            report_lines.append("")

        # Strategy statistics
        report_lines.append("Strategy Statistics:")
        report_lines.append("-" * 80)

        for strategy, stats in sorted(strategy_stats.items()):
            avg_rr = sum(stats["avg_rr"]) / len(stats["avg_rr"]) if stats["avg_rr"] else None

            report_lines.append(f"\nStrategy: {strategy.upper()}")
            report_lines.append(f"  Total Trades: {len(stats['trades'])}")
            report_lines.append(f"  BUY Orders:   {stats['buy_count']} ({stats['total_shares_bought']} shares)")
            report_lines.append(f"  SELL Orders:  {stats['sell_count']} ({stats['total_shares_sold']} shares)")
            report_lines.append(f"  Symbols:      {', '.join(sorted(stats['symbols']))}")

            if avg_rr:
                report_lines.append(f"  Avg R:R:      {avg_rr:.2f}")

        report_lines.append("")
        report_lines.append("=" * 80)

        # Detailed trade log
        report_lines.append("\nDetailed Trade Log:")
        report_lines.append("-" * 80)

        for i, trade in enumerate(self.trade_log, 1):
            # Safely extract fields with defaults
            timestamp = trade.get("timestamp", "").split("T")[1][:8] if "T" in trade.get("timestamp", "") else "unknown"
            side = trade.get("side", "unknown").upper()
            quantity = trade.get("quantity", 0)
            symbol = trade.get("symbol", "UNKNOWN")
            price = trade.get("price", 0.0)
            strategy = trade.get("strategy") or "unspecified"
            rr_str = f", R:R: {trade['risk_reward']:.2f}" if trade.get("risk_reward") else ""

            report_lines.append(
                f"{i}. [{timestamp}] {side} {quantity} {symbol} "
                f"@ ${price:.2f} - Strategy: {strategy}{rr_str}"
            )

            if trade.get("reason"):
                report_lines.append(f"   Reason: {trade['reason']}")

        report_lines.append("=" * 80)

        return "\n".join(report_lines)

    def save_strategy_report(self) -> str:
        """
        Save strategy performance report to file.

        Returns:
            Path to saved file
        """
        # Generate report
        report = self.generate_strategy_performance_report()

        # Create logs directory structure
        today = datetime.now()
        year_dir = AI_TRADER_DATA / "logs" / str(today.year)
        month_dir = year_dir / today.strftime("%B_%Y")
        month_dir.mkdir(parents=True, exist_ok=True)

        # Save strategy report
        strategy_file = month_dir / f"strategy_{today.day}.log"

        with open(strategy_file, 'w') as f:
            f.write(f"Strategy Performance Report - {today.strftime('%Y-%m-%d')}\n")
            f.write(f"Generated at: {today.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(report)
            f.write("\n\n")

            # Append raw JSON data for programmatic analysis
            f.write("=" * 80 + "\n")
            f.write("RAW DATA (JSON)\n")
            f.write("=" * 80 + "\n")
            f.write(json.dumps({
                "date": today.date().isoformat(),
                "timestamp": today.isoformat(),
                "strategy_log": self.strategy_log,
                "trade_log": self.trade_log,
                "daily_pnl_percent": self.daily_pnl_percent,
                "starting_portfolio_value": self.starting_portfolio_value
            }, indent=2))

        return str(strategy_file)

    def reset_conversation(self):
        """Clear conversation history (useful for testing)."""
        self.conversation_history = []


# Example usage
if __name__ == "__main__":
    # Initialize agent
    agent = ClaudeTradingAgent(
        rules_file="../grok_day_trader_rules_v2.txt"
    )

    # Start a new trading day
    print("=== Starting New Trading Day ===\n")
    response = agent.start_new_day(initial_cash=10000.0)
    print(response)

    print("\n=== Sending Update Command ===\n")
    response = agent.update_trades()
    print(response)

