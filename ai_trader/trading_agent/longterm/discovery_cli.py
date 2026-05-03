"""CLI helpers for building the long-term discovery research queue."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from longterm.discovery import DiscoveryEngine
from longterm.discovery_enrichment import apply_discovery_enrichment, load_discovery_enrichment_file
from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a long-term stock discovery queue from candidate files.")
    parser.add_argument("--candidates", default="", help="JSON file containing a list of raw discovery candidates.")
    parser.add_argument("--source-file", default="", help="Local CSV or NasdaqTrader pipe file to load as candidates.")
    parser.add_argument("--source-url", default="", help="Remote CSV or NasdaqTrader pipe URL to load as candidates.")
    parser.add_argument("--source", default="", help="Source label to attach when loading --source-file or --source-url.")
    parser.add_argument("--enrichment-file", default="", help="Optional local JSON/CSV metrics file keyed by symbol.")
    parser.add_argument("--enrichment-source", default="local_enrichment")
    parser.add_argument("--research-limit", type=int, default=25)
    parser.add_argument(
        "--research-ideas-output",
        default="",
        help="Optional path to write research-ready candidates as idea-batch JSON.",
    )
    parser.add_argument(
        "--watchlist-ideas-output",
        default="",
        help="Optional path to write top watchlist candidates as idea-batch JSON for enrichment.",
    )
    parser.add_argument("--watchlist-limit", type=int, default=100)
    return parser


def run_cli(args: argparse.Namespace, *, engine: DiscoveryEngine | None = None) -> int:
    payload = _load_candidates(args)
    if args.enrichment_file:
        payload = apply_discovery_enrichment(
            payload,
            load_discovery_enrichment_file(args.enrichment_file),
            source=args.enrichment_source,
        )

    result = (engine or DiscoveryEngine()).build_queue(
        payload,
        research_limit=args.research_limit,
    )
    research_ideas = DiscoveryEngine.to_research_ideas(result.research_queue)
    if args.research_ideas_output:
        output_path = Path(args.research_ideas_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(research_ideas, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.watchlist_ideas_output:
        watchlist_ideas = DiscoveryEngine.to_research_ideas(result.watchlist[: max(0, int(args.watchlist_limit))])
        output_path = Path(args.watchlist_ideas_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(watchlist_ideas, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(_result_payload(result), indent=2, sort_keys=True))
    return 0


def _load_candidates(args: argparse.Namespace) -> list[dict]:
    if args.source_file and args.source_url:
        raise ValueError("Use either --source-file or --source-url, not both.")

    if args.source_file:
        if not args.source:
            raise ValueError("--source is required when using --source-file.")
        return load_candidate_source_file(args.source_file, source=args.source)

    if args.source_url:
        if not args.source:
            raise ValueError("--source is required when using --source-url.")
        return load_candidate_source_url(args.source_url, source=args.source)

    if not args.candidates:
        raise ValueError("Either --candidates, --source-file, or --source-url must be provided.")

    candidate_path = Path(args.candidates)
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Discovery candidate file must contain a JSON list.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _result_payload(result) -> dict:
    return {
        "research_queue": [asdict(candidate) for candidate in result.research_queue],
        "watchlist": [asdict(candidate) for candidate in result.watchlist],
        "rejected": [asdict(candidate) for candidate in result.rejected],
    }


if __name__ == "__main__":
    raise SystemExit(main())
