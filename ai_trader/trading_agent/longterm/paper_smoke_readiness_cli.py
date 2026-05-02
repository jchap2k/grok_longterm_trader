"""CLI for supervised paper-smoke readiness reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.broker_capabilities import evaluate_broker_capability_match
from longterm.paper_account_cleanliness import evaluate_paper_account_cleanliness
from longterm.paper_smoke_readiness import (
    build_paper_smoke_readiness_markdown,
    build_paper_smoke_readiness_report,
)
from longterm.portfolio_state import PortfolioState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only paper-smoke readiness report.")
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--expected-cash", type=float, default=None)
    parser.add_argument("--cash-tolerance", type=float, default=1.0)
    parser.add_argument("--paper-broker", default="alpaca_paper")
    parser.add_argument("--live-broker", default="schwab_api")
    parser.add_argument(
        "--required-order-model",
        default="notional_fractional",
        choices=["notional_fractional", "whole_share"],
    )
    parser.add_argument("--scheduler-readiness", default="")
    parser.add_argument("--workflow-smoke", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    cleanliness = evaluate_paper_account_cleanliness(
        PortfolioState.from_file(args.portfolio_state),
        expected_cash=args.expected_cash,
        cash_tolerance=args.cash_tolerance,
    )
    broker_capabilities = evaluate_broker_capability_match(
        paper_broker=args.paper_broker,
        live_broker=args.live_broker,
        required_order_model=args.required_order_model,
    )
    payload = build_paper_smoke_readiness_report(
        account_cleanliness=cleanliness,
        broker_capabilities=broker_capabilities,
        scheduler_readiness=_load_json(args.scheduler_readiness) if args.scheduler_readiness else {},
        workflow_smoke=_load_json(args.workflow_smoke) if args.workflow_smoke else {},
    )
    if args.report_output:
        Path(args.report_output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_paper_smoke_readiness_markdown(payload), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
