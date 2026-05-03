"""CLI for filtering account action plans to Stage 6B paper-submit candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from longterm.action_plan_filter import build_paper_submit_candidate_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Filter an action plan to Stage 6B simple paper BUY candidates.")
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    candidate_plan = build_paper_submit_candidate_plan(_load_json(args.action_plan))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(candidate_plan, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(candidate_plan, indent=2, sort_keys=True))
    else:
        print(
            f"Wrote {candidate_plan['kept_count']} Stage 6B submit candidates "
            f"to {output_path}; excluded {candidate_plan['excluded_count']} intents."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
