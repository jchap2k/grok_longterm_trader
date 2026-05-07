"""CLI for validating and summarizing portfolio news monitor reports."""

from __future__ import annotations

import argparse
import json
import sys

from longterm.portfolio_news_monitor_ingest import (
    build_portfolio_news_followup_ideas,
    build_portfolio_news_monitor_ingest_summary,
    load_portfolio_news_monitor_report,
    write_portfolio_news_followup_ideas,
    write_portfolio_news_monitor_ingest_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a portfolio news monitor report for pipeline ingestion.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--followup-ideas-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    try:
        report = load_portfolio_news_monitor_report(args.input)
        summary = build_portfolio_news_monitor_ingest_summary(args.input)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_portfolio_news_monitor_ingest_summary(summary, args.output)
    if args.followup_ideas_output:
        write_portfolio_news_followup_ideas(
            build_portfolio_news_followup_ideas(report),
            args.followup_ideas_output,
        )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Portfolio news monitor ingest completed: {summary['queue_count']} trigger(s).")
        print(f"Output: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
