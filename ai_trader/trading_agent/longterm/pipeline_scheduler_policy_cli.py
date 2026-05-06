"""CLI for the no-submit scheduler cadence policy artifact."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from longterm.pipeline_scheduler_policy import (
    PipelineSchedulerPolicyConfig,
    build_pipeline_scheduler_policy_decision,
    build_pipeline_scheduler_policy_state,
    load_json_object,
    write_pipeline_scheduler_policy_decision,
    write_pipeline_scheduler_policy_state,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an advisory scheduler cadence policy JSON artifact.")
    parser.add_argument("--rules-path", required=True)
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--policy-state", default="")
    parser.add_argument("--state-output", default="")
    parser.add_argument("--pipeline-scheduler-summary", default="")
    parser.add_argument("--pipeline-summary", default="")
    parser.add_argument("--journal-db", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--now", default="")
    parser.add_argument("--account-refresh-minutes", type=float, default=30.0)
    parser.add_argument("--no-submit-preflight-hours", type=float, default=6.0)
    parser.add_argument("--full-research-days", type=float, default=7.0)
    parser.add_argument("--panic-min-vix", type=float, default=30.0)
    parser.add_argument("--review-candidate-limit", type=int, default=50)
    parser.add_argument("--mark-full-research-complete", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    policy_state = _load_optional_json_object(args.policy_state)
    scheduler_summary = _load_optional_json_object(args.pipeline_scheduler_summary)
    pipeline_summary = _load_optional_json_object(args.pipeline_summary)
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=args.rules_path,
        now=datetime.fromisoformat(args.now) if args.now else None,
        market_regime=load_json_object(args.market_regime),
        policy_state=policy_state,
        pipeline_scheduler_summary=scheduler_summary,
        journal_db=args.journal_db or None,
        config=PipelineSchedulerPolicyConfig(
            account_refresh_minutes=args.account_refresh_minutes,
            no_submit_preflight_hours=args.no_submit_preflight_hours,
            full_research_days=args.full_research_days,
            panic_min_vix=args.panic_min_vix,
            review_candidate_limit=args.review_candidate_limit,
        ),
    )
    if args.state_output:
        state = build_pipeline_scheduler_policy_state(
            decision,
            previous_state=policy_state,
            pipeline_scheduler_summary=scheduler_summary,
            pipeline_summary=pipeline_summary,
            mark_full_research_complete=args.mark_full_research_complete,
        )
        write_pipeline_scheduler_policy_state(state, args.state_output)
        decision["state_output"] = str(Path(args.state_output))
    if args.report_output:
        write_pipeline_scheduler_policy_decision(decision, args.report_output)
    if args.json:
        print(json.dumps(decision, indent=2, sort_keys=True))
    else:
        print(f"Scheduler policy: {decision['recommended_mode']} ({decision['urgency']})")
        print("No paper or live orders were submitted.")
    return 0


def _load_optional_json_object(path: str) -> dict[str, object]:
    """Load an optional JSON object, treating missing cadence artifacts as empty."""
    if not path:
        return {}
    if not Path(path).exists():
        return {}
    return load_json_object(path)


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
