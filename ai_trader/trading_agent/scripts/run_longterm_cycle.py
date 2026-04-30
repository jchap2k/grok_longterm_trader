"""CLI wrapper for one long-term research cycle."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.orchestration_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
