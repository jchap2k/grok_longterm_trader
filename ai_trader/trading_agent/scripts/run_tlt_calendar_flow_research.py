"""CLI wrapper for calendar-flow concept research."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.calendar_flow_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
