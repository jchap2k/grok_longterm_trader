"""
Anthropic LLM Client - Handles all Claude API communication

Extracted from ClaudeTradingAgent monolith (Phase 1 refactor)
Manages: API client creation, message sending, streaming, model selection, response parsing
"""

import json
import sys
import logging
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

# Add project root to path for relative imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


from anthropic import Anthropic
from anthropic.types import Message, TextBlock, ToolUseBlock

# Import token tracker for usage analytics
from analytics.token_tracker import TokenTracker

logger = logging.getLogger(__name__)


class AnthropicLLMClient:
    """
    Handles all Anthropic API communication for ClaudeTradingAgent.

    Responsibilities:
    - Create and manage Anthropic API client
    - Select appropriate model based on context (Sonnet/Haiku/Thinking)
    - Send messages with tool support
    - Handle streaming responses
    - Parse API responses into standard format
    - Manage conversation history and model switching
    - Track token usage
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        data_model: str,
        thinking_model: Optional[str] = None,
        use_thinking_for_regime: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        token_tracker: Optional[TokenTracker] = None
    ):
        """
        Initialize Anthropic LLM client.

        Args:
            api_key: Anthropic API key
            model: Primary model for strategic decisions (Sonnet)
            data_model: Model for data operations (Haiku)
            thinking_model: Optional deep reasoning model
            use_thinking_for_regime: Use thinking model for regime analysis
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
            token_tracker: Token usage tracker instance
        """
        self.api_key = api_key
        self.model = model
        self.data_model = data_model
        self.thinking_model = thinking_model
        self.use_thinking_for_regime = use_thinking_for_regime
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.token_tracker = token_tracker or TokenTracker()

        # Create Anthropic client with timeout to prevent hangs
        self.client = Anthropic(
            api_key=self.api_key,
            timeout=120.0  # 2 minute timeout
        )

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
            'emergency_update'
        }

        # Define which contexts can use thinking mode (if enabled)
        self.thinking_contexts = {
            'market_regime_analysis',  # Morning regime check - high leverage decision
        }

        # Conversation state
        self.conversation_history: List[Dict[str, Any]] = []
        self.current_context = "general"

        # Track last used model for context switches
        self._last_used_model = None

        # Manual override flag (set by Ctrl+G in main agent)
        self._force_thinking_model = False

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
            "claude-3-5-haiku-20241022",  # Alternative Haiku
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
            self.model = working_sonnet
            self.data_model = working_haiku
            logger.info(f"DUAL MODEL SYSTEM: Sonnet={self.model}, Haiku={self.data_model}")

        elif working_sonnet and not working_haiku:
            # Sonnet available but no Haiku - use Sonnet for both
            self.model = working_sonnet
            self.data_model = working_sonnet
            logger.warning(f"SONNET ONLY: Using {working_sonnet} for both strategic and data operations")

        elif not working_sonnet and working_haiku:
            # Haiku available but no Sonnet - use Haiku for both
            self.model = working_haiku
            self.data_model = working_haiku
            logger.warning(f"HAIKU ONLY: Using {working_haiku} for both strategic and data operations")

        else:
            # Neither model available - critical error
            raise ValueError(f"No Claude models are available with this API key")

        # Log final configuration
        if self.model == self.data_model:
            logger.warning(f"SINGLE MODEL MODE: Using {self.model} for all operations")
        else:
            logger.info(f"DUAL MODEL MODE: Strategic={self.model}, Data={self.data_model}")

    def _select_model(self, context: str) -> str:
        """
        Select the appropriate model based on operation context.

        Args:
            context: The current operation context

        Returns:
            Model name to use (Thinking for regime, Sonnet for strategic, Haiku for data)
        """
        # OVERRIDE: Force thinking model if manually triggered
        if self._force_thinking_model and self.thinking_model:
            logger.info(f"FORCED THINKING MODEL for context: {context}")
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
            Display name (e.g., "Sonnet", "Haiku", "Reasoning")
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

    def _get_ai_provider_name(self, model_name: str) -> str:
        """Get provider name from model name."""
        if not model_name:
            return "Unknown"

        model_lower = model_name.lower()
        if "grok" in model_lower:
            return "Grok"
        elif "claude" in model_lower:
            return "Anthropic"
        else:
            return "AI"

    def _clear_tool_history_for_model_switch(self, new_model: str):
        """
        Clear tool_use/tool_result pairs from conversation history when switching models.

        Claude API rejects messages with tool_use_ids that don't exist in the current
        conversation context. When switching between models, we strip out tool pairs.
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

    def send_message(
        self,
        user_message: str,
        tools: List[Dict],
        system_prompt: Any,
        context: str = "general",
        stream: bool = False
    ) -> str:
        """
        Send a message to Claude and get a response.

        Args:
            user_message: Message from user
            tools: Tool definitions for Claude
            system_prompt: System prompt (can be string or list of blocks)
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

        # Send message
        try:
            if stream:
                return self._send_streaming_message(tools, system_prompt)
            else:
                return self._send_standard_message(tools, system_prompt)
        except Exception as e:
            error_msg = f"Error communicating with Claude: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def _send_standard_message(self, tools: List[Dict], system_prompt: Any) -> str:
        """Send a standard (non-streaming) message."""
        # Select appropriate model based on current context
        selected_model = self._select_model(self.current_context)

        # Clear tool history when switching between models
        self._clear_tool_history_for_model_switch(selected_model)

        # Log what we're asking Claude
        if self.conversation_history:
            latest_user_msg = None
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
                model=selected_model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system_prompt,
                messages=self.conversation_history,
                tools=tools
            )
        except Exception as api_error:
            error_str = str(api_error)
            # Check for tool_use/tool_result mismatch error
            if "tool_use" in error_str and "tool_result" in error_str and "400" in error_str:
                logger.error(f"Conversation history corrupted (tool_use/tool_result mismatch). Clearing and retrying...")
                # Clear conversation history and retry
                self.conversation_history = []
                response = self.client.messages.create(
                    model=selected_model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": "Continue trading - check positions and market conditions."}],
                    tools=tools
                )
                # Rebuild history with fresh start
                self.conversation_history = [{"role": "user", "content": "Continue trading - check positions and market conditions."}]
                logger.info("Conversation history cleared and recovered from error")
            else:
                raise

        # Track token usage
        self.token_tracker.record_api_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=selected_model,
            context=self.current_context
        )

        # Process response and handle tool calls
        full_response = ""
        max_iterations = 50
        iteration = 0

        while response.stop_reason == "tool_use":
            iteration += 1
            if iteration > max_iterations:
                logger.error(f"Max tool iterations ({max_iterations}) exceeded. Stopping.")
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
            self.conversation_history.append({
                "role": "assistant",
                "content": response.content
            })

            # Execute tools and add results
            # NOTE: Tool execution is delegated to the main agent
            # We only handle the messaging loop here
            tool_results = []
            for tool_call in tool_calls:
                # This is a placeholder - actual tool execution happens in the agent
                # For now, we'll just log that we need to execute the tool
                logger.debug(f"Tool execution needs to be handled by agent: {tool_call.name}")
                # The agent will need to provide a callback for tool execution
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": json.dumps({"error": "Tool execution not configured"})
                })

            # Add tool results to history
            if tool_results:
                self.conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })

                # Add delay to prevent rate limiting
                time.sleep(1.5)

                # Get next response
                try:
                    response = self.client.messages.create(
                        model=selected_model,
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        system=system_prompt,
                        messages=self.conversation_history,
                        tools=tools
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
                    model=selected_model,
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

        return full_response.strip()

    def _send_streaming_message(self, tools: List[Dict], system_prompt: Any) -> str:
        """Send a streaming message (for real-time CLI output)."""
        # Select model (for streaming we always use primary model)
        selected_model = self.model

        full_response = ""

        with self.client.messages.stream(
            model=selected_model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=self.conversation_history,
            tools=tools
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

    def parse_response(self, response: Message, context: str = "general") -> Dict:
        """
        Parse Anthropic Message response into standard format.

        Args:
            response: Anthropic Message object
            context: Operation context

        Returns:
            Dict with keys: content, tool_calls, stop_reason, usage
        """
        # Extract text content
        text_content = ""
        tool_calls = []

        for block in response.content:
            if isinstance(block, TextBlock):
                text_content += block.text
            elif isinstance(block, ToolUseBlock):
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        return {
            "content": text_content,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens
            }
        }

    def format_tool_result(self, tool_use_id: str, result: Any) -> Dict:
        """
        Format tool execution result for Anthropic API.

        Args:
            tool_use_id: ID of the tool call
            result: Result from tool execution

        Returns:
            Dict in Anthropic tool_result format
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

        # Identify all tool_use_ids in the conversation
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
                            tool_uses = [block for block in prev_message.get("content", [])
                                        if isinstance(block, dict) and block.get("type") == "tool_use"]

                            if tool_uses:
                                tool_use_message_ids = {tu.get("id") for tu in tool_uses if tu.get("id")}

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

        # Update conversation history
        self.conversation_history = pruned_history
        logger.info(f"Conversation history pruned to {len(self.conversation_history)} messages")

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []
        logger.info("Conversation history cleared")
