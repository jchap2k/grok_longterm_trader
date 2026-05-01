"""CLI wrapper for long-term discovery queue generation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.discovery_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
