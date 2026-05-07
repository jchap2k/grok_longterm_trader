"""CLI for deterministic portfolio/watchlist news monitoring."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from longterm.portfolio_news_monitor import (
    build_portfolio_news_monitor_report,
    load_portfolio_news_inputs,
    write_portfolio_news_monitor_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a no-submit enrichment-needed queue from portfolio/watchlist news."
    )
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--watchlist-ideas", default="")
    parser.add_argument("--snapshot-file", default="", help="Symbol-keyed raw news JSON.")
    parser.add_argument("--journal-db", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--relevance-threshold", type=float, default=0.55)
    parser.add_argument("--max-articles-per-symbol", type=int, default=5)
    parser.add_argument("--include-protected-symbols", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    inputs = load_portfolio_news_inputs(
        portfolio_state_path=args.portfolio_state or None,
        watchlist_ideas_path=args.watchlist_ideas or None,
        snapshot_file=args.snapshot_file or None,
        journal_db=args.journal_db or None,
        relevance_threshold=args.relevance_threshold,
        max_articles_per_symbol=args.max_articles_per_symbol,
        include_protected_symbols=args.include_protected_symbols,
        published_after=args.published_after or "",
    )
    report = build_portfolio_news_monitor_report(inputs, now_func=_now_from_as_of_date(args.as_of_date))
    write_portfolio_news_monitor_report(report, args.output)
    summary = {
        "status": report["status"],
        "output": args.output,
        "monitored_count": report["monitored_count"],
        "articles_checked": report["articles_checked"],
        "enrichment_needed_count": report["enrichment_needed_count"],
        "order_submission_enabled": False,
        "llm_calls_enabled": False,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Portfolio news monitor "
            f"{summary['status']}: {summary['enrichment_needed_count']} enrichment trigger(s)."
        )
        print(f"Output: {args.output}")
        print("No broker orders or LLM calls were made.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _now_from_as_of_date(value: str):
    if not value:
        return None
    timestamp = datetime.fromisoformat(f"{value}T00:00:00+00:00")
    return lambda: timestamp.astimezone(timezone.utc)


__all__ = ["build_parser", "main", "run_cli"]
