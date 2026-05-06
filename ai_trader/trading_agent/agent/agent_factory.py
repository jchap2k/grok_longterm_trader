"""
Agent Factory - Creates the appropriate AI trading agent based on configuration.

Supports:
- Claude (Anthropic) - Higher quality, higher cost
- Grok (xAI) - Strong reasoning, ~10x lower cost
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_ai_config() -> dict:
    """
    Load AI provider configuration from broker_config.json.

    Returns:
        Dict with ai_provider, model settings, and ollama config
    """
    # Find broker_config.json
    config_paths = [
        Path(__file__).parent.parent.parent / "ai_trader_data" / "broker_config.json",
        Path(__file__).parent.parent / "ai_trader_data" / "broker_config.json",
    ]

    config_file = None
    for path in config_paths:
        if path.exists():
            config_file = path
            break

    if not config_file:
        logger.warning("broker_config.json not found, using default (claude)")
        return {
            "ai_provider": "claude",
            "ai_providers": {
                "claude": {
                    "reasoning_model": "claude-sonnet-4-20250514",
                    "fast_model": "claude-3-5-haiku-20241022"
                }
            },
            "ollama": {"enabled": False}
        }

    try:
        with open(config_file, 'r') as f:
            config = json.load(f)

        return {
            "ai_provider": config.get("ai_provider", "claude"),
            "ai_providers": config.get("ai_providers", {}),
            "ollama": config.get("ollama", {"enabled": False})
        }
    except Exception as e:
        logger.error(f"Error loading broker_config.json: {e}")
        return {"ai_provider": "claude", "ai_providers": {}, "ollama": {"enabled": False}}


def create_trading_agent(
    broker=None,
    data_provider=None,
    news_provider=None,
    rules_file: str = "rules/active_rules.txt",
    force_provider: Optional[str] = None,
    learning_db=None
):
    """
    Create a trading agent based on configuration.

    Args:
        broker: Trading broker instance
        data_provider: Market data provider instance
        news_provider: News provider instance (always Alpaca for news API)
        rules_file: Path to trading rules file
        force_provider: Override config and force specific provider ('claude' or 'grok')
        learning_db: Learning database for trade journal (optional)

    Returns:
        Trading agent instance (ClaudeTradingAgent or GrokTradingAgent)
    """
    config = get_ai_config()
    provider = force_provider or config.get("ai_provider", "claude")
    providers_config = config.get("ai_providers", {})

    # Initialize Ollama provider if enabled
    ollama_provider = None
    ollama_config = config.get("ollama", {})
    if ollama_config.get("enabled", False):
        from .ollama_provider import OllamaProvider
        ollama_provider = OllamaProvider.from_config(ollama_config)
        if ollama_provider:
            logger.info(f"Ollama enabled: {ollama_config.get('model')} for pre-processing/reflection")
        else:
            logger.info("Ollama configured but unavailable - using API-only mode")

    logger.info(f"Creating trading agent with provider: {provider}")

    if provider == "grok":
        # Use Grok
        from .grok_trading_agent import GrokTradingAgent

        grok_config = providers_config.get("grok", {})
        reasoning_model = grok_config.get("reasoning_model", "grok-4.3")
        fast_model = grok_config.get("fast_model", "grok-4.20-non-reasoning")
        thinking_model = grok_config.get("thinking_model")  # Optional deep reasoning model
        use_thinking_for_regime = grok_config.get("use_thinking_for_morning_regime", False)

        logger.info(f"Using Grok models: reasoning={reasoning_model}, fast={fast_model}")
        if thinking_model and use_thinking_for_regime:
            logger.info(f"  Thinking model enabled for morning regime: {thinking_model}")
        elif thinking_model:
            logger.info(f"  Thinking model available but disabled: {thinking_model}")

        return GrokTradingAgent(
            rules_file=rules_file,
            model=reasoning_model,
            data_model=fast_model,
            thinking_model=thinking_model,
            use_thinking_for_regime=use_thinking_for_regime,
            broker=broker,
            data_provider=data_provider,
            news_provider=news_provider,
            learning_db=learning_db,
            ollama_provider=ollama_provider
        )

    else:
        # Default to Claude
        from .claude_trading_agent import ClaudeTradingAgent

        claude_config = providers_config.get("claude", {})
        reasoning_model = claude_config.get("reasoning_model", "claude-sonnet-4-20250514")
        fast_model = claude_config.get("fast_model", "claude-3-5-haiku-20241022")

        logger.info(f"Using Claude models: reasoning={reasoning_model}, fast={fast_model}")

        return ClaudeTradingAgent(
            rules_file=rules_file,
            model=reasoning_model,
            data_model=fast_model,
            broker=broker,
            data_provider=data_provider,
            news_provider=news_provider,
            learning_db=learning_db,
            ollama_provider=ollama_provider
        )


def get_available_providers() -> dict:
    """
    Check which AI providers are available (have valid API keys).

    Returns:
        Dict with provider names and availability status
    """
    available = {}

    # Check Claude
    try:
        from config.credentials_manager import get_credentials_manager
        creds = get_credentials_manager()

        anthropic_key = creds.get_anthropic_key()
        available["claude"] = anthropic_key is not None
    except Exception:
        available["claude"] = False

    # Check Grok
    try:
        from config.credentials_manager import get_credentials_manager
        creds = get_credentials_manager()

        xai_key = creds.get_xai_key()
        available["grok"] = xai_key is not None
    except Exception:
        available["grok"] = False

    return available


if __name__ == "__main__":
    # Quick test
    print("AI Provider Configuration:")
    config = get_ai_config()
    print(f"  Active provider: {config['ai_provider']}")
    print(f"  Available providers: {list(config['ai_providers'].keys())}")
    print()

    print("Provider Availability:")
    available = get_available_providers()
    for provider, is_available in available.items():
        status = "[OK] Available" if is_available else "[X] Not available"
        print(f"  {provider}: {status}")
