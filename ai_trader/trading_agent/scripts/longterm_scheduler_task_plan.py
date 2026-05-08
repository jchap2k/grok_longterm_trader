#!/usr/bin/env python
"""Generate a reviewable Windows Task Scheduler plan."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.scheduler_task_plan_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
