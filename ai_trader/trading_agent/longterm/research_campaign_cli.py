"""CLI for managing long-term research batch campaigns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.research_campaign import (
    VALID_BATCH_STATUSES,
    build_research_campaign_manifest,
    build_suggested_cycle_command,
    mark_research_batch,
    next_research_batch,
    refresh_campaign_counts,
    summarize_research_campaign,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage long-term research batch campaigns.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a campaign manifest from batch files.")
    init.add_argument("--batch-dir", required=True)
    init.add_argument("--manifest-output", required=True)

    next_cmd = subparsers.add_parser("next", help="Show the next pending research batch.")
    next_cmd.add_argument("--manifest", required=True)
    next_cmd.add_argument("--journal-db", default="")
    next_cmd.add_argument("--portfolio-state", default="")

    mark = subparsers.add_parser("mark", help="Mark a research batch status.")
    mark.add_argument("--manifest", required=True)
    mark.add_argument("--batch-id", required=True)
    mark.add_argument("--status", choices=sorted(VALID_BATCH_STATUSES), required=True)
    mark.add_argument("--notes", default="")

    summary = subparsers.add_parser("summary", help="Summarize campaign progress.")
    summary.add_argument("--manifest", required=True)
    summary.add_argument("--json", action="store_true")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    if args.command == "init":
        manifest = build_research_campaign_manifest(args.batch_dir)
        _write_json(args.manifest_output, manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "next":
        manifest = refresh_campaign_counts(_read_json(args.manifest))
        batch = next_research_batch(manifest)
        payload = dict(batch)
        if batch:
            payload["suggested_command"] = build_suggested_cycle_command(
                batch,
                journal_db=args.journal_db,
                portfolio_state=args.portfolio_state,
            )
        else:
            payload = {
                "status": "complete",
                "suggested_command": "",
            }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "mark":
        manifest = mark_research_batch(
            _read_json(args.manifest),
            args.batch_id,
            args.status,
            notes=args.notes,
        )
        _write_json(args.manifest, manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "summary":
        summary = summarize_research_campaign(refresh_campaign_counts(_read_json(args.manifest)))
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(_summary_markdown(summary), end="")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _read_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Campaign manifest must contain a JSON object.")
    return payload


def _write_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _summary_markdown(summary: dict) -> str:
    lines = [
        "# Research Campaign Summary",
        "",
        f"- Campaign: {summary.get('campaign_id') or 'n/a'}",
        f"- Status: {summary.get('status')}",
        f"- Batches: {summary.get('batch_count')}",
        f"- Total ideas: {summary.get('total_ideas')}",
        f"- Completion: {summary.get('completion_pct')}%",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in sorted((summary.get("status_counts") or {}).items()):
        lines.append(f"| {status} | {count} |")
    next_batch = summary.get("next_batch") or {}
    if next_batch:
        lines.extend(["", f"Next batch: `{next_batch.get('batch_id')}`"])
    return "\n".join(lines) + "\n"


__all__ = ["build_parser", "main", "run_cli"]
