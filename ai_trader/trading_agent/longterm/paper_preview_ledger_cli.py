"""CLI for inspecting the paper preview ledger."""

from __future__ import annotations

import argparse
import json

from longterm.paper_trade_ledger import (
    PaperTradeLedger,
    build_paper_preview_ledger_summary_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect recorded paper preview rows.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_cmd = subparsers.add_parser("list", help="List preview rows.")
    list_cmd.add_argument("--ledger-db", default=None)
    list_cmd.add_argument("--limit", type=int, default=50)

    summary = subparsers.add_parser("summary", help="Summarize preview rows.")
    summary.add_argument("--ledger-db", default=None)
    summary.add_argument("--json", action="store_true")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    ledger = PaperTradeLedger(args.ledger_db)
    if args.command == "list":
        print(json.dumps(ledger.list_previews(limit=args.limit), indent=2, sort_keys=True))
        return 0
    if args.command == "summary":
        summary = ledger.summarize_previews()
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(build_paper_preview_ledger_summary_markdown(summary), end="")
        return 0
    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
