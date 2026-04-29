"""
Base Trading Agent - Abstract base class for multi-LLM trading agents

Provides shared infrastructure for Grok, Claude, and future LLM agents.

Architecture:
- BaseTradingAgent: Abstract base with all infrastructure
  |- GrokTradingAgent: Grok-specific implementation (xAI API)
  |- ClaudeTradingAgent: Claude-specific implementation (Anthropic API)

This base class will eventually contain all shared infrastructure:
- Position management
- Order execution
- PDT and capital management
- Market data handling
- State persistence
- Conversation history

Subclasses implement LLM-specific logic:
- API client creation
- Message formatting
- Response parsing
- Model selection
- Token tracking

Phase 1b: Create empty skeleton with abstract methods
Phase 2: Extract infrastructure from ClaudeTradingAgent into this base
Phase 3: Refactor GrokTradingAgent to extend this base instead of Claude
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pathlib import Path
from datetime import datetime
import json
import logging
from utils.position_quantity import get_position_quantity

logger = logging.getLogger(__name__)

# AI_TRADER_DATA path - will be set by subclass
AI_TRADER_DATA = None


class BaseTradingAgent(ABC):
    """
    Abstract base class for trading agents.

    Provides all infrastructure (PDT, capital, positions, orders, etc.)
    Subclasses implement LLM-specific logic (API calls, prompts, parsing).

    This is currently a skeleton. In Phase 2, infrastructure methods will be
    extracted from ClaudeTradingAgent and moved here.
    """

    def __init__(self):
        """
        Initialize base trading agent.

        Subclasses should call super().__init__() and then set up their
        LLM-specific components.
        """
        logger.debug(f"Initializing {self.__class__.__name__}")

        # Initialize checkpoint tracking
        self._checkpoint_count = 0
        self._last_checkpoint_time = datetime.now()

    # ==================== Abstract Methods (LLM-Specific) ====================
    #
    # These must be implemented by subclasses to handle LLM-specific operations.

    @abstractmethod
    def _create_llm_client(self) -> Any:
        """
        Create the LLM API client (Anthropic, OpenAI, xAI, etc.).

        Returns:
            API client instance

        Example implementations:
        - ClaudeTradingAgent: return Anthropic(api_key=self.api_key)
        - GrokTradingAgent: return openai.OpenAI(api_key=self.api_key, base_url="https://api.x.ai/v1")
        """
        pass

    @abstractmethod
    def _send_message_to_llm(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        context: str = "general"
    ) -> Any:
        """
        Send messages to LLM and get response.

        Args:
            messages: List of message dicts in LLM-specific format
            tools: Optional tool definitions
            context: Operation context (for model selection)

        Returns:
            Raw LLM response object

        Example implementations:
        - ClaudeTradingAgent: return self.client.messages.create(model=..., messages=..., tools=...)
        - GrokTradingAgent: return self.client.chat.completions.create(model=..., messages=...)
        """
        pass

    @abstractmethod
    def _parse_llm_response(self, response: Any, context: str = "general") -> Dict:
        """
        Parse LLM response into standard format.

        Args:
            response: Raw LLM response object
            context: Operation context

        Returns:
            Dict with keys:
            - 'content': Extracted text content
            - 'tool_calls': List of tool calls (if any)
            - 'stop_reason': Why the LLM stopped generating
            - 'usage': Token usage info

        Example implementations:
        - ClaudeTradingAgent: Parse Anthropic Messages API response
        - GrokTradingAgent: Parse OpenAI-compatible chat completion response
        """
        pass

    @abstractmethod
    def _format_tool_result(self, tool_use_id: str, result: Any) -> Dict:
        """
        Format tool execution result for LLM.

        Args:
            tool_use_id: ID of the tool call
            result: Result from tool execution

        Returns:
            Dict formatted for LLM's tool result format

        Example implementations:
        - ClaudeTradingAgent: {"type": "tool_result", "tool_use_id": ..., "content": ...}
        - GrokTradingAgent: OpenAI function call result format
        """
        pass

    # ==================== Concrete Methods (Shared Infrastructure) ====================
    #
    # Phase 2a: State management methods extracted from ClaudeTradingAgent

    def _get_state_file_path(self) -> Path:
        """Get the path to the agent state file."""
        # Subclass should set AI_TRADER_DATA path
        if not hasattr(self, 'ai_trader_data_path'):
            raise AttributeError("Subclass must set self.ai_trader_data_path in __init__")
        return self.ai_trader_data_path / "agent_state.json"

    def _save_position_state(self):
        """Save comprehensive agent state to disk for crash recovery."""
        try:
            state_file = self._get_state_file_path()
            state_file.parent.mkdir(parents=True, exist_ok=True)

            # Build comprehensive state snapshot
            state_snapshot = {
                # Position tracking
                "agent_opened_positions": self.agent_opened_positions,
                "agent_position_convictions": self.agent_position_convictions,
                "agent_position_entry_prices": self.agent_position_entry_prices,
                "agent_position_tp_targets": self.agent_position_tp_targets,
                "agent_position_sl_targets": self.agent_position_sl_targets,
                "agent_position_partial_profits": self.agent_position_partial_profits,
                "agent_bracket_order_updates": {
                    k: {**v, 'timestamp': v['timestamp'].isoformat() if isinstance(v.get('timestamp'), datetime) else v.get('timestamp', '')}
                    for k, v in self.agent_bracket_order_updates.items()
                },
                "forbidden_symbols": list(self.forbidden_symbols),
                "protective_moc_orders": self.protective_moc_orders,
                "recently_closed_losers": {
                    k: {**v, 'timestamp': v['timestamp'].isoformat() if isinstance(v['timestamp'], datetime) else v['timestamp']}
                    for k, v in self.recently_closed_losers.items()
                },
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

                # Component state (PDT, Capital managers)
                "pdt": self.pdt_manager.to_dict(),
                "capital": self.capital_manager.to_dict(),

                # Trading plan tracking
                "last_trading_plan_timestamp": self.last_trading_plan_timestamp,
                "current_trading_plan": self.current_trading_plan,

                # Market regime (so regime-gated logic works correctly after restart)
                "market_regime": getattr(self, 'market_regime', None),

                # Metadata
                "date": datetime.now().date().isoformat(),
                "timestamp": datetime.now().isoformat(),
                "checkpoint_count": getattr(self, '_checkpoint_count', 0) + 1
            }

            # Update checkpoint counter
            self._checkpoint_count = state_snapshot["checkpoint_count"]

            with open(state_file, 'w') as f:
                json.dump(state_snapshot, f, indent=2, default=str)

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

                if not file_content:
                    logger.warning("Empty agent state file, starting fresh")
                    state_file.unlink(missing_ok=True)
                    return

                try:
                    saved_state = json.loads(file_content)
                except json.JSONDecodeError as e:
                    logger.error(f"Corrupted agent state file (JSON error: {e}), attempting to delete and starting fresh")
                    try:
                        state_file.unlink(missing_ok=True)
                        logger.info("Corrupted agent state file deleted successfully")
                    except PermissionError as pe:
                        logger.warning(f"Could not delete corrupted state file (file in use): {pe}")
                    except Exception as delete_error:
                        logger.warning(f"Could not delete corrupted state file: {delete_error}")
                    return

            # Only load if same trading day
            state_date = saved_state.get("date", "")
            if state_date != datetime.now().date().isoformat():
                logger.info(f"State file is from {state_date}, not loading (today is {datetime.now().date().isoformat()})")
                return

            # Restore position tracking
            self.agent_opened_positions = saved_state.get("agent_opened_positions", {})
            self.agent_position_convictions = saved_state.get("agent_position_convictions", {})
            self.agent_position_entry_prices = saved_state.get("agent_position_entry_prices", {})
            self.agent_position_tp_targets = saved_state.get("agent_position_tp_targets", {})
            self.agent_position_sl_targets = saved_state.get("agent_position_sl_targets", {})
            self.agent_position_partial_profits = saved_state.get("agent_position_partial_profits", {})

            # Restore bracket order cooldown tracking
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
                    pass

            self.protective_moc_orders = saved_state.get("protective_moc_orders", {})
            self.high_water_mark = saved_state.get("high_water_mark", 25000.0)

            # Restore recently closed losers
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

            # Restore trading plan tracking
            self.last_trading_plan_timestamp = saved_state.get("last_trading_plan_timestamp")
            self.current_trading_plan = saved_state.get("current_trading_plan")

            # Restore component state
            if "pdt" in saved_state:
                self.pdt_manager.from_dict(saved_state["pdt"])
            elif "pdt_day_trades" in saved_state:
                self.pdt_manager.pdt_day_trades = saved_state.get("pdt_day_trades", [])
            self.pdt_manager.cleanup_old_trades()

            if "capital" in saved_state:
                self.capital_manager.from_dict(saved_state["capital"])
            elif "high_water_mark" in saved_state:
                self.capital_manager.high_water_mark = saved_state.get("high_water_mark", 25000.0)

            # Restore market regime (prevents regime=None/vix defaulting to 25.0 after restart,
            # which would incorrectly gate entry-window checks to the tight 7:15-7:45 window)
            market_regime = saved_state.get("market_regime")
            if market_regime and isinstance(market_regime, dict):
                self.market_regime = market_regime
                self.min_conviction_threshold = market_regime.get('min_conviction', 8.0)
                self.max_positions_allowed = market_regime.get('max_positions', 2)
                logger.info(
                    f"Restored market regime: {market_regime.get('regime', 'UNKNOWN')}, "
                    f"VIX={market_regime.get('vix', '?')}, "
                    f"min conviction={self.min_conviction_threshold}/10"
                )

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

    def checkpoint_if_needed(self, force: bool = False, interval_minutes: int = 5):
        """
        Perform periodic state checkpoint if enough time has elapsed.

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

    def export_state(self, file_path: Optional[str] = None) -> str:
        """
        Export current portfolio state to JSON.

        Args:
            file_path: Optional path to save state
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
                "strategy_log": self.strategy_log[-10:] if len(self.strategy_log) > 10 else self.strategy_log,
                "recent_trades": self.trade_log[-20:] if len(self.trade_log) > 20 else self.trade_log,
                "price_snapshots": self.price_snapshots,
                "pdt_status": self.pdt_manager.get_status() if hasattr(self, 'pdt_manager') else None,
                "capital_limits": self.capital_manager.get_status(
                    self.starting_portfolio_value, 0
                ) if hasattr(self, 'capital_manager') else None
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
                            "quantity": get_position_quantity(pos),
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

    def get_state(self) -> Dict[str, Any]:
        """Get current agent state."""
        return self.state.copy()

    # ==================== Market Regime Management ====================

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
        if not hasattr(self, 'market_regime') or not self.market_regime:
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

    # ==================== Strategy & Trade Logging ====================

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
            "conviction_score": conviction_score
        }

        self.trade_log.append(trade_entry)

        conviction_str = f", Conviction: {conviction_score}/10" if conviction_score else ""
        price_str = f"${price:.2f}" if price is not None else "N/A"
        rr_str = f"{risk_reward:.2f}" if risk_reward else "N/A"
        logger.info(
            f"Trade logged: {side.upper()} {quantity} {symbol} @ {price_str} "
            f"[Strategy: {self.current_strategy}, R:R: {rr_str}{conviction_str}]"
        )

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

    # ==================== Position Reconciliation ====================

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
                qty = get_position_quantity(pos)
                if qty > 0:  # Only track positive quantities
                    broker_positions[pos.symbol.upper()] = qty

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

    # ==================== Utility Methods ====================

    def get_token_summary(self) -> Dict[str, Any]:
        """Get current session's token usage summary."""
        if hasattr(self, 'token_tracker'):
            return self.token_tracker.get_session_summary()
        return {}

    def get_token_breakdown(self) -> Dict[str, Dict[str, Any]]:
        """Get token usage breakdown by context."""
        if hasattr(self, 'token_tracker'):
            return self.token_tracker.get_breakdown_by_context()
        return {}

    def reset_conversation(self):
        """Clear conversation history (useful for testing)."""
        self.conversation_history = []

    # ==================== Helper Methods ====================

    def _get_protected_positions_warning(self) -> str:
        """Get warning message about position restrictions."""
        if not hasattr(self, 'forbidden_symbols'):
            return "No restrictions - account was empty at start. You can trade any symbol."

        if not self.forbidden_symbols:
            return "No restrictions - account was empty at start. You can trade any symbol."

        symbols = ', '.join(sorted(self.forbidden_symbols))
        return f"""FORBIDDEN SYMBOLS (DO NOT TRADE): {symbols}

These symbols had existing positions when trading started. You CANNOT:
- Buy these symbols (would mix with existing positions)
- Sell these symbols (they're long-term holdings)

You CAN trade ANY OTHER symbols not in this list. When you buy a new symbol, you can add to it or sell it later."""

    def get_agent_info(self) -> Dict[str, str]:
        """
        Get basic information about this agent.

        Returns:
            Dict with agent type, class name, etc.
        """
        return {
            "class": self.__class__.__name__,
            "type": "trading_agent",
            "phase": "2b_infrastructure"
        }
