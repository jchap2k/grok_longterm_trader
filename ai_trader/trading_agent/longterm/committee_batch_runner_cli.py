"""CLI for running generated committee batches without order submission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.committee_batch_runner import run_committee_batch_dir
from longterm.orchestration_cli import DEFAULT_PROFILE_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run generated long-term committee batches in order.")
    parser.add_argument("--committee-batch-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--journal-db", required=True)
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--market-regime-file", default="")
    parser.add_argument("--motley-fool-config", default="")
    parser.add_argument("--agent-preset", default="decision_4")
    parser.add_argument("--active-rules-stage", default="decision")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-plan-only", action="store_true")
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Run or plan at most this many pending batches before returning partial progress.",
    )
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    result = run_committee_batch_dir(
        committee_batch_dir=args.committee_batch_dir,
        output_dir=args.output_dir,
        journal_db=args.journal_db,
        portfolio_state=args.portfolio_state,
        market_regime_file=args.market_regime_file or None,
        motley_fool_config=args.motley_fool_config or None,
        agent_preset=args.agent_preset,
        active_rules_stage=args.active_rules_stage,
        profile_config=args.profile_config,
        campaign_id=args.campaign_id,
        resume=args.resume,
        print_plan_only=args.print_plan_only,
        max_batches=args.max_batches,
        summary_output=Path(args.summary_output) if args.summary_output else None,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Committee batch runner {result['status']}: {result['completed_count']} completed.")
        print(f"Summary: {result['summary_output']}")
    return 0 if result["status"] in {"completed", "planned", "partial"} else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
