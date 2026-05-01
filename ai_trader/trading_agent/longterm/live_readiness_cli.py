"""CLI for the long-term live-readiness checklist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.live_readiness import LiveReadinessChecklist


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render long-term live-readiness gates.")
    parser.add_argument("--observed-file", default=None, help="Optional JSON file of observed gate values.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    observed = {}
    if args.observed_file:
        observed = json.loads(Path(args.observed_file).read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    raise SystemExit(main())
