"""CLI wrapper for long-term capital-needed alerts."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.capital_alert_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
