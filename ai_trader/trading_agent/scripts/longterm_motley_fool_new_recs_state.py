"""Script wrapper for the Motley Fool new-recommendation state CLI."""

from __future__ import annotations

import sys
from pathlib import Path

TRADING_AGENT_DIR = Path(__file__).resolve().parents[1]
if str(TRADING_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(TRADING_AGENT_DIR))

from longterm.motley_fool_new_recs_state_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
