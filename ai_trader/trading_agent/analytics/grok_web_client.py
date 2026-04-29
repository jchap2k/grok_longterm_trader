"""
GrokWebClient - Reusable wrapper for Grok web access via grok.com membership.

Uses grok3api (unofficial) which drives Chrome via undetected-chromedriver to
authenticate with your grok.com session. No xAI API key or per-token billing -
uses your existing Premium+/SuperGrok subscription limits.

Confirmed working model names (Feb 2026):
  "grok-4"   - full flagship model, may be rate-limited under heavy load
  "grok-3"   - always available fallback

Model names that do NOT exist (code=7 from API):
  "grok-4-heavy", "grok-4.2", "grok-4-20"

Usage:
    client = GrokWebClient.get_instance()   # lazy Chrome init, reuse across calls
    response = client.ask("Your prompt here")
    response = client.ask("Prompt", model="grok-4")  # explicit model

Requirements:
    pip install grok3api setuptools
    - Google Chrome installed
    - Logged into grok.com in the Chrome session that opens on first use
"""

import os
import time
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Singleton Chrome instance - Chrome is expensive to launch, share across calls
_instance: Optional["GrokWebClient"] = None


def _load_project_url() -> str:
    """Read grok_web.project_url from broker_config.json.
    Falls back to generic grok.com if not set or null (default for new users)."""
    import json  # noqa: PLC0415
    fallback = "https://grok.com"
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "ai_trader_data" / "broker_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg.get("grok_web", {}).get("project_url")
        return url if url else fallback
    except Exception:
        return fallback


class GrokWebClientError(Exception):
    """Raised when Grok web client cannot get a response."""
    pass


class GrokWebClient:
    """
    Reusable wrapper around grok3api for membership-based Grok access.

    Use GrokWebClient.get_instance() to share one Chrome session across
    multiple calls in the same process.
    """

    # Model preference order - first available wins
    MODEL_PRIORITY = ["grok-4", "grok-3"]

    # Error codes from grok.com internal API
    ERROR_NOT_FOUND = 7       # model name does not exist
    ERROR_HEAVY_LOAD = 8      # grok-4 overloaded, retry or fall back

    # Where save_grok_cookies.py saves extracted Chrome cookies
    COOKIES_FILE = os.path.join(os.path.expanduser("~"), ".grok3api_cookies.json")

    # Grok project URL - loaded from broker_config.json grok_web.project_url.
    # Set to None or omit from config to use generic grok.com (default for new users).
    # NOTE: grok3api does not use this for API routing; see GrokPlaywrightClient for
    # project-context access. Kept here for consistency/reference only.
    PROJECT_URL = _load_project_url()

    def __init__(self, timeout: int = 120):
        """
        Initialize the Chrome-backed Grok client.

        Automatically loads cookies from ~/.grok3api_cookies.json if present
        (created by extract_grok_cookies.py). This bypasses the Chrome login
        flow entirely by injecting your existing grok.com/x.com session.

        Args:
            timeout: Seconds to wait for Chrome init and each response.
                     120s is enough for Grok 4 reasoning responses.
        """
        try:
            from grok3api.client import GrokClient  # noqa: PLC0415
        except ImportError:
            raise GrokWebClientError(
                "grok3api not installed. Run: pip install grok3api setuptools"
            )

        cookies = self._load_cookies()

        if cookies:
            logger.info(
                f"[GrokWebClient] Loaded {len(cookies)} cookies from "
                f"{self.COOKIES_FILE} - no login required"
            )
        else:
            logger.info(
                "[GrokWebClient] No cookie file found - Chrome will open for login. "
                "Run extract_grok_cookies.py once to avoid this."
            )

        logger.info("[GrokWebClient] Initializing Chrome session...")

        self._client = GrokClient(
            cookies=cookies,
            always_new_conversation=True,
            timeout=timeout,
        )
        self._timeout = timeout
        logger.info("[GrokWebClient] Chrome session ready")
        # NOTE: grok3api does NOT support projectId - the API endpoint and payload
        # are hardcoded and do not include project context. Navigating the browser
        # to PROJECT_URL does not change which model the API uses.
        # For project-specific model access use GrokPlaywrightClient instead.

    @classmethod
    def _load_cookies(cls):
        """
        Load cookies from ~/.grok3api_cookies.json if it exists.

        Returns a flat dict {name: value} that GrokClient(cookies=...) accepts,
        or None if the file does not exist.
        """
        import json  # noqa: PLC0415
        if not os.path.exists(cls.COOKIES_FILE):
            return None
        try:
            with open(cls.COOKIES_FILE, "r", encoding="utf-8") as f:
                cookie_list = json.load(f)
            # Flatten list of {name, value, domain, ...} dicts into {name: value}
            return {c["name"]: c["value"] for c in cookie_list if c.get("value")}
        except Exception as exc:
            logger.warning(f"[GrokWebClient] Could not load cookies file: {exc}")
            return None

    @classmethod
    def get_instance(cls, timeout: int = 120) -> "GrokWebClient":
        """
        Return shared singleton instance, initializing Chrome if needed.

        Reusing the instance avoids re-launching Chrome for every call.
        Safe for sequential use within one process.

        Args:
            timeout: Used only on first call when Chrome is not yet running.
        """
        global _instance
        if _instance is None:
            _instance = cls(timeout=timeout)
        return _instance

    @classmethod
    def reset_instance(cls) -> None:
        """Force Chrome to reinitialize on next get_instance() call."""
        global _instance
        _instance = None

    def ask(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: str = "",
        disable_search: bool = True,
        max_retries: int = 2,
        retry_delay: float = 8.0,
    ) -> str:
        """
        Send a prompt to Grok and return the response text.

        Tries grok-4 first, falls back to grok-3 automatically if:
        - grok-4 is under heavy load (error code 8)
        - grok-4 model name rejected (error code 7, shouldn't happen)

        Args:
            prompt: The user message to send.
            model: Model name. None = auto (tries grok-4 -> grok-3).
                   Pass "grok-3" to skip grok-4 entirely.
            system_instruction: Custom instructions prepended to message
                                 (grok3api's equivalent of a system prompt).
            disable_search: Disable Grok's web search (default True -
                            we want deterministic reasoning, not web browsing).
            max_retries: Retries on recoverable errors (heavy load).
            retry_delay: Seconds to wait between retries.

        Returns:
            Response text from Grok.

        Raises:
            GrokWebClientError: If all models and retries are exhausted.
        """
        models_to_try = [model] if model else list(self.MODEL_PRIORITY)
        last_error = None

        for model_name in models_to_try:
            for attempt in range(max_retries + 1):
                try:
                    logger.debug(
                        f"[GrokWebClient] Sending to {model_name} "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )

                    result = self._client.ask(
                        message=prompt,
                        modelName=model_name,
                        customInstructions=system_instruction,
                        disableSearch=disable_search,
                        enableImageGeneration=False,
                        forceConcise=False,
                        disableTextFollowUps=True,
                    )

                    # Check for API-level errors
                    raw = getattr(result, "__dict__", {})
                    error_msg = raw.get("error")
                    error_code = raw.get("error_code")

                    if error_code == self.ERROR_NOT_FOUND:
                        logger.warning(
                            f"[GrokWebClient] {model_name} not found (code 7) - "
                            "skipping to next model"
                        )
                        last_error = f"Model not found: {model_name}"
                        break  # try next model

                    if error_code == self.ERROR_HEAVY_LOAD:
                        if attempt < max_retries:
                            logger.warning(
                                f"[GrokWebClient] {model_name} under heavy load "
                                f"(code 8) - waiting {retry_delay}s then retrying"
                            )
                            time.sleep(retry_delay)
                            continue
                        else:
                            logger.warning(
                                f"[GrokWebClient] {model_name} still overloaded "
                                "after retries - trying next model"
                            )
                            last_error = f"Heavy load on {model_name}: {error_msg}"
                            break  # try next model

                    if error_msg:
                        logger.warning(
                            f"[GrokWebClient] API error on {model_name}: "
                            f"{error_msg} (code={error_code})"
                        )
                        last_error = f"{model_name} error {error_code}: {error_msg}"
                        break  # try next model

                    # Extract message text
                    if result is None or result.modelResponse is None:
                        logger.warning(
                            f"[GrokWebClient] Null response from {model_name}"
                        )
                        last_error = f"Null response from {model_name}"
                        break

                    text = result.modelResponse.message or ""
                    if not text.strip():
                        logger.warning(
                            f"[GrokWebClient] Empty response from {model_name}"
                        )
                        last_error = f"Empty response from {model_name}"
                        break

                    logger.info(
                        f"[GrokWebClient] Got {len(text)} chars from {model_name}"
                    )
                    return text

                except Exception as exc:
                    logger.warning(
                        f"[GrokWebClient] Exception on {model_name} "
                        f"attempt {attempt + 1}: {exc}"
                    )
                    last_error = str(exc)
                    if attempt < max_retries:
                        time.sleep(retry_delay)

        raise GrokWebClientError(
            f"All Grok models exhausted. Last error: {last_error}"
        )

    def ask_json(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: str = "",
    ):
        """
        Send a prompt expecting a JSON response. Strips markdown fences if present.

        Args:
            prompt: Prompt instructing Grok to return JSON.
            model: Model name (default: auto grok-4 -> grok-3).
            system_instruction: Optional system-level instructions.

        Returns:
            Parsed Python object (list or dict).

        Raises:
            GrokWebClientError: If response cannot be obtained.
            ValueError: If response is not valid JSON.
        """
        import json  # noqa: PLC0415

        text = self.ask(prompt, model=model, system_instruction=system_instruction)

        # Strip markdown code fences if model wraps response
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        json_str = fence_match.group(1) if fence_match else text.strip()

        try:
            return json.loads(json_str)
        except Exception as exc:
            raise ValueError(
                f"Grok response was not valid JSON: {exc}\n"
                f"Response (first 300 chars): {text[:300]}"
            ) from exc
