"""
Grok API Backend for Simulator - mirrors production grok_client.py pattern.

Uses OpenAI SDK pointed at xAI endpoint, same as the live trading agent.
Model: grok-4.3 (same as production reasoning model)

When --backend grok is used:
- Trade evaluation (per-trade conviction scoring) uses Grok
- Lesson analysis (EOD pattern discovery) uses Grok
- This matches production exactly: same model makes decisions AND learns from them

Cost: higher than retired 4.1 fast models; use selectively for decision-grade simulation.
Speed: ~7 min per run vs ~65 min with local Ollama/DeepSeek
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Match production model name from broker_config.json
GROK_MODEL = "grok-4.3"
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_TIMEOUT = 60.0  # seconds - API is fast, no need for 180s Ollama timeout


def _get_client():
    """Create OpenAI client pointed at xAI, same as production grok_client.py."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "OpenAI package required for Grok backend. Run: pip install openai"
        )

    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable not set")

    return OpenAI(
        api_key=api_key,
        base_url=XAI_BASE_URL,
        timeout=GROK_TIMEOUT
    )


def call_grok(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 2000
) -> str:
    """
    Call Grok API using production pattern (OpenAI SDK + xAI endpoint).

    Args:
        prompt: User message / prompt text
        system: Optional system prompt
        temperature: Sampling temperature (0.3 = consistent scoring)
        max_tokens: Max tokens in response

    Returns:
        Response text string, or empty string on failure
    """
    try:
        client = _get_client()

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=GROK_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )

        text = response.choices[0].message.content or ""
        logger.debug(f"Grok ({GROK_MODEL}): {text[:80]}")
        return text

    except Exception as e:
        logger.error(f"Grok API call failed: {e}")
        return ""
