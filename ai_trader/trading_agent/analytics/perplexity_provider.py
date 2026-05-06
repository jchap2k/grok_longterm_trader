"""
Perplexity Sonar API provider for catalyst enrichment.

Replaces Brave Search + Ollama per-symbol enrichment when perplexity.enabled=true
in broker_config.json. Uses Perplexity's Sonar model which has live web search
built in - returns synthesized catalyst summaries with specific figures (EPS,
revenue, guidance) rather than raw headlines.

Cost: Sonar request/search-context fee plus token costs. Broad enrichment should
prefer Perplexity over Grok 4.3 after the May 2026 Grok 4.1 fast deprecation.
Rate limit: 50 RPM (Tier 0) - more than sufficient.

Prompt versions:
  v1: Simple 2-3 sentence summary
  v2: Structured JSON with type/direction/summary/relevance (Grok-reviewed)
  v3: v2 + gap context (change_pct, rel_volume), catalyst_date, priced_in field,
      time-of-day weighting, conflict resolution note (Grok-reviewed)
"""

import os
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger('trading_agent')

PERPLEXITY_API_URL = 'https://api.perplexity.ai/chat/completions'

# v3 prompt - requires symbol, change_pct, direction, rel_volume, volume, current_time
CATALYST_PROMPT = (
    "You are a day-trading analyst specializing in intraday catalysts. "
    "For {symbol} stock, identify ONLY significant news events or catalysts from the last 48-72 hours "
    "that could drive meaningful intraday price action or volatility.\n\n"
    "The stock is currently {change_pct:+.1f}% {direction} on {rel_volume:.1f}x relative volume "
    "({volume:,} shares). Focus your search on what is driving this specific move today.\n\n"
    "Current time: {current_time} PST. Weight catalysts from the last 24 hours more heavily, "
    "especially pre-market or early-session news.\n\n"
    "For each catalyst include:\n"
    "- type (earnings, FDA/approval, M&A, analyst upgrade/downgrade, guidance, "
    "macro/sector flow, product launch/recall, legal/regulatory, other)\n"
    "- direction (bullish, bearish, neutral, mixed)\n"
    "- summary (1-2 factual sentences ONLY, include specific numbers like EPS/revenue/guidance/"
    "target prices, dates, or % changes when available. No speculation.)\n"
    "- catalyst_date (ISO date of the main event, e.g. 2026-02-10)\n"
    "- relevance (high = likely >3-5% intraday move or volume surge; "
    "medium = 1-3% potential; low = minor or already priced in)\n"
    "- priced_in (already_moved = stock already reacted strongly; "
    "not_yet_reflected = news fresh and not priced in; partial = mixed reaction)\n\n"
    "Rules:\n"
    "- Only include catalysts that are NEW or have NEW developments in the last 48-72 hours.\n"
    "- If the news is old or already fully priced in, exclude it or mark priced_in=already_moved.\n"
    "- If multiple catalysts conflict, indicate which is more likely to drive price today in the summary.\n"
    "- If no significant catalysts exist, return an empty array [] for catalysts.\n"
    "- Sentiment should reflect the overall net impact on the stock price today.\n"
    "- Return STRICTLY valid JSON only. No markdown, no explanations, no extra text.\n\n"
    "Return format: "
    "{{\"symbol\": \"{symbol}\", \"sentiment\": \"bullish|bearish|neutral\", "
    "\"catalysts\": [{{\"type\": \"...\", \"direction\": \"bullish|bearish|neutral|mixed\", "
    "\"summary\": \"...\", \"catalyst_date\": \"YYYY-MM-DD\", "
    "\"relevance\": \"high|medium|low\", \"priced_in\": \"already_moved|not_yet_reflected|partial\"}}]}}"
)


XAI_BASE_URL = 'https://api.x.ai/v1'
XAI_CATALYST_MODEL = 'grok-4.20-non-reasoning'  # fallback only; prefer Perplexity for broad enrichment

# Grok batch enrichment settings (primary path over per-symbol calls)
XAI_BATCH_MODEL    = 'grok-4.3'  # fallback only; reasoning required for reliable multi-symbol JSON
XAI_BATCH_SIZE     = 5    # symbols per batch call (tested: 15/15 at batch=5)
XAI_BATCH_PARALLEL = 3    # concurrent batch calls
XAI_BATCH_TIMEOUT  = 120  # seconds per batch API call

GROK_BATCH_SYSTEM = "You are a precise JSON-only stock catalyst analyst."

GROK_BATCH_USER_TEMPLATE = (
    "You are an expert stock catalyst analyst with access to real-time web search.\n"
    "For each ticker below, search for the latest news catalyst driving today's price action.\n"
    "Think step-by-step, then output ONLY a valid JSON array - no extra text.\n"
    "Each summary MUST be 4-8 detailed factual sentences covering the catalyst driving the stock.\n\n"
    "Each object must have exactly these fields:\n"
    "{{\n"
    "  \"ticker\": str,\n"
    "  \"catalyst_type\": str,\n"
    "  \"summary\": str or null,\n"
    "  \"sentiment\": \"bullish|bearish|neutral\",\n"
    "  \"relevance\": \"high|medium|low\"\n"
    "}}\n\n"
    "Return one object per ticker. If no significant catalyst found, set summary to null.\n\n"
    "Stocks:\n{symbols_with_context}"
)


# Historical batch catalyst prompt - for CatalystCapture (backtest simulation only)
HISTORICAL_BATCH_SYSTEM = (
    "You are a financial research assistant with access to historical news archives. Return valid JSON arrays only."
)

HISTORICAL_BATCH_PROMPT = chr(10).join([
    "You are a quantitative trading analyst researching historical stock data.",
    "",
    "For each symbol below, find what was happening on or just before {date_str} "
    "(look back up to {lookback_days} trading days):",
    "",
    "CATALYST: Any news events - earnings, FDA decisions, analyst actions, M&A, "
    "macro/sector events, product announcements - that could drive price action "
    "on {date_str}. Rate quality: none / weak / moderate / strong.",
    "",
    "Rules:",
    "- Return ONLY confirmed facts. Use null for anything uncertain.",
    "- catalyst_quality=none if no catalyst exists for {date_str}.",
    "- Return STRICTLY valid JSON array, no markdown, no explanation.",
    "",
    "Symbols: {symbol_list}",
    "",
    "Return format (JSON array):",
    '[{"symbol":"TICKER","catalyst_quality":"none|weak|moderate|strong",',
    '"catalyst_summary":"description or null",',
    '"catalyst_events":["event1"],"confidence":"low|medium|high"}]',
])
class PerplexityProvider:
    """
    Lightweight wrapper around Perplexity Sonar API for catalyst enrichment.
    Falls back to Grok API (xAI) with live web search when Perplexity returns
    no catalyst for a symbol.
    """

    def __init__(self, api_key: str, model: str = 'sonar',
                 max_tokens: int = 600, timeout: int = 15,
                 xai_api_key: str = None):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._total_tokens = 0
        self._xai_api_key = xai_api_key

    @classmethod
    def from_config(cls, config_dir: str, broker_config: dict = None):
        """
        Build a PerplexityProvider from broker_config.json settings.
        Returns None if perplexity is disabled or key not found.

        enabled modes:
        - "auto" (default): Auto-detect if PERPLEXITY_API_KEY env var or config file exists
        - true: Force-enable (errors if no key found)
        - false: Force-disable (always returns None)
        """
        if broker_config is None:
            return None

        perp_cfg = broker_config.get('perplexity', {})
        enabled = perp_cfg.get('enabled', 'auto')

        # Force-disabled
        if enabled is False or enabled == 'false':
            logger.info("Perplexity: disabled (config enabled=false)")
            return None

        # Try to get API key from environment variable first
        api_key = os.getenv('PERPLEXITY_API_KEY')
        key_source = 'environment variable'

        # Fall back to config file
        if not api_key:
            key_file = perp_cfg.get('api_key_file', 'config/perplexity_api_key.txt')
            key_path = os.path.join(config_dir, '..', key_file) if not os.path.isabs(key_file) else key_file
            alt_path = os.path.join(config_dir, 'perplexity_api_key.txt')

            for path in [key_path, alt_path]:
                try:
                    path = os.path.normpath(path)
                    if os.path.exists(path):
                        api_key = open(path, 'r').read().strip()
                        key_source = f'file: {os.path.basename(path)}'
                        break
                except Exception:
                    continue

        # Handle missing key based on mode
        if not api_key:
            if enabled == 'auto':
                logger.info(
                    "Perplexity: not available (no PERPLEXITY_API_KEY env var or config file) - "
                    "using Grok web catalyst fallback/direct scan path"
                )
                return None
            else:  # enabled=true
                logger.error(
                    "Perplexity: enabled=true but API key not found - "
                    "falling back to Grok web catalyst fallback/direct scan path"
                )
                return None

        # Load xAI key for Grok fallback (same key the trading agent uses)
        xai_key = os.getenv('XAI_API_KEY')
        if not xai_key:
            xai_key_path = os.path.normpath(
                os.path.join(config_dir, '..', 'config', 'xai_api_key.txt')
            )
            try:
                if os.path.exists(xai_key_path):
                    xai_key = open(xai_key_path, 'r').read().strip() or None
            except Exception:
                xai_key = None

        logger.info(f"Perplexity: enabled (key from {key_source}), Grok fallback: {'yes' if xai_key else 'no'}")
        return cls(
            api_key=api_key,
            model=perp_cfg.get('model', 'sonar'),
            max_tokens=perp_cfg.get('max_tokens', 600),
            timeout=perp_cfg.get('timeout_seconds', 15),
            xai_api_key=xai_key,
        )

    def _build_prompt(self, symbol: str, candidate: dict = None) -> str:
        """
        Build v3 prompt with gap context if candidate data available,
        otherwise fall back to generic prompt without price context.
        """
        if candidate:
            change_pct = float(candidate.get('change_pct', candidate.get('gap_pct', 0)) or 0)
            rel_volume = float(candidate.get('rel_volume', candidate.get('relative_volume', 1.0)) or 1.0)
            volume = int(candidate.get('volume', 0) or 0)
            direction = 'up' if change_pct >= 0 else 'down'
            current_time = datetime.now().strftime('%H:%M')
            try:
                return CATALYST_PROMPT.format(
                    symbol=symbol,
                    change_pct=change_pct,
                    direction=direction,
                    rel_volume=rel_volume,
                    volume=volume,
                    current_time=current_time,
                )
            except Exception:
                pass

        # Fallback: no candidate context - strip the gap context lines
        fallback = (
            "You are a day-trading analyst specializing in intraday catalysts. "
            "For {symbol} stock, identify ONLY significant news events or catalysts from the last 48-72 hours "
            "that could drive meaningful intraday price action or volatility.\n\n"
            "For each catalyst include: type, direction (bullish/bearish/neutral/mixed), "
            "summary (1-2 factual sentences with specific numbers), "
            "catalyst_date (ISO date), "
            "relevance (high=>3-5% move; medium=1-3%; low=minor/priced-in), "
            "priced_in (already_moved/not_yet_reflected/partial).\n\n"
            "Rules: Only NEW catalysts from last 48-72 hours. "
            "Exclude old/fully priced-in news. Empty array if nothing significant. "
            "Return STRICTLY valid JSON only.\n\n"
            "Return format: "
            "{{\"symbol\": \"{symbol}\", \"sentiment\": \"bullish|bearish|neutral\", "
            "\"catalysts\": [{{\"type\": \"...\", \"direction\": \"...\", "
            "\"summary\": \"...\", \"catalyst_date\": \"YYYY-MM-DD\", "
            "\"relevance\": \"high|medium|low\", \"priced_in\": \"already_moved|not_yet_reflected|partial\"}}]}}"
        )
        return fallback.format(symbol=symbol)

    def discover_catalysts(self, queries: list = None) -> list:
        """
        Discover stocks with significant catalysts using Perplexity's live web search.

        Replaces: Brave Search + Ollama extraction with a single Perplexity call.

        Args:
            queries: Optional list of search focuses. Defaults to:
                     ['biotech FDA', 'earnings reactions', 'M&A guidance']

        Returns:
            List of dicts: [{"symbol": "TICKER", "catalyst": "type",
                            "news": "summary", "source": "category"}]
        """
        if queries is None:
            queries = [
                "biotech pharma stocks FDA clinical trials today",
                "stocks earnings reactions premarket today",
                "stock market catalysts today earnings M&A guidance"
            ]

        # Single comprehensive query to Perplexity
        prompt = (
            "Search for US stocks with significant price-moving catalysts from the last 24-48 hours.\n\n"
            "Focus areas:\n" + "\n".join(f"- {q}" for q in queries) + "\n\n"
            "Return ONLY a JSON array. No explanations, no markdown, just the array.\n"
            "If no catalysts found, return: []\n"
            "If catalysts found, format:\n"
            '[{"symbol": "TICKER", "catalyst": "type", "news": "1-sentence summary", "source": "biotech|earnings|general"}]\n\n'
            "Catalyst types: FDA_approval, FDA_rejection, earnings_beat, earnings_miss, M&A, "
            "guidance_raise, guidance_lower, analyst_upgrade, analyst_downgrade, product_launch\n\n"
            "Rules:\n"
            "- ONLY stocks with events from last 24-48 hours\n"
            "- Include specific numbers/dates in news summary\n"
            "- Return STRICTLY valid JSON array only, nothing else"
        )

        # Retry logic for transient errors (Grok recommendation)
        import time
        max_retries = 2
        last_error = None

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    PERPLEXITY_API_URL,
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type': 'application/json'
                    },
                    json={
                        'model': self.model,
                        'messages': [
                            {'role': 'system', 'content': 'You are a financial news analyst extracting catalyst data. Return valid JSON only.'},
                            {'role': 'user', 'content': prompt}
                        ],
                        'max_tokens': 1000,  # More tokens for multiple stocks
                        'temperature': 0.1
                    },
                    timeout=self.timeout
                )

                if response.status_code != 200:
                    if response.status_code in (429, 503):  # Rate limit or service unavailable
                        last_error = f"HTTP {response.status_code}"
                        if attempt < max_retries - 1:
                            wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s
                            logger.warning(f"Perplexity catalyst discovery: {last_error}, retrying in {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                    logger.error(f"Perplexity catalyst discovery failed: HTTP {response.status_code}")
                    return []

                data = response.json()
                content = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

                # Track token usage
                usage = data.get('usage', {})
                tokens_used = usage.get('total_tokens', 0)
                self._total_tokens += tokens_used

                # Parse JSON response
                import json
                import re

                # Debug: log raw response
                logger.debug(f"Perplexity raw response: {content[:500]}")

                # Try to extract JSON array from response (might have markdown code blocks)
                content_clean = content.replace('```json', '').replace('```', '').strip()
                json_match = re.search(r'\[.*\]', content_clean, re.DOTALL)
                if json_match:
                    try:
                        catalysts = json.loads(json_match.group(0))
                        if not catalysts:
                            logger.info("Perplexity found no catalysts - continuing with other sources")
                            return []
                        logger.info(f"Perplexity catalyst discovery: {len(catalysts)} stocks found ({tokens_used} tokens)")
                        return catalysts if isinstance(catalysts, list) else []
                    except json.JSONDecodeError as e:
                        logger.warning(f"Perplexity returned invalid JSON for catalysts: {e}")
                        logger.debug(f"Invalid JSON content: {json_match.group(0)[:200]}")
                        return []
                else:
                    logger.warning(f"Perplexity catalyst discovery: no JSON array in response")
                    logger.debug(f"Response content: {content[:200]}")
                    return []

            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Perplexity catalyst discovery: {type(e).__name__}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                logger.error(f"Perplexity catalyst discovery failed after {max_retries} attempts: {last_error}")
                return []
            except Exception as e:
                logger.error(f"Perplexity catalyst discovery failed: {e}")
                return []

        # If we exhausted retries
        logger.error(f"Perplexity catalyst discovery failed after {max_retries} attempts: {last_error}")
        return []

    def _serialize_catalysts(self, catalysts: list) -> str:
        """
        Serialize catalyst list to readable string for catalyst_summary field.

        Format per line:
          [HIGH/bullish|not_yet_reflected] earnings (2026-02-10): EPS $1.95 beat $1.90...
        """
        lines = []
        for c in catalysts:
            relevance = c.get('relevance', 'med').upper()[:3]
            direction = c.get('direction', 'neutral')
            priced_in = c.get('priced_in', '')
            ctype = c.get('type', 'news')
            summary = c.get('summary', '')
            date = c.get('catalyst_date', '')

            tag = f"[{relevance}/{direction}"
            if priced_in:
                tag += f"|{priced_in}"
            tag += "]"

            date_part = f" ({date})" if date else ""
            lines.append(f"{tag} {ctype}{date_part}: {summary}")

        return '\n'.join(lines)

    def get_catalyst_summary(self, symbol: str, candidate: dict = None) -> dict:
        """
        Fetch a structured catalyst summary for a single symbol.

        Args:
            symbol: Stock ticker
            candidate: Optional candidate dict with change_pct, rel_volume, volume
                       for gap-context enrichment (v3 prompt). If None, uses
                       generic prompt without price context.

        Returns:
            {
                'summary': str or None,   # serialized catalyst string for catalyst_summary field
                'sentiment': str,         # 'bullish' | 'bearish' | 'neutral'
                'tokens_used': int,
                'error': str or None
            }
        """
        prompt = self._build_prompt(symbol, candidate)
        try:
            response = requests.post(
                PERPLEXITY_API_URL,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': self.max_tokens,
                },
                timeout=self.timeout,
            )

            if response.status_code != 200:
                logger.warning(f"Perplexity API error for {symbol}: HTTP {response.status_code}")
                return {'summary': None, 'sentiment': 'neutral', 'tokens_used': 0,
                        'error': f'HTTP {response.status_code}'}

            data = response.json()
            text = data['choices'][0]['message']['content'].strip()
            usage = data.get('usage', {})
            tokens = usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)
            self._total_tokens += tokens

            # Strip markdown fences if model added them
            if text.startswith('```'):
                text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            # Parse structured JSON response
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None

            if parsed and isinstance(parsed, dict):
                catalysts = parsed.get('catalysts', [])
                sentiment = parsed.get('sentiment', 'neutral')

                if not catalysts:
                    if self._xai_api_key:
                        return self._grok_catalyst_fallback(symbol, candidate)
                    return {'summary': None, 'sentiment': 'neutral',
                            'tokens_used': tokens, 'error': None}

                summary_text = self._serialize_catalysts(catalysts)
                logger.debug(
                    f"Perplexity {symbol}: {tokens} tokens, {sentiment}, "
                    f"{len(catalysts)} catalysts"
                )
                return {'summary': summary_text, 'sentiment': sentiment,
                        'tokens_used': tokens, 'error': None}

            # Fallback: treat raw text as summary
            text_lower = text.lower()
            if any(p in text_lower for p in ('no recent catalyst', 'no significant catalyst')):
                if self._xai_api_key:
                    return self._grok_catalyst_fallback(symbol, candidate)
                return {'summary': None, 'sentiment': 'neutral',
                        'tokens_used': tokens, 'error': None}

            sentiment = 'neutral'
            bull_count = sum(1 for w in ('beat', 'raised', 'upgrade', 'approved', 'surged') if w in text_lower)
            bear_count = sum(1 for w in ('miss', 'cut', 'downgrade', 'rejected', 'fell') if w in text_lower)
            if bull_count > bear_count:
                sentiment = 'bullish'
            elif bear_count > bull_count:
                sentiment = 'bearish'

            logger.debug(f"Perplexity {symbol}: {tokens} tokens, {sentiment} (fallback plain text)")
            return {'summary': text, 'sentiment': sentiment, 'tokens_used': tokens, 'error': None}

        except requests.Timeout:
            logger.warning(f"Perplexity timeout for {symbol}")
            return {'summary': None, 'sentiment': 'neutral', 'tokens_used': 0, 'error': 'timeout'}
        except Exception as e:
            logger.warning(f"Perplexity error for {symbol}: {e}")
            return {'summary': None, 'sentiment': 'neutral', 'tokens_used': 0, 'error': str(e)}

    def _grok_catalyst_fallback(self, symbol: str, candidate: dict = None) -> dict:
        """
        Fallback catalyst lookup via xAI Grok Agent Tools API with live web search.
        Called once when Perplexity returns no catalyst for a symbol.
        Uses client.responses.create() with tools=[{"type": "web_search"}].

        Returns same dict format as get_catalyst_summary().
        """
        import re
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._xai_api_key, base_url=XAI_BASE_URL)
            change_pct = (candidate or {}).get("change_pct", 0.0)
            rel_volume = (candidate or {}).get("rel_volume", 1.0)
            if change_pct and abs(change_pct) > 0.5:
                context = (
                    f"{symbol} stock has moved {change_pct:+.1f}% today with "
                    f"{rel_volume:.1f}x relative volume. "
                )
            else:
                context = f"Stock: {symbol}. "
            prompt = (
                context +
                f"Search the web for the most recent news catalyst driving {symbol} stock "
                f"price movement today. In 2-3 sentences, summarize: (1) the main catalyst, "
                f"(2) whether the news is bullish or bearish, and (3) any key financial "
                f"details (earnings, deals, analyst changes, etc.). Be specific and factual."
            )
            logger.debug(f"[Grok fallback] {symbol}: calling xAI Agent Tools API with web_search")
            response = client.responses.create(
                model=XAI_CATALYST_MODEL,
                input=[{"role": "user", "content": prompt}],
                tools=[{"type": "web_search"}],
            )
            text = response.output_text or ""
            # Strip citation markers like [[1]], [[1]](url)
            text = re.sub(r"\[{1,2}\d+(?:,\s*\d+)*\]{1,2}(?:\([^)]*\))?", "", text).strip()
            if not text:
                logger.debug(f"[Grok fallback] {symbol}: empty response")
                return {"summary": None, "sentiment": "neutral", "tokens_used": 0, "error": None}
            text_lower = text.lower()
            bullish_words = ("surge", "beat", "record", "gain", "bullish", "positive",
                             "strong", "growth", "upgrade", "raised", "topped", "exceeded")
            bearish_words = ("down", "fall", "miss", "loss", "bearish", "negative",
                             "weak", "decline", "drop", "downgrade", "cut", "missed")
            if any(w in text_lower for w in bullish_words):
                sentiment = "bullish"
            elif any(w in text_lower for w in bearish_words):
                sentiment = "bearish"
            else:
                sentiment = "neutral"
            tokens_used = 0
            if hasattr(response, "usage") and response.usage:
                tokens_used = getattr(response.usage, "total_tokens", 0) or 0
            logger.info(
                f"[Grok fallback] {symbol}: got summary ({len(text)} chars), sentiment={sentiment}"
            )
            return {"summary": text, "sentiment": sentiment, "tokens_used": tokens_used, "error": None}
        except Exception as e:
            logger.warning(f"[Grok fallback] {symbol}: API call failed: {e}")
            return {"summary": None, "sentiment": "neutral", "tokens_used": 0, "error": str(e)}

    def _grok_batch_one_call(self, batch_items: list, batch_idx: int) -> tuple:
        """
        Execute one Grok batch API call for a group of symbols.

        Args:
            batch_items: list of (symbol, candidate_or_None) tuples
            batch_idx:   1-based index for logging

        Returns:
            (per_symbol_dict, tokens_used)
            per_symbol_dict: {symbol: {'summary', 'sentiment', 'tokens_used', 'error'}}
        """
        import re
        from openai import OpenAI

        symbols_in_batch = [sym for sym, _ in batch_items]
        null_result = {"summary": None, "sentiment": "neutral", "tokens_used": 0, "error": None}

        # Build context lines: "- AAPL (+5.2%, 3.5x rvol)"
        context_lines = []
        for sym, cand in batch_items:
            if cand:
                chg = float(cand.get("change_pct", cand.get("gap_pct", 0)) or 0)
                rvol = float(cand.get("rel_volume", cand.get("relative_volume", 1.0)) or 1.0)
                context_lines.append(f"- {sym} ({chg:+.1f}%, {rvol:.1f}x rvol)")
            else:
                context_lines.append(f"- {sym}")

        symbols_with_context = "\n".join(context_lines)
        user_prompt = GROK_BATCH_USER_TEMPLATE.format(symbols_with_context=symbols_with_context)

        try:
            client = OpenAI(api_key=self._xai_api_key, base_url=XAI_BASE_URL)
            response = client.responses.create(
                model=XAI_BATCH_MODEL,
                input=[
                    {"role": "system", "content": GROK_BATCH_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[{"type": "web_search"}],
                timeout=XAI_BATCH_TIMEOUT,
            )
            raw_text = response.output_text or ""
            # Strip citation markers [[N]], [[N]](url)
            raw_text = re.sub(r"\[{1,2}\d+(?:,\s*\d+)*\]{1,2}(?:\([^)]*\))?", "", raw_text).strip()
            tokens_used = 0
            if hasattr(response, "usage") and response.usage:
                tokens_used = getattr(response.usage, "total_tokens", 0) or 0

        except Exception as e:
            logger.warning(f"[Grok batch {batch_idx}] API call failed: {e}")
            err = str(e)
            return ({sym: {**null_result, "error": err} for sym in symbols_in_batch}, 0)

        # Parse JSON array from response
        parsed = None
        try:
            clean = raw_text.replace("```json", "").replace("```", "").strip()
            m = re.search(r"\[.*\]", clean, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
        except Exception as e:
            logger.warning(f"[Grok batch {batch_idx}] JSON parse failed: {e}")

        per_symbol = {}
        if isinstance(parsed, list):
            for item in parsed:
                ticker = item.get("ticker", "").upper().strip()
                if not ticker:
                    continue
                summary = item.get("summary") or None
                sentiment = item.get("sentiment", "neutral")
                if sentiment not in ("bullish", "bearish", "neutral"):
                    sentiment = "neutral"
                per_symbol[ticker] = {
                    "summary": summary,
                    "sentiment": sentiment,
                    "tokens_used": 0,
                    "error": None,
                }

        # Fill any missing symbols with null result
        for sym in symbols_in_batch:
            if sym not in per_symbol:
                per_symbol[sym] = {**null_result}

        got = sum(1 for r in per_symbol.values() if r["summary"])
        logger.info(
            f"[Grok batch {batch_idx}] {len(symbols_in_batch)} symbols: "
            f"{got} with catalyst, {tokens_used} tokens"
        )
        return (per_symbol, tokens_used)

    def get_catalyst_summaries_grok_batch(
        self,
        symbols: list,
        candidates: dict = None,
        batch_size: int = None,
        batch_parallel: int = None,
    ) -> dict:
        """
        Primary Grok batch catalyst enrichment path.

        Groups symbols into batches of batch_size (default XAI_BATCH_SIZE=5),
        runs batch_parallel (default XAI_BATCH_PARALLEL=3) concurrent API calls,
        then re-tries null-summary symbols as a single batch retry call (1 API call vs N).

        Args:
            symbols:        List of tickers
            candidates:     Optional dict mapping symbol -> candidate dict for context
            batch_size:     Symbols per batch call (default: XAI_BATCH_SIZE)
            batch_parallel: Concurrent batch calls (default: XAI_BATCH_PARALLEL)

        Returns:
            {symbol: {'summary', 'sentiment', 'tokens_used', 'error'}}
            Same format as get_catalyst_summaries_bulk().
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from collections import deque

        if batch_size is None:
            batch_size = XAI_BATCH_SIZE
        if batch_parallel is None:
            batch_parallel = XAI_BATCH_PARALLEL

        cands = candidates or {}
        batches = []
        for i in range(0, len(symbols), batch_size):
            chunk = symbols[i:i + batch_size]
            batch_items = [(sym, cands.get(sym)) for sym in chunk]
            batches.append(batch_items)

        results = {}
        total_tokens = 0
        max_retries = 2
        retry_fifo = deque()

        # Initial parallel batch pass - enqueue failures immediately as each batch completes
        with ThreadPoolExecutor(max_workers=batch_parallel) as executor:
            future_to_batch = {
                executor.submit(self._grok_batch_one_call, batch_items, idx + 1): batch_items
                for idx, batch_items in enumerate(batches)
            }
            for future in as_completed(future_to_batch):
                orig_items = future_to_batch[future]
                per_symbol, tokens = future.result()
                results.update(per_symbol)
                total_tokens += tokens
                null_items = [
                    (sym, cand) for sym, cand in orig_items
                    if not results.get(sym, {}).get("summary")
                ]
                if null_items:
                    retry_fifo.append((null_items, max_retries))

        # Drain retry FIFO: each entry is ([items], retries_remaining)
        # Pop, retry the shrinking batch, re-enqueue if still failing and retries > 0
        while retry_fifo:
            null_items, retries_left = retry_fifo.popleft()
            syms = [sym for sym, _ in null_items]
            logger.info(
                f"[Grok batch] FIFO retry: {len(null_items)} symbols, "
                f"{retries_left} retries left: {syms}"
            )
            per_symbol, tokens = self._grok_batch_one_call(null_items, batch_idx=0)
            results.update(per_symbol)
            total_tokens += tokens
            still_null = [
                (sym, cand) for sym, cand in null_items
                if not results.get(sym, {}).get("summary")
            ]
            if still_null and retries_left > 1:
                retry_fifo.append((still_null, retries_left - 1))

        successful = sum(1 for r in results.values() if r.get("summary"))
        logger.info(
            f"[Grok batch] enriched {len(symbols)} symbols: {successful} with catalyst, "
            f"{total_tokens} tokens total"
        )
        return results

    def get_catalyst_summaries_bulk(self, symbols: list,
                                    candidates: dict = None) -> dict:
        """
        Fetch catalyst summaries for multiple symbols sequentially.

        Args:
            symbols: List of tickers
            candidates: Optional dict mapping symbol -> candidate dict for gap context.
                        If provided, each symbol gets its change_pct/rel_volume/volume
                        injected into the prompt for targeted searching.

        Returns:
            Dict mapping symbol -> result dict from get_catalyst_summary()
        """
        results = {}
        for symbol in symbols:
            candidate = (candidates or {}).get(symbol)
            results[symbol] = self.get_catalyst_summary(symbol, candidate=candidate)

        successful = sum(1 for r in results.values() if not r.get('error'))
        total_tokens = sum(r['tokens_used'] for r in results.values())
        request_cost = successful * 0.005
        token_cost = total_tokens / 1_000_000 * 1.0
        total_cost = request_cost + token_cost
        logger.info(
            f"Perplexity enriched {len(symbols)} symbols: {successful} requests, "
            f"{total_tokens} tokens (~${total_cost:.4f} = "
            f"${request_cost:.4f} requests + ${token_cost:.5f} tokens)"
        )
        return results

    def discover_market_overview(self) -> str:
        """
        Fetch a broad pre-market market summary for the daily context.

        Called at ~6:20 AM warmup so the daily context generation at 6:35 AM
        has real market intelligence instead of placeholder text.

        Returns plain text summary (~5-8 sentences) covering futures direction,
        dominant macro themes, sector rotation, and overall sentiment.

        Returns empty string on any failure (non-fatal).
        """
        prompt = (
            "Give me a concise pre-market US stock market summary for today's trading session. "
            "Cover: S&P 500 and Nasdaq futures direction and magnitude, key overnight macro news "
            "(Fed, economic data releases, geopolitical events), dominant sector themes in play "
            "today and why, any major earnings reactions driving pre-market moves, and overall "
            "sentiment (risk-on, risk-off, or neutral). "
            "Be factual and specific with numbers where available. "
            "5-8 sentences total, plain prose. No bullet points or headers. "
            "Focus on what a US equities day trader needs to know before the 9:30 AM ET open."
        )
        try:
            response = requests.post(
                PERPLEXITY_API_URL,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': self.model,
                    'messages': [
                        {'role': 'system', 'content': 'You are a concise pre-market analyst. Summarize key market conditions for day traders in plain prose.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_tokens': 400,
                    'temperature': 0.1
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            overview = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            tokens = result.get('usage', {}).get('total_tokens', 0)
            self._total_tokens += tokens
            if overview:
                logger.info(f'[Perplexity] Market overview fetched ({len(overview)} chars, {tokens} tokens)')
            return overview
        except Exception as e:
            logger.warning(f'[Perplexity] Market overview fetch failed (non-fatal): {e}')
            return ''

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_historical_batch(self, symbols: list, date_str: str,
                             lookback_days: int = 5) -> list:
        """
        Fetch historical catalyst data for a batch of symbols on a specific
        past date. Designed for backtest simulation, not live trading.

        Uses Perplexity sonar live web search to retrieve archived news
        for the given date. One API call for all symbols (batched).

        Args:
            symbols:       List of tickers
            date_str:      Target date YYYY-MM-DD
            lookback_days: Trading days to look back

        Returns:
            List of dicts: symbol, catalyst_quality, catalyst_summary,
            catalyst_events, confidence
        """
        import re as _re
        import time as _time

        symbol_list = ", ".join(symbols)
        prompt = HISTORICAL_BATCH_PROMPT.format(
            date_str=date_str,
            lookback_days=lookback_days,
            symbol_list=symbol_list,
        )

        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    PERPLEXITY_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": HISTORICAL_BATCH_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max(600, len(symbols) * 120),
                        "temperature": 0.1,
                    },
                    timeout=max(self.timeout, 20),
                )

                if response.status_code != 200:
                    if response.status_code in (429, 503) and attempt < max_retries - 1:
                        _time.sleep(2 ** attempt)
                        continue
                    logger.error(
                        f"[Perplexity] historical_batch HTTP {response.status_code}"
                        f" for {len(symbols)} symbols on {date_str}"
                    )
                    return []

                data = response.json()
                raw = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                tokens = data.get("usage", {}).get("total_tokens", 0)
                self._total_tokens += tokens
                self._api_call_count = getattr(self, "_api_call_count", 0) + 1
                logger.info(
                    f"[Perplexity] historical_batch {len(symbols)} symbols"
                    f" on {date_str} ({tokens} tokens, call #{self._api_call_count})"
                )

                clean = raw.replace("```json", "").replace("```", "").strip()
                m = _re.search(r"\[.*\]", clean, _re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list):
                        return parsed

                logger.warning(
                    f"[Perplexity] historical_batch: no JSON array for {date_str}"
                )
                return []

            except Exception as exc:
                if attempt < max_retries - 1:
                    _time.sleep(2 ** attempt)
                    continue
                logger.error(f"[Perplexity] historical_batch failed: {exc}")
                return []

        return []
