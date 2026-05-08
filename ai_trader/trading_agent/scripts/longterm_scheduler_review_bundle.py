#!/usr/bin/env python
"""Build a no-submit scheduler review bundle for dashboard handoff."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.scheduler_review_bundle_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
