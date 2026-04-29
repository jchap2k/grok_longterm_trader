"""
Grok Trading Agent - AI agent for day trading using xAI's Grok models

This module implements a trading agent using xAI's Grok models via the
OpenAI-compatible API. It inherits from ClaudeTradingAgent and overrides
the API-specific methods.

Cost savings: Grok models are ~10x cheaper than Claude while maintaining
strong reasoning capabilities.

Models used:
- grok-4-1-fast-reasoning: Strategic decisions (replaces Sonnet)
- grok-4-1-fast-non-reasoning: Data operations (replaces Haiku)
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

# Use OpenAI SDK for xAI's OpenAI-compatible API
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Import base agent
from .claude_trading_agent import ClaudeTradingAgent

# Import token tracker
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from analytics.token_tracker import TokenTracker


class GrokTradingAgent(ClaudeTradingAgent):
    """
    Trading agent powered by xAI's Grok models.

    Uses the OpenAI-compatible API endpoint at api.x.ai.
    Inherits all trading logic from ClaudeTradingAgent but uses Grok models.
    """

    # xAI API endpoint
    XAI_BASE_URL = "https://api.x.ai/v1"

    # Default Grok models
    DEFAULT_REASONING_MODEL = "grok-beta"
    DEFAULT_FAST_MODEL = "grok-beta"

    def __init__(
        self,
        api_key: Optional[str] = None,
        rules_file: str = "rules/active_rules.txt",
        model: str = None,
        data_model: str = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        broker=None,
        data_provider=None,
        news_provider=None,
        learning_db=None
    ):
        """
        Initialize the Grok trading agent.

        Args:
            api_key: xAI API key (or set XAI_API_KEY env var)
            rules_file: Path to XML rules file
            model: Grok model for strategic decisions (default: grok-4-1-fast-reasoning)
            data_model: Grok model for data operations (default: grok-4-1-fast-non-reasoning)
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
            broker: Trading broker instance
            data_provider: Market data provider instance
            news_provider: News provider instance (always Alpaca)
            learning_db: Learning database for trade journal
        """
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            )

        # Get API key
        self.api_key = api_key
        if not self.api_key:
            # Try credentials manager
            try:
                from config.credentials_manager import get_credentials_manager
                creds = get_credentials_manager()
                self.api_key = creds.get_xai_key()
            except ImportError:
                pass

        if not self.api_key:
            self.api_key = os.getenv("XAI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "xAI API key not found. Set via config/xai_api_key.txt, "
                "XAI_API_KEY env var, or pass as api_key parameter"
            )

        # Set models
        self.model = model or self.DEFAULT_REASONING_MODEL
        self.data_model = data_model or self.DEFAULT_FAST_MODEL

        # Create OpenAI client pointed at xAI
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.XAI_BASE_URL,
            timeout=120.0
        )

        self.max_tokens = max_tokens
        self.temperature = temperature

        # Skip parent __init__ API setup, but call the rest manually
        # We need to replicate what ClaudeTradingAgent.__init__ does without Anthropic
        self._initialize_agent_state(rules_file, broker, data_provider, news_provider, learning_db)

        logger.info(f"GrokTradingAgent initialized with models: {self.model}, {self.data_model}")

    def _initialize_agent_state(self, rules_file: str, broker, data_provider, news_provider=None, learning_db=None):
        """Initialize agent state without calling parent __init__."""
        import threading
        self._state_lock = threading.Lock()

        # Define which contexts require the reasoning model
        self.sonnet_contexts = {
            'trading_decision',
            'strategy_change',
            'risk_assessment',
            'news_analysis',
            'trading_plan',
            'error_recovery',
            'initialization',
            'end_of_day',
            'emergency_update'
        }

        # Broker and data provider connections
        self.broker = broker
        self.data_provider = data_provider
        self.news_provider = news_provider
        self.learning_db = learning_db

        # Token tracking
        self.token_tracker = TokenTracker()
        self.current_context = "initialization"

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

        # Track positions opened by the agent
        self.agent_opened_positions = {}
        self.agent_position_convictions = {}
        self.agent_position_entry_prices = {}
        self.agent_position_tp_targets = {}
        self.agent_position_partial_profits = {}

        # Track bracket order updates to prevent churn (cooldown system)
        self.agent_bracket_order_updates = {}  # Dict: {symbol: {'timestamp': datetime, 'price': float}}

        # Blacklist of symbols
        self.forbidden_symbols = set()

        # Track recent orders and cooldowns (30-min to prevent LLM hallucination duplicates)
        self.recent_orders = []
        self.recently_sold_symbols = {}
        self.recently_bought_symbols = {}
        
        # Track recently closed losing positions (24-hour cooldown to prevent rebuying losers)
        self.recently_closed_losers = {}  # {symbol: {'timestamp': datetime, 'entry': float, 'exit': float, 'pnl_percent': float}}

        # Trading plan tracking
        self.last_trading_plan_timestamp = None
        self.current_trading_plan = None
        self.last_analysis_summary = ""  # Persists reasoning from last scan for Q&A

        # PDT tracking
        self.pdt_day_trades = []
        self.pdt_enabled = False
        self.pdt_max_trades = 3
        self.pdt_preferred_days = ["Tuesday", "Wednesday", "Thursday"]

        # Capital limits (fund $30k to avoid PDT, only trade with amount above base)
        self.capital_limits_enabled = False
        self.base_capital = 0  # Untouchable floor (e.g., $25k) - active = account - base
        self.high_water_mark = 25000.0  # Dynamic base tracking
        self.dynamic_base_enabled = True  # Use high_water * 0.8 as base

        # High watermarks for positions
        self.position_high_watermarks = {}

        # Chart tracking
        self.price_snapshots = {}

        # Strategy tracking (needed for log_strategy_change and other methods)
        self.strategy_log = []
        self.trade_log = []
        self.current_strategy = None

        # Protective MOC orders tracking
        self.protective_moc_orders = {}

        # Daily loss tracking
        self.daily_pnl_percent = 0.0
        self.starting_portfolio_value = 0.0

        # State checkpointing
        self._checkpoint_count = 0
        self._last_checkpoint_time = datetime.now()

        # Model tracking for context switches
        self._last_used_model = None

        # Initialize tools (inherited from ClaudeTradingAgent)
        self.tools = self._define_tools()

        # Load persisted position state (for crash recovery)
        self._load_position_state()

    def _validate_and_set_models(self):
        """Validate and set Grok models (override from parent)."""
        # Test if models are available
        available_models = {}

        for model_name, model_type in [
            (self.model, "reasoning"),
            (self.data_model, "fast")
        ]:
            try:
                response = self.client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=5
                )
                available_models[model_type] = model_name
                logger.info(f"Grok model {model_name} is available")
            except Exception as e:
                logger.warning(f"Grok model {model_name} not available: {e}")

        if not available_models:
            raise ValueError(f"No Grok models available. Check your xAI API key.")

        # Update models if fallbacks needed
        if "reasoning" not in available_models and "fast" in available_models:
            self.model = available_models["fast"]
            logger.warning(f"Using {self.model} for both reasoning and fast operations")

        if "fast" not in available_models and "reasoning" in available_models:
            self.data_model = available_models["reasoning"]
            logger.warning(f"Using {self.data_model} for both reasoning and fast operations")

    def _convert_tools_to_openai_format(self) -> List[Dict]:
        """Convert Anthropic tool format to OpenAI format."""
        openai_tools = []

        for tool in self.tools:
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}})
                }
            }
            openai_tools.append(openai_tool)

        return openai_tools

    def _convert_messages_to_openai_format(self, system_prompt: str) -> List[Dict]:
        """Convert Anthropic message format to OpenAI format."""
        messages = [{"role": "system", "content": system_prompt}]

        for msg in self.conversation_history:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    messages.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Tool results in Anthropic format
                    for item in content:
                        if item.get("type") == "tool_result":
                            # OpenAI expects tool results as separate messages
                            messages.append({
                                "role": "tool",
                                "tool_call_id": item.get("tool_use_id", "unknown"),
                                "content": item.get("content", "")
                            })

            elif role == "assistant":
                if isinstance(content, str):
                    messages.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    # Extract text and tool calls from Anthropic format
                    text_parts = []
                    tool_calls = []

                    for item in content:
                        if hasattr(item, 'text'):
                            text_parts.append(item.text)
                        elif hasattr(item, 'type') and item.type == 'text':
                            text_parts.append(item.text)
                        elif hasattr(item, 'type') and item.type == 'tool_use':
                            tool_calls.append({
                                "id": item.id,
                                "type": "function",
                                "function": {
                                    "name": item.name,
                                    "arguments": json.dumps(item.input)
                                }
                            })
                        elif isinstance(item, dict):
                            if item.get('type') == 'text':
                                text_parts.append(item.get('text', ''))
                            elif item.get('type') == 'tool_use':
                                tool_calls.append({
                                    "id": item.get('id', 'unknown'),
                                    "type": "function",
                                    "function": {
                                        "name": item.get('name', ''),
                                        "arguments": json.dumps(item.get('input', {}))
                                    }
                                })

                    assistant_msg = {"role": "assistant"}
                    if text_parts:
                        assistant_msg["content"] = "\n".join(text_parts)
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                        if "content" not in assistant_msg:
                            assistant_msg["content"] = None

                    messages.append(assistant_msg)

        return messages

    def _send_standard_message(self) -> str:
        """Send a standard (non-streaming) message using Grok."""
        # Select appropriate model based on current context
        selected_model = self._select_model(self.current_context)

        # Clear tool history when switching models
        self._clear_tool_history_for_model_switch(selected_model)

        # Log what we're asking
        if self.conversation_history:
            latest_user_msg = None
            for msg in reversed(self.conversation_history):
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    latest_user_msg = msg["content"]
                    break

            if latest_user_msg:
                preview = latest_user_msg[:197] + "..." if len(latest_user_msg) > 200 else latest_user_msg
                model_name = "Reasoning" if selected_model == self.model else "Fast"
                logger.info(f"Asking Grok ({model_name}): {preview}")

        # Convert formats
        system_prompt = self._build_system_prompt()
        messages = self._convert_messages_to_openai_format(system_prompt)
        tools = self._convert_tools_to_openai_format()

        try:
            response = self.client.chat.completions.create(
                model=selected_model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=messages,
                tools=tools if tools else None
            )
        except Exception as api_error:
            error_str = str(api_error)
            logger.error(f"Grok API error: {error_str}")

            # Try to recover from conversation errors
            if "400" in error_str or "invalid" in error_str.lower():
                logger.error("Conversation may be corrupted. Clearing and retrying...")
                self.conversation_history = []
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Continue trading - check positions and market conditions."}
                ]
                response = self.client.chat.completions.create(
                    model=selected_model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=messages,
                    tools=tools if tools else None
                )
                self.conversation_history = [{"role": "user", "content": "Continue trading - check positions and market conditions."}]
                logger.info("Conversation history cleared and recovered")
            else:
                raise

        # Track token usage
        if hasattr(response, 'usage') and response.usage:
            self.token_tracker.record_api_call(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model=selected_model,
                context=self.current_context
            )

        # Process response
        full_response = ""
        max_iterations = 50
        iteration = 0

        # Check for tool calls by examining the message content, not finish_reason
        # This is more robust across different OpenAI-compatible providers
        # Some use finish_reason="tool_calls", others use "stop" with tool_calls present
        while response.choices[0].message.tool_calls:
            iteration += 1
            if iteration > max_iterations:
                logger.error(f"Max tool iterations ({max_iterations}) exceeded.")
                full_response += "\n[ERROR: Maximum tool iteration limit reached.]"
                break

            message = response.choices[0].message

            # Extract text content
            if message.content:
                full_response += message.content + "\n"

            # Store assistant message with tool calls in history
            assistant_content = []
            if message.content:
                assistant_content.append({"type": "text", "text": message.content})

            tool_calls = message.tool_calls or []
            for tc in tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments)
                })

            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_content
            })

            # Execute tools
            tool_results = []
            for tc in tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                result = self._execute_tool(tc.function.name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result)
                })

            # Add tool results to history
            if tool_results:
                self.conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })

                # Rate limiting delay
                import time
                time.sleep(1.0)

                # Get next response
                messages = self._convert_messages_to_openai_format(system_prompt)
                try:
                    response = self.client.chat.completions.create(
                        model=selected_model,
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        messages=messages,
                        tools=tools if tools else None
                    )
                except Exception as api_error:
                    logger.error(f"Tool followup error: {api_error}")
                    full_response += "\n[Recovered from error - please retry]"
                    break

                # Track token usage
                if hasattr(response, 'usage') and response.usage:
                    self.token_tracker.record_api_call(
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                        model=selected_model,
                        context=f"{self.current_context}_tool_followup"
                    )
            else:
                break

        # Extract final response
        final_message = response.choices[0].message
        if final_message.content:
            full_response += final_message.content

        # Add final assistant message to history
        self.conversation_history.append({
            "role": "assistant",
            "content": [{"type": "text", "text": final_message.content or ""}]
        })

        # Prune conversation history
        self._prune_conversation_history()

        # Capture analysis summary for Q&A (when context is trading-related)
        if self.current_context in ('trading_decision', 'trading_plan', 'strategy_change'):
            # Store the response as analysis summary for dashboard Q&A
            if len(full_response) > 100:  # Only meaningful responses
                self.last_analysis_summary = full_response[:2000]  # Cap at 2000 chars

        return full_response.strip()

    def _send_streaming_message(self) -> str:
        """Send a streaming message (for real-time CLI output)."""
        # For simplicity, use non-streaming for Grok
        # OpenAI streaming has different format than Anthropic
        return self._send_standard_message()

    def _select_model(self, context: str) -> str:
        """Select appropriate model based on context (override from parent)."""
        if context in self.sonnet_contexts:
            logger.debug(f"Using Reasoning model for context: {context}")
            return self.model
        logger.debug(f"Using Fast model for context: {context}")
        return self.data_model

    def _build_system_prompt(self) -> str:
        """
        Build the system prompt for Grok (OpenAI format).

        Unlike Claude which uses structured content blocks with cache_control,
        OpenAI/Grok expects a plain string. xAI doesn't support prompt caching
        the same way Anthropic does, but their pricing is already ~10x lower.

        Returns:
            System prompt as a plain string
        """
        # Get the parent's structured prompt
        parent_prompt = super()._build_system_prompt()

        # Convert list of content blocks to plain string
        if isinstance(parent_prompt, list):
            text_parts = []
            for block in parent_prompt:
                if isinstance(block, dict) and 'text' in block:
                    text_parts.append(block['text'])
                elif isinstance(block, str):
                    text_parts.append(block)
            return "\n\n".join(text_parts)

        # Already a string (shouldn't happen but handle it)
        return str(parent_prompt)


# Factory function for easy instantiation
def create_grok_agent(**kwargs) -> GrokTradingAgent:
    """Create a GrokTradingAgent instance."""
    return GrokTradingAgent(**kwargs)
