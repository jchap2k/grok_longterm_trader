"""CLI for updating Motley Fool new-recommendation delta state."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from longterm.motley_fool_new_recs_state import (
    build_motley_fool_new_recs_delta,
    load_state,
    save_state,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track newly observed Motley Fool recommendations.")
    parser.add_argument("--ideas-file", required=True, help="JSON file containing captured Motley Fool ideas.")
    parser.add_argument("--state-file", required=True, help="Durable state JSON path.")
    parser.add_argument("--output", required=True, help="Delta report JSON output path.")
    parser.add_argument("--new-ideas-output", default="", help="Optional JSON output path for just newly observed ideas.")
    parser.add_argument("--bootstrap-if-empty", action="store_true")
    parser.add_argument("--now", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_ideas(args.ideas_file)
    previous_state = load_state(args.state_file)
    payload = build_motley_fool_new_recs_delta(
        ideas=ideas,
        previous_state=previous_state,
        now=args.now or _utc_now(),
        bootstrap_if_empty=bool(args.bootstrap_if_empty),
    )
    save_state(args.state_file, payload["state"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.new_ideas_output:
        new_output = Path(args.new_ideas_output)
        new_output.parent.mkdir(parents=True, exist_ok=True)
        new_output.write_text(
            json.dumps(payload["new_recommendations"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _load_ideas(path: str | Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("--ideas-file must contain a JSON list.")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
