"""CLI for preparing research batches from universe idea JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from longterm.research_universe import build_research_universe_batches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Chunk discovered long-term universe ideas into research batches."
    )
    parser.add_argument("--research-ideas", required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--output-dir", default="")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_research_ideas(args.research_ideas)
    batches = build_research_universe_batches(ideas, batch_size=args.batch_size)

    if args.output_dir:
        _write_batches(args.output_dir, batches)

    print(
        json.dumps(
            {
                "batch_count": len(batches),
                "total_ideas": len(ideas),
                "batches": [
                    {
                        "batch_id": batch["batch_id"],
                        "idea_count": len(batch["ideas"]),
                    }
                    for batch in batches
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _load_research_ideas(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Research ideas file must contain a JSON list.")
    return [dict(item) for item in payload]


def _write_batches(output_dir: str | Path, batches: list[dict[str, Any]]) -> None:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        target = target_dir / f"{batch['batch_id']}.json"
        target.write_text(json.dumps(batch["ideas"], indent=2, sort_keys=True), encoding="utf-8")


__all__ = ["build_parser", "main", "run_cli"]
