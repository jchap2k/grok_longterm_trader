"""CLI helpers for Motley Fool company-page enrichment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.motley_fool_company_enrichment import (
    DEFAULT_PROFILE_DIR,
    CompanyPageSnapshot,
    enrich_ideas_with_company_pages,
    fetch_company_snapshot_with_scrapling,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enrich Motley Fool research ideas from per-company Fool IQ pages."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--idea-file", default="")
    source.add_argument("--idea-batch", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--backend", choices=["scrapling_stealthy", "scrapling_dynamic"], default="scrapling_stealthy")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--headless", dest="headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--request-delay-seconds", type=float, default=0.0)
    parser.add_argument("--snapshot-dir", default="")
    parser.add_argument("--snapshot-output-dir", default="")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_ideas(args.idea_file or args.idea_batch, single=bool(args.idea_file))
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else None
    snapshot_output_dir = Path(args.snapshot_output_dir) if args.snapshot_output_dir else None
    if snapshot_output_dir:
        snapshot_output_dir.mkdir(parents=True, exist_ok=True)

    def fetch_snapshot(idea: Mapping[str, Any]) -> CompanyPageSnapshot:
        symbol = str(idea.get("symbol") or "").upper()
        if snapshot_dir:
            snapshot_path = snapshot_dir / f"{symbol}.json"
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            return CompanyPageSnapshot.from_dict(payload)
        url = str(idea.get("motley_fool_company_url") or idea.get("source_url") or "")
        snapshot = fetch_company_snapshot_with_scrapling(
            url,
            profile_dir=args.profile_dir,
            headless=bool(args.headless),
            backend=args.backend,
        )
        if snapshot_output_dir:
            (snapshot_output_dir / f"{symbol}.json").write_text(
                json.dumps(snapshot.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return snapshot

    enriched, summary = enrich_ideas_with_company_pages(
        ideas,
        fetch_snapshot=fetch_snapshot,
        limit=args.limit,
        request_delay_seconds=0.0 if snapshot_dir else max(0.0, float(args.request_delay_seconds or 0.0)),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        **summary,
        "output": str(output_path),
        "backend": args.backend if not snapshot_dir else "snapshot_dir",
        "request_delay_seconds": 0.0 if snapshot_dir else max(0.0, float(args.request_delay_seconds or 0.0)),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["error_count"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _load_ideas(path: str | Path, *, single: bool) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if single:
        if not isinstance(payload, dict):
            raise ValueError("Idea file must contain a JSON object.")
        return [dict(payload)]
    if not isinstance(payload, list):
        raise ValueError("Idea batch file must contain a JSON list.")
    return [dict(item) for item in payload]


__all__ = ["build_parser", "main", "run_cli"]
