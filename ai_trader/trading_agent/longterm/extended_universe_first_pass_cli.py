"""One-command broad-universe prepare + Python first-pass scan workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_url
from longterm.extended_universe import prepare_extended_universe
from longterm.extended_universe_scan import (
    build_python_first_pass_markdown,
    fetch_yfinance_fundamental_metrics,
    run_python_first_pass_scan,
)
from longterm.extended_universe_scan_cli import _load_optional_symbol_cache, _write_json, _write_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a broad universe, write watchlist ideas, and run the Python first-pass scan."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-file", default="")
    source_group.add_argument("--source-url", default="")
    parser.add_argument("--source", required=True)
    parser.add_argument("--include-symbols", default="")
    parser.add_argument("--watchlist-limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=10)
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument("--snapshot-file", default="", help="Symbol-keyed raw or normalized fundamentals JSON.")
    provider.add_argument("--provider", choices=["yfinance"], default="")
    parser.add_argument("--fundamentals-cache", default="")
    parser.add_argument("--fetch-limit", type=int, default=None)
    parser.add_argument("--top-percent", type=float, default=10.0)
    parser.add_argument("--min-pass-count", type=int, default=1)
    parser.add_argument("--max-pass-count", type=int, default=None)
    parser.add_argument("--min-coverage-percent-for-enrichment", type=float, default=80.0)
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--output-dir", required=True)
    return parser


def run_cli(args: argparse.Namespace, *, fetch_metrics=fetch_yfinance_fundamental_metrics) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = (
        load_candidate_source_url(args.source_url, source=args.source)
        if args.source_url
        else load_candidate_source_file(args.source_file, source=args.source)
    )
    prepared = prepare_extended_universe(
        candidates,
        source=args.source,
        include_symbols=_parse_symbols(args.include_symbols),
        watchlist_limit=args.watchlist_limit,
        batch_size=args.batch_size,
    )
    artifacts = _artifact_paths(output_dir)
    _write_json(artifacts["ideas"], prepared.watchlist_ideas)
    _write_batches(artifacts["batches_dir"], prepared.batches)
    prepare_summary = dict(prepared.summary)
    prepare_summary["ideas_output"] = str(artifacts["ideas"])
    prepare_summary["batches_output_dir"] = str(artifacts["batches_dir"])
    _write_json(artifacts["prepare_summary"], prepare_summary)

    snapshots, fetch_for_scan, cache_stats = _snapshot_and_fetcher(args, prepared.watchlist_ideas, fetch_metrics)
    scan = run_python_first_pass_scan(
        prepared.watchlist_ideas,
        metrics_by_symbol=snapshots,
        fetch_metrics=fetch_for_scan,
        top_percent=args.top_percent,
        min_pass_count=args.min_pass_count,
        max_pass_count=args.max_pass_count,
        as_of_date=args.as_of_date or None,
        min_coverage_percent_for_enrichment=args.min_coverage_percent_for_enrichment,
    )
    if args.provider and args.fundamentals_cache:
        _write_json(args.fundamentals_cache, snapshots or {})
    _write_json(artifacts["passed"], scan.passed_ideas)
    _write_json(artifacts["deferred"], scan.deferred_ideas)
    _write_json(artifacts["scanned"], scan.scanned_ideas)
    scan_summary = dict(scan.summary)
    scan_summary.update(cache_stats)
    scan_summary["input"] = str(artifacts["ideas"])
    scan_summary["fundamentals_mode"] = "snapshot_file" if args.snapshot_file else args.provider
    scan_summary["passed_output"] = str(artifacts["passed"])
    scan_summary["deferred_output"] = str(artifacts["deferred"])
    scan_summary["scanned_output"] = str(artifacts["scanned"])
    scan_summary["markdown_output"] = str(artifacts["markdown_report"])
    _write_json(artifacts["scan_summary"], scan_summary)
    _write_text(
        artifacts["markdown_report"],
        build_python_first_pass_markdown(scan.passed_ideas, scan.deferred_ideas, scan_summary),
    )
    summary = {
        "schema_version": 1,
        "mode": "extended_universe_first_pass",
        "output_dir": str(output_dir),
        "prepare": prepare_summary,
        "scan": scan_summary,
        "artifacts": {key: str(value) for key, value in artifacts.items()},
    }
    _write_json(artifacts["workflow_summary"], summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _artifact_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "ideas": output_dir / "extended_watchlist_ideas.json",
        "batches_dir": output_dir / "batches",
        "prepare_summary": output_dir / "extended_universe_summary.json",
        "passed": output_dir / "python_scan_passed.json",
        "deferred": output_dir / "python_scan_deferred.json",
        "scanned": output_dir / "python_scan_scanned.json",
        "scan_summary": output_dir / "python_scan_summary.json",
        "markdown_report": output_dir / "python_scan_report.md",
        "workflow_summary": output_dir / "extended_universe_first_pass_summary.json",
    }


def _snapshot_and_fetcher(args: argparse.Namespace, ideas: list[Mapping[str, Any]], fetch_metrics):
    if args.snapshot_file and args.fundamentals_cache:
        raise ValueError("--fundamentals-cache is only supported with --provider.")
    if args.snapshot_file:
        return _load_symbol_keyed_json(args.snapshot_file), None, _cache_stats()
    if not args.fundamentals_cache:
        return None, fetch_metrics, _cache_stats()
    snapshots = _load_optional_symbol_cache(args.fundamentals_cache)
    requested_symbols = _requested_symbols(ideas)
    cache_hits = sum(1 for symbol in requested_symbols if symbol in snapshots)
    allowed_fetch_symbols = _allowed_fetch_symbols(requested_symbols, snapshots, args.fetch_limit)
    fetch_skipped = [
        symbol for symbol in requested_symbols if symbol not in snapshots and symbol not in allowed_fetch_symbols
    ]
    stats = _cache_stats(
        cache_path=args.fundamentals_cache,
        hits=cache_hits,
        fetch_limit=args.fetch_limit,
        skipped=fetch_skipped,
    )

    def cached_fetch(symbol: str) -> Mapping[str, Any]:
        normalized = symbol.upper()
        if normalized not in snapshots:
            if normalized not in allowed_fetch_symbols:
                return {}
            try:
                fetched = dict(fetch_metrics(normalized))
            except Exception as exc:  # pragma: no cover - provider failures vary
                stats["fundamentals_fetch_errors"].append({"symbol": normalized, "error": str(exc)})
                stats["fundamentals_fetch_error_count"] = len(stats["fundamentals_fetch_errors"])
                return {}
            if fetched:
                snapshots[normalized] = fetched
                stats["fundamentals_cache_fetches"] += 1
        return snapshots[normalized]

    return snapshots, cached_fetch, stats


def _cache_stats(
    *,
    cache_path: str = "",
    hits: int = 0,
    fetch_limit: int | None = None,
    skipped: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "fundamentals_cache": cache_path,
        "fundamentals_cache_hits": hits,
        "fundamentals_cache_fetches": 0,
        "fundamentals_fetch_error_count": 0,
        "fundamentals_fetch_errors": [],
        "fundamentals_fetch_limit": fetch_limit,
        "fundamentals_fetch_skipped_count": len(skipped or []),
        "fundamentals_fetch_skipped_symbols": skipped or [],
    }


def _load_symbol_keyed_json(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Snapshot file must contain a symbol-keyed object.")
    return {str(symbol).upper(): dict(value) for symbol, value in payload.items() if isinstance(value, Mapping)}


def _requested_symbols(ideas: list[Mapping[str, Any]]) -> list[str]:
    symbols = []
    for idea in ideas:
        symbol = str(idea.get("symbol") or "").upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _allowed_fetch_symbols(
    requested_symbols: list[str],
    snapshots: Mapping[str, Mapping[str, Any]],
    fetch_limit: int | None,
) -> set[str]:
    missing = [symbol for symbol in requested_symbols if symbol not in snapshots]
    if fetch_limit is None:
        return set(missing)
    return set(missing[: max(0, int(fetch_limit))])


def _parse_symbols(value: str) -> list[str]:
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def _write_batches(output_dir: str | Path, batches: list[dict[str, Any]]) -> None:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        target = target_dir / f"{batch['batch_id']}.json"
        target.write_text(json.dumps(batch["ideas"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = ["build_parser", "main", "run_cli"]
