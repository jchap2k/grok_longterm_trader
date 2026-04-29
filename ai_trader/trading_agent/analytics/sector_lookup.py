"""
Sector Lookup: Dynamic sector classification with local caching

Uses yfinance to fetch GICS sectors on-demand, caches results in SQLite database
for fast future lookups. Database grows over time as new stocks are encountered.
"""

import logging
import sqlite3
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class SectorLookup:
    """Dynamic sector lookup with local caching."""
    
    def __init__(self, db_path: str = "ai_trader/ai_trader_data/sector_cache.db"):
        """Initialize sector lookup with cache database."""
        self.db_path = db_path
        self._ensure_database()
    
    def _ensure_database(self):
        """Create sector cache database if it doesn't exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # WAL mode: allows concurrent reads while the scheduler writes sector lookups
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")

        c.execute("""
            CREATE TABLE IF NOT EXISTS sector_cache (
                symbol TEXT PRIMARY KEY,
                sector TEXT,
                industry TEXT,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info(f"Sector cache database ready at {self.db_path}")
    
    def get_sector(self, symbol: str) -> Optional[str]:
        """
        Get sector for a stock symbol.
        
        Returns cached value if available, otherwise fetches from yfinance
        and caches the result.
        
        Args:
            symbol: Stock ticker symbol
            
        Returns:
            Sector name (e.g., 'Technology', 'Consumer Cyclical') or None
        """
        symbol = symbol.upper()
        
        # Try cache first
        cached_sector = self._get_from_cache(symbol)
        if cached_sector is not None:
            logger.debug(f"{symbol}: {cached_sector} (cached)")
            return cached_sector
        
        # Cache miss - fetch from yfinance
        logger.info(f"{symbol}: Not in cache, fetching from yfinance...")
        sector = self._fetch_from_yfinance(symbol)
        
        if sector:
            self._save_to_cache(symbol, sector)
            logger.info(f"{symbol}: {sector} (fetched and cached)")
        else:
            logger.warning(f"{symbol}: Could not determine sector")
        
        return sector
    
    def _get_from_cache(self, symbol: str) -> Optional[str]:
        """Get sector from local cache database."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("SELECT sector FROM sector_cache WHERE symbol = ?", (symbol,))
        row = c.fetchone()
        
        conn.close()
        return row[0] if row else None
    
    def _fetch_from_yfinance(self, symbol: str) -> Optional[str]:
        """Fetch sector from yfinance API."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            sector = ticker.info.get('sector', None)
            industry = ticker.info.get('industry', None)
            
            # Store both sector and industry for reference
            if sector:
                return sector
            
            return None
            
        except Exception as e:
            logger.error(f"Error fetching sector for {symbol}: {e}")
            return None
    
    def _save_to_cache(self, symbol: str, sector: str):
        """Save sector to cache database."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            industry = ticker.info.get('industry', '')
        except:
            industry = ''
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("""
            INSERT OR REPLACE INTO sector_cache (symbol, sector, industry, last_updated)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (symbol, sector, industry))
        
        conn.commit()
        conn.close()
    
    def get_cache_stats(self) -> dict:
        """Get statistics about the sector cache."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM sector_cache")
        total = c.fetchone()[0]
        
        c.execute("SELECT sector, COUNT(*) FROM sector_cache GROUP BY sector ORDER BY COUNT(*) DESC")
        by_sector = c.fetchall()
        
        conn.close()
        
        return {
            'total_stocks': total,
            'by_sector': by_sector
        }
    
    def bulk_populate(self, symbols: list):
        """Pre-populate cache with a list of symbols (useful for first-time setup)."""
        logger.info(f"Bulk populating cache with {len(symbols)} symbols...")
        
        for i, symbol in enumerate(symbols):
            if (i + 1) % 10 == 0:
                logger.info(f"  Progress: {i+1}/{len(symbols)} symbols")
            
            # Only fetch if not already cached
            if self._get_from_cache(symbol) is None:
                sector = self._fetch_from_yfinance(symbol)
                if sector:
                    self._save_to_cache(symbol, sector)
        
        logger.info(f"Bulk population complete!")


if __name__ == "__main__":
    # Test the sector lookup
    logging.basicConfig(level=logging.INFO)
    
    lookup = SectorLookup()
    
    # Test stocks
    test_symbols = ['TSLA', 'AMBA', 'CHWY', 'HOOD', 'MRNA', 'SNAP']
    
    print("Testing Sector Lookup:")
    print("=" * 80)
    
    for symbol in test_symbols:
        sector = lookup.get_sector(symbol)
        print(f"{symbol:6} => {sector or 'UNKNOWN'}")
    
    print("\nCache Statistics:")
    print("=" * 80)
    stats = lookup.get_cache_stats()
    print(f"Total stocks cached: {stats['total_stocks']}")
    print("\nBy sector:")
    for sector, count in stats['by_sector']:
        print(f"  {sector:30} {count:3} stocks")
