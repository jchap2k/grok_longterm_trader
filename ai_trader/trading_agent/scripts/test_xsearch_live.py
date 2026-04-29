"""
Live smoke test for XSearchDiscovery.

Usage:
    cd ai_trader/trading_agent
    python scripts/test_xsearch_live.py

Requirements:
    - XAI_API_KEY set in environment (or config/xai_api_key.txt)
    - xai_sdk installed: pip install xai-sdk

What this tests:
    1. XSearchDiscovery instantiates with default key users
    2. _batch_users() splits 17 users into 1 batch of 17 (all fit within limit of 20)
    3. Symbol parser works offline (no API call needed)
    4. _build_candidate() produces correct dict structure
    5. _call_xsearch_api() can make a real API call (requires key)

If xai_sdk is not available or key is missing, the test will report the error
without crashing.

Architecture note:
    Uses Architecture C: Agent Tools API.
    client.chat.create(tools=[x_search()]) then chat.append(user_msg(prompt))
    and chat.stream() to iterate response chunks.
    Handle filtering and lookback embedded in prompt text.
    Response text is accumulated from chunk.content in the stream.
"""

import os
import sys
from pathlib import Path

# Add trading_agent root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from analytics.xsearch_discovery import XSearchDiscovery, SWING_KEY_USERS


def load_api_key():
    """Load XAI API key from env or config file."""
    key = os.getenv("XAI_API_KEY", "")
    if key:
        return key

    key_file = Path(__file__).parent.parent / "config" / "xai_api_key.txt"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key

    return ""


def run_smoke_test():
    print("=" * 60)
    print("XSearchDiscovery Live Smoke Test")
    print("=" * 60)

    api_key = load_api_key()
    if not api_key:
        print("WARNING: No XAI_API_KEY found in env or config/xai_api_key.txt")
        print("         API calls will fail. Running structure tests only.\n")

    # -- Test 1: Instantiation --
    print("Test 1: Instantiation with defaults")
    d = XSearchDiscovery(key_users=SWING_KEY_USERS, api_key=api_key)
    print(f"  key_users count: {len(d.key_users)}")
    print(f"  min_faves: {d.min_faves}")
    print(f"  lookback_days: {d.lookback_days}")
    print(f"  max_handles_per_call: {d.max_handles_per_call}")
    print("  PASS\n")

    # -- Test 2: Batching --
    print("Test 2: Batch calculation")
    batches = d._batch_users()
    print(f"  {len(d.key_users)} users -> {len(batches)} batch(es)")
    for i, b in enumerate(batches):
        print(f"    Batch {i + 1}: {len(b)} users")
    assert len(batches) == 1, f"Expected 1 batch for {len(d.key_users)} users with limit=20"
    print("  PASS\n")

    # -- Test 3: Symbol parsing (no API needed) --
    print("Test 3: Symbol parser offline")
    test_text = '$AAPL breakout. $NVDA setup. $BTC crypto. $CEO noise.'
    symbols = d._parse_symbols(test_text)
    filtered = d._filter_noise(symbols)
    print(f"  Raw parsed: {symbols}")
    print(f"  After noise filter: {filtered}")
    assert "AAPL" in filtered
    assert "NVDA" in filtered
    assert "BTC" not in filtered
    assert "CEO" not in filtered
    print("  PASS\n")

    # -- Test 4: _build_candidate structure --
    print("Test 4: Candidate dict structure")
    c = d._build_candidate("AAPL", source_user="markminervini", snippet="breakout setup")
    required = ["symbol", "source", "source_user", "post_snippet",
                "price", "change_pct", "volume", "rel_volume",
                "avg_volume", "atr", "perf_3m", "sector", "trade_id"]
    missing = [f for f in required if f not in c]
    if missing:
        print(f"  FAIL - missing fields: {missing}")
    else:
        print(f"  All {len(required)} required fields present")
        print(f"  trade_id is None: {c['trade_id'] is None}")
        print(f"  source: {c['source']}")
        print("  PASS\n")

    # -- Test 5: Live API call --
    print("Test 5: Live API call (fetch_candidates)")
    if not api_key:
        print("  SKIP - no API key\n")
    else:
        try:
            # Use a small subset for the smoke test (3 users, 1 batch)
            test_d = XSearchDiscovery(
                key_users=["markminervini", "Qullamaggie", "alphatrends"],
                api_key=api_key,
                lookback_days=3,
            )
            results = test_d.fetch_candidates()
            print(f"  fetch_candidates() returned {len(results)} candidates")
            if results:
                print("  Sample candidates:")
                for c in results[:5]:
                    print(f"    {c['symbol']:6s} | source_user: {c['source_user'][:30]}")
                    print(f"           | snippet: {c['post_snippet'][:60]}...")
            else:
                print("  No candidates returned (may be normal - no recent mentions)")
            print("  PASS (no exception)\n")
        except ImportError as e:
            print(f"  SKIP - xai_sdk not installed: {e}\n")
            print("  ACTION NEEDED: pip install xai-sdk\n")
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            print("  Check XAI_API_KEY validity and xai_sdk version\n")

    print("=" * 60)
    print("Smoke test complete.")
    print("=" * 60)


if __name__ == "__main__":
    run_smoke_test()
