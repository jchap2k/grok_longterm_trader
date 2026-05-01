"""CLI wrapper for non-submitting paper order previews."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_order_preview_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
