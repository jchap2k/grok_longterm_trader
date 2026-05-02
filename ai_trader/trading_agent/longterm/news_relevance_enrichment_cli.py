"""CLI for long-term relevant-news enrichment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.news_relevance_enrichment import (
    CachedNewsProvider,
    FakeNewsProvider,
    PolygonNewsProvider,
    enrich_ideas_with_relevant_news_paced,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich long-term ideas with high-signal relevant news.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--idea-file", default="")
    source.add_argument("--idea-batch", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--snapshot-file", default="", help="Optional symbol-keyed raw news JSON for offline mode.")
    parser.add_argument("--cache-path", default="", help="Optional JSON cache path for live Polygon news.")
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--rate-limit-batch-size", type=int, default=0)
    parser.add_argument("--rate-limit-pause-seconds", type=float, default=66.0)
    parser.add_argument("--api-key-env", default="POLYGON_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_ideas(args.idea_file or args.idea_batch, single=bool(args.idea_file))
    if args.snapshot_file:
        provider = FakeNewsProvider(_load_symbol_articles(args.snapshot_file))
        mode = "snapshot_file"
    else:
        polygon = PolygonNewsProvider(api_key_env=args.api_key_env, timeout_seconds=args.timeout_seconds)
        if args.cache_path:
            provider = CachedNewsProvider(
                fetch=polygon.fetch_news,
                cache_path=args.cache_path,
                today=args.as_of_date or None,
            )
            mode = "polygon_cached"
        else:
            provider = polygon
            mode = "polygon"

    batch_size = int(args.rate_limit_batch_size or 0)
    if batch_size <= 0:
        batch_size = len(ideas) or 1
    pause_seconds = 0.0 if args.snapshot_file else float(args.rate_limit_pause_seconds or 0.0)
    enriched = enrich_ideas_with_relevant_news_paced(
        ideas,
        provider=provider,
        as_of_date=args.as_of_date or None,
        max_items=args.max_items,
        published_after=args.published_after or None,
        limit=args.limit,
        batch_size=batch_size,
        pause_seconds=pause_seconds,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "mode": mode,
        "input_count": len(ideas),
        "enriched_count": len(enriched),
        "output": str(output_path),
        "max_items": args.max_items,
        "rate_limit_batch_size": batch_size,
        "rate_limit_pause_seconds": pause_seconds,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _load_ideas(path: str | Path, *, single: bool) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if single:
        if not isinstance(payload, Mapping):
            raise ValueError("Idea file must contain a JSON object.")
        return [dict(payload)]
    if not isinstance(payload, list):
        raise ValueError("Idea batch file must contain a JSON list.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _load_symbol_articles(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Snapshot file must contain a symbol-keyed object.")
    return {
        str(symbol).upper(): [dict(item) for item in rows if isinstance(item, Mapping)]
        for symbol, rows in payload.items()
        if isinstance(rows, list)
    }


__all__ = ["build_parser", "main", "run_cli"]
