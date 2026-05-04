#!/usr/bin/env python
"""Run the dry-run long-term research automation campaign."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_automation_campaign_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
