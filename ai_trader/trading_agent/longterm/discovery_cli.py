"""CLI helpers for building the long-term discovery research queue."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from longterm.discovery import DiscoveryEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a long-term stock discovery queue from candidate JSON.")
    parser.add_argument("--candidates", required=True, help="JSON file containing a list of raw discovery candidates.")
    parser.add_argument("--research-limit", type=int, default=25)
    parser.add_argument(
        "--research-ideas-output",
        default="",
        help="Optional path to write research-ready candidates as idea-batch JSON.",
    )
    return parser


def run_cli(args: argparse.Namespace, *, engine: DiscoveryEngine | None = None) -> int:
    candidate_path = Path(args.candidates)
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Discovery candidate file must contain a JSON list.")

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

    print(json.dumps(_result_payload(result), indent=2, sort_keys=True))
    return 0


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
