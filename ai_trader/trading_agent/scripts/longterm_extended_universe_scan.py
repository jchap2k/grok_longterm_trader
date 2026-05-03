"""CLI wrapper for the extended-universe Python first-pass scan."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.extended_universe_scan_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
