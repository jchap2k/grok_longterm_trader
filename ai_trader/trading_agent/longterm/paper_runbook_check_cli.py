"""CLI for read-only paper runbook artifact checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.paper_runbook_check import (
    build_paper_runbook_check,
    build_paper_runbook_check_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check saved Monday paper runbook artifacts.")
    parser.add_argument("--workflow-smoke", required=True)
    parser.add_argument("--paper-smoke-readiness", required=True)
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = build_paper_runbook_check(
        workflow_smoke=args.workflow_smoke,
        paper_smoke_readiness=args.paper_smoke_readiness,
    )
    if args.report_output:
        Path(args.report_output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(build_paper_runbook_check_markdown(report), end="")
    return 0 if report.get("ready_for_supervised_submit") else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
