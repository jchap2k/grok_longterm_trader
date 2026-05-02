"""CLI for read-only paper lifecycle summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.paper_lifecycle import build_paper_lifecycle_markdown, build_paper_lifecycle_summary
from longterm.paper_trade_ledger import PaperTradeLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize paper preview/execution/outcome lifecycle state.")
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--price-map", default=None)
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    payload = build_paper_lifecycle_summary(
        PaperTradeLedger(args.ledger_db),
        price_map=_load_json(args.price_map) if args.price_map else None,
    )
    if args.report_output:
        Path(args.report_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_paper_lifecycle_markdown(payload), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Price map must contain a JSON object.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
