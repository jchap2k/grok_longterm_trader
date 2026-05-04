"""Batch/resume wrapper for long-term evidence enrichment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.evidence_enrichment_pipeline import (
    fetch_yfinance_fundamentals_for_pipeline,
    run_evidence_enrichment_pipeline,
)
from longterm.evidence_enrichment_pipeline_cli import (
    _fundamentals_snapshot,
    _grok_client,
    _load_ideas,
    _load_symbol_keyed_json,
    _news_provider,
)
from longterm.grok_research_enrichment import DEFAULT_GROK_MODEL, DEFAULT_XAI_BASE_URL
from longterm.extended_universe_scan_cli import _write_json, _write_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run evidence enrichment as resumable batch artifacts plus combined JSON/JSONL outputs."
    )
    parser.add_argument("--idea-batch", required=True)

    fundamentals = parser.add_mutually_exclusive_group()
    fundamentals.add_argument("--fundamentals-snapshot-file", default="")
    fundamentals.add_argument("--fundamentals-provider", choices=["yfinance"], default="")

    news = parser.add_mutually_exclusive_group()
    news.add_argument("--news-snapshot-file", default="")
    news.add_argument("--polygon-news", action="store_true")

    grok = parser.add_mutually_exclusive_group()
    grok.add_argument("--grok-snapshot-file", default="")
    grok.add_argument("--xai-grok", action="store_true")
    grok.add_argument("--skip-grok", action="store_true")

    parser.add_argument("--facts-file", default="", help="Optional symbol-keyed free facts JSON for Grok.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--max-news-items", type=int, default=5)
    parser.add_argument("--rate-limit-batch-size", type=int, default=5)
    parser.add_argument("--rate-limit-pause-seconds", type=float, default=66.0)
    parser.add_argument("--news-cache-path", default="")
    parser.add_argument("--polygon-api-key-env", default="POLYGON_API_KEY")
    parser.add_argument("--xai-api-key-env", default="XAI_API_KEY")
    parser.add_argument("--grok-model", default=DEFAULT_GROK_MODEL)
    parser.add_argument("--grok-base-url", default=DEFAULT_XAI_BASE_URL)
    parser.add_argument("--grok-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--allow-unsourced-grok", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_ideas(args.idea_batch, single=False)
    output_dir = Path(args.output_dir)
    batches_dir = output_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    batch_size = max(1, int(args.batch_size))
    batches = _chunked(ideas, batch_size)
    selected_batches = batches[: args.max_batches] if args.max_batches is not None else batches

    fundamentals_by_symbol = _fundamentals_snapshot(args)
    fetch_fundamentals = (
        fetch_yfinance_fundamentals_for_pipeline if args.fundamentals_provider == "yfinance" else None
    )
    news_provider = _news_provider(args)
    grok_client = _grok_client(args)
    free_facts_by_symbol = _load_symbol_keyed_json(args.facts_file) if args.facts_file else None

    enriched: list[dict[str, Any]] = []
    batch_summaries: list[dict[str, Any]] = []
    skipped_batches = 0

    for index, batch in enumerate(selected_batches, start=1):
        paths = _batch_paths(batches_dir, index)
        _write_json(paths["input"], batch)
        if args.resume and paths["output"].exists() and paths["summary"].exists():
            batch_ideas = _load_list(paths["output"])
            batch_summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
            skipped_batches += 1
        else:
            result = run_evidence_enrichment_pipeline(
                batch,
                fundamentals_by_symbol=fundamentals_by_symbol,
                fetch_fundamentals=fetch_fundamentals,
                news_provider=news_provider,
                grok_client=grok_client,
                free_facts_by_symbol=free_facts_by_symbol,
                as_of_date=args.as_of_date or None,
                max_news_items=args.max_news_items,
                published_after=args.published_after or None,
                news_batch_size=args.rate_limit_batch_size,
                news_pause_seconds=(0.0 if args.news_snapshot_file else args.rate_limit_pause_seconds),
                allow_unsourced_grok=bool(args.allow_unsourced_grok),
            )
            batch_ideas = [dict(item) for item in result["ideas"]]
            batch_summary = dict(result["summary"])
            _write_json(paths["output"], batch_ideas)
            _write_json(paths["summary"], batch_summary)
        batch_summary.update(
            {
                "batch_index": index,
                "batch_input": str(paths["input"]),
                "batch_output": str(paths["output"]),
                "batch_summary": str(paths["summary"]),
            }
        )
        enriched.extend(batch_ideas)
        batch_summaries.append(batch_summary)
        _write_combined_outputs(output_dir, enriched)

    summary = {
        "schema_version": 1,
        "mode": "evidence_enrichment_campaign",
        "input": str(Path(args.idea_batch)),
        "input_count": len(ideas),
        "batch_size": batch_size,
        "total_batch_count": len(batches),
        "selected_batch_count": len(selected_batches),
        "completed_batch_count": len(batch_summaries),
        "skipped_batch_count": skipped_batches,
        "enriched_count": len(enriched),
        "output_dir": str(output_dir),
        "batches_dir": str(batches_dir),
        "combined_output": str(output_dir / "campaign_enriched.json"),
        "combined_jsonl_output": str(output_dir / "campaign_enriched.jsonl"),
        "batch_summaries": batch_summaries,
    }
    _write_json(output_dir / "campaign_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _chunked(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]


def _batch_paths(batches_dir: Path, index: int) -> dict[str, Path]:
    prefix = f"batch_{index:04d}"
    return {
        "input": batches_dir / f"{prefix}_input.json",
        "output": batches_dir / f"{prefix}_output.json",
        "summary": batches_dir / f"{prefix}_summary.json",
    }


def _write_combined_outputs(output_dir: Path, rows: list[Mapping[str, Any]]) -> None:
    _write_json(output_dir / "campaign_enriched.json", list(rows))
    _write_jsonl(output_dir / "campaign_enriched.jsonl", list(rows))


def _load_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Batch output must contain a JSON list.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


__all__ = ["build_parser", "main", "run_cli"]
