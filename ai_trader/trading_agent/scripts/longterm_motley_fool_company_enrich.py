"""CLI wrapper for Motley Fool company-page enrichment."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_company_enrichment_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
