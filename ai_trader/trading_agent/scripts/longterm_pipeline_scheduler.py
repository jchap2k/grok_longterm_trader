#!/usr/bin/env python
"""Run the no-submit research-to-paper pipeline scheduler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.pipeline_scheduler_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
