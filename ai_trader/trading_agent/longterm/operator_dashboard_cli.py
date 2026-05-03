"""CLI for static long-term trader operator dashboards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from longterm.operator_dashboard import (
    build_operator_dashboard,
    build_operator_dashboard_html,
    build_operator_dashboard_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only long-term operator dashboard.")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--operator-status", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--html-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    dashboard = build_operator_dashboard(
        action_plan=_load_json(args.action_plan) if args.action_plan else None,
        market_regime=_load_json(args.market_regime) if args.market_regime else None,
        operator_status=_load_json(args.operator_status) if args.operator_status else None,
    )
    if args.report_output:
        output_path = Path(args.report_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(dashboard, indent=2, sort_keys=True), encoding="utf-8")
    if args.html_output:
        html_path = Path(args.html_output)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(build_operator_dashboard_html(dashboard), encoding="utf-8")
    if args.json:
        print(json.dumps(dashboard, indent=2, sort_keys=True))
    else:
        print(build_operator_dashboard_markdown(dashboard), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
