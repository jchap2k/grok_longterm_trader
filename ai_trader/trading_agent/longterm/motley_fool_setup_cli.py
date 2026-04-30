"""CLI helpers for interactive Motley Fool login setup."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass

from longterm.motley_fool_settings import load_motley_fool_capture_settings
from longterm.motley_fool_setup import complete_motley_fool_setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open and verify Motley Fool premium login setup.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--verification-source", default="dashboard")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    setup_func=complete_motley_fool_setup,
) -> int:
    settings = load_motley_fool_capture_settings(args.config)
    result = setup_func(
        settings=settings,
        verification_source=args.verification_source,
    )
    payload = asdict(result) if is_dataclass(result) else result
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
