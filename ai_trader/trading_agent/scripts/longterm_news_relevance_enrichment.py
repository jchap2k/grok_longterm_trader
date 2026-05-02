"""CLI wrapper for long-term relevant-news enrichment."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.news_relevance_enrichment_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
