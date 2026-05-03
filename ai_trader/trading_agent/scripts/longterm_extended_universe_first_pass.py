"""CLI wrapper for the one-command extended-universe first-pass workflow."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.extended_universe_first_pass_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
