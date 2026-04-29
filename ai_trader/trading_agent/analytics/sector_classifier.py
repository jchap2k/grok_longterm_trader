"""
Sector Classifier: Categorizes stocks by sector for sector-specific lesson application

This tool helps the trading agent apply sector-specific lessons by identifying
which sector a stock belongs to based on industry keywords and known mappings.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SectorClassifier:
    """Classifies stocks into sectors for sector-specific trading lessons."""

    # Known sector mappings (expandable via learning)
    SECTOR_MAP = {
        # Biotechnology & Pharmaceuticals
        'biotech': [
            'MRNA', 'BNTX', 'REGN', 'VRTX', 'GILD', 'ALNY', 'SRPT', 'RARE', 'ARVN',
            'BMRN', 'BIIB', 'IONS', 'EXEL', 'NBIX', 'INCY', 'FOLD', 'CRSP', 'NTLA',
            'EDIT', 'BEAM', 'BLUE', 'FATE', 'VCEL', 'ARCT', 'RGNX', 'VCYT', 'CDNA',
            'PLAB', 'NVAX', 'SGEN', 'HALO'
        ],

        # High Technology (Semiconductors, AI, Cloud)
        'hightech': [
            'NVDA', 'AMD', 'AVGO', 'QCOM', 'MRVL', 'MU', 'AMAT', 'KLAC', 'ASML',
            'LRCX', 'NXPI', 'MPWR', 'SWKS', 'MCHP', 'TER', 'ENTG', 'WOLF', 'ACLS',
            'ON', 'SLAB', 'CRUS', 'DIOD', 'LITE', 'MKSI', 'NVTS', 'MTSI', 'COHR',
            'SMCI', 'DELL', 'HPE', 'NET', 'DDOG', 'MDB', 'SNOW', 'GTLB', 'PATH',
            'DOMO', 'BILL', 'CFLT', 'FSLY', 'MNTN', 'TDC'
        ],

        # Financials (Banks, Payments, Fintech)
        'financials': [
            'JPM', 'BAC', 'C', 'WFC', 'GS', 'MS', 'BLK', 'SCHW', 'COF', 'PNC',
            'USB', 'TFC', 'AXP', 'DFS', 'SYF', 'HOOD', 'COIN', 'SQ', 'PYPL', 'V',
            'MA', 'ADP', 'FISV', 'FIS', 'SOFI', 'AFRM', 'UPST', 'LC'
        ],

        # Consumer/Retail
        'consumer': [
            'AMZN', 'SHOP', 'ETSY', 'CVNA', 'W', 'CHWY', 'RVLV', 'BBWI', 'LULU',
            'NKE', 'SBUX', 'CMG', 'WING', 'BROS', 'CAVA', 'DASH', 'UBER', 'ABNB',
            'BKNG', 'EXPE', 'TRIP', 'MAT', 'HAS', 'PLAY', 'GOOS', 'CROX'
        ],

        # Social Media/Communication
        'social': [
            'META', 'SNAP', 'PINS', 'TWTR', 'SPOT', 'RBLX', 'U', 'MTCH', 'BMBL'
        ],

        # Energy/Materials
        'energy': [
            'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'HAL', 'MPC', 'PSX', 'VLO', 'OXY'
        ],

        # Automotive/EV
        'automotive': [
            'TSLA', 'GM', 'F', 'RIVN', 'LCID', 'NIO', 'XPEV', 'LI', 'RIDE', 'FSR'
        ],

        # Healthcare Services
        'healthcare': [
            'UNH', 'CVS', 'CI', 'HUM', 'ANTM', 'TDOC', 'DOCS', 'HIMS', 'VEEV', 'PTON'
        ],

        # Real Estate/Construction
        'realestate': [
            'OPEN', 'Z', 'RDFN', 'COMP', 'TPH', 'DHI', 'LEN', 'PHM', 'NVR'
        ]
    }

    # Keyword patterns for unknown stocks
    SECTOR_KEYWORDS = {
        'biotech': ['biotech', 'pharma', 'drug', 'FDA', 'clinical', 'therapy', 'gene'],
        'hightech': ['semiconductor', 'chip', 'AI', 'cloud', 'software', 'SaaS', 'data center'],
        'financials': ['bank', 'financial', 'payment', 'fintech', 'lending', 'credit'],
        'consumer': ['retail', 'ecommerce', 'consumer', 'restaurant', 'apparel', 'footwear'],
        'social': ['social media', 'streaming', 'entertainment', 'gaming'],
        'energy': ['oil', 'gas', 'energy', 'refining', 'exploration'],
        'automotive': ['automotive', 'EV', 'electric vehicle', 'auto'],
        'healthcare': ['healthcare', 'medical', 'hospital', 'telemedicine'],
        'realestate': ['real estate', 'construction', 'homebuilder', 'property']
    }

    @classmethod
    def classify_stock(cls, symbol: str, catalyst: str = None, news: str = None) -> Optional[str]:
        """
        Classify a stock into a sector.

        Args:
            symbol: Stock ticker symbol
            catalyst: Catalyst description (optional)
            news: Recent news text (optional)

        Returns:
            Sector name (e.g., 'biotech', 'hightech') or None if unknown
        """
        symbol = symbol.upper()

        # Check known mappings first (fast path)
        for sector, symbols in cls.SECTOR_MAP.items():
            if symbol in symbols:
                logger.debug(f"Classified {symbol} as {sector} (known mapping)")
                return sector

        # Try keyword matching on catalyst/news
        if catalyst or news:
            text = f"{catalyst or ''} {news or ''}".lower()

            for sector, keywords in cls.SECTOR_KEYWORDS.items():
                if any(keyword in text for keyword in keywords):
                    logger.info(f"Classified {symbol} as {sector} (keyword match in catalyst/news)")
                    # TODO: Add to SECTOR_MAP for future lookups
                    return sector

        logger.warning(f"Could not classify {symbol} - no sector match")
        return None

    @classmethod
    def get_sector_lessons(cls, sector: str, all_lessons: list) -> list:
        """
        Filter lessons to those applicable to a specific sector.

        Args:
            sector: Sector name (e.g., 'biotech')
            all_lessons: List of all available lessons

        Returns:
            List of lessons applicable to this sector (general + sector-specific)
        """
        applicable_lessons = []

        for lesson in all_lessons:
            # Include general lessons (no sector specified)
            if not lesson.get('sector') or lesson.get('sector') == 'general':
                applicable_lessons.append(lesson)

            # Include sector-specific lessons
            elif lesson.get('sector') == sector:
                applicable_lessons.append(lesson)

        logger.debug(f"Found {len(applicable_lessons)} lessons for sector '{sector}'")
        return applicable_lessons

    @classmethod
    def add_stock_to_sector(cls, symbol: str, sector: str):
        """
        Add a stock to a sector mapping (for learning new classifications).

        Args:
            symbol: Stock ticker
            sector: Sector name
        """
        symbol = symbol.upper()

        if sector not in cls.SECTOR_MAP:
            logger.error(f"Unknown sector: {sector}")
            return

        if symbol not in cls.SECTOR_MAP[sector]:
            cls.SECTOR_MAP[sector].append(symbol)
            logger.info(f"Added {symbol} to {sector} sector")


# Example usage for trading agent integration
if __name__ == "__main__":
    # Test classification
    print("Testing Sector Classifier:")
    print("=" * 60)

    test_stocks = [
        ('NVDA', 'Nvidia earnings beat'),
        ('MRNA', 'Moderna FDA approval for new vaccine'),
        ('JPM', 'JPMorgan bank earnings'),
        ('TSLA', 'Tesla electric vehicle delivery numbers'),
        ('SNAP', 'Snapchat social media user growth'),
        ('XYZ', 'Unknown company with no catalyst')
    ]

    for symbol, catalyst in test_stocks:
        sector = SectorClassifier.classify_stock(symbol, catalyst)
        print(f"{symbol:6} + '{catalyst[:30]}...' => {sector or 'UNKNOWN'}")
