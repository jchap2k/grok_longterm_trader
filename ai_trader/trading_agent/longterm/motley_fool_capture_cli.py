"""CLI helpers for Motley Fool premium table capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.motley_fool_capture import capture_motley_fool_ideas
from longterm.motley_fool_intake import default_motley_fool_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture Motley Fool premium tables as research ideas.")
    parser.add_argument(
        "--source",
        choices=sorted(default_motley_fool_sources().keys()) + ["all_full"],
        default="new_recommendations",
        help=(
            "Source to capture. Defaults to fresh new recommendations. "
            "Use all_full for new recommendations, analyst rankings, and AI rankings."
        ),
    )
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--output", default="", help="Optional JSON output file for captured ideas.")
    return parser


def run_cli(args: argparse.Namespace, *, capture_func=capture_motley_fool_ideas) -> int:
    source_keys = (
        ["new_recommendations", "analyst_rankings", "quant_rankings"]
        if args.source == "all_full"
        else [args.source]
    )
    ideas: list[dict] = []
    for source_key in source_keys:
        ideas.extend(
            capture_func(
                source_key,
                profile_dir=args.profile_dir,
                url=args.url if len(source_keys) == 1 else None,
            )
        )
    payload = json.dumps(ideas, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
