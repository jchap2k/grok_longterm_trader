#!/usr/bin/env python
"""Script wrapper for advisory Grok committee preset routing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.committee_preset_policy_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
