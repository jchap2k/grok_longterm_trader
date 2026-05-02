"""CLI for the read-only Monday paper operator checklist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.paper_monday_check import build_paper_monday_check, build_paper_monday_check_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only Monday paper operator check.")
    parser.add_argument("--runbook", required=True)
    parser.add_argument("--workflow-smoke", required=True)
    parser.add_argument("--paper-smoke-readiness", required=True)
    parser.add_argument("--runbook-check", required=True)
    parser.add_argument("--status-refresh", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    payload = build_paper_monday_check(
        runbook=args.runbook,
        workflow_smoke=args.workflow_smoke,
        paper_smoke_readiness=args.paper_smoke_readiness,
        runbook_check=args.runbook_check,
        status_refresh=args.status_refresh or None,
    )
    if args.report_output:
        Path(args.report_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_paper_monday_check_markdown(payload), end="")
    return 0 if payload.get("ready_for_review") else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
