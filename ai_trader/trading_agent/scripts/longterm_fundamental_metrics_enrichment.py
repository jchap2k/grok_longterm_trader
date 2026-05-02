"""CLI wrapper for deterministic fundamental metric enrichment."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.fundamental_metrics_enrichment_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
