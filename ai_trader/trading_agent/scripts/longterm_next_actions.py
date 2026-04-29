"""CLI wrapper for long-term next-actions reports."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.next_actions_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
