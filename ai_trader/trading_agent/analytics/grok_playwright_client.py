"""
GrokPlaywrightClient - Project-aware Grok access via Playwright browser automation.

Unlike GrokWebClient (grok3api-based), this navigates directly to your specific Grok
Project URL, giving access to:
  - The project's configured model (Grok 4 / 4.20 / latest)
  - The project's custom instructions and knowledge base
  - Your full Premium+/SuperGrok quotas

grok3api does NOT support projectId - it only works with the generic grok.com chat.
Use this client when you need project-specific model access.

First-time setup (run once from a terminal to log in):
    python -c "
    from ai_trader.trading_agent.analytics.grok_playwright_client import GrokPlaywrightClient
    c = GrokPlaywrightClient(headless=False)
    print('Logged in? Try an ask() call.')
    "
    Log in to grok.com in the browser that opens.
    Session is saved to ~/.grok_playwright_session/ and reused automatically.

Requirements:
    pip install playwright
    playwright install chromium      # run once after pip install
"""

import os
import re
import time
import logging
import tempfile
from pathlib import Path
from typing import Optional

try:
    from playwright_stealth import Stealth as _PlaywrightStealth
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

logger = logging.getLogger(__name__)

# Singleton browser instance - browser is expensive to launch, share across calls
_instance: Optional["GrokPlaywrightClient"] = None


def _load_project_url() -> str:
    """Load the Grok project URL from env, safe project config, or broker config."""
    import json  # noqa: PLC0415
    fallback = "https://grok.com"
    env_url = os.environ.get("GROK_PROJECT_URL")
    if env_url:
        return env_url

    config_path = Path(__file__).resolve().parent.parent / "config" / "grok_project_config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg.get("default_project_url")
        if url:
            return url
    except Exception:
        pass

    try:
        broker_config_path = (
            Path(__file__).resolve().parent.parent.parent
            / "ai_trader_data"
            / "broker_config.json"
        )
        with open(broker_config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg.get("grok_web", {}).get("project_url")
        return url if url else fallback
    except Exception:
        return fallback


class GrokPlaywrightError(Exception):
    """Raised when Playwright-based Grok client cannot get a response."""
    pass


def _launch_persistent_context(chromium, *, user_data_dir: str, launch_options: dict):
    """Launch persistent context with installed Chrome before bundled Chromium.

    Existing user profiles can be incompatible with Playwright's bundled
    Chromium build. Installed Chrome matches the profile's on-disk format and
    avoids immediate browser exits on startup.
    """
    try:
        return chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",
            **launch_options,
        )
    except Exception as chrome_exc:
        logger.warning(
            "[GrokPlaywright] Chrome channel launch failed; falling back to bundled "
            "Chromium: %s",
            chrome_exc,
        )
        return chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            **launch_options,
        )


class GrokPlaywrightClient:
    """
    Project-aware Grok client using Playwright browser automation.

    Navigates directly to a Grok Project URL in a persistent Chromium session.
    Session cookies are preserved across Python runs - log in once, reuse forever.

    Use GrokPlaywrightClient.get_instance() to share one browser session across
    multiple calls in the same process.
    """

    # Grok project URL - loaded from broker_config.json grok_web.project_url.
    # Set to None in config for generic grok.com (default for new users).
    PROJECT_URL = _load_project_url()

    # Reuse the existing grok3api Chrome profile - already logged in to grok.com.
    # NOTE: Only one Chrome/Playwright instance can use this profile at a time.
    # Do not run grok3api (GrokWebClient) and GrokPlaywrightClient simultaneously.
    SESSION_DIR = os.path.join(os.path.expanduser("~"), ".grok3api_chrome_profile")

    # Chat input selectors (try in order, first match wins)
    # NOTE: 'div[contenteditable="true"]' is 2nd - at grok.com project URLs the
    # input does NOT have role="textbox", so the bare selector must be tried early
    # to avoid wasting ~20s on failing selectors before finding the correct one.
    INPUT_SELECTORS = [
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"]',
        'div[contenteditable="true"][data-placeholder*="Ask"]',
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="Message"]',
        'textarea[data-testid*="input"]',
        'textarea',
    ]

    # Response content selectors (last matching element = most recent reply)
    RESPONSE_SELECTORS = [
        'div[class*="markdown"]',
        'div[class*="message-content"]',
        'div[class*="prose"]',
        '[data-testid*="message"] div',
        'article div',
    ]

    # Loading/generating status selectors (wait for these to disappear)
    STATUS_SELECTORS = [
        'div[role="status"]',
        'div[class*="thinking"]',
        'div[class*="generating"]',
        'div[class*="loading"]',
        '[data-testid*="loading"]',
        '[data-testid*="thinking"]',
    ]

    # Realistic Chrome user agent - matches current stable Chrome on Windows 10/11.
    # Used in both the browser context and stealth init scripts so all fingerprint
    # signals are consistent (avoids UA mismatch that Cloudflare can detect).
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Completion signal selectors - appear when Grok FINISHES generating (positive signal).
    # Wait for any of these to become visible as the primary completion indicator.
    # Ranked: Copy button is most stable, Regenerate is reliable, action-bar is fallback.
    COMPLETION_SELECTORS = [
        'button:has-text("Copy")',
        'button:has-text("Regenerate")',
        '[data-testid*="copy"]',
        '[aria-label*="Copy"]',
        '[data-testid*="action-bar"]',
    ]

    # Streaming cursor selectors - present WHILE generating, absent when done.
    # Used as secondary signal when action buttons are not found.
    STREAMING_CURSOR_SELECTORS = [
        '[data-testid="streaming-cursor"]',
        '.streaming-cursor',
        '.blinking-cursor',
        'span.cursor',
    ]

    def __init__(self, headless: bool = True, timeout: int = 120, minimized: bool = False):
        """
        Initialize Playwright browser with persistent session.

        Args:
            headless: Run without a visible browser window.
                      Use False until logged in (headless may trigger bot detection).
                      After successful login, headless=True may work.
            timeout: Max seconds to wait for each Grok response (default 120s for
                     long reasoning responses).
            minimized: When headless=False, start browser window minimized.
                       Only applies when headless=False.
        """
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            raise GrokPlaywrightError(
                "playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        self._headless = headless
        self._timeout = timeout
        self._minimized = minimized

        logger.info(
            f"[GrokPlaywright] Starting Playwright (headless={headless}, "
            f"session={self.SESSION_DIR})"
        )

        self._pw = sync_playwright().start()
        self._context = None
        self._page = None
        self._init_browser()

    def _init_browser(self) -> None:
        """Launch browser with persistent context and navigate to project."""
        os.makedirs(self.SESSION_DIR, exist_ok=True)

        # Enhanced args for better headless stealth and stability
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-extensions",
            "--disable-sync",
            "--disable-translate",
        ]

        if self._minimized and not self._headless:
            # NOTE: Cannot use off-screen window position (e.g. -3000,-3000).
            # React's IntersectionObserver requires the window to be on a visible
            # display to mount response div elements into the DOM. When the window
            # is off-screen, querySelectorAll for response selectors always returns
            # 0 - Phase 1 detection never fires. Anti-throttle Chrome flags do NOT
            # fix this; only an on-screen window position resolves it.
            launch_args.extend([
                "--window-position=100,100",
                "--window-size=1280,900",
                "--mute-audio",
            ])
        elif not self._headless:
            # Visible headed mode - place window on-screen so the user can see it
            launch_args.extend([
                "--window-position=100,100",
                "--window-size=1280,900",
            ])
        elif self._headless:
            # Additional headless-specific stability flags
            launch_args.extend([
                "--hide-scrollbars",
                "--mute-audio",
                "--disable-backgrounding-occluded-windows",
                "--disable-default-apps",
                "--headless=new",
            ])
        self._context = _launch_persistent_context(
            self._pw.chromium,
            user_data_dir=self.SESSION_DIR,
            launch_options={
                "headless": self._headless,
                "viewport": {"width": 1280, "height": 900},
                "user_agent": self.USER_AGENT,
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "args": launch_args,
            },
        )

        self._page = (
            self._context.pages[0]
            if self._context.pages
            else self._context.new_page()
        )

        # Inject stealth scripts to hide Playwright automation fingerprint.
        # playwright-stealth patches ~20 detection vectors (webdriver, chrome runtime,
        # permissions API, canvas noise, navigator properties, etc.) that Cloudflare
        # Turnstile uses to identify automated browsers.
        if _STEALTH_AVAILABLE:
            try:
                _PlaywrightStealth(
                    chrome_runtime=True,
                    navigator_user_agent_override=self.USER_AGENT,
                ).apply_stealth_sync(self._page)
                logger.info("[GrokPlaywright] playwright-stealth applied")
            except Exception as e:
                logger.warning(f"[GrokPlaywright] playwright-stealth failed, using fallback: {e}")
                self._inject_manual_stealth()
        else:
            logger.warning("[GrokPlaywright] playwright-stealth not installed, using manual stealth")
            self._inject_manual_stealth()

        # Always start at grok.com home - avoids Cloudflare bot detection that
        # triggers on project URLs when the browser fingerprint looks automated.
        # The project URL is used per-chat in _start_new_chat() once the browser
        # has a warm, human-looking session from the home page visit.
        logger.info("[GrokPlaywright] Navigating to grok.com home...")
        try:
            self._page.goto(
                "https://grok.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            time.sleep(3)  # let JS render
        except Exception as exc:
            raise GrokPlaywrightError(
                f"Could not load grok.com: {exc}\n"
                "Is grok.com reachable? Are you connected to the internet?"
            )

        current_url = self._page.url
        if "login" in current_url.lower() or "signin" in current_url.lower():
            logger.warning(
                "[GrokPlaywright] Not logged in. Please log in at grok.com in the "
                "browser window that opened. Session will be saved automatically."
            )
        else:
            logger.info("[GrokPlaywright] grok.com home loaded - browser ready")

        # Wait for any Cloudflare Turnstile inline widget to auto-pass.
        # Turnstile appears at page load and auto-validates using stored session
        # cookies - we must wait for it to clear before interacting with the page.
        logger.info("[GrokPlaywright] Waiting for Cloudflare Turnstile to clear...")
        self._wait_for_turnstile(timeout_s=20.0)

    @classmethod
    def get_instance(
        cls, headless: bool = True, timeout: int = 120, minimized: bool = False
    ) -> "GrokPlaywrightClient":
        """
        Return shared singleton instance, launching browser if needed.

        Reusing the instance avoids re-launching the browser for every call.

        Args:
            headless: Passed only on first call when browser is not yet running.
            timeout: Passed only on first call.
            minimized: Passed only on first call. When headless=False, start minimized.
        """
        global _instance
        if _instance is None:
            _instance = cls(headless=headless, timeout=timeout, minimized=minimized)
        return _instance

    @classmethod
    def reset_instance(cls) -> None:
        """Force browser to reinitialize on next get_instance() call."""
        global _instance
        if _instance is not None:
            try:
                _instance.close()
            except Exception:
                pass
        _instance = None

    def close(self) -> None:
        """Close browser and stop Playwright."""
        try:
            if self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
        except Exception as exc:
            logger.warning(f"[GrokPlaywright] Error closing browser: {exc}")
        finally:
            self._context = None
            self._page = None

    def _inject_manual_stealth(self) -> None:
        """Fallback stealth script when playwright-stealth is not installed."""
        try:
            self._page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)
        except Exception as e:
            logger.warning(f"[GrokPlaywright] Manual stealth script failed: {e}")

    def _wait_for_turnstile(self, timeout_s: float = 15.0) -> bool:
        """Wait for Cloudflare Turnstile inline widget to auto-pass and disappear.

        The Turnstile (<div id="turnstile-widget">) appears at page load and
        auto-validates using stored session cookies, then hides itself.  We must
        wait for it to clear BEFORE clicking the chat input - while the widget is
        visible it intercepts all pointer events and blocks clicks.

        Returns True if the Turnstile is gone (or was never present), False if it
        remained visible after the timeout (will need manual intervention).
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                turnstile = self._page.locator("#turnstile-widget")
                if turnstile.count() == 0:
                    return True  # widget not present at all
                # Widget is present - check if it's visible/intercepting
                if not turnstile.is_visible():
                    return True  # present but hidden - safe to proceed
            except Exception:
                return True  # can't query, assume safe
            time.sleep(0.5)
        logger.warning(
            "[GrokPlaywright] Turnstile widget still visible after %.0fs - "
            "may need manual CAPTCHA solve in the browser window", timeout_s
        )
        return False

    def _check_cloudflare(self) -> bool:
        """Return True if Cloudflare security challenge is blocking the current page."""
        try:
            content = self._page.content()
            return "Performing security verification" in content
        except Exception:
            return False

    def _start_new_chat(self) -> None:
        """Navigate to a fresh chat page for a new conversation.

        Strategy (in priority order):
        1. Try project URL (provides project model/custom instructions context).
           If Cloudflare blocks it, fall back to grok.com home.
        2. grok.com home - always accessible, always has a fresh input box.

        Unlike the previous approach (hunting for a 'New chat' button), this
        navigates to a URL directly. Each navigation starts a fresh conversation
        thread - no stale context from prior messages.
        """
        project_url = self.PROJECT_URL
        use_project = (
            project_url
            and project_url != "https://grok.com"
            and "grok.com/project/" in project_url
        )

        if use_project:
            logger.info(f"[GrokPlaywright] Navigating to project URL for fresh chat: {project_url}")
            try:
                self._page.goto(project_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(3)
                if self._check_cloudflare():
                    logger.warning(
                        "[GrokPlaywright] Cloudflare detected on project URL - "
                        "falling back to grok.com home"
                    )
                    use_project = False
                else:
                    # Check input is available on project page
                    try:
                        self._page.wait_for_selector(
                            "textarea, [contenteditable='true']",
                            timeout=8000,
                            state="visible",
                        )
                        logger.info("[GrokPlaywright] New chat ready at project URL")
                        return
                    except Exception:
                        logger.warning(
                            "[GrokPlaywright] Project page loaded but no input found - "
                            "falling back to grok.com home"
                        )
                        use_project = False
            except Exception as exc:
                logger.warning(
                    f"[GrokPlaywright] Project URL navigation failed ({exc}) - "
                    "falling back to grok.com home"
                )
                use_project = False

        # Fall back to grok.com home - always accessible, always has a fresh input
        if not use_project:
            logger.info("[GrokPlaywright] Navigating to grok.com home for fresh chat")
            self._page.goto("https://grok.com", wait_until="domcontentloaded", timeout=15000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                time.sleep(4)
            logger.info("[GrokPlaywright] New chat ready at grok.com home")

    def ask(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: str = "",
        max_wait: Optional[int] = None,
        new_chat: bool = False,
    ) -> str:
        """
        Send a message to Grok and return the response text.

        For multi-turn conversations: set new_chat=True ONLY on the first prompt.
        All subsequent prompts automatically continue in the same conversation thread.

        Args:
            prompt: The message to send.
            model: Ignored - model is determined by the project configuration.
            system_instruction: If non-empty, prepended to the prompt.
            max_wait: Max seconds to wait for a response. Defaults to self._timeout.
            new_chat: Click 'New chat' button to start a fresh conversation thread.
                     Set True only on the FIRST prompt. Leave False for all others.

        Returns:
            Response text from Grok (plain text, markdown preserved).

        Raises:
            GrokPlaywrightError: If unable to send message or receive response.
        """
        if max_wait is None:
            max_wait = self._timeout

        if model:
            logger.debug(
                f"[GrokPlaywright] model={model!r} ignored - project sets model"
            )

        full_prompt = prompt
        if system_instruction:
            full_prompt = f"{system_instruction}\n\n{prompt}"

        # Start a fresh conversation thread if requested
        if new_chat:
            logger.debug("[GrokPlaywright] Starting new chat (clicking New chat button)")
            self._start_new_chat()
        else:
            logger.debug("[GrokPlaywright] Continuing existing conversation")
            time.sleep(0.5)  # Brief settle before next message

        # Check login status
        current_url = self._page.url
        if "login" in current_url.lower() or "signin" in current_url.lower():
            raise GrokPlaywrightError(
                "Not logged in to grok.com. Please run with headless=False "
                "and log in, then session will be saved automatically.\n"
                f"Current URL: {current_url}"
            )

        # Find chat input
        input_el = self._find_input()

        # Count existing response blocks AND action buttons before sending.
        # prior_action_count is used by Phase 2a to avoid firing on previous
        # responses' action buttons (false positive in multi-turn conversations).
        # pre_send_text is used by Phase 3 to avoid stabilizing on stale text
        # from a prior response (multi-turn false positive where Phase 1 fires
        # immediately because article div > prior_count is already true).
        prior_count = self._count_responses()
        prior_action_count = self._count_action_buttons()
        pre_send_text = self._extract_last_response()

        # Type and send the message
        try:
            # Wait for any Turnstile widget to auto-pass before interacting.
            # Turnstile intercepts pointer events; we cannot click while it's active.
            self._wait_for_turnstile(timeout_s=20.0)

            # Focus the input. Prefer JS-based focus (bypasses pointer event hit-
            # testing, including Cloudflare Turnstile overlays) then fall back to
            # Playwright click.
            try:
                input_el.evaluate("el => el.focus()")
            except Exception:
                input_el.click()
            time.sleep(0.5)
            # Insert prompt text into the contenteditable input.
            # For large prompts (>500 chars): use execCommand('insertText') instead
            # of keyboard.type() which sends individual key events. keyboard.type()
            # for 10000+ chars causes React to drop/corrupt input state, resulting
            # in an empty or truncated message that Grok never responds to.
            # execCommand fires React's input event handlers identically to typing
            # but is instantaneous and handles any text length correctly.
            # For short prompts: keyboard.type() is kept for maximum compatibility
            # (fires keydown/keypress/keyup events that some handlers may need).
            if len(full_prompt) > 500:
                input_el.evaluate(
                    "(el, text) => {"
                    "  el.focus();"
                    "  document.execCommand('insertText', false, text);"
                    "}",
                    full_prompt,
                )
                logger.debug(
                    f"[GrokPlaywright] Large prompt ({len(full_prompt)} chars): "
                    "used execCommand('insertText')"
                )
            else:
                self._page.keyboard.type(full_prompt)
            time.sleep(0.5)
            # Use input_el.press("Enter") instead of page.keyboard.press("Enter").
            # locator.press() focuses the element first, ensuring the Enter event
            # targets the contenteditable input even if focus shifted (e.g., due to
            # React re-renders or stealth script side effects during keyboard.type).
            _url_before_send = self._page.url
            input_el.press("Enter")
            time.sleep(2)
            _url_after_send = self._page.url
            if _url_after_send != _url_before_send:
                logger.info(
                    "[GrokPlaywright] URL changed after Enter - message submitted OK"
                )
            else:
                # URL change (grok.com adds ?chat=&rid= params) typically takes
                # 10-30s. Not changing within 2s is normal - log at debug only.
                logger.debug(
                    "[GrokPlaywright] URL unchanged 2s after Enter (normal - URL "
                    "change is a slow side effect, not required for success)"
                )
            logger.info(
                f"[GrokPlaywright] Sent {len(full_prompt)}-char prompt to project"
            )
            print(
                f"[Grok] Prompt sent ({len(full_prompt)} chars) - waiting for response (max {max_wait}s)...",
                flush=True,
            )
        except Exception as exc:
            raise GrokPlaywrightError(f"Could not send message: {exc}")

        # Wait for and extract response
        response = self._wait_for_response(
            prior_count, max_wait, prior_action_count,
            pre_send_text=pre_send_text,
        )
        logger.info(f"[GrokPlaywright] Got {len(response)}-char response")
        print(f"[Grok] Response received ({len(response)} chars)", flush=True)
        return response

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
            model: Ignored (project sets model).
            system_instruction: Optional system-level instructions.

        Returns:
            Parsed Python object (list or dict).

        Raises:
            GrokPlaywrightError: If unable to get response.
            ValueError: If response is not valid JSON.
        """
        import json  # noqa: PLC0415

        text = self.ask(prompt, model=model, system_instruction=system_instruction)

        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        json_str = fence_match.group(1) if fence_match else text.strip()

        try:
            return json.loads(json_str)
        except Exception as exc:
            raise ValueError(
                f"Grok response was not valid JSON: {exc}\n"
                f"Response (first 300 chars): {text[:300]}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_input(self):
        """
        Find the chat input element using multiple selector strategies.

        Returns a Playwright Locator (not ElementHandle). Locators re-query the DOM
        each time they are used, preventing stale-element failures when React
        re-renders the page after navigation.

        Returns:
            Playwright Locator pointing to the visible chat input.

        Raises:
            GrokPlaywrightError: If no input element found.
        """
        logger.debug("[GrokPlaywright] Searching for chat input...")

        # Try each selector - use Locator (not wait_for_selector/ElementHandle)
        # so React re-renders don't invalidate the reference.
        for selector in self.INPUT_SELECTORS:
            try:
                locator = self._page.locator(selector).first
                locator.wait_for(timeout=5000, state="visible")
                logger.debug(f"[GrokPlaywright] Input found with selector: {selector}")
                return locator
            except Exception as e:
                logger.debug(f"[GrokPlaywright] Selector '{selector}' failed: {type(e).__name__}")
                continue

        # If all selectors fail, dump diagnostic info
        logger.warning("[GrokPlaywright] No input selector matched. Gathering diagnostics...")

        try:
            screenshot_path = str(Path(tempfile.gettempdir()) / "grok_no_input.png")
            try:
                self._page.screenshot(path=screenshot_path)
                logger.warning(f"[GrokPlaywright] Screenshot saved to {screenshot_path}")
            except Exception as sc_err:
                logger.debug(f"[GrokPlaywright] Could not save screenshot: {sc_err}")

            textareas = self._page.locator("textarea").all()
            editables = self._page.locator('[contenteditable="true"]').all()
            logger.warning(
                f"[GrokPlaywright] Found {len(textareas)} textarea(s), "
                f"{len(editables)} contenteditable(s) on page"
            )
            for i, el in enumerate(textareas[:5]):
                try:
                    ph = el.get_attribute("placeholder") or ""
                    logger.warning(f"  textarea[{i}] placeholder={ph!r} visible={el.is_visible()}")
                except Exception:
                    pass
            for i, el in enumerate(editables[:5]):
                try:
                    ph = el.get_attribute("data-placeholder") or ""
                    logger.warning(f"  contenteditable[{i}] data-placeholder={ph!r} visible={el.is_visible()}")
                except Exception:
                    pass
        except Exception as diag_err:
            logger.warning(f"[GrokPlaywright] Could not gather diagnostics: {diag_err}")

        # Last resort: generic fallback locator
        logger.warning("[GrokPlaywright] Attempting fallback selectors...")
        try:
            fallback = self._page.locator("textarea, [contenteditable='true']").first
            fallback.wait_for(timeout=10000, state="visible")
            logger.info("[GrokPlaywright] Found input via fallback selector")
            return fallback
        except Exception as fallback_err:
            logger.debug(f"[GrokPlaywright] Fallback selector also failed: {fallback_err}")

        raise GrokPlaywrightError(
            "Could not find chat input box. Possible causes:\n"
            "  1. Not logged in to grok.com (check URL)\n"
            "  2. Grok UI may have changed\n"
            "  3. Headless mode may be blocked (try headless=False)\n"
            f"Current URL: {self._page.url}\n"
            "Check logs above for diagnostic info (textarea/contenteditable elements found)."
        )

    def _count_responses(self) -> int:
        """Return current count of response blocks using querySelectorAll.

        Must use querySelectorAll (not locator.count()) to match Phase 1 JS detection.
        locator.count() pierces shadow DOM and returns higher counts; using it here
        causes a mismatch with Phase 1's querySelectorAll check, producing false
        positives ('Response started' before Grok actually begins responding).
        """
        for selector in self.RESPONSE_SELECTORS:
            try:
                count = self._page.evaluate(
                    f"document.querySelectorAll({repr(selector)}).length"
                )
                if count > 0:
                    return count
            except Exception:
                continue
        return 0

    def _count_action_buttons(self) -> int:
        """Count action buttons currently in DOM (proxy for completed response count).

        Used by _wait_for_response Phase 2a to detect when a NEW response completes
        in multi-turn conversations. Without this, Phase 2a fires immediately on
        existing action buttons from previous responses (false positive).

        Uses CSS-compatible selectors only (no Playwright-specific :has-text() syntax)
        since this runs via page.evaluate(querySelectorAll).

        Returns:
            Max count across all action button selectors, or 0 if none found.
        """
        action_sels = [
            '[data-testid*="copy"]',
            '[aria-label*="Copy"]',
            '[data-testid*="action-bar"]',
        ]
        counts = []
        for sel in action_sels:
            try:
                n = self._page.evaluate(
                    f"document.querySelectorAll({repr(sel)}).length"
                )
                counts.append(n)
            except Exception:
                pass
        return max(counts) if counts else 0

    def _wait_for_response(
        self,
        prior_count: int,
        max_wait: int,
        prior_action_count: int = 0,
        pre_send_text: str = "",
    ) -> str:
        """
        Wait for Grok to finish generating and return the response text.

        Four-signal strategy (ranked by reliability, Grok 2026 UI):
        1. JS wait_for_function() detects new response block (instant vs 0.5s Python poll)
        2a. Action buttons (Copy/Regenerate) appear - PRIMARY positive completion signal
            Uses prior_action_count to avoid false-positive on previous responses' buttons
            in multi-turn conversations.
        2b. Streaming cursor disappears - secondary completion signal
        2c. Status/thinking indicator disappears - legacy negative signal fallback
        3. Content hash stabilization (1.5s window, 0.5s poll) - final safety net

        Args:
            prior_count: Number of response blocks before message was sent.
            max_wait: Maximum total seconds to wait.
            prior_action_count: Number of action buttons before message was sent.
                Used to detect NEW completion buttons vs existing buttons from
                previous responses. Default 0 (safe for single-turn, single-response).
            pre_send_text: Text of the last response BEFORE this message was sent.
                Phase 3 uses this to guard against returning stale text from a
                prior response. Phase 3 will not stabilize until extracted text
                changes from pre_send_text (i.e. new response has actually appeared).
                Default "" (safe for first turn where there is no prior response).

        Returns:
            Text content of the last/newest response block.

        Raises:
            GrokPlaywrightError: If no response is found within max_wait seconds.
        """
        deadline = time.time() + max_wait

        # --- Phase 1: JS-side detection - wait for new response block ---
        # wait_for_function polls every ~100ms in the browser vs Python sleep(0.5)
        logger.debug("[GrokPlaywright] Phase 1: Waiting for new response block...")
        print("[Grok] Waiting for response to start...", flush=True)
        appeared = False
        remaining_ms = max(2000, int((deadline - time.time()) * 1000))
        try:
            # Embed prior_count directly in JS - avoids arg parameter complexity
            js_check = (
                "() => {"
                "  var sels = ['div[class*=\"markdown\"]','div[class*=\"message-content\"]',"
                "    'div[class*=\"prose\"]','[data-testid*=\"message\"] div','article div'];"
                "  for (var i = 0; i < sels.length; i++) {"
                "    try { if (document.querySelectorAll(sels[i]).length > "
                + str(prior_count)
                + ") return true; }"
                "    catch(e) {}"
                "  }"
                "  return false;"
                "}"
            )
            self._page.wait_for_function(js_check, timeout=remaining_ms)
            appeared = True
            logger.debug("[GrokPlaywright] Phase 1: New response block detected")
            print("[Grok] Response started - waiting for completion...", flush=True)
        except Exception as exc:
            logger.warning(
                f"[GrokPlaywright] Phase 1 timeout ({type(exc).__name__}) - continuing"
            )

        if not appeared:
            logger.warning(
                "[GrokPlaywright] No new response block detected - continuing to Phase 2"
            )

        # --- Phase 2a: PRIMARY - wait for NEW action buttons to appear ---
        # Copy/Regenerate buttons appear only when Grok finishes generating.
        # IMPORTANT: Uses prior_action_count to avoid false-positive on existing
        # action buttons from previous responses in multi-turn conversations.
        # If prior_action_count=2 (two prior responses done), waits until count=3.
        # Uses JS querySelectorAll (CSS-compatible) not Playwright :has-text() syntax.
        logger.debug("[GrokPlaywright] Phase 2a: Waiting for new action buttons (primary)...")
        completed = False
        action_timeout = min(45000, max(2000, int((deadline - time.time()) * 1000)))
        # CSS selector for action buttons (no Playwright :has-text syntax)
        action_sels_css = '[data-testid*="copy"], [aria-label*="Copy"], [data-testid*="action-bar"]'
        js_action_check = (
            "() => {"
            "  var btns = document.querySelectorAll("
            + repr(action_sels_css)
            + ");"
            f"  return btns.length > {prior_action_count};"
            "}"
        )
        try:
            self._page.wait_for_function(js_action_check, timeout=action_timeout)
            completed = True
            logger.debug(
                "[GrokPlaywright] Phase 2a: New action button detected - response complete"
            )
            print("[Grok] Completion signal: new action button appeared", flush=True)
        except Exception:
            logger.debug(
                "[GrokPlaywright] Phase 2a: Action buttons not found - trying Phase 2b"
            )

        if not completed:
            # --- Phase 2b: SECONDARY - wait for streaming cursor to disappear ---
            logger.debug("[GrokPlaywright] Phase 2b: Checking streaming cursor...")
            cursor_sel = ", ".join(self.STREAMING_CURSOR_SELECTORS)
            try:
                cursor_locator = self._page.locator(cursor_sel)
                if cursor_locator.count() > 0 and cursor_locator.first.is_visible():
                    remaining_ms = max(2000, int((deadline - time.time()) * 1000))
                    cursor_locator.first.wait_for(state="hidden", timeout=remaining_ms)
                    # Cursor disappears 300-800ms before action buttons appear - add safety
                    self._page.wait_for_timeout(1200)
                    completed = True
                    logger.debug(
                        "[GrokPlaywright] Phase 2b: Streaming cursor gone - response complete"
                    )
                    print(
                        "[Grok] Completion signal: streaming cursor disappeared",
                        flush=True,
                    )
                else:
                    logger.debug(
                        "[GrokPlaywright] Phase 2b: No streaming cursor found - trying Phase 2c"
                    )
            except Exception as exc:
                logger.debug(
                    f"[GrokPlaywright] Phase 2b: {type(exc).__name__} - trying Phase 2c"
                )

        if not completed:
            # --- Phase 2c: LEGACY - wait for status/thinking indicator to disappear ---
            # Must check visibility FIRST - wait_for(state="hidden") returns immediately
            # if the element is not in the DOM at all, giving a false "done" signal.
            logger.debug(
                "[GrokPlaywright] Phase 2c: Checking status/thinking indicators..."
            )
            for status_sel in self.STATUS_SELECTORS:
                try:
                    locator = self._page.locator(status_sel)
                    if locator.count() > 0 and locator.first.is_visible():
                        remaining_ms = max(1000, int((deadline - time.time()) * 1000))
                        locator.first.wait_for(state="hidden", timeout=remaining_ms)
                        logger.debug(
                            f"[GrokPlaywright] Phase 2c: Status indicator gone ({status_sel})"
                        )
                        break
                except Exception:
                    continue

        # --- Phase 3: Content hash stabilization (1.5s window, 0.5s poll) ---
        # Reduced from 3.0s/1.0s: Phase 2a/2b guarantee content is done when they fire,
        # so Phase 3 is just a brief confirmation pass in those cases.
        if not completed:
            logger.debug(
                "[GrokPlaywright] Phase 3: Content hash stabilization (fallback)..."
            )
            print("[Grok] Using content hash stabilization fallback...", flush=True)
        else:
            logger.debug("[GrokPlaywright] Phase 3: Brief stability confirmation...")

        # Phase 3 requires text to CHANGE from pre_send_text before it can
        # stabilize. This prevents returning stale text from a prior response
        # in multi-turn conversations where Phase 1 fires immediately
        # (article div count > prior_count is true before new text appears).
        last_text = pre_send_text
        _changed = (pre_send_text == "")  # Already "changed" if no prior text
        stable_since: Optional[float] = None
        while time.time() < deadline:
            current_text = self._extract_last_response()
            if not _changed:
                # Wait for new response text to appear (different from pre-send)
                if current_text and current_text != pre_send_text:
                    _changed = True
                    last_text = current_text
                    stable_since = None
                    logger.debug(
                        "[GrokPlaywright] Phase 3: new response text detected "
                        f"({len(current_text)} chars) - starting stability check"
                    )
            else:
                # Text has changed - now wait for it to stabilize (1.5s window)
                if current_text and current_text == last_text:
                    if stable_since is None:
                        stable_since = time.time()
                    elif time.time() - stable_since >= 1.5:
                        logger.debug(
                            "[GrokPlaywright] Content stable - extraction complete"
                        )
                        break
                else:
                    stable_since = None
                    last_text = current_text
            time.sleep(0.5)

        # --- Final extraction ---
        text = self._extract_last_response()
        if not text.strip():
            raise GrokPlaywrightError(
                f"No response text extracted after {max_wait}s. "
                f"URL: {self._page.url}"
            )
        return text.strip()

    def _extract_last_response(self) -> str:
        """Extract the text of the last (most recent) response block on the page.

        Uses locator.text_content() which correctly handles both:
        - Off-screen windows (minimized=True): textContent has no layout requirement,
          unlike inner_text() which calls element.innerText and requires rendered layout.
        - Shadow DOM: Playwright locator pierces shadow DOM automatically, while
          page.evaluate(document.querySelectorAll) does NOT pierce shadow DOM and
          returns the outer container with empty textContent.
        """
        for selector in self.RESPONSE_SELECTORS:
            try:
                locator = self._page.locator(selector)
                count = locator.count()
                if count > 0:
                    text = locator.nth(count - 1).text_content(timeout=3000) or ""
                    if text.strip():
                        return text
            except Exception:
                continue
        return ""
