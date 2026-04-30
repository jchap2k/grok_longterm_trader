"""CLI wrapper for interactive Motley Fool setup."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_setup_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
