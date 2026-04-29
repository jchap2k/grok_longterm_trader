"""
Tool Executor - Manages tool registry and execution

Extracted from ClaudeTradingAgent monolith during Phase 1 refactor.
Contains all 17 tool implementations with their schemas and execution logic.
"""

import logging
import time
import json
import sys
import uuid
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime, date

# Add parent path for relative imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from brokers.base_broker import OrderSide, OrderType
from scheduler.swing_decision_packet import build_final_swing_decision_packet
from utils.position_quantity import get_position_quantity

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Manages tool registry and execution for ClaudeTradingAgent.

    Responsibilities:
    - Define tool schemas for Claude API
    - Register tool handlers (all tool implementations)
    - Execute tool calls from LLM
    - Validate tool inputs
    - Format tool results

    Contains 16 tools across categories (Phase 6.2: removed calculate_position_size, use create_trading_plan):
    - Market Data: get_market_data (3 modes: quote, intraday, historical)
    - Orders: place_order, place_bracket_order
    - Account: get_account_info
    - Planning: create_trading_plan (canonical - Gate 1 = Option A: 1% portfolio value)
    - Time: get_market_time_info
    - News: search_market_news
    - Strategy: set_trading_strategy, get_strategy_performance
    - Analysis: get_market_regime, analyze_multi_timeframe, check_correlation_risk,
                analyze_technical_indicators, calculate_dynamic_position_size
    - Position Management: extend_take_profit, update_position_conviction
    """

    def __init__(
        self,
        broker=None,
        data_provider=None,
        news_provider=None,
        learning_db=None,
        position_manager=None,
        order_manager=None,
        state_manager=None,
        trading_logic=None,
        parent_agent=None  # Reference to ClaudeTradingAgent for accessing its methods
    ):
        """
        Initialize tool executor.

        Args:
            broker: Trading broker instance
            data_provider: Market data provider
            news_provider: News API provider
            learning_db: Learning database
            position_manager: Position manager instance
            order_manager: Order manager instance
            state_manager: State manager instance
            trading_logic: Trading logic instance
            parent_agent: Reference to ClaudeTradingAgent (for _get_validated_quote, etc.)
        """
        self.broker = broker
        self.data_provider = data_provider
        self.news_provider = news_provider
        self.learning_db = learning_db
        self.position_manager = position_manager
        self.order_manager = order_manager
        self.state_manager = state_manager
        self.trading_logic = trading_logic
        self.parent_agent = parent_agent

        # Trading plan enforcement state
        self.last_trading_plan_timestamp = None
        self.current_trading_plan = None

        # Order deduplication tracking
        self.recent_orders = []  # (symbol, side, quantity, timestamp)

        # Entry context captured at order placement time - read by scheduler to populate
        # learning DB and swing_metadata after fills.
        # {symbol: {why_entered, catalyst, market_context, setup_type, confidence_level, order_id, ...}}
        self.pending_entry_context = {}

        # Tool registry
        self.tools_schema = self._define_tools()
        self.tool_handlers = self._register_tool_handlers()

    def _define_tools(self) -> List[Dict[str, Any]]:
        """
        Define tool schemas for Claude API.

        Extracted from ClaudeTradingAgent._define_tools() method.
        Contains all 17 tool schemas with their input/output specifications.

        Returns:
            List of tool definition dicts for Anthropic API
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
                        "reason": {"type": "string", "description": "Reason for trade (e.g., 'breakout', 'momentum', 'dip_buy')"},
                        "catalyst": {"type": "string", "description": "What triggered this trade (e.g., 'earnings_beat', 'gap_up', 'vwap_reclaim', 'news_catalyst')"},
                        "market_context": {"type": "string", "description": "Overall market condition at entry (e.g., 'bullish_open', 'choppy', 'sector_rotation', 'risk_off')"},
                        "setup_type": {"type": "string", "description": "Trade setup pattern (e.g., 'gap_and_go', 'vwap_bounce', 'breakout', 'momentum_continuation', 'dip_buy')"},
                        "candidate_lane": {"type": "string", "description": "Scanner lane that produced the trade (e.g., 'FORCESWING', 'PEAD')"},
                        "strategy": {"type": "string", "description": "Strategy label to persist with the trade (e.g., 'PEAD', 'FORCESWING')"},
                        "hold_mode": {"type": "string", "description": "Position lifecycle mode (e.g., 'swing', 'day')"},
                        "hold_type": {"type": "string", "description": "Hold-type tag used by swing logic (e.g., 'swing', 'pead', 'rotation')"},
                        "next_earnings_date": {"type": "string", "description": "Upcoming earnings date in YYYY-MM-DD format, if known"},
                        "profit_target": {"type": "number", "description": "Expected swing profit target for metadata/journaling"},
                        "initial_stop": {"type": "number", "description": "Initial protective stop used for swing metadata"},
                        "current_stop": {"type": "number", "description": "Current protective stop used for swing metadata"},
                        "confidence_level": {"type": "number", "description": "Conviction score 1-10 for this trade setup"},
                        "lessons_applied": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "List of lesson IDs that triggered or supported this trade (e.g., [142, 229]). Used for hypothesis generation and lesson performance tracking."
                        }
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
                        "risk_percent": {"type": "number", "description": "Total risk as % of portfolio value across all positions (default 1.0%). Gate 1 = Option A: 1% means each plan risks 1% of total portfolio value."}
                    },
                    "required": ["trading_candidates"]
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
                "description": "PREFER create_trading_plan instead (it handles multi-position allocation correctly). Use this only for single-position sizing when you need Kelly Criterion adjustments for a standalone position. Uses 1% of portfolio value as base risk (Gate 1 = Option A).",
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
            },
            {
                "name": "record_candidate_evaluation",
                "description": "MANDATORY: Record your evaluation for a candidate you are NOT trading. You MUST call this for EVERY stock in the candidate list presented to you that you do not place an order for - not just the ones you deliberated on. If you received N candidates and trade 0, call this N times. If you trade 1, call this N-1 times. No exceptions - incomplete coverage breaks the learning loop. Do NOT call this for stocks you are trading (place_bracket_order handles those).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol you evaluated and decided to pass on"
                        },
                        "conviction_score": {
                            "type": "number",
                            "description": "Your conviction score 1-10 for this candidate (1=very low, 10=very high). Be honest - this feeds the learning loop."
                        },
                        "rejection_reason": {
                            "type": "string",
                            "description": "Brief reason you are passing (e.g., 'conviction below threshold', 'position limit reached', 'pattern unclear', 'poor risk/reward', 'catalyst too old')"
                        },
                        "strategy": {
                            "type": "string",
                            "description": "The trading strategy you considered for this candidate (e.g., 'NEWS_CATALYST', 'MOMENTUM', 'OPENING_RANGE_BREAKOUT', 'VWAP_RECLAIM', 'MEAN_REVERSION', 'BOUNCE'). Include even for skipped candidates so strategy-level analysis is possible."
                        },
                        "lessons_applied": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "List of lesson IDs (integers) you consulted or that influenced this skip decision. Example: [142, 229] if lessons L142 and L229 were relevant to your evaluation. Include any lesson that shaped your thinking, even if it reinforced skipping. Omit entirely if you did not consult any lessons for this candidate."
                        }
                    },
                    "required": ["symbol", "conviction_score", "rejection_reason"]
                }
            }
        ]

    def _register_tool_handlers(self) -> Dict[str, Callable]:
        """
        Register tool name -> handler method mapping.

        Returns:
            Dict mapping tool names to their handler functions
        """
        return {
            "get_market_data": self.tool_get_market_data,
            "place_order": self.tool_place_order,
            "place_bracket_order": self.tool_place_bracket_order,
            "get_account_info": self.tool_get_account_info,
            "create_trading_plan": self.tool_create_trading_plan,
            "get_market_time_info": self.tool_get_market_time_info,
            "search_market_news": self.tool_search_market_news,
            "set_trading_strategy": self.tool_set_trading_strategy,
            "get_market_regime": self.tool_get_market_regime,
            "analyze_multi_timeframe": self.tool_analyze_multi_timeframe,
            "check_correlation_risk": self.tool_check_correlation_risk,
            "get_strategy_performance": self.tool_get_strategy_performance,
            "calculate_dynamic_position_size": self.tool_calculate_dynamic_position_size,
            "analyze_technical_indicators": self.tool_analyze_technical_indicators,
            "extend_take_profit": self.tool_extend_take_profit,
            "update_position_conviction": self.tool_update_position_conviction,
            "record_candidate_evaluation": self.tool_record_candidate_evaluation,
        }

    def execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool call from Claude.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool

        Returns:
            Tool execution result
        """
        logger.info(f"Tool execution requested: {tool_name}")

        # Check if trading plan is required (for order placement tools)
        plan_check = self._check_trading_plan_required(tool_name)
        if plan_check:
            logger.error(f"Trading plan enforcement: {plan_check['error']}")
            return plan_check

        # Wrap in error handling
        try:
            handler = self.tool_handlers.get(tool_name)
            if handler:
                return handler(tool_input)
            else:
                logger.error(f"Unknown tool: {tool_name}")
                return {
                    "error": f"Unknown tool: {tool_name}",
                    "available_tools": list(self.tool_handlers.keys())
                }
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

        Enforces that create_trading_plan must be called before place_order or place_bracket_order.

        Args:
            tool_name: Name of the tool being executed

        Returns:
            Error dict if plan is required but missing, None if OK to proceed
        """
        # Only enforce plan requirement for order placement tools
        if tool_name not in ("place_order", "place_bracket_order"):
            return None

        # Check if a trading plan exists and is recent (within last 10 minutes)
        if not self.last_trading_plan_timestamp:
            return {
                "error": "BLOCKED: You must call 'create_trading_plan' BEFORE placing any orders. This ensures proper capital allocation across all positions.",
                "blocked": True,
                "reason": "no_trading_plan"
            }

        # Check if plan is recent (20 minute expiry)
        # NOTE: idle portfolio rate-limit is 10 min + API overhead ~30s, so plans
        # created in turn N were always expiring by turn N+1 with a 10-min TTL.
        # 20 min gives two full idle cycles before requiring a refresh.
        time_since_plan = time.time() - self.last_trading_plan_timestamp
        if time_since_plan > 1200:  # 20 minutes
            return {
                "error": f"BLOCKED: Trading plan expired ({time_since_plan/60:.1f} minutes old). Create a fresh trading plan before placing orders.",
                "blocked": True,
                "reason": "trading_plan_expired",
                "plan_age_minutes": time_since_plan / 60
            }

        return None

    # ==================== TOOL IMPLEMENTATIONS ====================
    # NOTE: These implementations are extracted from claude_trading_agent.py
    # and adapted to use injected dependencies instead of self references

    def tool_get_market_data(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch market data for a symbol.

        Supports three data types:
        - quote: Current price with validation
        - intraday: 15-minute bars for day trading
        - historical: Daily bars for longer-term analysis
        """
        symbol = inputs["symbol"]
        data_type = inputs.get("data_type", "quote")

        if not self.data_provider:
            return {"error": "Market data provider not connected"}

        try:
            if data_type == "quote":
                # Get validated quote from parent agent (uses multi-source validation)
                if self.parent_agent and hasattr(self.parent_agent, '_get_validated_quote'):
                    validated_quote = self.parent_agent._get_validated_quote(symbol)
                    return {
                        "symbol": symbol,
                        "price": validated_quote["consensus_price"],
                        "confidence": validated_quote["confidence"],
                        "sources_used": validated_quote["sources_used"],
                        "validation_status": validated_quote["validation_status"],
                        "warnings": validated_quote.get("warnings", []),
                        "timestamp": validated_quote["timestamp"]
                    }
                else:
                    # Fallback: Direct quote from data provider
                    quote = self.data_provider.get_quote(symbol)
                    return {
                        "symbol": symbol,
                        "price": quote.last if hasattr(quote, 'last') else quote,
                        "timestamp": str(datetime.now())
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
            logger.error(f"Failed to fetch market data for {symbol}: {e}")
            return {"error": f"Failed to fetch market data: {str(e)}"}

    def tool_place_order(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place a trading order through the broker.

        CRITICAL SAFETY CHECKS:
        - Validates order parameters
        - Prevents duplicate orders (60s cooldown)
        - Prevents double-sells (10s cooldown)
        - Enforces $25k minimum account protection
        - Blocks buying more of losing positions
        - Validates available cash (no margin!)
        - Tracks orders in learning database

        This is the MOST COMPLEX tool with extensive safety logic.
        """
        if not self.broker:
            return {"error": "Broker not connected"}

        try:
            # Normalize symbol
            symbol = inputs["symbol"].strip().upper()

            # Validate order parameters
            if inputs["quantity"] <= 0:
                return {"error": "Quantity must be positive", "blocked": True}

            if inputs.get("limit_price") and inputs["limit_price"] <= 0:
                return {"error": "Limit price must be positive", "blocked": True}

            # Check for duplicate orders within last 60 seconds
            current_time = time.time()
            order_signature = (symbol, inputs["side"], inputs["quantity"])

            for recent_order in self.recent_orders:
                recent_symbol = recent_order[0]
                recent_side = recent_order[1]
                recent_time = recent_order[3]

                # AGGRESSIVE CHECK: Block ANY sell for same symbol within 10 seconds
                if recent_symbol == symbol and recent_side == inputs["side"] and recent_side.lower() == "sell":
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
            self.recent_orders.append((symbol, inputs["side"], inputs["quantity"], current_time))

            # Cleanup old orders (>60 seconds)
            self.recent_orders = [o for o in self.recent_orders if (current_time - o[3]) < 60]

            # HARD BLOCK: Forbidden/protected symbols (e.g. SPY Roth IRA benchmark)
            if self.parent_agent and hasattr(self.parent_agent, 'state_manager'):
                if self.parent_agent.state_manager.is_symbol_forbidden(symbol):
                    order_side_str = inputs['side'].lower()
                    error_msg = (
                        'BLOCKED: Cannot ' + order_side_str + ' ' + symbol + ' - protected benchmark position. '
                        'This is a pre-existing holding the agent must not trade.'
                    )
                    logger.error(error_msg)
                    return {
                        'error': error_msg,
                        'blocked': True,
                        'reason': 'forbidden_symbol_protected',
                        'symbol': symbol
                    }

            # Map string side to enum
            side = OrderSide.BUY if inputs["side"].lower() == "buy" else OrderSide.SELL

            # Get current account info for safety checks
            current_account = self.broker.get_account_info()
            current_value = current_account.portfolio_value
            available_cash = current_account.cash

            # CRITICAL: $25k minimum account protection
            MINIMUM_ACCOUNT_VALUE = 25000.0

            logger.info(f"CAPITAL CHECK: Account=${current_value:.2f}, Cash=${available_cash:.2f}, Order={symbol} qty={inputs['quantity']}")

            # EMERGENCY CHECK: Block all BUYs if below $25k
            if current_value < MINIMUM_ACCOUNT_VALUE:
                deficit = MINIMUM_ACCOUNT_VALUE - current_value
                error_msg = f"EMERGENCY BLOCK: Account value ${current_value:.2f} is BELOW $25k minimum by ${deficit:.2f}. ALL BUY orders blocked."
                logger.error(error_msg)
                return {
                    "error": error_msg,
                    "blocked": True,
                    "reason": "account_below_25k_minimum",
                    "current_value": current_value,
                    "minimum_required": MINIMUM_ACCOUNT_VALUE,
                    "deficit": deficit
                }

            if side == OrderSide.BUY:
                # Get validated quote for cost calculation
                limit_price = inputs.get("limit_price", 0)
                if not limit_price or limit_price <= 0:
                    if self.parent_agent and hasattr(self.parent_agent, '_get_validated_quote'):
                        validated_quote = self.parent_agent._get_validated_quote(symbol)
                        limit_price = validated_quote["consensus_price"]
                    else:
                        limit_price = 100  # Conservative fallback

                # Calculate order cost
                order_cost = inputs["quantity"] * limit_price
                logger.info(f"ORDER COST: {inputs['quantity']} shares @ ${limit_price:.2f} = ${order_cost:.2f}")

                # CRITICAL CHECK: Block buying MORE of a LOSING position (no averaging down!)
                if self.parent_agent and hasattr(self.parent_agent, 'agent_opened_positions'):
                    agent_positions = self.parent_agent.agent_opened_positions
                    entry_prices = self.parent_agent.agent_position_entry_prices

                    if symbol in agent_positions and agent_positions[symbol] > 0:
                        entry_price_existing = entry_prices.get(symbol)
                        if entry_price_existing and limit_price < entry_price_existing:
                            loss_pct = ((limit_price - entry_price_existing) / entry_price_existing) * 100
                            error_msg = f"BLOCKED: Cannot buy MORE of {symbol} while position is LOSING ({loss_pct:.1f}%). Sell first!"
                            logger.error(error_msg)
                            return {
                                "error": error_msg,
                                "blocked": True,
                                "reason": "buying_more_of_loser",
                                "current_price": limit_price,
                                "entry_price": entry_price_existing,
                                "loss_percent": loss_pct
                            }

                # CHECK: Order exceeds available cash
                if order_cost > available_cash:
                    error_msg = f"BLOCKED: Order cost ${order_cost:.2f} exceeds available cash ${available_cash:.2f}"
                    logger.error(error_msg)
                    return {
                        "error": error_msg,
                        "blocked": True,
                        "reason": "exceeds_available_cash",
                        "order_cost": order_cost,
                        "available_cash": available_cash
                    }

                # CHECK: Order would bring account below $25k
                projected_value = current_value - order_cost
                if projected_value < MINIMUM_ACCOUNT_VALUE:
                    error_msg = f"BLOCKED: Order would bring account below $25k minimum (projected: ${projected_value:.2f})"
                    logger.error(error_msg)
                    return {
                        "error": error_msg,
                        "blocked": True,
                        "reason": "would_violate_25k_minimum"
                    }

            # Map order type
            order_type_map = {
                "market": OrderType.MARKET,
                "limit": OrderType.LIMIT,
                "stop": OrderType.STOP,
                "moc": OrderType.MOC
            }
            order_type = order_type_map.get(inputs["order_type"].lower(), OrderType.MARKET)

            # Place the order through broker
            logger.info(f"Placing order: {side.name} {inputs['quantity']} {symbol} @ {order_type.name}")

            order_result = self.broker.place_order(
                symbol=symbol,
                side=side,
                quantity=inputs["quantity"],
                order_type=order_type,
                limit_price=inputs.get("limit_price"),
                stop_price=inputs.get("stop_price")
            )

            # Track BUY order entry in learning database
            if self.learning_db and order_result and side == OrderSide.BUY:
                try:
                    self.learning_db.record_trade_entry(
                        symbol=symbol,
                        entry_price=inputs.get("limit_price", 0),
                        why_entered=inputs.get("reason", ""),
                        shares=inputs["quantity"],
                        order_id=str(order_result.order_id) if hasattr(order_result, 'order_id') else None
                    )
                except Exception as e:
                    logger.warning(f"Failed to record trade entry in learning DB: {e}")

            # Update parent agent position tracking for BUY orders
            if side == OrderSide.BUY and self.parent_agent:
                if hasattr(self.parent_agent, 'agent_opened_positions'):
                    current_qty = self.parent_agent.agent_opened_positions.get(symbol, 0)
                    self.parent_agent.agent_opened_positions[symbol] = current_qty + inputs["quantity"]
                    logger.info(f"Updated position tracking: {symbol} = {self.parent_agent.agent_opened_positions[symbol]} shares")

            return {
                "success": True,
                "order_id": str(order_result.order_id) if hasattr(order_result, 'order_id') else "pending",
                "symbol": symbol,
                "side": side.name,
                "quantity": inputs["quantity"],
                "order_type": order_type.name,
                "message": f"Order placed successfully"
            }

        except Exception as e:
            logger.error(f"Order placement failed for {symbol}: {e}", exc_info=True)
            return {"error": f"Order placement failed: {str(e)}"}

    def tool_place_bracket_order(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place a bracket order (entry + TP + SL in one atomic operation).

        PREFERRED for BUY entries because:
        - Guarantees TP/SL are set when entry fills
        - Single API call (no race conditions)
        - Broker enforces OCO on exits
        - Prevents naked positions

        Delegates to parent agent's place_bracket_order method if available.
        """
        if not self.broker:
            return {"error": "Broker not connected"}

        try:
            symbol = inputs["symbol"].strip().upper()

            # Validate prices
            if inputs["entry_price"] <= 0:
                return {"error": "Entry price must be positive"}
            if inputs["take_profit_price"] <= inputs["entry_price"]:
                logger.warning(
                    "Take profit price %.4f <= entry price %.4f - "
                    "invalid for bracket order; trailing stops will manage exits",
                    inputs["take_profit_price"], inputs["entry_price"]
                )
            if inputs["stop_loss_price"] >= inputs["entry_price"]:
                logger.warning(
                    "Stop loss price %.4f >= entry price %.4f - "
                    "invalid for bracket order; trailing stops will manage exits",
                    inputs["stop_loss_price"], inputs["entry_price"]
                )

            # TP floor: clamp take profit to at least entry * 1.12 (12% upside minimum).
            # Prevents degenerate bracket orders where TP is too close to entry and
            # the OCO leg fires immediately on normal volatility.
            tp_floor = inputs["entry_price"] * 1.12
            if inputs["take_profit_price"] < tp_floor:
                logger.info(
                    "[TPFloor] Clamping TP from %.4f to %.4f "
                    "(12%% floor on entry %.4f)",
                    inputs["take_profit_price"], tp_floor, inputs["entry_price"]
                )
                inputs["take_profit_price"] = tp_floor

            # Capture entry context before placing - used by scheduler to populate learning DB
            reason = inputs.get("reason", "bracket_order")
            # Generate canonical UUID4 trade_id at entry time (broker order_id used as bridge only)
            canonical_trade_id = str(uuid.uuid4())
            # lessons_applied: explicit list from Grok (preferred) or empty (fallback: parsed from reason text)
            lessons_applied_raw = inputs.get("lessons_applied", None)
            if isinstance(lessons_applied_raw, list):
                lessons_applied = [int(x) for x in lessons_applied_raw if str(x).isdigit() or isinstance(x, int)]
            else:
                lessons_applied = None  # will be auto-parsed from reason text in learning_database
            hold_type = inputs.get("hold_type")
            hold_mode = inputs.get("hold_mode")
            if hold_mode is None and (
                str(hold_type or "").lower() in {"swing", "pead", "rotation"}
                or getattr(self.parent_agent, "current_context", "") == "swing_scan_session"
            ):
                hold_mode = "swing"
            self.pending_entry_context[symbol] = {
                "why_entered": reason,
                "catalyst": inputs.get("catalyst", "premarket_scan"),
                "market_context": inputs.get("market_context", "unknown"),
                "setup_type": inputs.get("setup_type", "bracket_order"),
                "candidate_lane": inputs.get("candidate_lane"),
                "strategy": inputs.get("strategy"),
                "hold_mode": hold_mode,
                "hold_type": hold_type,
                "next_earnings_date": inputs.get("next_earnings_date"),
                "profit_target": inputs.get("profit_target", inputs.get("take_profit_price")),
                "initial_stop": inputs.get("initial_stop", inputs.get("stop_loss_price")),
                "current_stop": inputs.get("current_stop", inputs.get("stop_loss_price")),
                "confidence_level": inputs.get("confidence_level", 7.0),
                "entry_price": inputs["entry_price"],
                "lessons_applied": lessons_applied,
                "trade_id": canonical_trade_id,
                "captured_at": datetime.now().isoformat()
            }
            raw_decision_packet = inputs.get("decision_packet")
            candidate_meta = self._get_current_swing_scan_candidate(symbol) or {}
            if isinstance(raw_decision_packet, dict):
                decision_packet = build_final_swing_decision_packet(
                    raw_decision_packet,
                    candidate_meta=candidate_meta,
                    default_action="BUY_STOP",
                )
            else:
                decision_packet = build_final_swing_decision_packet(
                    {
                        "symbol": symbol,
                        "action": "BUY_STOP",
                        "candidate_lane": inputs.get("candidate_lane"),
                        "setup_type": inputs.get("setup_type"),
                        "conviction": inputs.get("confidence_level"),
                        "buy_stop_price": inputs.get("entry_price"),
                        "stop_loss_price": inputs.get("stop_loss_price"),
                        "hold_type": inputs.get("hold_type"),
                        "reason": reason,
                    },
                    candidate_meta=candidate_meta,
                    default_action="BUY_STOP",
                )
            self.pending_entry_context[symbol]["decision_packet"] = decision_packet

            # Delegate to parent agent if available (has full safety checks)
            if self.parent_agent and hasattr(self.parent_agent, 'place_bracket_order'):
                result = self.parent_agent.place_bracket_order(
                    symbol=symbol,
                    quantity=inputs["quantity"],
                    entry_price=inputs["entry_price"],
                    take_profit_price=inputs["take_profit_price"],
                    stop_loss_price=inputs["stop_loss_price"],
                    reason=reason
                )
                # Store order_id in context for full DB linkage
                if isinstance(result, dict) and result.get("entry_order_id"):
                    self.pending_entry_context[symbol]["order_id"] = result["entry_order_id"]
                return result
            else:
                # parent_agent.place_bracket_order() not available - call broker directly.
                # broker.place_bracket_order() is atomic (single API call: entry + TP + SL OCO).
                logger.info(
                    "Parent agent does not have place_bracket_order - "
                    "calling broker.place_bracket_order() directly."
                )

                bracket_result = self.broker.place_bracket_order(
                    symbol=symbol,
                    entry_price=inputs["entry_price"],
                    take_profit_price=inputs["take_profit_price"],
                    stop_loss_price=inputs["stop_loss_price"],
                    quantity=inputs["quantity"]
                )

                order_id = str(bracket_result.order_id) if hasattr(bracket_result, 'order_id') else "pending"
                self.pending_entry_context[symbol]["order_id"] = order_id

                return {
                    "success": True,
                    "message": "Bracket order placed (entry + TP + SL, atomic)",
                    "entry_order_id": order_id,
                    "symbol": symbol,
                    "quantity": inputs["quantity"],
                    "entry_price": inputs["entry_price"],
                    "take_profit_price": inputs["take_profit_price"],
                    "stop_loss_price": inputs["stop_loss_price"],
                    "mode": "direct_broker_bracket"
                }

        except Exception as e:
            logger.error(f"Bracket order failed for {symbol}: {e}", exc_info=True)
            return {"error": f"Bracket order failed: {str(e)}"}

    def tool_get_account_info(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get current account information.

        Returns:
        - Portfolio value
        - Cash balance
        - Buying power
        - Current positions with P&L
        - Open orders
        """
        if not self.broker:
            return {"error": "Broker not connected"}

        try:
            account = self.broker.get_account_info()

            # Format positions
            from datetime import date as _date
            positions = []
            for pos in account.positions:
                quantity = get_position_quantity(pos)
                pos_dict = {
                    "symbol": pos.symbol,
                    "quantity": quantity,
                    "avg_entry_price": pos.avg_entry_price,
                    "current_price": pos.current_price,
                    "market_value": quantity * pos.current_price,
                    "unrealized_pl": pos.unrealized_pnl,
                    "unrealized_pl_percent": pos.unrealized_pnl_percent
                }
                # Attach swing metadata when available (entry_date, days_held, stop levels)
                if self.position_manager is not None:
                    meta = self.position_manager.get_swing_metadata(pos.symbol)
                    if meta:
                        entry_date_str = meta.get("entry_date")
                        days_held = None
                        if entry_date_str:
                            try:
                                entry_date = _date.fromisoformat(entry_date_str)
                                days_held = (_date.today() - entry_date).days
                            except Exception:
                                pass
                        pos_dict["entry_date"] = entry_date_str
                        pos_dict["days_held"] = days_held
                        pos_dict["hold_type"] = meta.get("hold_type")
                        pos_dict["current_stop"] = meta.get("current_stop")
                        pos_dict["initial_stop"] = meta.get("initial_stop")
                        pos_dict["entry_catalyst"] = meta.get("entry_catalyst")
                        ned = meta.get("next_earnings_date")
                        pos_dict["next_earnings_date"] = (
                            ned.isoformat() if hasattr(ned, "isoformat") else ned
                        )
                # Aliases: active_rules.txt uses entry_price, shares_remaining
                pos_dict["entry_price"] = pos_dict.get("avg_entry_price")
                # shares_remaining: use swing metadata value if available, else broker quantity
                pos_dict["shares_remaining"] = pos_dict.get("quantity")
                positions.append(pos_dict)

            result = {
                "portfolio_value": account.portfolio_value,
                "cash": account.cash,
                "buying_power": account.buying_power,
                "positions": positions,
                "position_count": len(positions)
            }
            # Alias: active_rules.txt uses account_equity
            result["account_equity"] = result["portfolio_value"]
            return result

        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return {"error": f"Failed to get account info: {str(e)}"}

    def tool_create_trading_plan(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a complete trading plan with conviction-based capital allocation.

        MANDATORY before placing orders. Prevents overbuying by:
        - Allocating total risk across ALL planned positions
        - Using conviction scores to weight allocations
        - Validating against available buying power
        - Returning exact position sizes to use

        This is THE CORE of proper portfolio management.

        Gate 1 = Option A: risk_percent is % of TOTAL portfolio value (not just tradeable cash).
        Example: 1% risk on $50k portfolio = $500 total risk budget across all positions.
        PDT $25k floor is still enforced for position validation (cannot trade into protected capital).
        """
        try:
            candidates = inputs["trading_candidates"]
            # Gate 1 = Option A: 1% of total portfolio value (not tradeable cash)
            risk_percent = inputs.get("risk_percent", 1.0)

            # Get current account info
            if not self.broker:
                return {"error": "Broker not connected"}

            account = self.broker.get_account_info()
            portfolio_value = account.portfolio_value
            available_cash = account.cash

            # $25k PDT base is protected and cannot be traded.
            # Tradeable capital = cash above the $25k floor.
            BASE_CAPITAL = 25000.0
            tradeable_cash = max(0.0, available_cash - BASE_CAPITAL)

            if tradeable_cash <= 0:
                return {
                    "error": f"No tradeable capital available. Cash ${available_cash:.2f} is at or below the $25k PDT base. Cannot open new positions.",
                    "available_cash": available_cash,
                    "base_capital": BASE_CAPITAL,
                    "tradeable_cash": tradeable_cash
                }

            # Risk budget is based on TOTAL PORTFOLIO VALUE (Gate 1 = Option A).
            # This means 1% risk on a $50k portfolio = $500 total risk budget.
            # The $25k PDT floor is still protected via tradeable_cash validation below.
            total_risk_dollars = portfolio_value * (risk_percent / 100.0)

            # Per-position hard cap: no single position > 50% of tradeable cash.
            # Prevents any one trade from wiping out the trading account.
            per_position_cap = tradeable_cash * 0.50

            # Calculate total conviction
            total_conviction = sum(c["conviction_score"] for c in candidates)

            # Allocate risk proportionally by conviction
            plan_details = []
            for candidate in candidates:
                symbol = candidate["symbol"].upper()
                entry_price = candidate["entry_price"]
                stop_price = candidate["stop_price"]
                conviction = candidate["conviction_score"]
                strategy = candidate["strategy"]
                take_profit = candidate.get("take_profit")

                # Calculate conviction-weighted risk allocation
                conviction_weight = conviction / total_conviction
                position_risk = total_risk_dollars * conviction_weight

                # Calculate position size from risk
                risk_per_share = abs(entry_price - stop_price)
                if risk_per_share == 0:
                    logger.warning(
                        f"create_trading_plan: {symbol} entry_price == stop_price "
                        f"({entry_price}) - skipping (zero risk per share)"
                    )
                    continue
                position_size = int(position_risk / risk_per_share)

                # Calculate position cost and enforce per-position cap
                position_cost = position_size * entry_price
                if position_cost > per_position_cap and entry_price > 0:
                    position_size = int(per_position_cap / entry_price)
                    position_cost = position_size * entry_price

                plan_details.append({
                    "symbol": symbol,
                    "conviction_score": conviction,
                    "conviction_weight": conviction_weight,
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "take_profit": take_profit,
                    "strategy": strategy,
                    "position_size": position_size,
                    "position_cost": position_cost,
                    "allocated_risk": position_risk
                })

            # Calculate total cost
            total_cost = sum(p["position_cost"] for p in plan_details)

            # Validate against tradeable cash (not total cash)
            if total_cost > tradeable_cash:
                return {
                    "error": f"Trading plan exceeds tradeable capital: ${total_cost:.2f} needed, ${tradeable_cash:.2f} tradeable (cash ${available_cash:.2f} minus $25k base)",
                    "total_cost": total_cost,
                    "available_cash": available_cash,
                    "tradeable_cash": tradeable_cash,
                    "suggestion": "Reduce position sizes or number of positions"
                }

            # Store plan with timestamp
            self.last_trading_plan_timestamp = time.time()
            self.current_trading_plan = plan_details

            # Return plan
            return {
                "success": True,
                "message": "Trading plan created successfully",
                "portfolio_value": portfolio_value,
                "available_cash": available_cash,
                "base_capital_protected": BASE_CAPITAL,
                "tradeable_cash": tradeable_cash,
                "total_risk_percent": risk_percent,
                "total_risk_dollars": total_risk_dollars,
                "risk_basis": f"{risk_percent}% of portfolio_value (${portfolio_value:.2f})",
                "per_position_cap": per_position_cap,
                "total_cost": total_cost,
                "tradeable_cash_after_entries": tradeable_cash - total_cost,
                "positions": plan_details,
                "position_count": len(plan_details)
            }

        except Exception as e:
            logger.error(f"Failed to create trading plan: {e}", exc_info=True)
            return {"error": f"Failed to create trading plan: {str(e)}"}

    def tool_calculate_position_size(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        REMOVED FROM TOOL SCHEMA (Phase 6.2 dual sizing merge).

        This tool has been removed from the available tools list. It is not invocable by the agent.
        Claude must use create_trading_plan instead (Gate 1 = Option A: 1% portfolio value).

        This method is kept as a tombstone to prevent AttributeError if called directly.
        """
        logger.error(
            "calculate_position_size called but tool is removed from schema. "
            "Use create_trading_plan instead (Gate 1 = Option A)."
        )
        return {
            "error": "This tool has been removed. Use create_trading_plan instead.",
            "action": "Call create_trading_plan with your trading_candidates list"
        }

    def tool_get_market_time_info(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get current market time and status.

        Returns:
        - Current time (ET)
        - Market status (open/closed/premarket/afterhours)
        - Minutes until market open/close
        """
        try:
            from datetime import datetime
            import pytz

            # Get ET time
            et_tz = pytz.timezone('US/Eastern')
            now_et = datetime.now(et_tz)

            # Market hours: 9:30 AM - 4:00 PM ET
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

            # Determine market status
            if now_et < market_open:
                status = "premarket"
                minutes_until = int((market_open - now_et).total_seconds() / 60)
                message = f"Market opens in {minutes_until} minutes"
            elif now_et > market_close:
                status = "afterhours"
                # Calculate time until tomorrow's open
                tomorrow_open = market_open.replace(day=market_open.day + 1)
                minutes_until = int((tomorrow_open - now_et).total_seconds() / 60)
                message = f"Market closed - opens in {minutes_until} minutes"
            else:
                status = "open"
                minutes_until = int((market_close - now_et).total_seconds() / 60)
                message = f"Market open - closes in {minutes_until} minutes"

            return {
                "current_time": now_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "market_status": status,
                "minutes_until_change": minutes_until,
                "message": message
            }

        except Exception as e:
            logger.error(f"Failed to get market time: {e}")
            return {"error": f"Failed to get market time: {str(e)}"}

    def tool_search_market_news(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search for market news and catalysts.

        Uses Perplexity Sonar (live web search) if available, falls back
        to Alpaca get_news() (archived ticker news, symbol-based only).
        """
        try:
            query = inputs["query"]
            limit = inputs.get("limit", 10)

            from datetime import date as _date_cls
            today_iso = _date_cls.today().isoformat()

            # --- Perplexity path (preferred: live web search) ---
            perplexity = getattr(self, 'perplexity_provider', None)
            if perplexity and perplexity.is_available():
                # get_catalyst_summary expects a symbol; extract first word as best guess
                symbol_guess = query.split()[0].upper() if query else 'SPY'
                result = perplexity.get_catalyst_summary(
                    symbol=symbol_guess,
                    candidate={'symbol': symbol_guess, 'change_pct': 0.0,
                               'rel_volume': 1.0, 'volume': 0}
                )
                if result and not result.get('error'):
                    # Perplexity is live search - publish_date approximates to today
                    # source_date from result if present (some providers include it)
                    pub_date = result.get("source_date") or today_iso
                    return {
                        "query": query,
                        "source": "perplexity",
                        "search_date": today_iso,
                        "article_count": 1,
                        "articles": [{
                            "summary": result.get("summary", ""),
                            "sentiment": result.get("sentiment", "neutral"),
                            "symbol": symbol_guess,
                            "publish_date": pub_date,
                            "source": "perplexity_live",
                        }]
                    }

            # --- Alpaca fallback (archived news, symbol-based) ---
            if not self.news_provider:
                return {"error": "No news provider available (Perplexity disabled, Alpaca not connected)"}

            if not hasattr(self.news_provider, 'get_news'):
                return {"error": "News provider has no get_news method"}

            # Extract a ticker symbol from the query for Alpaca symbol filter
            symbol_guess = query.split()[0].upper() if query else None
            articles_raw = self.news_provider.get_news(
                symbols=[symbol_guess] if symbol_guess else None,
                limit=limit,
                hours_back=24
            )
            articles_raw = articles_raw or []

            # Normalize articles: ensure publish_date field exists
            articles = []
            for art in articles_raw[:limit]:
                if isinstance(art, dict):
                    # Alpaca uses created_at, updated_at, or timestamp
                    pub = (art.get("created_at") or art.get("updated_at")
                           or art.get("timestamp") or art.get("date") or today_iso)
                    if hasattr(pub, "isoformat"):
                        pub = pub.isoformat()
                    elif pub and "T" in str(pub):
                        pub = str(pub)[:10]
                    art_copy = dict(art)
                    art_copy["publish_date"] = str(pub)[:10] if pub else today_iso
                    articles.append(art_copy)

            return {
                "query": query,
                "source": "alpaca",
                "search_date": today_iso,
                "article_count": len(articles),
                "articles": articles
            }

        except Exception as e:
            logger.error("News search failed: %s", e)
            return {"error": "News search failed: %s" % str(e)}

    def tool_set_trading_strategy(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Declare the current trading strategy.

        Used for:
        - Strategy performance tracking
        - Learning/optimization
        - Trade context annotation
        """
        try:
            strategy = inputs["strategy"]
            reason = inputs["reason"]

            # Update parent agent state if available
            if self.parent_agent and hasattr(self.parent_agent, 'state'):
                if 'current_strategies' not in self.parent_agent.state:
                    self.parent_agent.state['current_strategies'] = []

                # Add to strategies list if not already there
                if strategy not in self.parent_agent.state['current_strategies']:
                    self.parent_agent.state['current_strategies'].append(strategy)

            # Log to parent agent (learning database is just for storage)
            if self.parent_agent and hasattr(self.parent_agent, 'log_strategy_change'):
                self.parent_agent.log_strategy_change(
                    strategy=strategy,
                    reason=reason
                )

            logger.info(f"Trading strategy set: {strategy} - {reason}")

            return {
                "success": True,
                "strategy": strategy,
                "reason": reason,
                "message": f"Strategy set to: {strategy}"
            }

        except Exception as e:
            logger.error(f"Failed to set strategy: {e}")
            return {"error": f"Failed to set strategy: {str(e)}"}

    def tool_get_market_regime(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get swing trading market regime with full gate breakdown.

        Returns FORCESWING-compatible regime: swing_score (0-5), mode (FULL/REDUCED/CASH),
        per-gate pass/fail with raw values, VIX level/mode, and VIX daily delta
        for spike detection (>= 5 pts triggers all-exit rule).

        Falls back to day-trader gap-fade regime if swing regime_filter not wired.
        """
        try:
            # Swing regime filter (primary path - injected by scheduler after agent init)
            regime_filter = getattr(self, 'regime_filter', None)
            if regime_filter is not None and hasattr(regime_filter, 'get_swing_regime_detail'):
                return regime_filter.get_swing_regime_detail()

            # Legacy day-trader regime fallback
            if self.trading_logic and hasattr(self.trading_logic, 'get_market_regime'):
                return self.trading_logic.get_market_regime()
            elif self.parent_agent and hasattr(self.parent_agent, '_get_market_regime'):
                return self.parent_agent._get_market_regime()
            else:
                return {
                    "regime": "unknown",
                    "message": "Market regime analysis not available - regime_filter not wired"
                }

        except Exception as e:
            logger.error("Failed to get market regime: %s", e)
            return {"error": "Failed to get market regime: %s" % str(e)}

    def tool_analyze_multi_timeframe(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze symbol across daily and weekly timeframes for swing entry confirmation.

        Daily view: SMA50 position, recent trend direction (higher highs/lows)
        Weekly view: above weekly SMA10, weekly RSI overbought check, weekly trend
        Swing relevance: confirms trend alignment before placing buy stop order.
        """
        try:
            symbol = inputs["symbol"]

            # Try trading_logic delegate first (legacy day-trader path)
            if self.trading_logic and hasattr(self.trading_logic, 'analyze_multi_timeframe'):
                base = self.trading_logic.analyze_multi_timeframe(symbol)
                if base and not base.get("message"):
                    return base

            # Swing-specific multi-timeframe via yfinance
            try:
                import yfinance as yf
                tick = yf.Ticker(symbol)

                # Daily bars (60 days for SMA50 + trend)
                daily = tick.history(period="60d", interval="1d")
                # Weekly bars (12 weeks for RSI + weekly trend)
                weekly = tick.history(period="12wk", interval="1wk")

                result = {"symbol": symbol}

                if daily is not None and len(daily) >= 20:
                    dc = list(daily["Close"])
                    dh = list(daily["High"])
                    dl = list(daily["Low"])
                    sma20d = sum(dc[-20:]) / 20
                    sma50d = sum(dc[-50:]) / 50 if len(dc) >= 50 else None
                    last_d = dc[-1]
                    # Daily higher-highs check (last 5 days)
                    hh5 = all(dh[-i] > dh[-i-1] for i in range(1, 4)) if len(dh) >= 5 else None
                    hl5 = all(dl[-i] > dl[-i-1] for i in range(1, 4)) if len(dl) >= 5 else None
                    result["daily"] = {
                        "last_close": round(last_d, 2),
                        "sma20": round(sma20d, 2),
                        "sma50": round(sma50d, 2) if sma50d else None,
                        "above_sma50": last_d > sma50d if sma50d else None,
                        "uptrend_higher_highs_5d": hh5,
                        "uptrend_higher_lows_5d": hl5,
                        "daily_trend": "UP" if (sma50d and last_d > sma50d) else ("DOWN" if sma50d else "UNKNOWN"),
                    }

                if weekly is not None and len(weekly) >= 6:
                    wc = list(weekly["Close"])
                    wh = list(weekly["High"])
                    last_w = wc[-1]
                    sma10w = sum(wc[-10:]) / min(10, len(wc))
                    # Weekly RSI(14) - simplified via last 14 weeks
                    if len(wc) >= 15:
                        diffs = [wc[i] - wc[i-1] for i in range(1, len(wc))]
                        gains = [max(0, d) for d in diffs[-14:]]
                        losses = [max(0, -d) for d in diffs[-14:]]
                        avg_g = sum(gains) / 14
                        avg_l = sum(losses) / 14
                        rsi_w = 100 - (100 / (1 + avg_g / avg_l)) if avg_l else 100.0
                    else:
                        rsi_w = None
                    ww_hh = all(wh[-i] > wh[-i-1] for i in range(1, 4)) if len(wh) >= 4 else None
                    result["weekly"] = {
                        "last_close": round(last_w, 2),
                        "sma10w": round(sma10w, 2),
                        "above_sma10w": last_w > sma10w,
                        "weekly_rsi14": round(rsi_w, 1) if rsi_w is not None else None,
                        "weekly_overbought": (rsi_w >= 70) if rsi_w is not None else None,
                        "weekly_higher_highs": ww_hh,
                        "weekly_trend": "UP" if last_w > sma10w else "DOWN",
                    }

                # Swing entry alignment summary
                daily_ok = result.get("daily", {}).get("above_sma50", None)
                weekly_ok = result.get("weekly", {}).get("above_sma10w", None)
                weekly_not_overbought = not result.get("weekly", {}).get("weekly_overbought", False)
                result["swing_entry_alignment"] = {
                    "daily_trend_up": daily_ok,
                    "weekly_trend_up": weekly_ok,
                    "weekly_not_overbought": weekly_not_overbought,
                    "aligned": bool(daily_ok and weekly_ok and weekly_not_overbought),
                    "note": "All 3 conditions needed for highest-conviction swing entry",
                }
                return result

            except Exception as mtf_err:
                return {
                    "symbol": symbol,
                    "error": "Multi-timeframe analysis failed: %s" % str(mtf_err),
                    "message": "Use analyze_technical_indicators for FORCESWING conditions instead",
                }

        except Exception as e:
            logger.error("Multi-timeframe analysis failed: %s", e)
            return {"error": "Multi-timeframe analysis failed: %s" % str(e)}

    def tool_check_correlation_risk(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check correlation risk with existing positions.

        Prevents over-concentration in correlated stocks.
        """
        try:
            symbol = inputs["symbol"]

            # Delegate to trading logic
            if self.trading_logic and hasattr(self.trading_logic, 'check_correlation_risk'):
                return self.trading_logic.check_correlation_risk(symbol)
            else:
                return {
                    "symbol": symbol,
                    "correlation_risk": "unknown",
                    "message": "Correlation analysis not available"
                }

        except Exception as e:
            logger.error(f"Correlation check failed: {e}")
            return {"error": f"Correlation check failed: {str(e)}"}

    def tool_get_strategy_performance(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get performance metrics for trading strategies.

        Returns win rate, R:R, confidence scores per strategy.
        """
        try:
            # Delegate to learning database
            if self.learning_db and hasattr(self.learning_db, 'get_strategy_performance'):
                return self.learning_db.get_strategy_performance()
            else:
                return {
                    "message": "Strategy performance tracking not available"
                }

        except Exception as e:
            logger.error(f"Failed to get strategy performance: {e}")
            return {"error": f"Failed to get strategy performance: {str(e)}"}

    def tool_calculate_dynamic_position_size(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate optimal position size using Kelly Criterion for a SINGLE position.

        Prefer create_trading_plan for multi-position plans (handles allocation correctly).
        Use this only when sizing a standalone position.

        Gate 1 = Option A: base risk = 1% of total portfolio value.

        Adjusts for:
        - Strategy performance
        - Volatility
        - Win/loss streak
        - Correlation risk
        """
        try:
            symbol = inputs["symbol"]
            entry_price = inputs["entry_price"]
            stop_price = inputs["stop_price"]
            strategy = inputs.get("strategy", "unknown")

            # Delegate to trading logic (Batch F: pass broker+learning_db for DynamicPositionSizer)
            if self.trading_logic and hasattr(self.trading_logic, 'calculate_dynamic_position_size'):
                return self.trading_logic.calculate_dynamic_position_size(
                    symbol=symbol,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    strategy=strategy,
                    broker=self.broker,
                    learning_db=self.learning_db,
                )
            else:
                # Fallback to basic calculation
                risk_per_share = abs(entry_price - stop_price)
                if risk_per_share == 0:
                    return {"error": "Entry and stop prices cannot be equal"}

                # Gate 1 = Option A: 1% of total portfolio value
                if self.broker:
                    account = self.broker.get_account_info()
                    risk_amount = account.portfolio_value * 0.01
                    position_size = int(risk_amount / risk_per_share)

                    return {
                        "position_size": position_size,
                        "method": "basic_1pct_portfolio_risk",
                        "message": "Dynamic sizing not available - used 1% portfolio value (Gate 1 = Option A)"
                    }
                else:
                    return {"error": "No sizing method available"}

        except Exception as e:
            logger.error(f"Dynamic position sizing failed: {e}")
            return {"error": f"Dynamic position sizing failed: {str(e)}"}

    def tool_analyze_technical_indicators(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze technical indicators including FORCESWING conditions.

        Returns RSI, Bollinger Bands (from trading_logic if available) PLUS
        swing-specific indicators: force3, force13, ADX(14), SMA10, SMA20, SMA50,
        above_sma10, above_sma20, declining_high_day1, declining_high_day2.

        FORCESWING checklist returned so agent can score the 0-2 pt rubric criterion
        without relying solely on the pre-scanned candidate list.
        """
        try:
            symbol = inputs["symbol"]
            timeframe = inputs.get("timeframe", "daily")

            # Base result from trading logic (RSI, BB, mean-reversion signal)
            result = {}
            if self.trading_logic and hasattr(self.trading_logic, 'analyze_technical_indicators'):
                base = self.trading_logic.analyze_technical_indicators(symbol, timeframe)
                if isinstance(base, dict):
                    result.update(base)

            # FORCESWING indicators via yfinance daily bars
            try:
                import yfinance as yf
                hist = yf.Ticker(symbol).history(period="40d")
                if hist is not None and len(hist) >= 22:
                    closes = list(hist["Close"])
                    highs  = list(hist["High"])
                    volumes = list(hist["Volume"])
                    n = len(closes)

                    # SMA calculations
                    sma10  = sum(closes[-10:]) / 10
                    sma20  = sum(closes[-20:]) / 20
                    sma50  = sum(closes[-50:]) / 50 if n >= 50 else None
                    last_c = closes[-1]

                    # Force Index (Elder): FI(1) = (close - prev_close) * volume
                    fi1 = [(closes[i] - closes[i-1]) * volumes[i] for i in range(1, n)]
                    # EMA helper
                    def _ema(series, period):
                        k = 2.0 / (period + 1)
                        e = series[0]
                        for v in series[1:]:
                            e = v * k + e * (1 - k)
                        return e
                    force3  = _ema(fi1[-15:], 3)  if len(fi1) >= 3  else None
                    force13 = _ema(fi1[-26:], 13) if len(fi1) >= 13 else None

                    # ADX(20) - Wilder's smoothing; active_rules.txt conviction rubric uses ADX(20)
                    adx20 = None
                    period = 20
                    if n >= period * 2 + 1:
                        trs, pdms, mdms = [], [], []
                        for i in range(1, n):
                            h, l = highs[i], hist["Low"].iloc[i]
                            ph = highs[i-1]; pl = hist["Low"].iloc[i-1]; pc = closes[i-1]
                            tr = max(h - l, abs(h - pc), abs(l - pc))
                            up = h - ph; dn = pl - l
                            pdms.append(up if up > dn and up > 0 else 0.0)
                            mdms.append(dn if dn > up and dn > 0 else 0.0)
                            trs.append(tr)
                        s_tr = sum(trs[:period]); s_pdm = sum(pdms[:period]); s_mdm = sum(mdms[:period])
                        dx_vals = []
                        for i in range(period, len(trs)):
                            s_tr  = s_tr  - s_tr  / period + trs[i]
                            s_pdm = s_pdm - s_pdm / period + pdms[i]
                            s_mdm = s_mdm - s_mdm / period + mdms[i]
                            if s_tr == 0:
                                continue
                            pdi = 100.0 * s_pdm / s_tr; mdi = 100.0 * s_mdm / s_tr
                            di_sum = pdi + mdi
                            dx_vals.append(100.0 * abs(pdi - mdi) / di_sum if di_sum else 0.0)
                        adx20 = round(sum(dx_vals[-period:]) / min(period, len(dx_vals)), 1) if dx_vals else None

                    # FORCESWING conditions
                    declining_high_day1 = (highs[-1] < highs[-2]) if n >= 2 else None
                    declining_high_day2 = (highs[-2] < highs[-3]) if n >= 3 else None
                    forceswing_conditions = {
                        "close_above_sma10": last_c > sma10,
                        "close_above_sma20": last_c > sma20,
                        "declining_high_day1": declining_high_day1,
                        "declining_high_day2": declining_high_day2,
                        "force3_lte_0": (force3 <= 0) if force3 is not None else None,
                        "force13_gte_0": (force13 >= 0) if force13 is not None else None,
                    }
                    conditions_met = sum(1 for v in forceswing_conditions.values() if v is True)
                    conditions_total = sum(1 for v in forceswing_conditions.values() if v is not None)

                    # --- RSI14 (Wilder's smoothing) ---
                    def _calc_rsi(cls_list, period=14):
                        if len(cls_list) < period + 1:
                            return None
                        deltas = [cls_list[i] - cls_list[i-1] for i in range(1, len(cls_list))]
                        gains = [max(0.0, d) for d in deltas]
                        losses = [max(0.0, -d) for d in deltas]
                        avg_gain = sum(gains[:period]) / period
                        avg_loss = sum(losses[:period]) / period
                        for i in range(period, len(gains)):
                            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                        if avg_loss == 0:
                            return 100.0
                        rs = avg_gain / avg_loss
                        return round(100.0 - 100.0 / (1.0 + rs), 2)

                    # --- ATR14 (Wilder's smoothing) ---
                    def _calc_atr(bar_list, period=14):
                        if len(bar_list) < period + 1:
                            return None
                        trs = []
                        for i in range(1, len(bar_list)):
                            h = bar_list[i]["high"]
                            l = bar_list[i]["low"]
                            pc = bar_list[i-1]["close"]
                            tr = max(h - l, abs(h - pc), abs(l - pc))
                            trs.append(tr)
                        atr = sum(trs[:period]) / period
                        for tr in trs[period:]:
                            atr = (atr * (period - 1) + tr) / period
                        return round(atr, 4)

                    bars_for_atr = [
                        {"high": float(hist["High"].iloc[i]),
                         "low": float(hist["Low"].iloc[i]),
                         "close": float(hist["Close"].iloc[i])}
                        for i in range(n)
                    ]

                    rsi14_daily = _calc_rsi(closes)
                    atr14_daily = _calc_atr(bars_for_atr)

                    ema21_daily = None
                    close_below_ema21 = None
                    if len(closes) >= 21:
                        ema21_daily = round(_ema(closes, 21), 4)
                        close_below_ema21 = closes[-1] < ema21_daily

                    result.update({
                        "symbol": symbol,
                        "last_close": round(last_c, 2),
                        "sma10": round(sma10, 2),
                        "sma20": round(sma20, 2),
                        "sma50": round(sma50, 2) if sma50 else None,
                        "adx20": adx20,
                        "force3": round(force3, 0) if force3 is not None else None,
                        "force13": round(force13, 0) if force13 is not None else None,
                        "forceswing_conditions": forceswing_conditions,
                        "forceswing_conditions_met": "%d/%d" % (conditions_met, conditions_total),
                        "forceswing_pass": conditions_met == 6,
                        # Swing exit indicators (per active_rules.txt naming)
                        "rsi14_daily": rsi14_daily,
                        "ema21_daily": ema21_daily,
                        "close_below_ema21": close_below_ema21,
                        "atr14_daily": atr14_daily,
                    })
            except Exception as fi_err:
                result["forceswing_error"] = "FORCESWING indicators unavailable: %s" % str(fi_err)

            result.setdefault("symbol", symbol)
            result.setdefault("timeframe", timeframe)
            return result

        except Exception as e:
            logger.error("Technical analysis failed: %s", e)
            return {"error": "Technical analysis failed: %s" % str(e)}

    def tool_extend_take_profit(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extend take-profit target for an open position.

        Only extends upward (never lowers TP).
        Replaces OCO bracket atomically.
        """
        try:
            symbol = inputs["symbol"].strip().upper()
            new_tp = inputs["new_take_profit"]
            reason = inputs["reason"]

            # Delegate to parent agent if available
            if self.parent_agent and hasattr(self.parent_agent, 'extend_take_profit'):
                return self.parent_agent.extend_take_profit(symbol, new_tp, reason)
            else:
                logger.warning("Extend TP: Parent agent not available")
                return {
                    "error": "TP extension not available in this configuration",
                    "message": "Requires parent agent with OCO bracket management"
                }

        except Exception as e:
            logger.error(f"Extend TP failed for {symbol}: {e}")
            return {"error": f"Failed to extend take profit: {str(e)}"}

    def tool_update_position_conviction(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update conviction score for an open position.

        CRITICAL: If conviction drops below entry threshold (8), triggers immediate exit.
        """
        try:
            symbol = inputs["symbol"].strip().upper()
            conviction = inputs["conviction"]
            reason = inputs["reason"]

            # Delegate to parent agent if available
            if self.parent_agent and hasattr(self.parent_agent, 'update_position_conviction'):
                return self.parent_agent.update_position_conviction(symbol, conviction, reason)
            else:
                logger.warning("Update conviction: Parent agent not available")
                return {
                    "message": f"Conviction updated to {conviction}/10 for {symbol}",
                    "reason": reason,
                    "warning": "Auto-exit on low conviction not available in this configuration"
                }

        except Exception as e:
            logger.error(f"Update conviction failed for {symbol}: {e}")
            return {"error": f"Failed to update conviction: {str(e)}"}

    def tool_record_candidate_evaluation(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Record the agent's evaluation result for a candidate it decided NOT to trade.

        Called at decision time so learning.db captures the actual conviction score,
        not the hardcoded 0 that the EOD fallback used to write.
        """
        try:
            symbol = inputs["symbol"].strip().upper()
            conviction_score = int(inputs.get("conviction_score", 0))
            rejection_reason = inputs.get("rejection_reason", "Agent passed - no reason given")
            strategy = inputs.get("strategy")
            # Extract and validate lessons_applied - coerce to list of ints
            raw_lessons = inputs.get("lessons_applied")
            if raw_lessons and isinstance(raw_lessons, list):
                lessons_applied = [int(x) for x in raw_lessons if isinstance(x, (int, float))]
            else:
                lessons_applied = []
            decision_packet = inputs.get("decision_packet")

            from trade_logging.daily_snapshot import DailySnapshot
            from datetime import date
            snapshot = DailySnapshot()
            snapshot.record_candidate_decision(
                trade_date=date.today(),
                symbol=symbol,
                conviction_score=conviction_score,
                rejection_reason=rejection_reason,
                strategy=strategy,
                lessons_applied=lessons_applied,
            )
            logger.info(
                f"[candidate-eval] {symbol}: conviction {conviction_score}/10 "
                f"strategy={strategy} lessons={lessons_applied} - {rejection_reason}"
            )

            followup_result = self._record_candidate_followup(
                symbol=symbol,
                conviction_score=conviction_score,
                rejection_reason=rejection_reason,
                strategy=strategy,
                lessons_applied=lessons_applied,
                decision_packet=decision_packet,
            )
            return {
                "recorded": True,
                "symbol": symbol,
                "conviction_score": conviction_score,
                "rejection_reason": rejection_reason,
                "strategy": strategy,
                "lessons_applied": lessons_applied,
                "candidate_followup_id": followup_result.get("candidate_followup_id"),
                "decision_journal_id": followup_result.get("decision_journal_id"),
            }
        except Exception as e:
            logger.warning(f"[candidate-eval] Failed to record pass for {inputs.get('symbol', '?')}: {e}")
            return {"recorded": False, "error": str(e)}

    def _get_current_swing_scan_candidate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return scheduler-registered scan metadata for the active swing scan session."""
        parent = self.parent_agent
        if not parent:
            return None
        if getattr(parent, "current_context", None) != "swing_scan_session":
            return None
        current_candidates = getattr(parent, "current_swing_scan_candidates", None) or {}
        candidate_meta = current_candidates.get(symbol.upper())
        if isinstance(candidate_meta, dict):
            return candidate_meta
        return None

    def _record_candidate_followup(
        self,
        *,
        symbol: str,
        conviction_score: int,
        rejection_reason: str,
        strategy: Optional[str],
        lessons_applied: List[int],
        decision_packet: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create decision-journal and follow-up records for final swing-scan passes."""
        candidate_meta = self._get_current_swing_scan_candidate(symbol)
        if not candidate_meta or not self.learning_db:
            return {}

        decision_journal_id = None
        candidate_followup_id = None

        try:
            scan_date = candidate_meta.get("scan_date") or date.today().isoformat()
            scan_date_obj = date.fromisoformat(scan_date)
        except Exception:
            scan_date_obj = date.today()
            scan_date = scan_date_obj.isoformat()

        try:
            from utils.market_calendar import get_market_calendar

            next_review_date = get_market_calendar().get_next_trading_day(scan_date_obj).isoformat()
        except Exception as e:
            logger.warning("[candidate-eval] Could not compute next trading day for %s: %s", symbol, e)
            next_review_date = scan_date

        evaluation_snapshot = dict(candidate_meta.get("candidate_snapshot") or {})
        final_packet = build_final_swing_decision_packet(
            decision_packet if isinstance(decision_packet, dict) else {
                "symbol": symbol,
                "action": "PASS",
                "setup_type": strategy,
                "conviction": conviction_score,
                "reason": rejection_reason,
            },
            candidate_meta=candidate_meta,
            default_action="PASS",
        )
        evaluation_snapshot["final_agent_evaluation"] = {
            "conviction_score": conviction_score,
            "rejection_reason": rejection_reason,
            "strategy": strategy,
            "lessons_applied": list(lessons_applied or []),
            "decision_packet": final_packet,
        }

        decision_payload = {
            "symbol": symbol,
            "conviction_score": conviction_score,
            "strategy": strategy,
            "decision_reason": rejection_reason,
            "candidate_lane": candidate_meta.get("candidate_lane"),
            "source_bucket": candidate_meta.get("source_bucket"),
            "leader_quality_score": candidate_meta.get("leader_quality_score"),
            "leader_quality_flags": candidate_meta.get("leader_quality_flags"),
            "in_plan": False,
            "decision_packet": final_packet,
        }
        market_context = (
            "context=swing_scan_session;"
            f"regime_mode={candidate_meta.get('regime_mode')};"
            f"swing_score={candidate_meta.get('swing_score')};"
            f"min_conviction_today={candidate_meta.get('min_conviction_today')};"
            f"leader_quality_score={candidate_meta.get('leader_quality_score')};"
            f"decision_packet_action={final_packet.get('recommended_action')}"
        )
        try:
            if hasattr(self.learning_db, "record_decision"):
                decision_journal_id = self.learning_db.record_decision(
                    decision_type="CANDIDATE_EVALUATION",
                    candidates_considered=[decision_payload],
                    decision_reason=rejection_reason,
                    agent_reasoning=(
                        f"{symbol} passed at conviction {conviction_score}/10 in swing scan session."
                    ),
                    market_context=market_context,
                    decision_date=scan_date_obj,
                )
        except Exception as e:
            logger.warning("[candidate-eval] Failed to journal decision for %s: %s", symbol, e)

        try:
            if hasattr(self.learning_db, "create_candidate_followup"):
                candidate_followup_id = self.learning_db.create_candidate_followup(
                    candidate_instance_id=str(
                        candidate_meta.get("candidate_instance_id")
                        or f"{scan_date}_{symbol}_{candidate_meta.get('candidate_lane', 'UNKNOWN')}"
                    ),
                    scan_date=scan_date,
                    scan_time=str(candidate_meta.get("scan_time") or datetime.now().strftime("%H:%M:%S")),
                    symbol=symbol,
                    candidate_lane=candidate_meta.get("candidate_lane"),
                    source=candidate_meta.get("source"),
                    source_bucket=candidate_meta.get("source_bucket"),
                    regime_mode=candidate_meta.get("regime_mode"),
                    swing_score=candidate_meta.get("swing_score"),
                    min_conviction_today=candidate_meta.get("min_conviction_today"),
                    decision_type="PASS",
                    decision_reason=rejection_reason,
                    agent_reasoning=(
                        f"Agent passed {symbol} at conviction {conviction_score}/10 during swing scan."
                    ),
                    forceswing_reason=candidate_meta.get("forceswing_reason"),
                    soft_miss_reasons=candidate_meta.get("soft_miss_reasons"),
                    catalyst_summary=candidate_meta.get("catalyst_summary"),
                    candidate_snapshot=evaluation_snapshot,
                    decision_journal_id=str(decision_journal_id) if decision_journal_id is not None else None,
                    trade_id=None,
                    next_review_date=next_review_date,
                )
        except Exception as e:
            logger.warning("[candidate-eval] Failed to create follow-up row for %s: %s", symbol, e)

        return {
            "decision_journal_id": decision_journal_id,
            "candidate_followup_id": candidate_followup_id,
        }

    # ==================== UTILITY METHODS ====================

    def get_tool_count(self) -> int:
        """Get count of registered tools."""
        return len(self.tool_handlers)

    def get_tool_names(self) -> List[str]:
        """Get list of registered tool names."""
        return list(self.tool_handlers.keys())

    def has_tool(self, tool_name: str) -> bool:
        """
        Check if a tool is registered.

        Args:
            tool_name: Tool name to check

        Returns:
            True if tool is registered
        """
        return tool_name in self.tool_handlers

    def get_tool_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """
        Get schema for a specific tool.

        Args:
            tool_name: Tool name

        Returns:
            Tool schema dict or None if not found
        """
        for tool in self.tools_schema:
            if tool["name"] == tool_name:
                return tool
        return None
