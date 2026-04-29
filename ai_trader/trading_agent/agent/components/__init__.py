"""
Trading Agent Components

Extracted components from the monolithic ClaudeTradingAgent for better organization.

Modules:
- technical_indicators: Pure calculation functions (RSI, Bollinger Bands, Volume Profile)
- pdt_manager: Pattern Day Trader tracking and limits
- capital_manager: Capital allocation and limits
- position_manager: Position management and bracket orders (TODO)
- trading_tools: Tool definitions for LLM agents (TODO)
"""

from .technical_indicators import (
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_volume_profile,
    calculate_technical_indicators
)

from .pdt_manager import PDTManager
from .capital_manager import CapitalManager

__all__ = [
    # Technical indicators
    'calculate_rsi',
    'calculate_bollinger_bands',
    'calculate_volume_profile',
    'calculate_technical_indicators',
    # Managers
    'PDTManager',
    'CapitalManager',
]
