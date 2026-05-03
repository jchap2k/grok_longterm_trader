"""CLI wrapper for long-term market-regime snapshot generation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.market_regime_snapshot_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
