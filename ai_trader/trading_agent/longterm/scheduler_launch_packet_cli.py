"""CLI for building the no-submit scheduler launch packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.scheduler_launch_packet import (
    SchedulerLaunchPacketInputs,
    build_scheduler_launch_packet,
    build_scheduler_launch_packet_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a no-submit scheduler launch packet from saved artifacts.")
    parser.add_argument("--scheduler-config-validation", required=True)
    parser.add_argument("--scheduler-task-plan", required=True)
    parser.add_argument("--scheduler-handoff", required=True)
    parser.add_argument("--scheduler-task-registration", required=True)
    parser.add_argument("--dashboard-manifest", required=True)
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--stage6b-candidate-plan", default="")
    parser.add_argument("--position-review-queue", default="")
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--portfolio-news-monitor", default="")
    parser.add_argument("--api-usage", default="")
    parser.add_argument("--pipeline-summary", default="")
    parser.add_argument("--research-queue-summary", default="")
    parser.add_argument("--scheduler-soak-plan", default="")
    parser.add_argument("--pipeline-scheduler-summary", default="")
    parser.add_argument("--post-run-verification", default="")
    parser.add_argument("--scheduler-review-bundle", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    packet = build_scheduler_launch_packet(
        SchedulerLaunchPacketInputs(
            scheduler_config_validation=args.scheduler_config_validation,
            scheduler_task_plan=args.scheduler_task_plan,
            scheduler_handoff=args.scheduler_handoff,
            scheduler_task_registration=args.scheduler_task_registration,
            dashboard_manifest=args.dashboard_manifest,
            action_plan=args.action_plan,
            stage6b_candidate_plan=args.stage6b_candidate_plan,
            position_review_queue=args.position_review_queue,
            market_regime=args.market_regime,
            portfolio_news_monitor=args.portfolio_news_monitor,
            api_usage=args.api_usage,
            pipeline_summary=args.pipeline_summary,
            research_queue_summary=args.research_queue_summary,
            scheduler_soak_plan=args.scheduler_soak_plan,
            pipeline_scheduler_summary=args.pipeline_scheduler_summary,
            post_run_verification=args.post_run_verification,
            scheduler_review_bundle=args.scheduler_review_bundle,
        )
    )
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    if args.markdown_output:
        markdown = Path(args.markdown_output).expanduser().resolve()
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(build_scheduler_launch_packet_markdown(packet), encoding="utf-8")
    if args.json:
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        print(f"Scheduler launch packet {packet['status']}.")
        print("No broker orders, LLM calls, or Windows task registration were performed.")
    return 0 if packet["status"] == "ready_for_no_submit_launch_review" else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
