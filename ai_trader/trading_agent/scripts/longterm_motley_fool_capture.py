"""CLI wrapper for Motley Fool premium table capture."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_capture_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
