"""Script wrapper for guarded no-submit scheduler task registration."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.scheduler_task_register_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
