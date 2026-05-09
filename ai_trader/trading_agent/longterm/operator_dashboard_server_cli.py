"""CLI for serving a live read-only operator dashboard from a manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.operator_dashboard_server import build_dashboard_manifest, find_latest_dashboard_manifest, serve_dashboard_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a live read-only long-term operator dashboard.")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--auto-manifest-root", default="")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--write-manifest-only", action="store_true")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--operator-status", default="")
    parser.add_argument("--evidence-file", default="")
    parser.add_argument("--price-history-file", default="")
    parser.add_argument("--api-usage", default="")
    parser.add_argument("--pipeline-summary", default="")
    parser.add_argument("--pipeline-scheduler-summary", default="")
    parser.add_argument("--scheduler-config-validation", default="")
    parser.add_argument("--scheduler-task-plan", default="")
    parser.add_argument("--scheduler-handoff", default="")
    parser.add_argument("--scheduler-task-registration", default="")
    parser.add_argument("--position-review-queue", default="")
    parser.add_argument("--paper-submit-mode-plan", default="")
    parser.add_argument("--scheduler-policy", default="")
    parser.add_argument("--committee-preset-policy", default="")
    parser.add_argument("--decision-journal", default="")
    parser.add_argument("--active-rules", default="")
    parser.add_argument("--lessons-snapshot", default="")
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, server_func=serve_dashboard_manifest) -> int:
    if not args.manifest and not args.auto_manifest_root:
        raise ValueError("Provide --manifest or --auto-manifest-root.")
    manifest_path = Path(args.manifest) if args.manifest else find_latest_dashboard_manifest(args.auto_manifest_root)
    if args.write_manifest:
        manifest = build_dashboard_manifest(
            action_plan=args.action_plan,
            portfolio_state=args.portfolio_state,
            market_regime=args.market_regime,
            operator_status=args.operator_status,
            evidence_file=args.evidence_file,
            price_history_file=args.price_history_file,
            api_usage=args.api_usage,
            pipeline_summary=args.pipeline_summary,
            pipeline_scheduler_summary=args.pipeline_scheduler_summary,
            scheduler_config_validation=args.scheduler_config_validation,
            scheduler_task_plan=args.scheduler_task_plan,
            scheduler_handoff=args.scheduler_handoff,
            scheduler_task_registration=args.scheduler_task_registration,
            position_review_queue=args.position_review_queue,
            paper_submit_mode_plan=args.paper_submit_mode_plan,
            scheduler_policy=args.scheduler_policy,
            committee_preset_policy=args.committee_preset_policy,
            decision_journal_path=args.decision_journal,
            active_rules_path=args.active_rules,
            lessons_snapshot_path=args.lessons_snapshot,
            campaign_id=args.campaign_id,
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    payload = {
        "mode": "operator_dashboard_server",
        "manifest": str(manifest_path),
        "auto_manifest_root": str(args.auto_manifest_root or ""),
        "url": f"http://{args.host}:{args.port}/",
        "order_submission_enabled": False,
        "served": not args.write_manifest_only,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Serving read-only dashboard at {payload['url']}")
    if args.write_manifest_only:
        return 0
    server_func(
        manifest_path=manifest_path,
        host=args.host,
        port=args.port,
        auto_manifest_root=args.auto_manifest_root,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
