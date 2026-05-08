#!/usr/bin/env python
"""Build a disabled paper submit-mode readiness plan."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_submit_mode_plan_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
