"""CLI for bundling verified no-submit scheduler artifacts for dashboard review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.scheduler_review_bundle import SchedulerReviewBundleInputs, build_scheduler_review_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a no-submit scheduler review gate bundle.")
    parser.add_argument("--dashboard-manifest", required=True)
    parser.add_argument("--scheduler-handoff", required=True)
    parser.add_argument("--pipeline-scheduler-summary", required=True)
    parser.add_argument("--position-review-queue", required=True)
    parser.add_argument("--post-run-verification", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-handoff-age-hours", type=int, default=24)
    parser.add_argument(
        "--min-clean-scheduler-runs",
        type=int,
        default=3,
        help="Minimum clean completed no-submit scheduler runs before submit-mode manual review.",
    )
    parser.add_argument("--buy-promotion-artifact", default="")
    parser.add_argument("--final-action-plan", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    summary = build_scheduler_review_bundle(
        SchedulerReviewBundleInputs(
            dashboard_manifest=args.dashboard_manifest,
            scheduler_handoff=args.scheduler_handoff,
            pipeline_scheduler_summary=args.pipeline_scheduler_summary,
            position_review_queue=args.position_review_queue,
            post_run_verification=args.post_run_verification,
            output_dir=args.output_dir,
            max_handoff_age_hours=args.max_handoff_age_hours,
            min_clean_scheduler_runs=args.min_clean_scheduler_runs,
            buy_promotion_artifact=args.buy_promotion_artifact,
            final_action_plan=args.final_action_plan,
        )
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Scheduler review bundle: {summary['status']}")
        print(f"Manifest: {summary['dashboard_review_gates_manifest']}")
        if summary["blockers"]:
            print("Blockers: " + ", ".join(summary["blockers"]))
    return 0 if summary["status"] == "ready_for_manual_review" else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
