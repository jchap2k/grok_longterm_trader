"""
Grok LLM Client - Handles all xAI Grok API communication

Extracted from GrokTradingAgent (refactor)
Manages: API client creation, message sending, model selection, response parsing
Uses OpenAI SDK with xAI endpoint for compatibility
"""

import json
import logging
import time
import sys
import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

# Daily interaction log directory (ai_trader_data/logs/grok/)
_GROK_LOG_DIR = Path(__file__).parent.parent.parent.parent / "ai_trader_data" / "logs" / "grok"

# Add project root to path for relative imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Use OpenAI SDK for xAI's OpenAI-compatible API
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Import token tracker for usage analytics
from analytics.token_tracker import TokenTracker

logger = logging.getLogger(__name__)


class GrokLLMClient:
    """
    Handles all xAI Grok API communication for GrokTradingAgent.

    Responsibilities:
    - Create and manage OpenAI client pointed at xAI endpoint
    - Select appropriate model based on context (Reasoning/Fast)
    - Send messages with tool support
    - Convert tool schemas from Anthropic to OpenAI format
    - Convert messages from Anthropic to OpenAI format
    - Parse API responses into standard format
    - Manage conversation history and model switching
    - Track token usage

    This client provides the same interface as AnthropicLLMClient so they're
    interchangeable, but uses OpenAI SDK and xAI endpoint.
    """

    # xAI API endpoint
    XAI_BASE_URL = "https://api.x.ai/v1"

    # Default Grok models (fallback only - always prefer broker_config.json ai_providers.grok)
    DEFAULT_REASONING_MODEL = "grok-4.3"
    DEFAULT_FAST_MODEL = "grok-4.20-non-reasoning"

    def __init__(
        self,
        api_key: str,
        model: str = None,
        data_model: str = None,
        thinking_model: Optional[str] = None,
        use_thinking_for_regime: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        token_tracker: Optional[TokenTracker] = None
    ):
        """
        Initialize Grok LLM client.

        Args:
            api_key: xAI API key
            model: Primary model for strategic decisions (Reasoning)
            data_model: Model for data operations (Fast)
            thinking_model: Optional deep reasoning model
            use_thinking_for_regime: Use thinking model for regime analysis
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
            token_tracker: Token usage tracker instance
        """
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            )

        self.api_key = api_key
        self.model = model or self.DEFAULT_REASONING_MODEL
        self.data_model = data_model or self.DEFAULT_FAST_MODEL
        self.thinking_model = thinking_model
        self.use_thinking_for_regime = use_thinking_for_regime
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.token_tracker = token_tracker or TokenTracker()

        # Create OpenAI client pointed at xAI
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.XAI_BASE_URL,
            timeout=120.0  # 2 minute timeout
        )

        # Validate models and set fallbacks
        self._validate_and_set_models()

        # Define which contexts require Reasoning model (strategic)
        self.sonnet_contexts = {
            'trading_decision',
            'strategy_change',
            'risk_assessment',
            'news_analysis',
            'trading_plan',
            'error_recovery',
            'initialization',
            'end_of_day',
            'emergency_update',
            'market_regime_analysis',
            'opportunity_scan',
            'swing_startup_brief',
            'swing_scan_session',
            'swing_eod_review',
        }

        # Conversation state
        self.conversation_history: List[Dict[str, Any]] = []
        self.current_context = "general"

        # Track last used model for context switches
        self._last_used_model = None

        # Manual override flag (set by Ctrl+G in main agent)
        self._force_thinking_model = False

        # Last API call metadata - populated after every call, used for logging and retry diagnostics
        self._last_finish_reason: Optional[str] = None
        self._last_input_tokens: int = 0
        self._last_output_tokens: int = 0

    def _validate_and_set_models(self):
        """
        Validate that the specified Grok models are available and set fallbacks if needed.
        """
        available_models = {}

        # Test each model
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

    def _select_model(self, context: str) -> str:
        """
        Select the appropriate model based on operation context.

        Args:
            context: The current operation context

        Returns:
            Model name to use (Reasoning for strategic, Fast for data)
        """
        # OVERRIDE: Force thinking model if manually triggered
        if self._force_thinking_model and self.thinking_model:
            logger.info(f"FORCED THINKING MODEL for context: {context}")
            return self.thinking_model

        # Use Reasoning model for strategic decision-making contexts
        if context in self.sonnet_contexts:
            logger.debug(f"Using Reasoning model for context: {context}")
            return self.model

        # Use Fast model for all other contexts (data fetching, monitoring, etc.)
        logger.debug(f"Using Fast model for context: {context}")
        return self.data_model

    def _get_model_display_name(self, model_name: str) -> str:
        """
        Get a display name for the model based on its type.

        Args:
            model_name: The model name being used

        Returns:
            Display name (e.g., "Reasoning", "Fast", "Grok")
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

        # Default
        return "AI"

    def _get_ai_provider_name(self, model_name: str) -> str:
        """Get provider name from model name."""
        if not model_name:
            return "Unknown"

        model_lower = model_name.lower()
        if "grok" in model_lower:
            return "Grok"
        else:
            return "xAI"

    def _clear_tool_history_for_model_switch(self, new_model: str):
        """
        Clear tool_use/tool_result pairs from conversation history when switching models.

        When switching between models, we strip out tool pairs to prevent API errors.
        """
        if not self._last_used_model:
            self._last_used_model = new_model
            return

        # Check if we're switching models
        if self._last_used_model != new_model:
            logger.info(f"Model switch detected: {self._last_used_model} -> {new_model}")
            logger.info("Clearing tool history for model switch")

            # Preserve the current user message if present
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
                logger.info("Conversation history CLEARED for model switch")

            # Update last used model
            self._last_used_model = new_model

    def _convert_tools_to_openai_format(self, tools: List[Dict]) -> List[Dict]:
        """
        Convert Anthropic tool format to OpenAI format.

        Args:
            tools: List of tools in Anthropic format

        Returns:
            List of tools in OpenAI format
        """
        openai_tools = []

        for tool in tools:
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
        """
        Convert Anthropic message format to OpenAI format.

        Args:
            system_prompt: System prompt string

        Returns:
            List of messages in OpenAI format (system as first message)
        """
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

    def _build_system_prompt_from_anthropic(self, anthropic_prompt: Any) -> str:
        """
        Convert Anthropic system prompt format to plain string for OpenAI.

        Args:
            anthropic_prompt: System prompt (can be list of blocks or string)

        Returns:
            Plain string system prompt
        """
        # Convert list of content blocks to plain string
        if isinstance(anthropic_prompt, list):
            text_parts = []
            for block in anthropic_prompt:
                if isinstance(block, dict) and 'text' in block:
                    text_parts.append(block['text'])
                elif isinstance(block, str):
                    text_parts.append(block)
            return "\n\n".join(text_parts)

        # Already a string
        return str(anthropic_prompt)

    def send_message(
        self,
        user_message: str,
        tools: List[Dict],
        system_prompt: Any,
        context: str = "general",
        stream: bool = False
    ) -> str:
        """
        Send a message to Grok and get a response.

        Args:
            user_message: Message from user
            tools: Tool definitions in Anthropic format (will be converted)
            system_prompt: System prompt (Anthropic format - will be converted)
            context: Operation context for model selection
            stream: Whether to stream the response

        Returns:
            Agent's text response
        """
        # Update context
        self.current_context = context

        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Send message - retry once on timeout with 15s backoff
        _RETRY_WAIT_S = 15
        for _attempt in range(2):
            try:
                if stream:
                    return self._send_streaming_message(tools, system_prompt)
                else:
                    return self._send_standard_message(tools, system_prompt)
            except TimeoutError as te:
                if _attempt == 0:
                    logger.warning(
                        f"[Grok] API timeout on attempt 1 (context={self.current_context}), "
                        f"retrying after {_RETRY_WAIT_S}s..."
                    )
                    time.sleep(_RETRY_WAIT_S)
                    # History is already clean after a timeout: the API call never
                    # completed so no assistant message was appended. The user message
                    # added at the top of send_message() is still in history and the
                    # retry must use it. Do NOT call clear_history() here - that would
                    # erase the user message and send a bare system-prompt to xAI.
                    logger.info(
                        f"[Grok] Retrying with existing history "
                        f"({len(self.conversation_history)} msgs) - no response was "
                        "appended on timeout so history is already clean"
                    )
                    continue
                # Second attempt also timed out
                error_msg = (
                    f"Error communicating with Grok: {str(te)} "
                    f"(context={self.current_context}, timed out on both attempts)"
                )
                logger.error(error_msg)
                return error_msg
            except Exception as e:
                error_msg = f"Error communicating with Grok: {str(e)}"
                logger.error(error_msg)
                return error_msg

    def _call_api_with_timeout(self, **kwargs) -> object:
        """
        Wrap a single client.chat.completions.create() call with a 90s daemon-thread timeout.

        xAI uses HTTP chunked transfer encoding even for non-streaming calls, so
        httpx's timeout=120s behaves as a per-chunk limit rather than a total limit.
        A slow xAI day can hang the calling thread indefinitely without this wrapper.

        The daemon thread is intentionally leaked on timeout (it exits when the
        process ends). threading.active_count() is logged so operator can see
        accumulation during high-timeout periods.

        Returns the ChatCompletion response object, or raises TimeoutError / API error.
        """
        import threading as _threading
        _TIMEOUT_S = 90
        _result = [None]
        _err = [None]

        def _api_call(_r=_result, _e=_err):
            try:
                _r[0] = self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                _e[0] = exc

        _t = _threading.Thread(target=_api_call, daemon=True)
        _t.start()
        _t.join(timeout=_TIMEOUT_S)

        if _t.is_alive():
            logger.warning(
                f"[Grok] API call timed out after {_TIMEOUT_S}s "
                f"(active threads: {_threading.active_count()}, daemon thread leaked)"
            )
            raise TimeoutError(
                f"[GrokLLMClient] xAI API call timed out after {_TIMEOUT_S}s"
            )
        if _err[0] is not None:
            raise _err[0]
        return _result[0]

    def _send_standard_message(self, tools: List[Dict], system_prompt: Any) -> str:
        """
        Send a standard (non-streaming) message using Grok.

        Args:
            tools: Tool definitions in Anthropic format
            system_prompt: System prompt in Anthropic format

        Returns:
            Full response text
        """
        # Select appropriate model based on current context
        selected_model = self._select_model(self.current_context)

        # Clear tool history when switching between models
        self._clear_tool_history_for_model_switch(selected_model)

        # Log what we're asking Grok
        latest_user_msg = ""
        if self.conversation_history:
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

        # Convert formats
        system_prompt_str = self._build_system_prompt_from_anthropic(system_prompt)
        messages = self._convert_messages_to_openai_format(system_prompt_str)
        openai_tools = self._convert_tools_to_openai_format(tools) if tools else None

        try:
            response = self._call_api_with_timeout(
                model=selected_model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=messages,
                tools=openai_tools
            )
        except Exception as api_error:
            error_str = str(api_error)
            logger.error(f"Grok API error: {error_str}")

            # Try to recover from conversation errors
            if "400" in error_str or "invalid" in error_str.lower():
                logger.error("Conversation may be corrupted. Clearing and retrying...")
                self.conversation_history = []
                messages = [
                    {"role": "system", "content": system_prompt_str},
                    {"role": "user", "content": "Continue trading - check positions and market conditions."}
                ]
                response = self._call_api_with_timeout(
                    model=selected_model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=messages,
                    tools=openai_tools
                )
                self.conversation_history = [{"role": "user", "content": "Continue trading - check positions and market conditions."}]
                logger.info("Conversation history cleared and recovered")
            else:
                raise

        # Track token usage and capture call metadata for logging/diagnostics
        if hasattr(response, 'usage') and response.usage:
            self.token_tracker.record_api_call(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model=selected_model,
                context=self.current_context,
                provider='grok'  # Specify provider for accurate cost tracking
            )
            self._last_input_tokens = response.usage.prompt_tokens
            self._last_output_tokens = response.usage.completion_tokens
        self._last_finish_reason = response.choices[0].finish_reason

        # Process response and handle tool calls
        # Note: Tool execution must be handled by the agent, not the client
        # We return the full response text and update conversation history
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

            # NOTE: Tool execution happens in the agent, not here
            # The agent will need to provide tool results via add_tool_results()
            # For now, we break out of the loop
            logger.debug(f"Tool execution needs to be handled by agent: {[tc.function.name for tc in tool_calls]}")
            break

        # Extract final response
        final_message = response.choices[0].message
        if final_message.content:
            full_response += final_message.content

        # Retry on empty response when no tool calls are pending (transient API failure).
        # Tool-call paths are intentionally excluded - continue_after_tools handles those.
        if not full_response.strip() and not response.choices[0].message.tool_calls:
            for retry in range(2):
                logger.warning(
                    f"[Grok] Empty response on attempt {retry + 1}/3 "
                    f"(finish_reason={self._last_finish_reason}, "
                    f"in={self._last_input_tokens} out={self._last_output_tokens}) "
                    f"- retrying in 6s..."
                )
                time.sleep(6)
                try:
                    response = self._call_api_with_timeout(
                        model=selected_model,
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        messages=messages,
                        tools=openai_tools
                    )
                    self._last_finish_reason = response.choices[0].finish_reason
                    if hasattr(response, 'usage') and response.usage:
                        self._last_input_tokens = response.usage.prompt_tokens
                        self._last_output_tokens = response.usage.completion_tokens
                        self.token_tracker.record_api_call(
                            input_tokens=response.usage.prompt_tokens,
                            output_tokens=response.usage.completion_tokens,
                            model=selected_model,
                            context=f"{self.current_context}_retry{retry + 1}",
                            provider='grok'
                        )
                    final_message = response.choices[0].message
                    full_response = final_message.content or ""
                    if full_response.strip():
                        logger.info(f"[Grok] Response received on retry {retry + 1}/2")
                        break
                except Exception as retry_err:
                    logger.error(f"[Grok] Retry {retry + 1} API error: {retry_err}")
                    break
            if not full_response.strip():
                logger.error(
                    f"[Grok] All 3 attempts returned empty response "
                    f"(finish_reason={self._last_finish_reason}, "
                    f"in={self._last_input_tokens} out={self._last_output_tokens}) - skipping cycle"
                )

        # Add final assistant message to history if not already added
        if not (response.choices[0].message.tool_calls):
            self.conversation_history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": final_message.content or ""}]
            })

        return full_response.strip()

    def add_tool_results(self, tool_results: List[Dict]):
        """
        Add tool results to conversation history.

        Args:
            tool_results: List of tool results in Anthropic format
        """
        if tool_results:
            self.conversation_history.append({
                "role": "user",
                "content": tool_results
            })

    def _append_assistant_message(self, message: Any) -> bool:
        """
        Append an assistant message to conversation history, preserving tool calls.

        Returns:
            True if the assistant message contained tool calls.
        """
        assistant_content = []
        if getattr(message, "content", None):
            assistant_content.append({"type": "text", "text": message.content})

        tool_calls = getattr(message, "tool_calls", None) or []
        for tc in tool_calls:
            assistant_content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": json.loads(tc.function.arguments)
            })

        if not assistant_content:
            assistant_content = [{"type": "text", "text": ""}]

        self.conversation_history.append({
            "role": "assistant",
            "content": assistant_content
        })
        return bool(tool_calls)

    def continue_after_tools(self, tools: List[Dict], system_prompt: Any) -> str:
        """
        Continue conversation after tool execution.

        Args:
            tools: Tool definitions in Anthropic format
            system_prompt: System prompt in Anthropic format

        Returns:
            Next response text
        """
        selected_model = self._select_model(self.current_context)

        # Convert formats once - same messages used across all retry attempts
        system_prompt_str = self._build_system_prompt_from_anthropic(system_prompt)
        messages = self._convert_messages_to_openai_format(system_prompt_str)
        openai_tools = self._convert_tools_to_openai_format(tools) if tools else None

        # Rate limiting delay
        time.sleep(1.0)

        # Attempt API call up to 3 times (1 original + 2 retries) on empty response
        full_response = ""
        appended_assistant_message = False
        MAX_ATTEMPTS = 3
        for attempt in range(MAX_ATTEMPTS):
            if attempt > 0:
                logger.warning(
                    f"[Grok] After-tools empty response on attempt {attempt}/3 "
                    f"(finish_reason={self._last_finish_reason}, "
                    f"in={self._last_input_tokens} out={self._last_output_tokens}) "
                    f"- retrying in 6s..."
                )
                time.sleep(6)

            # If Grok returned finish_reason=tool_calls on a prior attempt it is
            # trying to make more tool calls instead of writing its response text.
            # Two-part fix:
            # 1. Omit tools from the API call (can't call what isn't defined)
            # 2. Append an explicit user instruction to stop calling tools and
            #    write the analysis - needed because the message history contains
            #    prior tool_calls entries that Grok pattern-matches on even when
            #    tools are removed from the payload.
            force_no_tools = attempt > 0 and self._last_finish_reason == 'tool_calls'
            if force_no_tools:
                logger.warning(
                    "[Grok] After-tools retry: last finish_reason=tool_calls - "
                    "omitting tools and injecting stop-tools instruction"
                )
                retry_messages = list(messages) + [{
                    "role": "user",
                    "content": (
                        "All tool calls have been completed. "
                        "Please now provide your complete trading analysis in text format. "
                        "Do not make any additional tool calls."
                    )
                }]
            else:
                retry_messages = messages

            try:
                call_kwargs = {
                    'model': selected_model,
                    'max_tokens': self.max_tokens,
                    'temperature': self.temperature,
                    'messages': retry_messages,
                }
                if not force_no_tools:
                    call_kwargs['tools'] = openai_tools
                response = self._call_api_with_timeout(**call_kwargs)
            except Exception as api_error:
                logger.error(f"[Grok] After-tools API error (attempt {attempt + 1}/{MAX_ATTEMPTS}): {api_error}")
                if attempt < MAX_ATTEMPTS - 1:
                    continue
                # All attempts exhausted with API errors
                self.conversation_history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": ""}]
                })
                return "[Recovered from error - please retry]"

            # Capture call metadata
            self._last_finish_reason = response.choices[0].finish_reason
            if hasattr(response, 'usage') and response.usage:
                self._last_input_tokens = response.usage.prompt_tokens
                self._last_output_tokens = response.usage.completion_tokens
                self.token_tracker.record_api_call(
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                    model=selected_model,
                    context=f"{self.current_context}_tool_followup" + (f"_retry{attempt}" if attempt > 0 else ""),
                    provider='grok'
                )

            # Extract response
            message = response.choices[0].message
            full_response = message.content or ""

            # If Grok wants another tool round after seeing tool results, preserve
            # the tool calls in history so the outer agent loop can continue.
            if message.tool_calls:
                appended_assistant_message = True
                self._append_assistant_message(message)
                logger.debug(
                    "[Grok] After-tools response produced more tool calls: %s",
                    [tc.function.name for tc in (message.tool_calls or [])]
                )
                break

            if full_response.strip():
                if attempt > 0:
                    logger.info(f"[Grok] After-tools response received on retry {attempt}/{MAX_ATTEMPTS - 1}")
                break

        if not full_response.strip():
            logger.error(
                f"[Grok] All {MAX_ATTEMPTS} after-tools attempts returned empty response "
                f"(finish_reason={self._last_finish_reason}, "
                f"in={self._last_input_tokens} out={self._last_output_tokens})"
            )

        # Add to history (after all retry attempts, preserves conversation flow)
        if not appended_assistant_message:
            self.conversation_history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": full_response}]
            })

        return full_response.strip()

    def _send_streaming_message(self, tools: List[Dict], system_prompt: Any) -> str:
        """
        Send a streaming message (for real-time CLI output).

        For simplicity, use non-streaming for Grok.
        OpenAI streaming has different format than Anthropic.
        """
        return self._send_standard_message(tools, system_prompt)

    def parse_response(self, response: Any, context: str = "general") -> Dict:
        """
        Parse OpenAI-compatible response into standard format.

        Args:
            response: OpenAI ChatCompletion object
            context: Operation context

        Returns:
            Dict with keys: content, tool_calls, stop_reason, usage
        """
        message = response.choices[0].message

        # Extract text content
        text_content = message.content or ""

        # Extract tool calls
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments)
                })

        return {
            "content": text_content,
            "tool_calls": tool_calls,
            "stop_reason": response.choices[0].finish_reason,
            "usage": {
                "input_tokens": response.usage.prompt_tokens if hasattr(response, 'usage') else 0,
                "output_tokens": response.usage.completion_tokens if hasattr(response, 'usage') else 0
            }
        }

    def format_tool_result(self, tool_use_id: str, result: Any) -> Dict:
        """
        Format tool execution result for Grok API.

        Args:
            tool_use_id: ID of the tool call
            result: Result from tool execution

        Returns:
            Dict in Anthropic tool_result format (for compatibility)
        """
        # Convert result to string if needed
        if isinstance(result, (dict, list)):
            result_content = json.dumps(result, indent=2)
        else:
            result_content = str(result)

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": result_content
        }

    def prune_conversation_history(self, max_messages: int = 20):
        """
        Keep only last N messages to prevent token overflow.

        Args:
            max_messages: Maximum number of messages to keep
        """
        if len(self.conversation_history) <= max_messages:
            return

        # Simple pruning - keep last N messages
        # More sophisticated pruning (like Anthropic client) could be added later
        self.conversation_history = self.conversation_history[-max_messages:]
        logger.info(f"Conversation history pruned to {len(self.conversation_history)} messages")

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []
        logger.info("Conversation history cleared")

    def _log_interaction(
        self,
        system_prompt: str,
        user_message: str,
        response_text: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        interaction_type: str = "standard"
    ):
        """
        Append a full Grok interaction (prompt + response) to the daily log file.

        File: logs/grok/grok_YYYY-MM-DD.log
        One file per calendar day, all interactions appended chronologically.
        Logs the complete system prompt, full user prompt, and full response
        so we can review exactly what Grok saw and decided each trading day.

        Args:
            system_prompt: System prompt string sent to Grok
            user_message: User/prompt message sent to Grok
            response_text: Grok's full response text
            model: Model name used
            input_tokens: Input token count from API response
            output_tokens: Output token count from API response
            interaction_type: Label for this interaction (standard, tool_followup)
        """
        try:
            _GROK_LOG_DIR.mkdir(parents=True, exist_ok=True)
            today = datetime.date.today().strftime("%Y-%m-%d")
            log_path = _GROK_LOG_DIR / f"grok_{today}.log"

            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            separator = "=" * 80
            token_info = (
                f"TOKENS: {input_tokens:,} in / {output_tokens:,} out"
                if (input_tokens or output_tokens) else "TOKENS: n/a"
            )

            entry = (
                f"\n{separator}\n"
                f"[{now_str}] CONTEXT: {self.current_context} | MODEL: {model}"
                f" | TYPE: {interaction_type} | {token_info}\n"
                f"{separator}\n"
                f"\n=== SYSTEM PROMPT ===\n"
                f"{system_prompt}\n"
                f"\n=== USER PROMPT ===\n"
                f"{user_message}\n"
                f"\n=== GROK RESPONSE ===\n"
                f"{response_text}\n"
            )

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)

        except Exception as log_err:
            # Never let logging break trading
            logger.debug(f"[GrokLog] Failed to write interaction log: {log_err}")
