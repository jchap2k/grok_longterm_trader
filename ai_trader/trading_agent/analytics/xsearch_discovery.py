"""
XSearchDiscovery - standalone X (Twitter) search candidate discovery class.

Searches posts from key swing trading accounts on X to surface new stock candidates.
Uses xAI's x_search tool (Architecture C: Agent Tools API).

Phase 1: Standalone class, no dependency on market_scanner.py.
Phase 2: Integration gated on paper trading performance (4+ weeks, TV pipeline validated).

Run live smoke test: python scripts/test_xsearch_live.py
Run unit tests: cd ai_trader/trading_agent && python -m pytest scheduler/tests/test_xsearch_discovery.py -v
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Default key users to monitor (confirmed swing traders with public X accounts)
SWING_KEY_USERS = [
    "markminervini",
    "Qullamaggie",
    "PatternProfits",
    "TraderLion_",
    "DanFitzpatrick",
    "pradatrades",
    "stockbee",
    "traderstewie",
    "alphatrends",
    "corymitc",
    "RedDogT3",
    "sjosephburns",
    "Trader_Dante",
    "Trader_XO",
    "PeterLBrandt",
    "KoroushAK",
    "steenbab",
]

# Conviction keywords for scoring (bullish/setup language only - intentionally tight)
CONVICTION_KEYWORDS = ["breakout", "long", "target", "setup", "entry", "measured move"]

# Noise filter: symbols that look like tickers but are not tradeable stocks
_NOISE_SYMBOLS = frozenset({
    # Fiat currencies
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR", "MXN",
    "NZD", "HKD", "SGD", "SEK", "NOK", "DKK", "TRY", "BRL", "RUB", "ZAR",
    # Crypto
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "USDT", "USDC",
    "AVAX", "DOT", "MATIC", "LINK", "LTC", "BCH", "UNI", "SHIB", "ATOM",
    "ALGO", "XLM", "VET", "FIL", "THETA", "XMR", "EOS", "AAVE", "MKR",
    # Common false positives (abbreviations/acronyms appearing in financial text)
    "CEO", "CFO", "COO", "IPO", "ETF", "ETFs", "SEC", "FDA", "NYSE", "NASDAQ",
    "LLC", "INC", "LTD", "LP", "AI", "IS", "IT", "AT", "TO", "OR", "AND",
    "THE", "FOR", "ON", "OF", "IN", "MY", "BY", "AS", "AN", "AM", "PM",
    "QE", "QT", "MBS", "EPS", "PE", "PEG", "RSI", "ATR", "SMA", "EMA",
    "MACD", "VWAP", "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT",
    "VIX", "OTC", "PPP", "YOY", "QOQ", "GDP", "CPI", "PPI", "NFP",
    "FOMC", "FED", "ECB", "BOJ", "BOE", "IMF", "WTO", "USD",
    # Common broad market ETFs that aren't swing candidates
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLB", "XLC", "XLP", "XLU", "XLRE",
    "VTI", "VOO", "IVV", "AGG", "BND", "HYG", "LQD",
})


class XSearchDiscovery:
    """
    Standalone X search candidate discovery with built-in top-N scoring.

    Queries xAI Grok with x_search tool to surface stock tickers
    mentioned by key swing trading accounts on X.

    Args:
        key_users: List of X handles to monitor (no @ prefix). Defaults to SWING_KEY_USERS.
        min_faves: Minimum favorites/likes filter (embedded in search query)
        lookback_days: How many days back to search
        api_key: xAI API key. Defaults to XAI_API_KEY env var.
        max_handles_per_call: Batch size limit for x_search API (default: 20)
        max_added_candidates: Hard cap on candidates returned (default: 8)
        model: xAI model to use (default: grok-4-1-fast-reasoning)
    """

    def __init__(
        self,
        key_users=None,
        min_faves=15,
        lookback_days=7,
        api_key=None,
        max_handles_per_call=20,
        max_added_candidates=8,
        model="grok-4-1-fast-reasoning",
    ):
        self.key_users = list(key_users or SWING_KEY_USERS)
        self.min_faves = min_faves
        self.lookback_days = lookback_days
        self.max_handles_per_call = max_handles_per_call
        self.max_added_candidates = max_added_candidates
        self.model = model
        self.api_key = api_key or os.getenv("XAI_API_KEY", "")
        self._last_raw_response = ""  # stores last API response for debugging/testing

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_candidates(self) -> list:
        """
        Search X for stock mentions by key users.

        Returns up to max_added_candidates candidates, scored and sorted.

        Returns:
            List of candidate dicts (empty list on any failure).
            Each dict has: symbol, source, source_user, post_snippet,
            mentioned_at, x_score, price, change_pct, volume, rel_volume,
            avg_volume, atr, perf_3m, sector, trade_id.
            Price/volume fields are None until enriched in Phase 2.
            x_score is 0.0 placeholder until _calculate_score() is enhanced.
        """
        seen_symbols = set()
        results = []

        batches = self._batch_users()
        for batch in batches:
            try:
                raw_text = self._call_xsearch_api(batch)
                symbols = self._parse_symbols(raw_text)
                symbols = self._filter_noise(symbols)
                for symbol in symbols:
                    if symbol not in seen_symbols:
                        seen_symbols.add(symbol)
                        candidate = self._build_candidate(
                            symbol,
                            source_user=", ".join(batch),
                            snippet=raw_text[:280] if raw_text else "",
                        )
                        results.append(candidate)
            except Exception as exc:
                logger.warning("XSearchDiscovery batch failed: %s", exc)
                # Continue with remaining batches

        # Score and keep only top N candidates
        if results:
            for c in results:
                c["x_score"] = self._calculate_score(c)
            results.sort(key=lambda c: c["x_score"], reverse=True)
            results = results[:self.max_added_candidates]
            logger.info(
                "XSearchDiscovery: extracted %d top candidates (cap=%d)",
                len(results), self.max_added_candidates,
            )

        return results

    def get_last_raw_response(self) -> str:
        """Return the raw text from the most recent xAI API call (for debugging/testing)."""
        return self._last_raw_response

    # ------------------------------------------------------------------
    # Internal helpers (public for testing / mocking)
    # ------------------------------------------------------------------

    def _batch_users(self) -> list:
        """Split key_users into batches of max_handles_per_call."""
        batches = []
        for i in range(0, len(self.key_users), self.max_handles_per_call):
            batches.append(self.key_users[i : i + self.max_handles_per_call])
        return batches

    def _call_xsearch_api(self, handles: list) -> str:
        """
        Call xAI API with X search to find posts from given handles.

        Architecture C: Agent Tools API.
        Uses xai_sdk.tools.x_search() as a native tool in chat.create().
        Handle filtering and lookback period are embedded in the prompt.
        Prompt instructs Grok to return ONLY a valid JSON array (no prose).
        Response text is accumulated from chat.stream() chunks.

        Args:
            handles: List of X handles (no @ prefix)

        Returns:
            Raw text response from Grok (JSON array or fallback prose)

        Raises:
            ImportError: If xai_sdk not installed
            Exception: On API errors (caller handles gracefully)
        """
        from xai_sdk import Client  # noqa: import inside method, optional dep
        from xai_sdk.tools import x_search
        from xai_sdk.chat import user as user_msg

        client = Client(api_key=self.api_key)

        handles_str = ", ".join(f"@{h}" for h in handles)
        prompt = (
            f"You are a precise swing-trade ticker extractor. "
            f"Search X posts from the last {self.lookback_days} days by these traders only: {handles_str}. "
            f"Return ONLY a valid JSON array of objects for clear US equity swing setups. "
            f'Format: [{{"symbol": "AAPL", "mentioned_by": "Qullamaggie", "summary": "one-sentence context"}}, ...] '
            f"Rules: "
            f"- Only posts with at least {self.min_faves} likes "
            f"- Only uppercase 1-5 letter US stock tickers "
            f"- Exclude crypto, ETFs, indices, currencies, abbreviations "
            f"- Empty array [] if nothing qualifies "
            f"- NO prose, explanations, or extra text outside the JSON array"
        )

        chat = client.chat.create(
            model=self.model,
            tools=[x_search()],
        )
        chat.append(user_msg(prompt))

        full_text = ""
        for _response, chunk in chat.stream():
            if hasattr(chunk, "content") and chunk.content:
                full_text += chunk.content

        logger.debug("X search raw response length: %d chars", len(full_text))
        self._last_raw_response = full_text  # stored for testing/debugging
        return full_text

    def _parse_symbols(self, text: str) -> list:
        """
        Extract stock ticker symbols from text.

        Tries JSON array parsing first (most reliable when prompt is tight),
        falls back to dollar-sign pattern for prose responses.

        Returns:
            Deduplicated list of ticker strings (uppercase, no $)
        """
        if not text:
            return []

        found = []

        # 1. JSON-first: look for outermost [...] and parse
        try:
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                if isinstance(data, list) and not data:
                    logger.info("Grok returned empty array - no qualifying mentions")
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "symbol" in item:
                            sym = str(item["symbol"]).upper().strip()
                            if re.match(r"^[A-Z]{1,5}$", sym):
                                found.append(sym)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # 2. Fallback: $TICKER pattern (uppercase only, 1-5 chars)
        if not found:
            dollar_pattern = re.compile(r'\$([A-Z]{1,5})\b')
            for match in dollar_pattern.finditer(text):
                ticker = match.group(1)
                if ticker not in found:
                    found.append(ticker)

        # Dedup preserving order
        return list(dict.fromkeys(found))

    def _filter_noise(self, symbols: list) -> list:
        """
        Remove non-stock symbols from candidate list.

        Filters:
        - Currencies (USD, EUR, GBP, JPY, ...)
        - Crypto (BTC, ETH, SOL, XRP, ...)
        - Common false positives (CEO, IPO, LLC, AI, ...)
        - Symbols longer than 5 characters

        Args:
            symbols: List of ticker strings

        Returns:
            Filtered list of likely-valid stock tickers
        """
        return [s for s in symbols if len(s) <= 5 and s.upper() not in _NOISE_SYMBOLS]

    def _calculate_score(self, candidate: dict) -> float:
        """
        Lightweight score for top-N selection.

        Scores on conviction keywords and chart/volume language in the post snippet.
        Phase 2 enhancement: add mention count, handle reputation, RS score.

        Args:
            candidate: Candidate dict from _build_candidate()

        Returns:
            Float score 0.0-1.0 (higher = more relevant)
        """
        summary = (candidate.get("post_snippet") or "").lower()
        score = 0.65  # base
        if any(kw in summary for kw in CONVICTION_KEYWORDS):
            score += 0.15
        if "chart" in summary or "volume" in summary:
            score += 0.10
        return min(1.0, score)

    def _build_candidate(self, symbol: str, source_user: str = "", snippet: str = "") -> dict:
        """
        Build a candidate dict in the standard market_scanner format.

        Price/volume fields are None until enriched downstream (Phase 2).
        x_score is 0.0 placeholder, populated by fetch_candidates() after scoring.
        trade_id is None for downstream pipeline compatibility (Grok review finding).

        Args:
            symbol: Stock ticker (e.g. "AAPL")
            source_user: X handle(s) that mentioned this ticker
            snippet: Raw response excerpt (truncated to 280 chars)

        Returns:
            Candidate dict with all required fields
        """
        return {
            "symbol": symbol,
            "source": "x_search",
            "source_user": source_user,
            "post_snippet": snippet[:280] if snippet else "",
            "mentioned_at": datetime.now().isoformat(),
            "x_score": 0.0,  # populated in fetch_candidates after _calculate_score
            # Price/volume enriched in Phase 2
            "price": None,
            "change_pct": None,
            "volume": None,
            "rel_volume": None,
            "avg_volume": None,
            "atr": None,
            "perf_3m": None,
            "sector": None,
            # Downstream compatibility (Grok review: required field)
            "trade_id": None,
        }
