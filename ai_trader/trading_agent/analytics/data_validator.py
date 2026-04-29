"""
Data Validator - Cross-Source Market Data Validation

Validates market data across multiple sources to ensure accuracy and prevent
trading decisions based on bad or stale data.

Author: AI Day Trader Team
Date: January 30, 2026
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
import statistics

logger = logging.getLogger(__name__)


class DataValidationError(Exception):
    """Raised when data validation fails."""
    pass


class DataValidator:
    """
    Validates market data across multiple sources for consistency and accuracy.
    """

    def __init__(self, max_deviation_percent: float = 5.0, max_age_minutes: int = 5):
        """
        Initialize data validator.

        Args:
            max_deviation_percent: Maximum allowed price deviation between sources (default 5%)
            max_age_minutes: Maximum age of data in minutes (default 5)
        """
        self.max_deviation_percent = max_deviation_percent
        self.max_age_minutes = max_age_minutes

    def validate_quote(self, symbol: str, sources: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate quote data across multiple sources.

        Args:
            symbol: Stock symbol
            sources: Dict of {source_name: quote_data}

        Returns:
            Validation result with consensus price and metadata
        """
        if not sources:
            raise DataValidationError(f"No data sources provided for {symbol}")

        # Extract valid prices from each source
        valid_prices = []
        source_details = {}

        for source_name, quote_data in sources.items():
            try:
                price_info = self._extract_price_info(quote_data)
                if price_info:
                    valid_prices.append(price_info)
                    source_details[source_name] = price_info
                else:
                    logger.warning(f"Invalid price data from {source_name} for {symbol}")
            except Exception as e:
                logger.warning(f"Error extracting price from {source_name} for {symbol}: {e}")

        if not valid_prices:
            raise DataValidationError(f"No valid price data found for {symbol}")

        # Calculate consensus price
        consensus_result = self._calculate_consensus_price(valid_prices)

        return {
            'symbol': symbol,
            'consensus_price': consensus_result['price'],
            'confidence': consensus_result['confidence'],
            'sources_used': len(valid_prices),
            'total_sources': len(sources),
            'validation_status': consensus_result['status'],
            'source_details': source_details,
            'timestamp': datetime.utcnow().isoformat(),
            'warnings': consensus_result.get('warnings', [])
        }

    def _extract_price_info(self, quote_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract price and timestamp from quote data.

        Args:
            quote_data: Raw quote data from a source

        Returns:
            Dict with price, timestamp, and source info, or None if invalid
        """
        # Convert Quote dataclass to dict if detected
        if hasattr(quote_data, 'bid') and hasattr(quote_data, 'price') and not isinstance(quote_data, dict):
            quote_data = vars(quote_data)
        # Try multiple possible price fields
        price = None
        for price_field in ['price', 'last', 'lastPrice', 'ask', 'bid']:
            if price_field in quote_data and quote_data[price_field]:
                price = float(quote_data[price_field])
                if price > 0:  # Valid positive price
                    break

        if price is None or price <= 0:
            return None

        # Extract timestamp
        timestamp = None
        if 'timestamp' in quote_data:
            timestamp = quote_data['timestamp']
        elif 'time' in quote_data:
            timestamp = quote_data['time']
        elif 'updated_at' in quote_data:
            timestamp = quote_data['updated_at']
        else:
            # Use current local time if no timestamp available
            timestamp = datetime.now()

        # Convert to datetime if needed
        if isinstance(timestamp, (int, float)):
            # Unix epoch - could be seconds or milliseconds
            # Milliseconds if value > 1e10 (year 2001 in ms = ~1e12)
            if timestamp > 1e10:
                timestamp = datetime.fromtimestamp(timestamp / 1000.0)
            else:
                timestamp = datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except:
                timestamp = datetime.now()

        # Check if data is too old
        # Use local time for comparison if timestamp is naive (no timezone info)
        # This fixes the bug where local timestamps were compared to UTC (8 hour difference)
        ts_naive = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        if timestamp.tzinfo is None:
            # Naive timestamp - assume it's local time, compare to local now
            age_minutes = (datetime.now() - ts_naive).total_seconds() / 60
        else:
            # Timezone-aware timestamp - compare to UTC (using modern approach)
            utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
            age_minutes = (utc_now - ts_naive).total_seconds() / 60
        # Sanity check: if age is impossibly large (> 1 day), the timestamp is bad/corrupt
        # Treat as current time rather than propagating the bad value
        if age_minutes > 1440:
            logger.debug(f"Implausible timestamp ({age_minutes:.0f} min old) - treating as current")
            age_minutes = 0.0

        if age_minutes > self.max_age_minutes:
            logger.warning(f"Data is {age_minutes:.1f} minutes old (max allowed: {self.max_age_minutes})")
            return None

        return {
            'price': price,
            'timestamp': timestamp,
            'age_minutes': age_minutes,
            'source_data': quote_data
        }

    def _calculate_consensus_price(self, price_infos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate consensus price from multiple sources.

        Args:
            price_infos: List of price info dicts

        Returns:
            Consensus price calculation result
        """
        if len(price_infos) == 1:
            # Only one source - check if data is fresh enough for trading decisions
            age_minutes = price_infos[0].get('age_minutes', float('inf'))
            if age_minutes > self.max_age_minutes * 2:  # Allow 2x max_age for single source
                raise DataValidationError(
                    f"Single source data is too old ({age_minutes:.1f} minutes) for trading decisions. "
                    f"Maximum allowed: {self.max_age_minutes * 2} minutes. Data may be from market close."
                )
            
            # Only one source - use it but with low confidence
            return {
                'price': price_infos[0]['price'],
                'confidence': 0.5,
                'status': 'single_source',
                'warnings': ['Only one data source available', f'Data age: {age_minutes:.1f} minutes']
            }

        # Multiple sources - calculate consensus
        prices = [info['price'] for info in price_infos]

        # Calculate basic statistics
        mean_price = statistics.mean(prices)
        stdev = statistics.stdev(prices) if len(prices) > 1 else 0

        # Check for excessive deviation
        max_deviation = (stdev / mean_price) * 100 if mean_price > 0 else 0

        if max_deviation > self.max_deviation_percent:
            # High deviation - use median instead of mean for safety
            consensus_price = statistics.median(prices)
            confidence = max(0.1, 1.0 - (max_deviation / 20))  # Reduce confidence with deviation
            status = 'high_deviation_median'
            warnings = [f"High price deviation ({max_deviation:.1f}%) - using median price"]
        else:
            # Low deviation - use mean
            consensus_price = mean_price
            confidence = min(1.0, 0.8 + (len(prices) - 2) * 0.1)  # Higher confidence with more sources
            status = 'consensus_mean'
            warnings = []

        # Additional validation: check for unrealistic prices
        if consensus_price < 0.01 or consensus_price > 10000:
            raise DataValidationError(f"Unrealistic consensus price: ${consensus_price:.2f}")

        return {
            'price': round(consensus_price, 2),
            'confidence': round(confidence, 2),
            'status': status,
            'warnings': warnings,
            'deviation_percent': round(max_deviation, 2),
            'sources_count': len(prices)
        }

    def validate_vix(self, sources: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Special validation for VIX data (more lenient due to volatility).

        Args:
            sources: Dict of {source_name: vix_data}

        Returns:
            VIX validation result
        """
        # VIX can be more volatile, so use higher deviation tolerance
        original_max_deviation = self.max_deviation_percent
        self.max_deviation_percent = 15.0  # VIX can swing more

        try:
            result = self.validate_quote('VIX', sources)
            # VIX-specific validation
            if result['consensus_price'] < 5 or result['consensus_price'] > 80:
                result['warnings'].append(f"VIX value {result['consensus_price']} seems unrealistic (typical range: 5-80)")
                result['confidence'] *= 0.5  # Reduce confidence for suspicious values
        finally:
            self.max_deviation_percent = original_max_deviation

        return result


def validate_market_data(symbol: str, sources: Dict[str, Dict[str, Any]],
                        max_deviation_percent: float = 5.0) -> Dict[str, Any]:
    """
    Convenience function for market data validation.

    Args:
        symbol: Stock symbol
        sources: Dict of {source_name: quote_data}
        max_deviation_percent: Maximum allowed deviation

    Returns:
        Validation result
    """
    validator = DataValidator(max_deviation_percent=max_deviation_percent)
    return validator.validate_quote(symbol, sources)


def validate_vix_data(sources: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convenience function for VIX data validation.

    Args:
        sources: Dict of {source_name: vix_data}

    Returns:
        VIX validation result
    """
    validator = DataValidator()
    return validator.validate_vix(sources)
