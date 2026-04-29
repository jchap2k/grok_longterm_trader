"""
dynamic_universe_loader.py
===========================
Fetches a broad US swing trading universe from TradingView and caches it
as backtest_data/universe_cache.json.

Used by WeeklyStockRotator._get_base_universe() to replace the 56-stock
hardcoded list with a dynamically fetched pool of ~1000 liquid US equities.

The FORCESWING weekly scorer in WeeklyStockRotator.get_weekly_movers()
then filters this pool down to 30-40 qualifying stocks per week based on
real historical price/volume data.

Source: TradingView scanner API (same as used by grok_day_trader).
Criteria:
  - NASDAQ or NYSE listed, primary ticker only, stock type (no ETFs)
  - Close price: $12 - $500
  - 30-day avg volume: >= 500K shares
  - Market cap: >= $500M (excludes micro-caps)
  - Sorted by market cap descending (larger, more liquid names first)
  - Up to 1000 symbols per fetch

Supplemental list: adds ~20 high-beta names that may fall outside the
top 1000 by current market cap but are FORCESWING targets by nature
(SNAP, lower-cap crypto miners, small EV plays, etc.).

Cache TTL: configurable via broker_config.json swing_backtest.universe_cache_stale_days
(default 30 days - appropriate for a fixed backtest pool).

Fallback: If TradingView fetch fails entirely, returns _FALLBACK_56 (the
original hardcoded list) so backtest can still proceed.
"""

import json
import logging
import datetime
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TradingView sector name -> sector ETF ticker mapping
# TradingView uses its own sector taxonomy different from GICS.
# ---------------------------------------------------------------------------
_TV_SECTOR_TO_ETF: Dict[str, str] = {
    'Technology Services':   'XLK',
    'Electronic Technology': 'XLK',
    'Health Technology':     'XLV',
    'Health Services':       'XLV',
    'Finance':               'XLF',
    'Consumer Services':     'XLY',
    'Consumer Durables':     'XLY',
    'Retail Trade':          'XLY',
    'Consumer Non-Durables': 'XLP',
    'Energy Minerals':       'XLE',
    'Non-Energy Minerals':   'XLB',
    'Process Industries':    'XLB',
    'Producer Manufacturing': 'XLI',
    'Industrial Services':   'XLI',
    'Commercial Services':   'XLI',
    'Transportation':        'XLI',
    'Utilities':             'XLU',
    'Communications':        'XLC',
    'Real Estate':           'XLRE',
    'Distribution Services': 'XLI',
    'Miscellaneous':         'SPY',  # no specific ETF - no sector RS penalty
}

# ---------------------------------------------------------------------------
# Supplemental list: high-beta names that may rank below top-1000 by market
# cap today but are core FORCESWING targets.
# Explicit sector ETF mapping included.
# ---------------------------------------------------------------------------
_SUPPLEMENTAL: List[Tuple[str, str]] = [
    # Lower-cap fintech / crypto
    ('SNAP', 'XLC'), ('DAVE', 'XLF'), ('OPFI', 'XLF'), ('FUTU', 'XLF'),
    ('TIGR', 'XLF'), ('QFIN', 'XLF'), ('LC', 'XLF'), ('NRDS', 'XLF'),
    ('PAYO', 'XLF'), ('LPRO', 'XLF'), ('IIIV', 'XLF'),
    # Small-cap crypto miners
    ('HUT', 'XLK'), ('CIFR', 'XLK'), ('WULF', 'XLK'), ('BTBT', 'XLK'),
    # Small EV / clean energy
    ('EVGO', 'XLE'), ('BLNK', 'XLE'), ('STEM', 'XLE'), ('AMPS', 'XLE'),
    ('NOVA', 'XLE'), ('SHLS', 'XLE'),
    # Small biotech / gene editing
    ('NTLA', 'XLV'), ('BEAM', 'XLV'), ('CLDX', 'XLV'), ('IOVA', 'XLV'),
    ('ACRS', 'XLV'), ('VCEL', 'XLV'), ('SPRY', 'XLV'), ('ARDX', 'XLV'),
    # Small defense / space
    ('LUNR', 'XLI'), ('JOBY', 'XLI'), ('SPIR', 'XLI'),
    # Small SaaS
    ('TASK', 'XLK'), ('AMPL', 'XLK'), ('BRZE', 'XLK'), ('FRSH', 'XLK'),
    ('NCNO', 'XLK'), ('ALRM', 'XLK'), ('GAMB', 'XLC'), ('MAPS', 'XLC'),
    # Consumer
    ('ARHS', 'XLY'), ('FAT', 'XLY'), ('KRUS', 'XLY'), ('XPOF', 'XLY'),
    ('DRVN', 'XLY'), ('GOLF', 'XLY'),
]

# Default cache TTL in days
_DEFAULT_CACHE_STALE_DAYS = 30

# Fallback: original 56-stock list used before dynamic discovery.
# Used only if TradingView fetch fails entirely.
_FALLBACK_56: Dict[str, str] = {
    'NVTS': 'XLK', 'COHR': 'XLK', 'ENTG': 'XLK', 'MKSI': 'XLK',
    'TER':  'XLK', 'AMBA': 'XLK', 'LSCC': 'XLK', 'CGNX': 'XLK',
    'MPWR': 'XLK', 'SLAB': 'XLK', 'DIOD': 'XLK', 'PLAB': 'XLK',
    'ON':   'XLK', 'MRVL': 'XLK',
    'FSLY': 'XLK', 'PATH': 'XLK', 'DOCN': 'XLK', 'DOMO': 'XLK',
    'GTLB': 'XLK', 'ASAN': 'XLK', 'FROG': 'XLK', 'BILL': 'XLK',
    'WEAV': 'XLK', 'TDC':  'XLK', 'NET':  'XLK', 'DDOG': 'XLK',
    'MDB':  'XLK', 'VERX': 'XLK',
    'PLAY': 'XLY', 'CABO': 'XLC', 'WING': 'XLY', 'CAVA': 'XLY',
    'BROS': 'XLY', 'EAT':  'XLY', 'SCVL': 'XLY', 'LULU': 'XLY',
    'CROX': 'XLY', 'CHWY': 'XLY', 'ETSY': 'XLY', 'CVCO': 'XLY',
    'GOOS': 'XLY', 'MAT':  'XLY', 'BBWI': 'XLY',
    'SRPT': 'XLV', 'RGNX': 'XLV', 'ARVN': 'XLV', 'RARE': 'XLV', 'ALNY': 'XLV',
    'SOFI': 'XLF', 'AFRM': 'XLF', 'HOOD': 'XLF', 'DAVE': 'XLF', 'ALKT': 'XLF',
    'LYFT': 'XLY', 'UBER': 'XLY', 'RIVN': 'XLY',
    'SMCI': 'XLK', 'LITE': 'XLK', 'CVNA': 'XLY', 'SHOP': 'XLK', 'ABNB': 'XLY',
    'COIN': 'XLF', 'SNAP': 'XLC', 'DASH': 'XLK', 'RBLX': 'XLC',
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    p = Path(__file__).parent.parent / 'backtest_data' / 'universe_cache.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _get_stale_days() -> int:
    """Read universe_cache_stale_days from broker_config.json, or return default."""
    try:
        config_path = (
            Path(__file__).parent.parent.parent / 'ai_trader_data' / 'broker_config.json'
        )
        cfg = json.loads(config_path.read_text(encoding='utf-8'))
        return int(
            cfg.get('swing_backtest', {}).get(
                'universe_cache_stale_days', _DEFAULT_CACHE_STALE_DAYS
            )
        )
    except Exception:
        return _DEFAULT_CACHE_STALE_DAYS


def _is_stale(cache_file: Path) -> bool:
    if not cache_file.exists():
        return True
    mtime = datetime.datetime.fromtimestamp(cache_file.stat().st_mtime)
    age = (datetime.datetime.now() - mtime).days
    return age >= _get_stale_days()


# ---------------------------------------------------------------------------
# TradingView fetch
# ---------------------------------------------------------------------------

def _fetch_tv_universe(max_symbols: int = 1000) -> List[Tuple[str, str]]:
    """
    Fetch liquid US equities from TradingView scanner.

    Criteria:
      - NASDAQ / NYSE, primary listing, stock type
      - Close $12-$500
      - 30-day avg volume >= 500K
      - Market cap >= $500M
    Sorted by market cap descending so the most liquid names appear first.

    Returns:
        List of (symbol, sector_etf) tuples.
        Empty list on any network error.
    """
    results: List[Tuple[str, str]] = []
    batch = 500
    url = 'https://scanner.tradingview.com/america/scan'

    for start in range(0, max_symbols, batch):
        payload = json.dumps({
            'filter': [
                {'left': 'exchange', 'operation': 'in_range',
                 'right': ['NASDAQ', 'NYSE']},
                {'left': 'is_primary', 'operation': 'equal', 'right': True},
                {'left': 'type', 'operation': 'equal', 'right': 'stock'},
                {'left': 'close', 'operation': 'in_range', 'right': [12, 500]},
                {'left': 'average_volume_30d_calc', 'operation': 'greater',
                 'right': 500000},
                {'left': 'market_cap_basic', 'operation': 'greater',
                 'right': 500000000},
            ],
            'options': {'lang': 'en'},
            'markets': ['america'],
            'symbols': {'query': {'types': []}, 'tickers': []},
            'columns': ['name', 'close', 'market_cap_basic',
                        'average_volume_30d_calc', 'sector'],
            'sort': {'sortBy': 'market_cap_basic', 'sortOrder': 'desc'},
            'range': [start, start + batch],
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0',
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=20)
            data = json.loads(resp.read().decode())
            batch_data = data.get('data', [])
            total_available = data.get('totalCount', 0)

            for row in batch_data:
                sym = row['d'][0]
                tv_sector = row['d'][4] or ''
                sector_etf = _TV_SECTOR_TO_ETF.get(tv_sector, 'SPY')
                results.append((sym, sector_etf))

            logger.debug(
                f"[DynamicUniverse] Fetched batch {start}-{start + batch}: "
                f"{len(batch_data)} results (total available: {total_available})"
            )

            if len(batch_data) < batch:
                break  # No more results

        except Exception as e:
            logger.warning(
                f"[DynamicUniverse] TradingView fetch failed at offset {start}: {e}"
            )
            break  # Return whatever we have

    logger.info(f"[DynamicUniverse] TradingView fetch complete: {len(results)} symbols")
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_universe(force_refresh: bool = False) -> Tuple[List[str], Dict[str, str]]:
    """
    Load the full swing screening universe.

    Returns cached result if not stale; otherwise fetches from TradingView
    and rebuilds the cache.

    Args:
        force_refresh: If True, bypass cache and fetch fresh.

    Returns:
        (symbols, sector_etf_map) where:
          symbols        - deduplicated list of all symbols
          sector_etf_map - {symbol: sector_etf} for RS scoring in WeeklyStockRotator
    """
    cache_file = _cache_path()

    # Try cache first
    if not force_refresh and not _is_stale(cache_file):
        try:
            data = json.loads(cache_file.read_text(encoding='utf-8'))
            symbols = data.get('symbols', [])
            sector_map = data.get('sector_etf_map', {})
            if symbols:
                logger.info(
                    f"[DynamicUniverse] Loaded {len(symbols)} symbols from cache "
                    f"({cache_file.name})"
                )
                return symbols, sector_map
        except Exception as e:
            logger.warning(f"[DynamicUniverse] Cache read failed: {e} -- fetching fresh")

    # Fetch from TradingView
    logger.info("[DynamicUniverse] Fetching fresh universe from TradingView...")
    tv_results = _fetch_tv_universe(max_symbols=1000)

    sector_map: Dict[str, str] = {}

    # Add TradingView results
    for sym, etf in tv_results:
        sector_map[sym] = etf

    # Add supplemental names (override TradingView sector if conflict)
    for sym, etf in _SUPPLEMENTAL:
        sector_map[sym] = etf

    symbols = sorted(sector_map.keys())

    if not symbols:
        # Complete failure: fall back to original 56-stock list
        logger.error(
            "[DynamicUniverse] TradingView fetch returned nothing - "
            "falling back to 56-stock base universe"
        )
        sector_map = dict(_FALLBACK_56)
        symbols = sorted(sector_map.keys())

    # Write cache
    try:
        cache_file.write_text(
            json.dumps(
                {
                    'generated': datetime.datetime.now().isoformat(),
                    'count': len(symbols),
                    'source': 'tradingview',
                    'symbols': symbols,
                    'sector_etf_map': sector_map,
                },
                indent=2,
            ),
            encoding='utf-8',
        )
        logger.info(
            f"[DynamicUniverse] Cache written: {len(symbols)} symbols -> {cache_file}"
        )
    except Exception as e:
        logger.warning(f"[DynamicUniverse] Cache write failed: {e}")

    return symbols, sector_map


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    force = '--refresh' in sys.argv
    print(f"Loading universe (force_refresh={force})...")
    symbols, sector_map = load_universe(force_refresh=force)

    print(f"\nTotal symbols: {len(symbols)}")
    print(f"Sample (first 10): {symbols[:10]}")
    print(f"\nSector map size: {len(sector_map)}")

    # Key name check
    key_names = [
        'HOOD', 'CVNA', 'COIN', 'RBLX', 'AFRM', 'RIVN', 'SNAP',
        'DKNG', 'DDOG', 'NET', 'MSTR', 'CRWD',
    ]
    print("\nKey FORCESWING names:")
    for name in key_names:
        etf = sector_map.get(name, 'MISSING')
        print(f"  {name}: {etf}")

    # Verify no unmapped sector_etf = None
    bad = [sym for sym, etf in sector_map.items() if not etf]
    if bad:
        print(f"\nWARN: {len(bad)} symbols with empty sector ETF: {bad[:5]}")
    else:
        print("\nAll symbols have sector ETF mapping: OK")

    print(
        "\nPASS" if len(symbols) >= 56 else "\nFAIL: fewer symbols than fallback"
    )
