"""CLI wrapper for long-term research campaign manifests."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_campaign_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
