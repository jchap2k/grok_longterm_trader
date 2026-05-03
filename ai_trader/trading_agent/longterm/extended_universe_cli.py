"""CLI for preparing broad universe watchlist batches for enrichment."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_url
from longterm.extended_universe import prepare_extended_universe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare an extended stock universe for enrichment.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-file", default="")
    source_group.add_argument("--source-url", default="")
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--include-symbols",
        default="",
        help="Optional comma-separated symbols to keep from the source, preserving this order.",
    )
    parser.add_argument("--watchlist-limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--ideas-output", required=True)
    parser.add_argument("--batches-output-dir", default="")
    parser.add_argument("--summary-output", default="")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    candidates = (
        load_candidate_source_url(args.source_url, source=args.source)
        if args.source_url
        else load_candidate_source_file(args.source_file, source=args.source)
    )
    result = prepare_extended_universe(
        candidates,
        source=args.source,
        include_symbols=_parse_symbols(args.include_symbols),
        watchlist_limit=args.watchlist_limit,
        batch_size=args.batch_size,
    )
    ideas_path = Path(args.ideas_output)
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text(
        json.dumps(result.watchlist_ideas, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.batches_output_dir:
        _write_batches(args.batches_output_dir, result.batches)
    summary = dict(result.summary)
    summary["ideas_output"] = str(ideas_path)
    if args.batches_output_dir:
        summary["batches_output_dir"] = str(Path(args.batches_output_dir))
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary["summary_output"] = str(summary_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _write_batches(output_dir: str | Path, batches: list[dict]) -> None:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        target = target_dir / f"{batch['batch_id']}.json"
        target.write_text(json.dumps(batch["ideas"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_symbols(value: str) -> list[str]:
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


__all__ = ["build_parser", "main", "run_cli"]
