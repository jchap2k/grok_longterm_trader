"""CLI for packaging a no-submit scheduler readiness smoke artifact."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from longterm.scheduler_launch_packet import (
    SchedulerLaunchPacketInputs,
    build_scheduler_launch_packet,
    build_scheduler_launch_packet_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package a no-submit scheduler readiness smoke from saved artifacts.")
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
    parser.add_argument("--pipeline-scheduler-summary", default="")
    parser.add_argument("--post-run-verification", default="")
    parser.add_argument("--scheduler-review-bundle", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
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
            pipeline_scheduler_summary=args.pipeline_scheduler_summary,
            post_run_verification=args.post_run_verification,
            scheduler_review_bundle=args.scheduler_review_bundle,
        )
    )
    packet_path = output_dir / "scheduler_launch_packet.json"
    markdown_path = output_dir / "scheduler_launch_packet.md"
    summary_path = output_dir / "scheduler_no_submit_smoke.json"
    _write_json(packet_path, packet)
    markdown_path.write_text(build_scheduler_launch_packet_markdown(packet), encoding="utf-8")
    summary = {
        "schema_version": 1,
        "mode": "scheduler_no_submit_readiness_smoke",
        "status": packet["status"],
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "launch_packet": str(packet_path),
        "launch_packet_markdown": str(markdown_path),
        "blockers": packet.get("blockers") or [],
        "warnings": packet.get("warnings") or [],
        "order_submission_enabled": False,
        "windows_task_registration_executed": False,
        "scheduler_executed": False,
        "next_safe_action": packet.get("next_safe_action"),
        "notes": [
            "This smoke packages existing artifacts only.",
            "It does not run the scheduler, register Windows tasks, call brokers, or call LLMs.",
        ],
    }
    _write_json(summary_path, summary)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"No-submit scheduler readiness smoke {summary['status']}.")
        print(f"Summary: {summary_path}")
    return 0 if summary["status"] == "ready_for_no_submit_launch_review" else 1


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
