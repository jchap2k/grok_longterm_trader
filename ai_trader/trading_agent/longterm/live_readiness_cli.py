"""CLI for the long-term live-readiness checklist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.live_readiness import LiveReadinessChecklist


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render long-term live-readiness gates.")
    parser.add_argument("--observed-file", default=None, help="Optional JSON file of observed gate values.")
    parser.add_argument(
        "--observed-fragment",
        action="append",
        default=[],
        help="Optional JSON object fragment to merge after --observed-file. Can be repeated.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    observed = _load_observed(args.observed_file, args.observed_fragment)
    result = LiveReadinessChecklist.default().evaluate(observed)
    if args.json:
        print(
            json.dumps(
                {
                    "ready": result.ready,
                    "unmet_gate_keys": result.unmet_gate_keys,
                    "gates": result.gates,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(result.to_markdown(), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_observed(observed_file: str | None, observed_fragments: list[str]) -> dict:
    observed: dict = {}
    if observed_file:
        observed.update(_load_json_object(observed_file))
    for fragment in observed_fragments or []:
        observed.update(_load_json_object(fragment))
    return observed


def _load_json_object(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
