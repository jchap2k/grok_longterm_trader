"""
News Fetcher with Ollama Summarization

Fetches real-time news from the web via MCP servers and uses Ollama
to summarize into concise, actionable intelligence for the main trading agent.

This reduces token usage on expensive APIs (Grok, Claude) by pre-processing
news data locally before passing to the decision-making agent.

Flow:
1. Fetch news via MCP Brave Search or web scraping
2. Pass raw headlines/articles to Ollama for summarization
3. Return clean, concise summary to trading agent
4. Trading agent uses summary instead of raw news data

Requires:
- Ollama running locally with model loaded
- MCP servers configured in .mcp.json (brave-search, fetch)
- Brave API key (free tier: 2000 searches/month)
"""

import logging
import json
import time
import threading
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Global rate limiter for Brave Search API (shared across all NewsFetcher instances)
_brave_api_lock = threading.Lock()
_brave_last_request_time = None


class NewsFetcher:
    """
    Fetches and summarizes financial news using MCP + Ollama.
    """

    def __init__(self, ollama_provider=None, mcp_client=None, usage_file=None, worker_pool=None):
        """
        Initialize news fetcher.

        Args:
            ollama_provider: OllamaProvider instance for summarization
            mcp_client: MCP client for web access (will use Cline's built-in MCP)
            usage_file: Path to usage tracking file (default: ai_trader_data/brave_search_usage.json)
            worker_pool: OllamaWorkerPool for parallel processing (optional)
        """
        self.ollama = ollama_provider
        self.mcp_client = mcp_client
        self.worker_pool = worker_pool
        self._cache = {}  # Simple cache to avoid refetching same news
        self._cache_duration = timedelta(minutes=15)  # Cache news for 15 min
        
        # Usage tracking for Brave Search rate limits
        if usage_file is None:
            usage_file = Path(__file__).parent.parent.parent / "ai_trader_data" / "brave_search_usage.json"
        self.usage_file = Path(usage_file)
        self._last_request_time = None
        self._usage_data = self._load_usage_data()

        pool_info = f" with {worker_pool.num_workers}-worker pool" if worker_pool else ""
        logger.info(f"NewsFetcher initialized{pool_info} with rate limiting")

    def get_stock_news(
        self,
        symbol: str,
        max_results: int = 10,
        use_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch and summarize news for a specific stock.

        Args:
            symbol: Stock ticker (e.g., "AAPL")
            max_results: Maximum number of news items to fetch
            use_cache: If True, return cached results if available

        Returns:
            Dict with 'summary', 'sentiment', 'headlines', 'fetched_at'
            or None if error/unavailable
        """
        cache_key = f"{symbol}_{max_results}"

        # Check cache
        if use_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            age = datetime.now() - cached['fetched_at']
            if age < self._cache_duration:
                logger.debug(f"Using cached news for {symbol} (age: {age.seconds}s)")
                return cached

        try:
            # Fetch raw news
            headlines = self._fetch_headlines(symbol, max_results)
            if not headlines:
                logger.warning(f"No news found for {symbol}")
                return None

            # Summarize with Ollama
            summary_data = self._summarize_with_ollama(symbol, headlines)
            if not summary_data:
                logger.warning(f"Ollama summarization failed for {symbol}")
                return None

            # Build result
            result = {
                'symbol': symbol,
                'summary': summary_data.get('summary', ''),
                'sentiment': summary_data.get('sentiment', 'neutral'),
                'key_points': summary_data.get('key_points', []),
                'headlines': headlines[:5],  # Include top 5 raw headlines
                'fetched_at': datetime.now(),
                'source': 'brave_search'
            }

            # Cache it
            self._cache[cache_key] = result

            logger.info(f"News fetched and summarized for {symbol}: {result['sentiment']} sentiment")
            return result

        except Exception as e:
            logger.error(f"Error fetching news for {symbol}: {e}")
            return None

    def get_market_news(
        self,
        topics: List[str] = None,
        max_results: int = 15
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch and summarize general market news.

        Args:
            topics: List of topics to search (e.g., ["SPY", "market", "economy"])
            max_results: Maximum number of news items to fetch

        Returns:
            Dict with 'summary', 'key_themes', 'headlines', 'fetched_at'
            or None if error/unavailable
        """
        if topics is None:
            topics = ["stock market today", "SPY", "market news"]

        cache_key = f"market_{','.join(topics)}"

        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            age = datetime.now() - cached['fetched_at']
            if age < self._cache_duration:
                logger.debug(f"Using cached market news (age: {age.seconds}s)")
                return cached

        try:
            all_headlines = []
            for topic in topics[:3]:  # Limit to 3 topics to avoid rate limits
                headlines = self._fetch_headlines(topic, max_results // len(topics))
                if headlines:
                    all_headlines.extend(headlines)

            if not all_headlines:
                logger.warning("No market news found")
                return None

            # Deduplicate by title similarity (simple exact match)
            seen_titles = set()
            unique_headlines = []
            for h in all_headlines:
                title_lower = h.get('title', '').lower()
                if title_lower not in seen_titles:
                    seen_titles.add(title_lower)
                    unique_headlines.append(h)

            # Summarize with Ollama
            summary_data = self._summarize_market_news(unique_headlines)
            if not summary_data:
                logger.warning("Ollama market news summarization failed")
                return None

            result = {
                'summary': summary_data.get('summary', ''),
                'key_themes': summary_data.get('key_themes', []),
                'sentiment': summary_data.get('sentiment', 'neutral'),
                'headlines': unique_headlines[:10],
                'fetched_at': datetime.now(),
                'source': 'brave_search'
            }

            self._cache[cache_key] = result

            logger.info(f"Market news fetched: {len(unique_headlines)} items, {result['sentiment']} sentiment")
            return result

        except Exception as e:
            logger.error(f"Error fetching market news: {e}")
            return None

    def _fetch_headlines(self, query: str, max_results: int = 10) -> List[Dict]:
        """
        Fetch headlines via Brave Search API with rate limiting.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            List of dicts with 'title', 'url', 'description', 'age'
        """
        logger.info(f"Fetching news for query: '{query}' (max {max_results})")

        # Enforce rate limit (1 req/sec)
        self._enforce_rate_limit()
        
        # Track usage
        self._track_request()

        # Get API key from config
        api_key = self._get_brave_api_key()
        if not api_key:
            logger.warning("Brave Search API key not found")
            return []

        # Call Brave Search API directly
        try:
            import requests
            
            url = "https://api.search.brave.com/res/v1/web/search"
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key
            }
            params = {
                "q": query,
                "count": max_results,
                "freshness": "pd"  # Past day
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=10)

            # Log rate limit headers for debugging
            rate_limit_info = {
                'limit': response.headers.get('X-RateLimit-Limit'),
                'remaining': response.headers.get('X-RateLimit-Remaining'),
                'reset': response.headers.get('X-RateLimit-Reset'),
                'policy': response.headers.get('X-RateLimit-Policy')
            }
            logger.debug(f"Rate limit headers: {rate_limit_info}")

            # Handle 429 specifically with rate limit info
            if response.status_code == 429:
                reset_time = response.headers.get('X-RateLimit-Reset', 'unknown')
                remaining = response.headers.get('X-RateLimit-Remaining', 'unknown')
                logger.warning(
                    f"Brave API rate limit exceeded! "
                    f"Remaining: {remaining}, Reset in: {reset_time}s"
                )
                return []

            response.raise_for_status()

            data = response.json()

            # Parse results
            headlines = []
            for result in data.get('web', {}).get('results', [])[:max_results]:
                headlines.append({
                    'title': result.get('title', ''),
                    'url': result.get('url', ''),
                    'description': result.get('description', ''),
                    'age': result.get('age', 'unknown')
                })

            logger.info(f"Fetched {len(headlines)} headlines for '{query}'")
            return headlines

        except Exception as e:
            if '429' not in str(e):  # Don't log 429 twice
                logger.error(f"Brave Search API error: {e}")
            return []
    
    def _get_brave_api_key(self) -> Optional[str]:
        """Get Brave API key from config."""
        try:
            import json
            from pathlib import Path
            
            # Try broker_config.json first
            config_path = Path(__file__).parent.parent.parent / "ai_trader_data" / "broker_config.json"
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    api_key = config.get('brave_search', {}).get('api_key')
                    if api_key:
                        return api_key
            
            # Try .mcp.json as fallback
            mcp_path = Path(__file__).parent.parent.parent.parent / ".mcp.json"
            if mcp_path.exists():
                with open(mcp_path, 'r') as f:
                    config = json.load(f)
                    api_key = config.get('mcpServers', {}).get('brave-search', {}).get('env', {}).get('BRAVE_API_KEY')
                    if api_key and api_key != 'YOUR_BRAVE_API_KEY_HERE':
                        return api_key
                        
        except Exception as e:
            logger.error(f"Error loading Brave API key: {e}")
        
        return None

    def _summarize_with_ollama(self, symbol: str, headlines: List[Dict]) -> Optional[Dict]:
        """
        Use Ollama to summarize stock-specific news.

        Args:
            symbol: Stock ticker
            headlines: List of headline dicts

        Returns:
            Dict with 'summary', 'sentiment', 'key_points'
        """
        if not self.ollama or not self.ollama.is_available():
            logger.warning("Ollama not available for summarization")
            return None

        if not headlines:
            return None

        # Format headlines for Ollama
        headlines_text = "\n".join([
            f"- {h.get('title', '')} ({h.get('age', 'unknown')})"
            for h in headlines[:10]
        ])

        prompt = f"""Analyze these news headlines for {symbol} and provide:

1. A 2-3 sentence summary of the main catalyst/story. NOTE: headlines show age in parentheses.
   Weight RECENT news (today/hours ago) heavily. News older than 1 day is already priced in -
   mention it but note it is stale. If all news is >1 day old, say so explicitly.
2. Sentiment (bullish/bearish/neutral) - based primarily on fresh news, not stale articles
3. 2-3 key actionable points for day trading

Headlines:
{headlines_text}

Respond in JSON format:
{{
  "summary": "your summary here (note if news is stale)",
  "sentiment": "bullish/bearish/neutral",
  "key_points": ["point 1", "point 2", "point 3"]
}}"""

        system = (
            "You are a financial news analyst specializing in day trading. "
            "Focus on price-moving catalysts, volume implications, and intraday opportunities. "
            "Recency matters enormously - old news is priced in. Always note article age in your analysis. "
            "Be concise and actionable."
        )

        result = self.ollama.complete_json(prompt, system=system)
        return result

    def _summarize_market_news(self, headlines: List[Dict]) -> Optional[Dict]:
        """
        Use Ollama to summarize general market news.

        Args:
            headlines: List of headline dicts

        Returns:
            Dict with 'summary', 'key_themes', 'sentiment'
        """
        if not self.ollama or not self.ollama.is_available():
            logger.warning("Ollama not available for summarization")
            return None

        if not headlines:
            return None

        headlines_text = "\n".join([
            f"- {h.get('title', '')}"
            for h in headlines[:15]
        ])

        prompt = f"""Analyze these market news headlines and provide:

1. A 3-4 sentence summary of today's market environment
2. 3-5 key themes (e.g., "Fed rate concerns", "Tech earnings", "Sector rotation")
3. Overall market sentiment (bullish/bearish/neutral/mixed)

Headlines:
{headlines_text}

Respond in JSON format:
{{
  "summary": "your summary here",
  "key_themes": ["theme1", "theme2", "theme3"],
  "sentiment": "bullish/bearish/neutral/mixed"
}}"""

        system = (
            "You are a market analyst. Focus on macro trends, sector rotations, "
            "and how they affect intraday trading opportunities. Be concise."
        )

        result = self.ollama.complete_json(prompt, system=system)
        return result

    def get_news_for_symbols(
        self,
        symbols: List[str],
        max_per_symbol: int = 5,
        use_parallel: bool = True
    ) -> Dict[str, Dict]:
        """
        Batch fetch news for multiple symbols.

        If worker_pool is available, processes symbols in parallel (3x faster).
        Otherwise falls back to sequential processing.

        Args:
            symbols: List of stock tickers
            max_per_symbol: Max news items per symbol
            use_parallel: Use worker pool if available (default True)

        Returns:
            Dict mapping symbol -> news data
        """
        # Use parallel processing if worker pool available
        if use_parallel and self.worker_pool and self.worker_pool.is_available():
            logger.info(f"Fetching news for {len(symbols)} symbols in parallel")
            
            def fetch_symbol_news(symbol: str, worker_id: int) -> tuple:
                """Fetch news for one symbol (called by worker pool)."""
                news = self._get_stock_news_with_worker(symbol, max_per_symbol, worker_id)
                return (symbol, news)
            
            # Process in parallel
            results_list = self.worker_pool.process_parallel(symbols, fetch_symbol_news)
            
            # Convert list of tuples to dict, filtering out None results
            results = {}
            for symbol, news in results_list:
                if news:
                    results[symbol] = news
                else:
                    logger.warning(f"No news available for {symbol}")
            
            logger.info(f"Parallel fetch complete: {len(results)}/{len(symbols)} symbols")
            return results
        
        # Fallback to sequential processing
        logger.info(f"Fetching news for {len(symbols)} symbols sequentially")
        results = {}

        for symbol in symbols:
            news = self.get_stock_news(symbol, max_results=max_per_symbol)
            if news:
                results[symbol] = news
            else:
                logger.warning(f"No news available for {symbol}")

        logger.info(f"Sequential fetch complete: {len(results)}/{len(symbols)} symbols")
        return results
    
    def _get_stock_news_with_worker(
        self,
        symbol: str,
        max_results: int,
        worker_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch news for a stock using a specific worker from the pool.
        
        This is called by the worker pool for parallel processing.
        
        Args:
            symbol: Stock ticker
            max_results: Max news items
            worker_id: Which worker is processing this
            
        Returns:
            News dict or None
        """
        cache_key = f"{symbol}_{max_results}"

        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            age = datetime.now() - cached['fetched_at']
            if age < self._cache_duration:
                logger.debug(f"[Worker {worker_id}] Using cached news for {symbol}")
                return cached

        try:
            # Fetch raw news
            headlines = self._fetch_headlines(symbol, max_results)
            if not headlines:
                logger.warning(f"[Worker {worker_id}] No news found for {symbol}")
                return None

            # Summarize with worker from pool
            summary_data = self._summarize_with_worker(symbol, headlines, worker_id)
            if not summary_data:
                logger.warning(f"[Worker {worker_id}] Summarization failed for {symbol}")
                return None

            # Build result
            result = {
                'symbol': symbol,
                'summary': summary_data.get('summary', ''),
                'sentiment': summary_data.get('sentiment', 'neutral'),
                'key_points': summary_data.get('key_points', []),
                'headlines': headlines[:5],
                'fetched_at': datetime.now(),
                'source': 'brave_search',
                'worker_id': worker_id
            }

            # Cache it
            self._cache[cache_key] = result

            logger.info(f"[Worker {worker_id}] News fetched for {symbol}: {result['sentiment']}")
            return result

        except Exception as e:
            logger.error(f"[Worker {worker_id}] Error fetching news for {symbol}: {e}")
            return None

    def _summarize_with_worker(
        self,
        symbol: str,
        headlines: List[Dict],
        worker_id: int
    ) -> Optional[Dict]:
        """
        Use specific worker to summarize news.
        
        Args:
            symbol: Stock ticker
            headlines: List of headline dicts
            worker_id: Which worker to use
            
        Returns:
            Dict with 'summary', 'sentiment', 'key_points'
        """
        if not headlines:
            return None

        # Format headlines
        headlines_text = "\n".join([
            f"- {h.get('title', '')} ({h.get('age', 'unknown')})"
            for h in headlines[:10]
        ])

        prompt = f"""Analyze these news headlines for {symbol} and provide:

1. A 2-3 sentence summary of the main catalyst/story. NOTE: headlines show age in parentheses.
   Weight RECENT news (today/hours ago) heavily. News older than 1 day is already priced in -
   mention it but note it is stale. If all news is >1 day old, say so explicitly.
2. Sentiment (bullish/bearish/neutral) - based primarily on fresh news, not stale articles
3. 2-3 key actionable points for day trading

Headlines:
{headlines_text}

Respond in JSON format:
{{
  "summary": "your summary here (note if news is stale)",
  "sentiment": "bullish/bearish/neutral",
  "key_points": ["point 1", "point 2", "point 3"]
}}"""

        system = (
            "You are a financial news analyst specializing in day trading. "
            "Focus on price-moving catalysts, volume implications, and intraday opportunities. "
            "Recency matters enormously - old news is priced in. Always note article age in your analysis. "
            "Be concise and actionable."
        )

        # Use worker from pool if available
        if self.worker_pool and self.worker_pool.is_available():
            result_text = self.worker_pool.complete_with_worker(prompt, worker_id, system)
            if result_text:
                try:
                    # Parse JSON
                    result_text = result_text.strip()
                    if result_text.startswith("```"):
                        lines = result_text.split("\n")
                        result_text = "\n".join(lines[1:-1])
                    return json.loads(result_text)
                except json.JSONDecodeError as e:
                    logger.error(f"[Worker {worker_id}] Invalid JSON: {e}")
                    return None
        
        # Fallback to standard ollama provider
        return self._summarize_with_ollama(symbol, headlines)

    def _load_usage_data(self) -> Dict:
        """Load usage tracking data from file."""
        try:
            if self.usage_file.exists():
                with open(self.usage_file, 'r') as f:
                    data = json.load(f)
                # Check if new month - reset if needed
                current_month = datetime.now().strftime("%Y-%m")
                if data.get('current_month') != current_month:
                    logger.info(f"New month detected, resetting usage counter")
                    data['current_month'] = current_month
                    data['requests_this_month'] = 0
                    data['requests_log'] = []
                    self._save_usage_data(data)
                return data
        except Exception as e:
            logger.warning(f"Error loading usage data: {e}")
        
        # Return default if file doesn't exist or error
        return {
            'current_month': datetime.now().strftime("%Y-%m"),
            'requests_this_month': 0,
            'monthly_limit': 2000,
            'warning_threshold': 1900,
            'last_request_time': None,
            'requests_log': []
        }
    
    def _save_usage_data(self, data: Dict):
        """Save usage tracking data to file."""
        try:
            self.usage_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving usage data: {e}")
    
    def _enforce_rate_limit(self):
        """
        Enforce rate limit using global lock.

        Thread-safe rate limiting for parallel workers - all workers share
        the same lock so only 1 Brave API request happens per interval globally.
        Brave free tier consistently 429s at 1 req/sec; use 2s to be safe.
        """
        global _brave_last_request_time
        MIN_INTERVAL = 2.0  # seconds between requests (free tier limit is ~1/sec but 429s observed)

        with _brave_api_lock:
            # Check if we need to wait
            if _brave_last_request_time:
                elapsed = time.time() - _brave_last_request_time
                if elapsed < MIN_INTERVAL:
                    sleep_time = MIN_INTERVAL - elapsed
                    logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
                    time.sleep(sleep_time)

            # Update global timestamp while still holding the lock
            _brave_last_request_time = time.time()
            self._last_request_time = _brave_last_request_time
    
    def _track_request(self):
        """Track a request and check monthly limits."""
        self._usage_data['requests_this_month'] += 1
        self._usage_data['last_request_time'] = datetime.now().isoformat()
        self._usage_data['requests_log'].append({
            'timestamp': datetime.now().isoformat(),
            'count': self._usage_data['requests_this_month']
        })
        
        # Keep only last 100 entries in log
        if len(self._usage_data['requests_log']) > 100:
            self._usage_data['requests_log'] = self._usage_data['requests_log'][-100:]
        
        self._save_usage_data(self._usage_data)
        
        # Check warnings
        count = self._usage_data['requests_this_month']
        limit = self._usage_data.get('monthly_limit', 2000)
        warning_threshold = self._usage_data.get('warning_threshold', 1900)
        
        if count >= limit:
            logger.error(f"⚠️ BRAVE SEARCH LIMIT REACHED: {count}/{limit} requests this month!")
        elif count >= warning_threshold:
            logger.warning(f"⚠️ BRAVE SEARCH WARNING: {count}/{limit} requests ({limit-count} remaining)")
    
    def get_usage_stats(self) -> Dict:
        """Get current usage statistics."""
        return {
            'requests_this_month': self._usage_data['requests_this_month'],
            'monthly_limit': self._usage_data.get('monthly_limit', 2000),
            'remaining': self._usage_data.get('monthly_limit', 2000) - self._usage_data['requests_this_month'],
            'current_month': self._usage_data.get('current_month'),
            'last_request': self._usage_data.get('last_request_time')
        }

    def clear_cache(self):
        """Clear the news cache."""
        self._cache.clear()
        logger.info("News cache cleared")

    def get_cache_stats(self) -> Dict:
        """Get cache statistics."""
        return {
            'cached_items': len(self._cache),
            'cache_duration_minutes': self._cache_duration.total_seconds() / 60,
            'cached_symbols': [
                k.split('_')[0] for k in self._cache.keys()
                if not k.startswith('market_')
            ]
        }


def create_news_fetcher(ollama_provider=None, worker_pool=None) -> Optional[NewsFetcher]:
    """
    Factory function to create NewsFetcher with proper dependencies.

    Args:
        ollama_provider: OllamaProvider instance (required for fallback)
        worker_pool: OllamaWorkerPool for parallel processing (optional)

    Returns:
        NewsFetcher instance or None if Ollama unavailable
    """
    if ollama_provider is None or not ollama_provider.is_available():
        if worker_pool is None or not worker_pool.is_available():
            logger.warning("Cannot create NewsFetcher: No Ollama provider available")
            return None

    return NewsFetcher(ollama_provider=ollama_provider, worker_pool=worker_pool)
