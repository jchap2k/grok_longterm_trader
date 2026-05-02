"""CLI for deterministic fundamental metric enrichment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.fundamental_metrics_enrichment import (
    enrich_ideas_with_fundamental_metrics,
    fetch_yfinance_fundamental_metrics,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich ideas with Python-computed fundamental metric tables.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--idea-file", default="")
    source.add_argument("--idea-batch", default="")
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument("--snapshot-file", default="", help="Symbol-keyed raw fundamentals JSON.")
    provider.add_argument("--provider", choices=["yfinance"], default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--limit", type=int, default=None)
    return parser


def run_cli(args: argparse.Namespace, *, fetch_metrics=fetch_yfinance_fundamental_metrics) -> int:
    ideas = _load_ideas(args.idea_file or args.idea_batch, single=bool(args.idea_file))
    if args.snapshot_file:
        snapshots = _load_symbol_keyed_json(args.snapshot_file)
        mode = "snapshot_file"
    else:
        snapshots = {
            str(idea.get("symbol") or "").upper(): fetch_metrics(str(idea.get("symbol") or "").upper())
            for idea in ideas[: args.limit] if str(idea.get("symbol") or "").strip()
        }
        mode = args.provider
    enriched = enrich_ideas_with_fundamental_metrics(
        ideas,
        snapshots,
        as_of_date=args.as_of_date or None,
        limit=args.limit,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "mode": mode,
        "input_count": len(ideas),
        "snapshot_count": len(snapshots),
        "enriched_count": len(enriched),
        "output": str(output_path),
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


def _load_symbol_keyed_json(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Snapshot file must contain a symbol-keyed object.")
    return {str(symbol).upper(): dict(value) for symbol, value in payload.items() if isinstance(value, Mapping)}


__all__ = ["build_parser", "main", "run_cli"]
