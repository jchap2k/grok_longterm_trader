"""CLI wrapper for dry-run paper account reconciliation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_reconciliation_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
