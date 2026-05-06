#!/usr/bin/env python
"""Script wrapper for the no-submit scheduler cadence policy artifact."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.pipeline_scheduler_policy_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
