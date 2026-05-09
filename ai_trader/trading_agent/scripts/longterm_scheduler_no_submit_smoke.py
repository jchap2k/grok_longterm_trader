"""Script wrapper for no-submit scheduler readiness smoke packaging."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.scheduler_no_submit_smoke_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
