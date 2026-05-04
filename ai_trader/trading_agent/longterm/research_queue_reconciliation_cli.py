"""CLI for reconciling selected research queues before committee runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.extended_universe_scan_cli import _write_json, _write_jsonl
from longterm.research_campaign import build_research_campaign_manifest
from longterm.research_queue_reconciliation import ResearchQueueReconciliationResult, reconcile_research_queue
from longterm.research_universe_cli import _write_batches
from longterm.research_universe import build_research_universe_batches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Annotate a selected research queue with source convergence and create committee batches."
    )
    parser.add_argument("--research-queue", required=True)
    parser.add_argument(
        "--comparison-source",
        action="append",
        default=[],
        help="Optional label=path JSON list to compare for source convergence, e.g. motley_fool=path.json.",
    )
    parser.add_argument("--recent-symbols-file", default="")
    parser.add_argument("--primary-source-label", default="wide_universe")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    queue = _load_list(args.research_queue)
    comparison_sources = _load_comparison_sources(args.comparison_source)
    recent_symbols = _load_symbols(args.recent_symbols_file)
    result = reconcile_research_queue(
        queue,
        comparison_sources=comparison_sources,
        recent_symbols=recent_symbols,
        primary_source_label=args.primary_source_label,
    )
    summary = dict(result.summary)
    batches_dir = output_dir / "committee_batches"
    batches = build_research_universe_batches(result.rows, batch_size=args.batch_size)
    _write_batches(batches_dir, batches)
    manifest = build_research_campaign_manifest(batches_dir)
    summary.update(
        {
            "input": str(Path(args.research_queue)),
            "output_dir": str(output_dir),
            "reconciled_output": str(output_dir / "research_queue_reconciled.json"),
            "reconciled_jsonl_output": str(output_dir / "research_queue_reconciled.jsonl"),
            "skipped_duplicates_output": str(output_dir / "research_queue_reconciliation_skipped.json"),
            "batches_dir": str(batches_dir),
            "batch_count": len(batches),
            "manifest_output": str(output_dir / "research_campaign_manifest.json"),
            "report_output": str(output_dir / "research_queue_reconciliation_report.md"),
        }
    )
    manifest["source_reconciliation_summary"] = summary
    _write_json(output_dir / "research_queue_reconciled.json", result.rows)
    _write_jsonl(output_dir / "research_queue_reconciled.jsonl", result.rows)
    _write_json(output_dir / "research_queue_reconciliation_skipped.json", result.skipped_duplicates)
    _write_json(output_dir / "research_queue_reconciliation_summary.json", summary)
    _write_json(output_dir / "research_campaign_manifest.json", manifest)
    (output_dir / "research_queue_reconciliation_report.md").write_text(
        _format_report(result, summary),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _format_report(result: ResearchQueueReconciliationResult, summary: Mapping[str, Any]) -> str:
    lines = [
        "# Research Queue Reconciliation",
        "",
        f"- Input count: {summary.get('input_count')}",
        f"- Reconciled count: {summary.get('reconciled_count')}",
        f"- Source convergence count: {summary.get('converged_symbol_count')}",
        f"- Recent research count: {summary.get('recent_research_symbol_count')}",
        f"- Committee batches: {summary.get('batch_count')}",
        f"- Reconciled queue: `{summary.get('reconciled_output')}`",
        f"- Manifest: `{summary.get('manifest_output')}`",
        "",
        "## Converged Symbols",
        "",
    ]
    converged = summary.get("converged_symbols") or []
    if converged:
        for symbol in converged:
            lines.append(f"- `{symbol}`")
    else:
        lines.append("- none")
    lines.extend(["", "## First Batch Preview", ""])
    for row in result.rows[:10]:
        metadata = row.get("source_convergence") or {}
        lines.append(
            f"- `{row.get('symbol')}`: {', '.join(metadata.get('sources') or [])}; "
            f"{metadata.get('suggested_research_mode')}"
        )
    return "\n".join(lines) + "\n"


def _load_comparison_sources(values: list[str]) -> dict[str, list[dict[str, Any]]]:
    sources: dict[str, list[dict[str, Any]]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--comparison-source must use label=path format.")
        label, path = value.split("=", 1)
        sources[label.strip()] = _load_list(path.strip())
    return sources


def _load_symbols(path: str) -> set[str]:
    if not path:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {str(item).upper().strip() for item in payload if str(item).strip()}
    if isinstance(payload, Mapping):
        values = payload.get("symbols") or payload.get("recent_research_symbols") or []
        return {str(item).upper().strip() for item in values if str(item).strip()}
    return set()


def _load_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list: {path}")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


__all__ = ["build_parser", "main", "run_cli"]
