"""CLI wrapper for Stage 6B action-plan filtering."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.action_plan_filter_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
