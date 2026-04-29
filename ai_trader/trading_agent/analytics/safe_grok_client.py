"""
SafeGrokClient - Reliable Grok 4/4.20 access with automatic retries.

Wraps GrokPlaywrightClient with:
- Automatic retry on failure (for cron jobs, overnight runs)
- Better error messages
- Session lifecycle management
- Safe shutdown

Perfect for daily EOD lessons learning without human intervention.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SafeGrokClient:
    """
    Safe wrapper around GrokPlaywrightClient with retry logic.

    Usage:
        client = SafeGrokClient(headless=False, minimized=True)
        try:
            response = client.ask(prompt, max_retries=2)
        finally:
            client.close()
    """

    def __init__(
        self,
        headless: bool = False,
        minimized: bool = True,
        timeout: int = 120,
        max_retries: int = 2,
    ):
        """
        Initialize SafeGrokClient.

        Args:
            headless: Run headless (not recommended due to bot detection).
            minimized: Start with off-screen window (only when headless=False).
            timeout: Max seconds per Grok response.
            max_retries: Number of retries on failure (e.g., 2 = 3 total attempts).
        """
        self.headless = headless
        self.minimized = minimized
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = None
        self._launch()

    def _launch(self) -> None:
        """Launch the underlying GrokPlaywrightClient."""
        try:
            from .grok_playwright_client import GrokPlaywrightClient, GrokPlaywrightError

            self._client = GrokPlaywrightClient(
                headless=self.headless,
                timeout=self.timeout,
                minimized=self.minimized,
            )
            logger.info(
                f"[SafeGrok] Client launched (headless={self.headless}, "
                f"minimized={self.minimized})"
            )
        except ImportError as e:
            raise ImportError(
                "GrokPlaywrightClient not found. Ensure it's in PYTHONPATH: "
                f"{e}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to launch GrokPlaywrightClient: {e}") from e

    def ask(
        self,
        prompt: str,
        max_retries: Optional[int] = None,
        max_wait: Optional[int] = None,
        close_after: bool = False,
        new_chat: bool = False,
    ) -> str:
        """
        Ask Grok with automatic retry on failure.

        Args:
            prompt: The message to send.
            max_retries: Retries for this call (overrides __init__ default).
            max_wait: Max seconds to wait for response (overrides timeout).
            close_after: Close browser after response (True only on last prompt in multi-turn).
            new_chat: Click 'New chat' button to start a fresh conversation.
                     Set True only on the FIRST prompt. Leave False for all others.

        Returns:
            Response text from Grok.

        Raises:
            RuntimeError: If all retries exhausted.
        """
        if max_retries is None:
            max_retries = self.max_retries

        if max_wait is None:
            max_wait = self.timeout

        for attempt in range(max_retries + 1):
            try:
                logger.debug(
                    f"[SafeGrok] Asking Grok (attempt {attempt + 1}/{max_retries + 1})..."
                )
                response = self._client.ask(prompt, max_wait=max_wait, new_chat=new_chat)
                logger.info(f"[SafeGrok] Got response ({len(response)} chars)")

                # Close browser if this is the last prompt in a multi-turn conversation
                if close_after:
                    logger.debug("[SafeGrok] Closing browser after final prompt")
                    self.close()

                return response

            except Exception as e:
                if attempt == max_retries:
                    # Last attempt failed, raise
                    logger.error(f"[SafeGrok] All {max_retries + 1} attempts failed")
                    raise RuntimeError(
                        f"Grok failed after {max_retries + 1} attempts: {e}"
                    ) from e

                # Retry: wait, relaunch, try again
                retry_delay = 8 * (attempt + 1)  # 8s, 16s, 24s...
                logger.warning(
                    f"[SafeGrok] Attempt {attempt + 1} failed: {type(e).__name__}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)

                # Relaunch fresh browser for next attempt
                try:
                    self.close()
                except Exception:
                    pass
                self._launch()

        # Should never reach here
        raise RuntimeError("SafeGrokClient.ask() logic error")

    def ask_json(
        self,
        prompt: str,
        max_retries: Optional[int] = None,
        max_wait: Optional[int] = None,
    ) -> dict:
        """
        Ask Grok expecting JSON response, with retry.

        Args:
            prompt: Prompt requesting JSON output.
            max_retries: Retries for this call.
            max_wait: Max seconds to wait.

        Returns:
            Parsed JSON as dict or list.

        Raises:
            RuntimeError: If all retries exhausted or response is not valid JSON.
        """
        if max_retries is None:
            max_retries = self.max_retries

        response_text = self.ask(prompt, max_retries=max_retries, max_wait=max_wait)

        # Let underlying client handle JSON parsing
        try:
            return self._client.ask_json(
                prompt, max_wait=max_wait
            )  # Actually we need to parse here
            # Since ask_json calls ask() internally, we already got the raw text
        except Exception:
            pass

        # Fallback: manually parse JSON from response_text
        import json
        import re

        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", response_text, re.DOTALL)
        json_str = fence_match.group(1) if fence_match else response_text.strip()

        try:
            return json.loads(json_str)
        except Exception as e:
            raise ValueError(
                f"Grok response was not valid JSON: {e}\n"
                f"Response (first 300 chars): {response_text[:300]}"
            ) from e

    def close(self) -> None:
        """Close browser and cleanup."""
        if self._client:
            try:
                self._client.close()
                logger.debug("[SafeGrok] Client closed")
            except Exception as e:
                logger.warning(f"[SafeGrok] Error during close: {e}")
            finally:
                self._client = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    def __del__(self):
        """Cleanup on object destruction."""
        self.close()
