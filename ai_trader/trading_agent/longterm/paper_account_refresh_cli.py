"""CLI for read-only paper account artifact refresh."""

from __future__ import annotations

import argparse
import json
from typing import Callable

from longterm.alpaca_paper_account import AlpacaPaperAccountReader
from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.paper_account_refresh import refresh_paper_account_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh read-only paper account, status, and dashboard artifacts."
    )
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--journal-db", required=True)
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--paper-ledger-db", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--evidence-file", default="")
    parser.add_argument("--price-history-file", default="")
    parser.add_argument("--pipeline-summary", default="")
    parser.add_argument("--pipeline-scheduler-summary", default="")
    parser.add_argument("--scheduler-config-validation", default="")
    parser.add_argument("--scheduler-task-plan", default="")
    parser.add_argument("--scheduler-policy", default="")
    parser.add_argument("--committee-preset-policy", default="")
    parser.add_argument("--status-refresh-file", default="")
    parser.add_argument("--dashboard-manifest-output", default="")
    parser.add_argument("--dashboard-site-output-dir", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    reader_factory: Callable[[], AlpacaPaperAccountReader] | None = None,
) -> int:
    summary = refresh_paper_account_artifacts(
        profile_config=args.profile_config,
        journal_db=args.journal_db,
        action_plan_path=args.action_plan,
        paper_ledger_db=args.paper_ledger_db,
        output_dir=args.output_dir,
        market_regime_path=args.market_regime,
        evidence_file=args.evidence_file,
        price_history_file=args.price_history_file,
        pipeline_summary_path=args.pipeline_summary,
        pipeline_scheduler_summary_path=args.pipeline_scheduler_summary,
        scheduler_config_validation_path=args.scheduler_config_validation,
        scheduler_task_plan_path=args.scheduler_task_plan,
        scheduler_policy_path=args.scheduler_policy,
        committee_preset_policy_path=args.committee_preset_policy,
        status_refresh_file=args.status_refresh_file,
        dashboard_manifest_output=args.dashboard_manifest_output,
        dashboard_site_output_dir=args.dashboard_site_output_dir,
        reader_factory=reader_factory,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Refreshed paper account artifacts in {summary['refresh_summary_path']}.")
        print("No paper or live orders were submitted.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
