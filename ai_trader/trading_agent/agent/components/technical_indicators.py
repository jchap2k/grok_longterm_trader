"""
Technical Indicators - Pure calculation functions

Extracted from ClaudeTradingAgent to reduce complexity.
These are pure functions with no LLM or broker interaction.

Functions:
- calculate_rsi(): Relative Strength Index
- calculate_bollinger_bands(): Bollinger Bands (upper, middle, lower)
- calculate_volume_profile(): Volume Profile (POC, value area)
- calculate_technical_indicators(): Wrapper that calculates all indicators
"""

import logging
import numpy as np
from typing import Dict, List, Tuple, Any

logger = logging.getLogger(__name__)


def calculate_rsi(prices: np.ndarray, period: int = 14) -> float:
    """
    Calculate Relative Strength Index.

    Args:
        prices: Array of closing prices
        period: RSI period (default 14)

    Returns:
        RSI value (0-100)
    """
    if len(prices) < period + 1:
        return 50.0  # Neutral if not enough data

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0  # Maximum RSI if no losses

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_bollinger_bands(
    prices: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0
) -> Tuple[float, float, float]:
    """
    Calculate Bollinger Bands (middle, upper, lower).

    Args:
        prices: Array of closing prices
        period: Moving average period (default 20)
        std_dev: Standard deviation multiplier (default 2.0)

    Returns:
        Tuple of (middle, upper, lower) band values
    """
    if len(prices) < period:
        # Not enough data - return current price for all bands
        current = prices[-1]
        return current, current, current

    # Use last 'period' prices for calculation
    recent_prices = prices[-period:]

    middle = np.mean(recent_prices)
    std = np.std(recent_prices)

    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)

    return middle, upper, lower


def calculate_volume_profile(bars: List[Dict], num_buckets: int = 20) -> Dict[str, Any]:
    """
    Calculate volume profile to find high-volume price levels.

    Args:
        bars: List of OHLCV bars
        num_buckets: Number of price buckets for volume distribution

    Returns:
        dict with POC (point of control), value area, and support/resistance levels
    """
    if len(bars) < 10:
        return {"error": "Not enough bars for volume profile"}

    # Extract prices and volumes
    prices = [bar['close'] for bar in bars]
    volumes = [bar['volume'] for bar in bars]

    min_price = min(prices)
    max_price = max(prices)

    # Create price buckets
    bucket_size = (max_price - min_price) / num_buckets
    if bucket_size == 0:
        bucket_size = max_price * 0.01  # 1% if price range is zero

    volume_by_bucket = [0] * num_buckets

    # Accumulate volume in each price bucket
    for i, bar in enumerate(bars):
        price = bar['close']
        volume = bar['volume']

        bucket_idx = int((price - min_price) / bucket_size) if bucket_size > 0 else 0
        bucket_idx = min(bucket_idx, num_buckets - 1)  # Clamp to last bucket

        volume_by_bucket[bucket_idx] += volume

    # Find Point of Control (POC) - price level with highest volume
    poc_bucket_idx = volume_by_bucket.index(max(volume_by_bucket))
    poc_price = min_price + (poc_bucket_idx * bucket_size) + (bucket_size / 2)

    # Find Value Area (70% of total volume)
    total_volume = sum(volume_by_bucket)
    value_area_volume = total_volume * 0.70

    # Sort buckets by volume (descending)
    sorted_buckets = sorted(enumerate(volume_by_bucket), key=lambda x: x[1], reverse=True)

    accumulated_volume = 0
    value_area_buckets = []

    for bucket_idx, bucket_volume in sorted_buckets:
        accumulated_volume += bucket_volume
        value_area_buckets.append(bucket_idx)
        if accumulated_volume >= value_area_volume:
            break

    # Calculate value area high/low
    value_area_buckets.sort()
    vah_bucket = value_area_buckets[-1] if value_area_buckets else poc_bucket_idx
    val_bucket = value_area_buckets[0] if value_area_buckets else poc_bucket_idx

    vah_price = min_price + (vah_bucket * bucket_size) + bucket_size
    val_price = min_price + (val_bucket * bucket_size)

    return {
        "poc": float(poc_price),  # Point of Control (highest volume price)
        "value_area_high": float(vah_price),  # Top of value area
        "value_area_low": float(val_price),  # Bottom of value area
        "current_vs_poc": "above" if prices[-1] > poc_price else "below",
        "total_volume": int(total_volume)
    }


def calculate_technical_indicators(symbol: str, bars: List[Dict]) -> Dict[str, Any]:
    """
    Calculate technical indicators for mean reversion and volume profile analysis.

    This is a wrapper function that calculates all indicators and returns
    a comprehensive dict with analysis.

    Args:
        symbol: Stock symbol
        bars: List of historical bars (OHLCV data)

    Returns:
        dict with technical indicators including RSI, Bollinger Bands, volume profile
    """
    try:
        if not bars or len(bars) < 20:
            return {"error": "Insufficient data for technical analysis (need 20+ bars)"}

        # Convert to numpy arrays for calculations
        closes = np.array([bar['close'] for bar in bars])
        current_price = closes[-1]

        # Calculate RSI (14-period)
        rsi = calculate_rsi(closes, period=14)

        # Calculate Bollinger Bands (20-period, 2 std dev)
        bb_middle, bb_upper, bb_lower = calculate_bollinger_bands(closes, period=20, std_dev=2.0)

        # Calculate Volume Profile (find high-volume price levels)
        volume_profile = calculate_volume_profile(bars)

        # Determine mean reversion setups
        mean_reversion_signal = None
        mean_reversion_confidence = 0

        if rsi < 30 and current_price < bb_lower:
            mean_reversion_signal = "OVERSOLD_BOUNCE"
            mean_reversion_confidence = 8
        elif rsi > 70 and current_price > bb_upper:
            mean_reversion_signal = "OVERBOUGHT_FADE"
            mean_reversion_confidence = 7
        elif 30 <= rsi <= 40 and current_price <= bb_middle:
            mean_reversion_signal = "OVERSOLD_MILD"
            mean_reversion_confidence = 6
        elif 60 <= rsi <= 70 and current_price >= bb_middle:
            mean_reversion_signal = "OVERBOUGHT_MILD"
            mean_reversion_confidence = 5

        return {
            "symbol": symbol,
            "current_price": float(current_price),
            "rsi": float(rsi),
            "bollinger_bands": {
                "upper": float(bb_upper),
                "middle": float(bb_middle),
                "lower": float(bb_lower),
                "position": "above" if current_price > bb_upper else "below" if current_price < bb_lower else "inside"
            },
            "volume_profile": volume_profile,
            "mean_reversion_signal": mean_reversion_signal,
            "mean_reversion_confidence": mean_reversion_confidence
        }

    except Exception as e:
        logger.error(f"Error calculating technical indicators for {symbol}: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Swing trading indicators (Task 5)
# ---------------------------------------------------------------------------

def calculate_force_index(close_today: float, close_prev: float, volume: float) -> float:
    """Elder's Force Index: volume * price_change."""
    return volume * (close_today - close_prev)


def calculate_ema_series(values: list, period: int) -> list:
    """EMA of a list of values. Returns same-length list (seeds from first value)."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    ema = [float(values[0])]
    for v in values[1:]:
        ema.append(float(v) * k + ema[-1] * (1 - k))
    return ema


def calculate_adx(highs: list, lows: list, closes: list, period: int = 20) -> float | None:
    """Wilder's ADX. Requires at least 2*period+1 bars. Returns float 0-100, or None."""
    if len(closes) < (2 * period + 1):
        return None
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    # True Range
    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    # Directional movement
    up_move   = h[1:] - h[:-1]
    down_move = l[:-1] - l[1:]
    dm_plus  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    def wilder_smooth(arr, n):
        """Wilder smoothing (running sum method)."""
        if len(arr) < n:
            return np.array([])
        smoothed = [arr[:n].sum()]
        for v in arr[n:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / n + v)
        return np.array(smoothed, dtype=float)

    atr_s  = wilder_smooth(tr, period)
    dmp_s  = wilder_smooth(dm_plus, period)
    dmm_s  = wilder_smooth(dm_minus, period)
    if len(atr_s) == 0:
        return None
    safe_atr = np.where(atr_s == 0, 1.0, atr_s)
    di_plus  = 100.0 * dmp_s / safe_atr
    di_minus = 100.0 * dmm_s / safe_atr
    denom    = di_plus + di_minus
    dx = 100.0 * np.abs(di_plus - di_minus) / np.where(denom == 0, 1.0, denom)
    adx_series = wilder_smooth(dx, period)
    if len(adx_series) == 0:
        return None
    # wilder_smooth returns a running sum; ADX is the Wilder-smoothed average.
    adx = float(adx_series[-1]) / period
    return max(0.0, min(100.0, adx))


def three_day_declining_highs(bars: list) -> bool:
    """True if last 3 bars have declining highs (ties allowed): oldest >= mid >= newest, oldest > newest.

    Uses non-strict comparison to avoid false rejection when consecutive bars share a high
    (e.g. two inside days at the same high still represent a pause/pullback in momentum).
    Requires at least some net decline from oldest to newest to filter pure flat consolidation.
    """
    if len(bars) < 3:
        return False
    h = [bars[-3]["high"], bars[-2]["high"], bars[-1]["high"]]
    return (h[0] >= h[1] >= h[2]) and (h[0] > h[2])


def calculate_atr(bars: list, period: int = 14) -> float:
    """Average True Range over last `period` bars."""
    if len(bars) < period + 1:
        return 0.0
    h = np.array([b["high"]  for b in bars], dtype=float)
    l = np.array([b["low"]   for b in bars], dtype=float)
    c = np.array([b["close"] for b in bars], dtype=float)
    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(tr[-period:].mean())


def calculate_swing_indicators(bars: list) -> dict:
    """
    Main wrapper for FORCESWING scan indicators.
    Requires >= 21 bars of daily OHLCV dicts with keys: open, high, low, close, volume.
    Returns dict with: force3, force13, adx20, sma5, sma10, sma20, volume_ratio, three_day_pullback, atr14.
    adx20 is None until the 20-period ADX has enough warmup history (>=41 bars).
    Returns empty dict if insufficient data.
    """
    if len(bars) < 21:
        return {}
    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]
    highs   = [b["high"]   for b in bars]
    lows    = [b["low"]    for b in bars]

    force_raw = [
        calculate_force_index(closes[i], closes[i - 1], volumes[i])
        for i in range(1, len(closes))
    ]
    force3_series  = calculate_ema_series(force_raw, period=3)
    force13_series = calculate_ema_series(force_raw, period=13)

    sma5  = sum(closes[-5:])  / 5
    sma10 = sum(closes[-10:]) / 10
    sma20 = sum(closes[-20:]) / 20

    adv20 = sum(volumes[-20:]) / 20
    volume_ratio = round(volumes[-1] / adv20, 2) if adv20 > 0 else 1.0

    return {
        "force3":             force3_series[-1]  if force3_series  else None,
        "force13":            force13_series[-1] if force13_series else None,
        "adx20":              calculate_adx(highs, lows, closes, period=20),
        "sma5":               sma5,
        "sma10":              sma10,
        "sma20":              sma20,
        "volume_ratio":       volume_ratio,
        "three_day_pullback": three_day_declining_highs(bars[-3:]),
        "atr14":              calculate_atr(bars, period=14),
    }
