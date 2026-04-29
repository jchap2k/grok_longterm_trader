#!/usr/bin/env python3
"""
Alpaca News API Earnings Catalyst Fetcher

RELIABLE alternative to yfinance earnings_dates (which broke Feb 15, 2026)
Uses Alpaca News API to discover historical earnings announcements.

Advantages:
- Works for historical data (tested back to Sept 2025)
- More reliable than yfinance
- Includes sentiment context (headlines/summaries)
- Free with Alpaca account

Usage:
    from alpaca_earnings_fetcher import AlpacaEarningsFetcher

    fetcher = AlpacaEarningsFetcher(alpaca_broker)
    catalysts = fetcher.fetch_earnings_catalysts(
        symbols=['NVDA', 'PATH', 'NVTS'],
        start_date=datetime(2025, 11, 1),
        end_date=datetime(2025, 11, 30)
    )
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from alpaca.data.requests import NewsRequest

logger = logging.getLogger(__name__)


class AlpacaEarningsFetcher:
    """Fetch historical earnings catalysts using Alpaca News API."""

    def __init__(self, alpaca_broker):
        """
        Initialize with Alpaca broker instance.

        Args:
            alpaca_broker: AlpacaBroker instance (must be connected)
        """
        self.broker = alpaca_broker

        if not hasattr(alpaca_broker, 'news_client') or not alpaca_broker.news_client:
            raise RuntimeError("Alpaca broker must have news_client initialized")

    def fetch_earnings_catalysts(
        self,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime,
        search_window_days: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Fetch earnings catalysts for symbols within date range.

        Strategy:
        1. Split date range into weekly chunks
        2. For each week, search for "earnings" news for all symbols
        3. Extract earnings dates from article timestamps
        4. Deduplicate and format as catalyst list

        Args:
            symbols: List of stock symbols (e.g., ['NVDA', 'PATH'])
            start_date: Start of search range
            end_date: End of search range
            search_window_days: Days before/after to search around each week (default 2)

        Returns:
            List of catalyst dicts with format:
            {
                'symbol': 'NVDA',
                'date': datetime(2025, 11, 20),
                'type': 'earnings',
                'sentiment': 'positive/negative/neutral',
                'headline': 'NVIDIA Reports Record Q3 Earnings',
                'gap_pct': None  # Will be filled by caller
            }
        """
        logger.info(f"Fetching earnings catalysts from Alpaca News API")
        logger.info(f"  Symbols: {len(symbols)}")
        logger.info(f"  Date range: {start_date.date()} to {end_date.date()}")

        all_catalysts = []

        # Process in weekly chunks to avoid overwhelming the API
        current_date = start_date
        week_num = 0

        while current_date < end_date:
            week_end = min(current_date + timedelta(days=7), end_date)
            week_num += 1

            # Add search window buffer
            search_start = current_date - timedelta(days=search_window_days)
            search_end = week_end + timedelta(days=search_window_days)

            logger.info(f"Week {week_num}: Searching {search_start.date()} to {search_end.date()}")

            # Batch symbols (Alpaca supports comma-separated symbols)
            # Process 10 symbols at a time to avoid overly long requests
            batch_size = 10
            for i in range(0, len(symbols), batch_size):
                symbol_batch = symbols[i:i+batch_size]

                try:
                    catalysts = self._fetch_batch(
                        symbols=symbol_batch,
                        start_date=search_start,
                        end_date=search_end
                    )
                    all_catalysts.extend(catalysts)

                except Exception as e:
                    logger.error(f"Error fetching batch {symbol_batch}: {e}")
                    continue

            current_date = week_end

        # Deduplicate by (symbol, date)
        seen = set()
        unique_catalysts = []
        for cat in all_catalysts:
            key = (cat['symbol'], cat['date'].date())
            if key not in seen:
                seen.add(key)
                unique_catalysts.append(cat)

        logger.info(f"Found {len(unique_catalysts)} unique earnings catalysts")

        # Sort by date
        unique_catalysts.sort(key=lambda x: x['date'])

        return unique_catalysts

    def _fetch_batch(
        self,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch earnings news for a batch of symbols.

        Returns list of catalyst dicts.
        """
        if not symbols:
            return []

        try:
            # Build request
            request = NewsRequest(
                start=start_date,
                end=end_date,
                symbols=",".join(symbols),
                limit=50,  # Max per request
                sort="desc"
            )

            news_response = self.broker.news_client.get_news(request)
            articles = news_response.data.get('news', []) if hasattr(news_response, 'data') else []

            # Filter for earnings-related articles
            earnings_articles = [
                a for a in articles
                if a.headline and self._is_earnings_article(a.headline)
            ]

            logger.debug(f"  Batch {symbols[:3]}... found {len(articles)} total, {len(earnings_articles)} earnings")

            # Convert to catalysts
            catalysts = []
            for article in earnings_articles:
                # Extract symbols from article (may cover multiple stocks)
                article_symbols = list(article.symbols) if article.symbols else []

                # Only include symbols we requested
                relevant_symbols = [s for s in article_symbols if s in symbols]

                for symbol in relevant_symbols:
                    # Determine sentiment from headline
                    sentiment = self._extract_sentiment(article.headline)

                    catalyst = {
                        'symbol': symbol,
                        'date': article.created_at,
                        'type': 'earnings',
                        'sentiment': sentiment,
                        'headline': article.headline[:200],  # Truncate long headlines
                        'gap_pct': None  # Will be filled by historical_data_fetcher
                    }
                    catalysts.append(catalyst)

            return catalysts

        except Exception as e:
            logger.error(f"Error fetching news for {symbols}: {e}")
            return []

    def _is_earnings_article(self, headline: str) -> bool:
        """
        Check if headline is earnings-related.

        Keywords:
        - earnings
        - reports Q1/Q2/Q3/Q4
        - beats/misses estimates
        - guidance
        - EPS
        """
        headline_lower = headline.lower()

        earnings_keywords = [
            'earning',
            'reports q1', 'reports q2', 'reports q3', 'reports q4',
            'q1 earnings', 'q2 earnings', 'q3 earnings', 'q4 earnings',
            'beats estimate', 'misses estimate',
            'eps',
            'guidance',
            'quarterly results',
            'fiscal quarter'
        ]

        return any(keyword in headline_lower for keyword in earnings_keywords)

    def _extract_sentiment(self, headline: str) -> str:
        """
        Extract sentiment from earnings headline.

        Returns: 'positive', 'negative', or 'neutral'
        """
        headline_lower = headline.lower()

        positive_words = [
            'beats', 'jumps', 'surges', 'soars', 'rallies', 'record',
            'strong', 'exceeds', 'tops', 'outperforms'
        ]

        negative_words = [
            'misses', 'dives', 'plunges', 'tumbles', 'falls', 'drops',
            'weak', 'disappoints', 'cuts guidance', 'warning', 'slumps'
        ]

        has_positive = any(word in headline_lower for word in positive_words)
        has_negative = any(word in headline_lower for word in negative_words)

        if has_positive and not has_negative:
            return 'positive'
        elif has_negative and not has_positive:
            return 'negative'
        else:
            return 'neutral'
