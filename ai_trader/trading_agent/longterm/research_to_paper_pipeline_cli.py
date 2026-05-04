"""CLI for the dry-run research-to-paper pipeline command planner."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.research_to_paper_pipeline import (
    build_paper_preflight_stages,
    run_pipeline_stages,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or print a no-submit research-to-paper preflight pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--rules-path", default=str(Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"))
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--journal-db", required=True)
    parser.add_argument("--ledger-db", required=True)
    parser.add_argument("--price-map", default="")
    parser.add_argument("--expected-cash", type=float, default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--skip-price-map", action="store_true")
    parser.add_argument("--print-plan-only", action="store_true")
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    summary_output = Path(args.summary_output) if args.summary_output else output_dir / "pipeline_summary.json"
    stages = build_paper_preflight_stages(
        output_dir=output_dir,
        rules_path=args.rules_path,
        action_plan=args.action_plan,
        portfolio_state=args.portfolio_state,
        journal_db=args.journal_db,
        ledger_db=args.ledger_db,
        price_map=args.price_map or None,
        expected_cash=args.expected_cash,
        profile_config=args.profile_config,
        skip_price_map=args.skip_price_map,
    )
    result = run_pipeline_stages(
        stages,
        output_dir=output_dir,
        summary_output=summary_output,
        print_plan_only=args.print_plan_only,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Pipeline {result.status}: {len(result.stages)} / {result.stage_count} stages recorded.")
        print(f"Summary: {summary_output}")
    return 0 if result.status in {"completed", "planned"} else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
