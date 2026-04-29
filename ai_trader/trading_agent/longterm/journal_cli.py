"""CLI helpers for inspecting and updating the long-term decision journal."""

from __future__ import annotations

import argparse
import json

from longterm.decision_journal import LongTermDecisionJournal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect long-term decision journal.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Summarize benchmark outcomes.")
    summary.add_argument("--journal-db", default=None)

    list_cmd = subparsers.add_parser("list", help="List recent decisions.")
    list_cmd.add_argument("--journal-db", default=None)
    list_cmd.add_argument("--limit", type=int, default=20)

    update = subparsers.add_parser("update-outcome", help="Update active-vs-benchmark outcome.")
    update.add_argument("--journal-db", default=None)
    update.add_argument("--decision-id", required=True)
    update.add_argument("--candidate-price", type=float, required=True)
    update.add_argument("--benchmark-price", type=float, required=True)
    update.add_argument("--notes", default="")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    journal = LongTermDecisionJournal(args.journal_db)

    if args.command == "summary":
        print(json.dumps(journal.summarize_benchmark_performance(), indent=2, sort_keys=True))
        return 0

    if args.command == "list":
        print(json.dumps(journal.list_recent_decisions(limit=args.limit), indent=2, sort_keys=True))
        return 0

    if args.command == "update-outcome":
        journal.update_outcome(
            args.decision_id,
            candidate_price=args.candidate_price,
            benchmark_price=args.benchmark_price,
            notes=args.notes,
        )
        print(f"updated {args.decision_id}")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
