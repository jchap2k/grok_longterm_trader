"""CLI for rendering the long-term scheduler operating model."""

from __future__ import annotations

import argparse
import json

from longterm.scheduler_operating_model import SchedulerOperatingModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render long-term scheduler operating model.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    model = SchedulerOperatingModel.default()
    if args.json:
        print(json.dumps(model.to_dict(), indent=2, sort_keys=True))
    else:
        print(model.to_markdown(), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
