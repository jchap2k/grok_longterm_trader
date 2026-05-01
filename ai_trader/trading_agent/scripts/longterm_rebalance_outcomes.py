"""CLI wrapper for long-term rebalance outcome analysis."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.rebalance_outcome_analysis_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
