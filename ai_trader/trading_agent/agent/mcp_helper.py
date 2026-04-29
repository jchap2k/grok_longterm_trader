"""
MCP Helper - Interface to MCP servers for web access

Provides a simple interface to interact with MCP servers configured in .mcp.json.
Currently supports:
- Brave Search (web search)
- Fetch (direct URL fetching)

This module is designed to be used by news_fetcher.py and other components
that need internet access through MCP servers.

Note: Since we're running inside Cline, we can't directly call MCP servers.
Instead, this module provides the structure and you'll need to use Cline's
MCP tools (use_mcp_tool) when actually implementing.

For standalone usage outside Cline, you'd need to implement actual MCP client.
"""

import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class MCPHelper:
    """
    Helper class to interact with MCP servers.
    
    In practice with Cline, you'll use Cline's built-in MCP tools.
    This class documents the expected interface and data structures.
    """

    def __init__(self):
        """Initialize MCP helper."""
        logger.info("MCPHelper initialized")

    def brave_search(
        self,
        query: str,
        count: int = 10,
        search_lang: str = "en",
        country: str = "us",
        freshness: str = None
    ) -> Optional[List[Dict]]:
        """
        Search the web using Brave Search.

        Args:
            query: Search query
            count: Number of results (max 20 for free tier)
            search_lang: Search language (default: "en")
            country: Country code (default: "us")
            freshness: Time filter - "pd" (past day), "pw" (past week), 
                      "pm" (past month), "py" (past year)

        Returns:
            List of result dicts with 'title', 'url', 'description', 'age'
            or None if error

        MCP Tool Call (when using Cline):
        ```python
        result = use_mcp_tool(
            server_name='brave-search',
            tool_name='brave_web_search',
            arguments={
                'query': query,
                'count': count,
                'search_lang': search_lang,
                'country': country,
                'freshness': freshness  # optional
            }
        )
        ```
        """
        logger.info(f"Brave search: '{query}' (count={count})")

        # PLACEHOLDER: Actual implementation would call MCP
        # For now, document expected response format
        
        # Expected response format from Brave Search MCP:
        # {
        #     "results": [
        #         {
        #             "title": "Article title",
        #             "url": "https://example.com/article",
        #             "description": "Article description...",
        #             "age": "2 hours ago"  # or None
        #         },
        #         ...
        #     ]
        # }

        logger.warning("MCPHelper.brave_search is a placeholder - implement MCP call here")
        return None

    def fetch_url(
        self,
        url: str,
        max_length: int = 50000,
        start_index: int = 0,
        raw: bool = False
    ) -> Optional[str]:
        """
        Fetch content from a URL.

        Args:
            url: URL to fetch
            max_length: Maximum content length
            start_index: Starting position for partial fetch
            raw: If True, return raw content; if False, extract readable text

        Returns:
            Content string or None if error

        MCP Tool Call (when using Cline):
        ```python
        result = use_mcp_tool(
            server_name='fetch',
            tool_name='fetch',
            arguments={
                'url': url,
                'max_length': max_length,
                'start_index': start_index,
                'raw': raw
            }
        )
        ```
        """
        logger.info(f"Fetching URL: {url[:100]}...")

        logger.warning("MCPHelper.fetch_url is a placeholder - implement MCP call here")
        return None

    def search_news_for_symbol(
        self,
        symbol: str,
        max_results: int = 10
    ) -> Optional[List[Dict]]:
        """
        Convenience method to search news for a stock symbol.

        Args:
            symbol: Stock ticker (e.g., "AAPL")
            max_results: Maximum results to return

        Returns:
            List of news articles or None
        """
        query = f"{symbol} stock news today"
        return self.brave_search(
            query=query,
            count=max_results,
            freshness="pd"  # Past day
        )

    def search_market_news(
        self,
        topics: List[str] = None,
        max_results: int = 15
    ) -> Optional[List[Dict]]:
        """
        Convenience method to search general market news.

        Args:
            topics: List of topics/keywords
            max_results: Maximum results to return

        Returns:
            List of news articles or None
        """
        if not topics:
            topics = ["stock market today"]

        all_results = []
        results_per_topic = max_results // len(topics)

        for topic in topics:
            results = self.brave_search(
                query=topic,
                count=results_per_topic,
                freshness="pd"
            )
            if results:
                all_results.extend(results)

        return all_results if all_results else None


# Singleton instance
_mcp_helper = None


def get_mcp_helper() -> MCPHelper:
    """Get or create singleton MCP helper instance."""
    global _mcp_helper
    if _mcp_helper is None:
        _mcp_helper = MCPHelper()
    return _mcp_helper


# Instructions for manual integration when using outside of Cline:
"""
To integrate MCP servers manually (not using Cline):

1. Install MCP SDK:
   npm install -g @modelcontextprotocol/sdk

2. Install MCP servers:
   npm install -g @modelcontextprotocol/server-brave-search
   npm install -g @modelcontextprotocol/server-fetch

3. Get Brave API key:
   - Go to https://brave.com/search/api/
   - Sign up for free tier (2000 searches/month)
   - Copy your API key

4. Update .mcp.json with your API key:
   "brave-search": {
     "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-brave-search"],
     "env": {
       "BRAVE_API_KEY": "your_actual_api_key_here"
     }
   }

5. In your code, use MCP client to call tools:
   
   from mcp import Client
   
   async def search_news(query):
       async with Client() as client:
           await client.connect_stdio(
               "npx", 
               ["-y", "@modelcontextprotocol/server-brave-search"],
               env={"BRAVE_API_KEY": "your_key"}
           )
           
           result = await client.call_tool(
               "brave_web_search",
               arguments={"query": query, "count": 10}
           )
           return result

For more details, see: https://modelcontextprotocol.io/docs
"""