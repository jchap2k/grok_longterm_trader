"""CLI wrapper for optional Kronos advisory batch runs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.kronos_advisory_batch_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
