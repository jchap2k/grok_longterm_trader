"""CLI for the pure-Python extended-universe first-pass scan."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from longterm.extended_universe_scan import (
    fetch_yfinance_fundamental_metrics,
    run_python_first_pass_scan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank broad-universe ideas with deterministic fundamentals before expensive enrichment."
    )
    parser.add_argument("--idea-batch", required=True)
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument("--snapshot-file", default="", help="Symbol-keyed raw fundamentals JSON.")
    provider.add_argument("--provider", choices=["yfinance"], default="")
    parser.add_argument("--top-percent", type=float, default=10.0)
    parser.add_argument("--min-pass-count", type=int, default=1)
    parser.add_argument("--max-pass-count", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--passed-output", required=True)
    parser.add_argument("--deferred-output", default="")
    parser.add_argument("--scanned-output", default="")
    parser.add_argument("--summary-output", default="")
    return parser


def run_cli(args: argparse.Namespace, *, fetch_metrics=fetch_yfinance_fundamental_metrics) -> int:
    ideas = _load_list(args.idea_batch, label="Idea batch")
    snapshots = _load_symbol_keyed_json(args.snapshot_file) if args.snapshot_file else None
    result = run_python_first_pass_scan(
        ideas,
        metrics_by_symbol=snapshots,
        fetch_metrics=fetch_metrics if args.provider else None,
        top_percent=args.top_percent,
        min_pass_count=args.min_pass_count,
        max_pass_count=args.max_pass_count,
        limit=args.limit,
        as_of_date=args.as_of_date or None,
    )
    passed_path = _write_json(args.passed_output, result.passed_ideas)
    deferred_path = _write_json(args.deferred_output, result.deferred_ideas) if args.deferred_output else None
    scanned_path = _write_json(args.scanned_output, result.scanned_ideas) if args.scanned_output else None
    summary = dict(result.summary)
    summary["input"] = str(Path(args.idea_batch))
    summary["fundamentals_mode"] = "snapshot_file" if args.snapshot_file else args.provider
    summary["passed_output"] = str(passed_path)
    if deferred_path:
        summary["deferred_output"] = str(deferred_path)
    if scanned_path:
        summary["scanned_output"] = str(scanned_path)
    if args.summary_output:
        summary_path = _write_json(args.summary_output, summary)
        summary["summary_output"] = str(summary_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_list(path: str | Path, *, label: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{label} must contain a JSON list.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _load_symbol_keyed_json(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Snapshot file must contain a symbol-keyed object.")
    return {str(symbol).upper(): dict(value) for symbol, value in payload.items() if isinstance(value, Mapping)}


def _write_json(path: str | Path, payload: Any) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


__all__ = ["build_parser", "main", "run_cli"]
