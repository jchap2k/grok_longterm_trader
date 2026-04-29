"""
Ollama Provider - Local LLM inference via Ollama REST API.

Used as a pre-processor and cost-reduction layer:
- Data formatting and cleaning (scanner results, MTF data)
- News summarization before passing to main AI
- EOD trade reflection and lesson generation
- Morning context summary generation

Main AI (Grok/Claude) is reserved for actual trade decisions only.

Requires Ollama running locally: https://ollama.com
Recommended model: qwen3:32b (fits in 24GB VRAM, thinking mode available)

Config in broker_config.json:
    "ollama": {
        "enabled": false,
        "base_url": "http://localhost:11434",
        "model": "qwen3:32b",
        "fallback_on_error": true,
        "timeout_seconds": 120
    }
"""

import json
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class OllamaProvider:
    """
    Local LLM provider via Ollama REST API.

    Handles all non-time-critical LLM tasks locally to reduce API costs.
    Falls back gracefully to None/error if Ollama is unavailable.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:32b",
        fallback_on_error: bool = True,
        timeout_seconds: int = 120
    ):
        """
        Initialize Ollama provider.

        Args:
            base_url: Ollama server URL (default: http://localhost:11434)
            model: Model name to use (default: qwen3:32b)
            fallback_on_error: If True, return None on errors instead of raising
            timeout_seconds: Request timeout (32b model needs ~60-120s for complex tasks)
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallback_on_error = fallback_on_error
        self.timeout = timeout_seconds
        self._available: Optional[bool] = None  # Cached after first check

        logger.info(f"OllamaProvider initialized: {self.base_url} model={self.model}")

    def is_available(self) -> bool:
        """
        Check if Ollama is running and the model is available.

        Caches result after first successful check to avoid repeated pings.

        Returns:
            True if Ollama is running and model exists, False otherwise
        """
        if self._available is True:
            return True

        try:
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=5
            )
            if response.status_code != 200:
                logger.warning(f"Ollama not reachable: HTTP {response.status_code}")
                self._available = False
                return False

            # Check if our model is installed
            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]

            # Check exact match or prefix match (e.g. "qwen3:32b" matches "qwen3:32b")
            model_available = any(
                m == self.model or m.startswith(self.model.split(":")[0])
                for m in models
            )

            if not model_available:
                logger.warning(f"Ollama running but model '{self.model}' not found. Available: {models}")
                self._available = False
                return False

            logger.info(f"Ollama available: {self.model} ready")
            self._available = True
            return True

        except requests.exceptions.ConnectionError:
            logger.info("Ollama not running (connection refused) - using API fallback")
            self._available = False
            return False
        except Exception as e:
            logger.warning(f"Ollama availability check failed: {e}")
            self._available = False
            return False

    def complete(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """
        Send a prompt to Ollama and return the response text.

        Args:
            prompt: User prompt
            system: Optional system prompt

        Returns:
            Response text, or None if unavailable/error (when fallback_on_error=True)
        """
        if not self.is_available():
            return None

        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Low temp for consistent formatting/analysis
                    "num_predict": 4096  # Max output tokens
                }
            }

            if system:
                payload["system"] = system

            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code != 200:
                logger.error(f"Ollama error: HTTP {response.status_code}: {response.text}")
                if self.fallback_on_error:
                    return None
                raise RuntimeError(f"Ollama request failed: {response.status_code}")

            data = response.json()
            result = data.get("response", "").strip()

            # Log token usage if available
            if "eval_count" in data:
                logger.debug(f"Ollama tokens: prompt={data.get('prompt_eval_count', 0)}, response={data.get('eval_count', 0)}")

            return result

        except requests.exceptions.Timeout:
            logger.error(f"Ollama timeout after {self.timeout}s - consider increasing timeout_seconds")
            if self.fallback_on_error:
                return None
            raise
        except Exception as e:
            logger.error(f"Ollama complete() error: {e}")
            if self.fallback_on_error:
                return None
            raise

    def warmup(self, keep_alive_seconds: int = 28800) -> bool:
        """
        Load model into VRAM with a long keepalive duration.

        Args:
            keep_alive_seconds: How long to keep model loaded (default 8h = 28800s).
                                 Set to cover full trading day; unload() will release early.

        Returns:
            True if model loaded successfully, False otherwise
        """
        if not self.is_available():
            return False

        try:
            payload = {
                "model": self.model,
                "prompt": "Ready.",
                "stream": False,
                "keep_alive": keep_alive_seconds
            }
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )
            if response.status_code == 200:
                hours = keep_alive_seconds / 3600
                logger.info(f"Ollama warm-up complete - model loaded, keepalive={hours:.1f}h")
                return True
            else:
                logger.warning(f"Ollama warm-up returned HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Ollama warm-up failed: {e}")
            return False

    def unload(self) -> bool:
        """
        Unload the model from VRAM immediately.

        Uses keep_alive=0 per Ollama API docs to release GPU memory.
        Call this when trading is done for the day.

        Returns:
            True if unloaded successfully, False otherwise
        """
        try:
            payload = {
                "model": self.model,
                "prompt": "",
                "keep_alive": 0
            }
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                logger.debug(f"Ollama model '{self.model}' unloaded from VRAM")
                self._available = None  # Reset cache so next is_available() re-checks
                return True
            else:
                logger.warning(f"Ollama unload returned HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Ollama unload failed: {e}")
            return False

    def complete_json(self, prompt: str, system: Optional[str] = None) -> Optional[Dict]:
        """
        Send a prompt expecting a JSON response.

        Appends JSON formatting instruction to prompt automatically.

        Args:
            prompt: User prompt (should describe desired JSON structure)
            system: Optional system prompt

        Returns:
            Parsed dict, or None if unavailable/error/invalid JSON
        """
        json_prompt = f"{prompt}\n\nRespond with valid JSON only, no markdown code blocks, no explanation."

        result = self.complete(json_prompt, system=system)
        if result is None:
            return None

        # Strip any markdown code blocks if model added them anyway
        result = result.strip()
        if result.startswith("```"):
            lines = result.split("\n")
            result = "\n".join(lines[1:-1])  # Remove first and last lines

        try:
            return json.loads(result)
        except json.JSONDecodeError as e:
            logger.error(f"Ollama returned invalid JSON: {e}\nResponse: {result[:500]}")
            return None

    def summarize_news(self, symbol: str, headlines: list) -> Optional[str]:
        """
        Summarize news headlines into a concise catalyst summary.

        Args:
            symbol: Stock ticker
            headlines: List of news headline strings

        Returns:
            2-3 sentence summary, or None if unavailable
        """
        if not headlines:
            return None

        headlines_text = "\n".join(f"- {h}" for h in headlines[:10])
        prompt = (
            f"Summarize the following news for {symbol} into 2-3 sentences. "
            f"Focus on: what is the catalyst, how significant is it, "
            f"and is it bullish or bearish for today's trading.\n\n"
            f"Headlines:\n{headlines_text}"
        )

        return self.complete(prompt, system="You are a financial news analyst. Be concise and factual.")

    def format_scanner_data(self, raw_data: str, criteria: str) -> Optional[str]:
        """
        Parse and format raw scanner/screener data into clean structured text.

        Args:
            raw_data: Raw text from web scrape or scanner
            criteria: What to extract (e.g. "symbol, price, % change, volume")

        Returns:
            Cleaned formatted data, or None if unavailable
        """
        prompt = (
            f"Extract and format the following market data. "
            f"Return only: {criteria}. "
            f"Remove duplicates, sort by relevance, format as a clean list.\n\n"
            f"Raw data:\n{raw_data[:3000]}"  # Cap input to avoid huge prompts
        )

        return self.complete(prompt, system="You are a data formatter. Extract only the requested fields, clean format.")

    def reflect_on_trade(self, trade_data: dict, entry_reason: str, outcome: str) -> Optional[str]:
        """
        Generate a trade reflection/lesson from a completed trade.

        Args:
            trade_data: Dict with symbol, entry, exit, pnl, etc.
            entry_reason: Why the trade was entered
            outcome: What happened (win/loss/breakeven)

        Returns:
            Reflection text with lessons learned, or None if unavailable
        """
        prompt = (
            f"Analyze this completed swing trade and extract lessons:\n\n"
            f"Symbol: {trade_data.get('symbol')}\n"
            f"Entry: ${trade_data.get('entry_price', 0):.2f}\n"
            f"Exit: ${trade_data.get('exit_price', 0):.2f}\n"
            f"P&L: ${trade_data.get('pnl', 0):.2f}\n"
            f"Hold time: {trade_data.get('hold_time', 'unknown')}\n"
            f"Entry reason: {entry_reason}\n"
            f"Outcome: {outcome}\n\n"
            f"Provide: 1) What went right or wrong, 2) What to do differently, "
            f"3) One specific rule or lesson to remember. Be concise."
        )

        return self.complete(
            prompt,
            system="You are a trading coach analyzing completed trades. Be specific and actionable."
        )

    @classmethod
    def from_config(cls, config: dict) -> Optional["OllamaProvider"]:
        """
        Create OllamaProvider from broker_config.json ollama section.

        Args:
            config: The "ollama" section of broker_config.json

        Returns:
            OllamaProvider if enabled, None otherwise
        """
        if not config.get("enabled", False):
            logger.info("Ollama disabled in config - using API-only mode")
            return None

        provider = cls(
            base_url=config.get("base_url", "http://localhost:11434"),
            model=config.get("model", "qwen3:32b"),
            fallback_on_error=config.get("fallback_on_error", True),
            timeout_seconds=config.get("timeout_seconds", 120)
        )

        # Verify availability at creation time
        if not provider.is_available():
            logger.warning("Ollama enabled in config but not available - falling back to API-only mode")
            return None

        return provider
