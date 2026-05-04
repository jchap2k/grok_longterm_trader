"""CLI for selecting an evidence-ready long-term research queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from longterm.extended_universe_scan_cli import _write_json, _write_jsonl
from longterm.portfolio_state import PortfolioState
from longterm.research_selection import FORMULA_VERSION, ResearchSelectionResult, select_research_queue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select a deterministic committee research queue from evidence-ready long-term ideas."
    )
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--top-percent", type=float, default=20.0)
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--max-count", type=int, default=50)
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--recent-research-symbols-file", default="")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ideas = _load_list(args.evidence_file)
    current_symbols = _current_symbols(args.portfolio_state)
    recent_symbols = _load_symbols(args.recent_research_symbols_file)
    result = select_research_queue(
        ideas,
        campaign_id=args.campaign_id or Path(args.evidence_file).stem,
        current_symbols=current_symbols,
        recent_research_symbols=recent_symbols,
        top_percent=args.top_percent,
        min_count=args.min_count,
        max_count=args.max_count,
    )
    summary = dict(result.summary)
    summary.update(
        {
            "input": str(Path(args.evidence_file)),
            "output_dir": str(output_dir),
            "selected_output": str(output_dir / "research_queue_selected.json"),
            "selected_jsonl_output": str(output_dir / "research_queue_selected.jsonl"),
            "deferred_output": str(output_dir / "research_queue_deferred.json"),
            "deferred_jsonl_output": str(output_dir / "research_queue_deferred.jsonl"),
            "report_output": str(output_dir / "research_queue_report.md"),
        }
    )
    _write_json(output_dir / "research_queue_selected.json", result.selected)
    _write_jsonl(output_dir / "research_queue_selected.jsonl", result.selected)
    _write_json(output_dir / "research_queue_deferred.json", result.deferred)
    _write_jsonl(output_dir / "research_queue_deferred.jsonl", result.deferred)
    _write_json(output_dir / "research_queue_skipped.json", result.skipped)
    _write_json(output_dir / "research_queue_summary.json", summary)
    (output_dir / "research_queue_report.md").write_text(
        _format_report(result, summary),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _format_report(result: ResearchSelectionResult, summary: Mapping[str, Any]) -> str:
    lines = [
        "# Research Selection Queue",
        "",
        f"- Formula: `{FORMULA_VERSION}`",
        f"- Input count: {summary.get('input_count')}",
        f"- Selected: {summary.get('selected_count')}",
        f"- Deferred: {summary.get('deferred_count')}",
        f"- Skipped protected: {', '.join(summary.get('skipped_protected_symbols') or []) or 'none'}",
        f"- Selected idea batch: `{summary.get('selected_output')}`",
        "",
        "## Selected",
        "",
        "| Rank | Symbol | Score | Context | Reasons |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in result.selected[:50]:
        metadata = row.get("research_selection") or {}
        lines.append(
            "| "
            f"{metadata.get('selected_rank') or ''} | "
            f"{row.get('symbol') or ''} | "
            f"{metadata.get('selection_score') or 0} | "
            f"{metadata.get('portfolio_context') or ''} | "
            f"{'; '.join(metadata.get('selection_reasons') or [])} |"
        )
    lines.extend(["", "## Deferred Summary", ""])
    for row in result.deferred[:25]:
        metadata = row.get("research_selection") or {}
        lines.append(
            f"- `{row.get('symbol')}` score {metadata.get('selection_score')}: "
            f"{'; '.join(metadata.get('defer_reasons') or [])}"
        )
    return "\n".join(lines) + "\n"


def _current_symbols(path: str) -> set[str]:
    if not path:
        return set()
    state = PortfolioState.from_file(path)
    return {holding.symbol for holding in state.holdings}


def _load_symbols(path: str) -> set[str]:
    if not path:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {str(item).upper().strip() for item in payload if str(item).strip()}
    if isinstance(payload, Mapping):
        values: Iterable[Any] = payload.get("symbols") or payload.get("recent_research_symbols") or []
        return {str(item).upper().strip() for item in values if str(item).strip()}
    return set()


def _load_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list: {path}")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


__all__ = ["build_parser", "main", "run_cli"]
