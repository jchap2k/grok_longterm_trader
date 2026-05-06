"""CLI for the recurring no-submit research-to-paper pipeline scheduler."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.pipeline_scheduler import (
    PipelineSchedulerConfig,
    PipelineSchedulerInputs,
    run_pipeline_scheduler,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a recurring no-submit research-to-paper pipeline scheduler."
    )
    parser.add_argument(
        "--preset",
        choices=["", "ongoing-no-submit"],
        default="",
        help="Use a safe built-in scheduler command set instead of manual command templates.",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pre-pipeline-refresh-command-template", default="")
    parser.add_argument("--pipeline-command-template", default="")
    parser.add_argument("--committee-preset-policy-command-template", default="")
    parser.add_argument("--scheduler-policy-command-template", default="")
    parser.add_argument("--account-refresh-command-template", default="")
    parser.add_argument("--journal-db", default="")
    parser.add_argument("--ledger-db", default="")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--market-regime-file", default="")
    parser.add_argument("--price-map", default="")
    parser.add_argument("--skip-price-map", action="store_true")
    parser.add_argument("--allow-existing-paper-positions", action="store_true")
    parser.add_argument("--final-planning-refresh", action="store_true")
    parser.add_argument("--planning-capital-from-portfolio-state", action="store_true")
    parser.add_argument("--expected-cash-from-portfolio-state", action="store_true")
    parser.add_argument(
        "--rules-path",
        default=str(Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"),
    )
    parser.add_argument("--max-runs", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=3600.0)
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--print-plan-only", action="store_true")
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def _quote(value: str | Path) -> str:
    return subprocess.list2cmdline([str(value)])


def _require_preset_path(args: argparse.Namespace, attr: str, flag: str) -> str:
    value = getattr(args, attr, "")
    if not value:
        raise ValueError(f"{flag} is required when --preset ongoing-no-submit is used.")
    return value


def _append_optional_path(parts: list[str], flag: str, value: str) -> None:
    if value:
        parts.extend([flag, _quote(value)])


def _append_optional_flag(parts: list[str], flag: str, enabled: bool) -> None:
    if enabled:
        parts.append(flag)


def _build_ongoing_no_submit_templates(args: argparse.Namespace) -> dict[str, str]:
    """Build safe no-submit scheduler command templates from stable core paths."""
    journal_db = _require_preset_path(args, "journal_db", "--journal-db")
    ledger_db = _require_preset_path(args, "ledger_db", "--ledger-db")
    action_plan = _require_preset_path(args, "action_plan", "--action-plan")
    profile_config = args.profile_config or str(DEFAULT_PROFILE_PATH)

    pre_refresh = " ".join(
        [
            "python",
            "scripts/longterm_alpaca_paper_snapshot.py",
            "--profile-config",
            _quote(profile_config),
            "--portfolio-state-output",
            "{portfolio_state}",
        ]
    )

    pipeline_parts = [
        "python",
        "scripts/longterm_research_to_paper_pipeline.py",
        "--output-dir",
        "{pipeline_output_dir}",
        "--action-plan",
        _quote(action_plan),
        "--portfolio-state",
        "{portfolio_state}",
        "--journal-db",
        _quote(journal_db),
        "--ledger-db",
        _quote(ledger_db),
        "--profile-config",
        _quote(profile_config),
        "--json",
    ]
    _append_optional_path(pipeline_parts, "--market-regime-file", args.market_regime_file)
    _append_optional_path(pipeline_parts, "--price-map", args.price_map)
    _append_optional_flag(pipeline_parts, "--skip-price-map", args.skip_price_map)
    _append_optional_flag(
        pipeline_parts,
        "--allow-existing-paper-positions",
        args.allow_existing_paper_positions,
    )
    _append_optional_flag(pipeline_parts, "--final-planning-refresh", args.final_planning_refresh)
    _append_optional_flag(
        pipeline_parts,
        "--planning-capital-from-portfolio-state",
        args.planning_capital_from_portfolio_state,
    )
    _append_optional_flag(
        pipeline_parts,
        "--expected-cash-from-portfolio-state",
        args.expected_cash_from_portfolio_state,
    )

    scheduler_policy_parts = [
        "python",
        "scripts/longterm_pipeline_scheduler_policy.py",
        "--rules-path",
        "{rules_path}",
        "--journal-db",
        _quote(journal_db),
        "--policy-state",
        "{scheduler_policy_state}",
        "--state-output",
        "{scheduler_policy_state}",
        "--pipeline-scheduler-summary",
        "{scheduler_summary}",
        "--pipeline-summary",
        "{pipeline_summary}",
        "--report-output",
        "{scheduler_policy}",
        "--json",
    ]
    _append_optional_path(scheduler_policy_parts, "--market-regime", args.market_regime_file)

    account_refresh_parts = [
        "python",
        "scripts/longterm_paper_account_refresh.py",
        "--profile-config",
        _quote(profile_config),
        "--journal-db",
        _quote(journal_db),
        "--action-plan",
        _quote(action_plan),
        "--paper-ledger-db",
        _quote(ledger_db),
        "--pipeline-summary",
        "{pipeline_summary}",
        "--output-dir",
        "{account_refresh_output_dir}",
        "--dashboard-manifest-output",
        "{dashboard_manifest}",
        "--dashboard-site-output-dir",
        "{dashboard_site_output_dir}",
        "--json",
    ]
    _append_optional_path(account_refresh_parts, "--market-regime", args.market_regime_file)

    return {
        "pre_pipeline_refresh": pre_refresh,
        "pipeline": " ".join(pipeline_parts),
        "scheduler_policy": " ".join(scheduler_policy_parts),
        "account_refresh": " ".join(account_refresh_parts),
    }


def _resolve_command_templates(args: argparse.Namespace) -> dict[str, str]:
    explicit_templates = [
        args.pre_pipeline_refresh_command_template,
        args.pipeline_command_template,
        args.committee_preset_policy_command_template,
        args.scheduler_policy_command_template,
        args.account_refresh_command_template,
    ]
    if args.preset == "ongoing-no-submit":
        if any(explicit_templates):
            raise ValueError("Choose --preset ongoing-no-submit or explicit command templates, not both.")
        return _build_ongoing_no_submit_templates(args)
    if not args.pipeline_command_template:
        raise ValueError("--pipeline-command-template is required unless --preset ongoing-no-submit is used.")
    return {
        "pre_pipeline_refresh": args.pre_pipeline_refresh_command_template,
        "pipeline": args.pipeline_command_template,
        "committee_preset_policy": args.committee_preset_policy_command_template,
        "scheduler_policy": args.scheduler_policy_command_template,
        "account_refresh": args.account_refresh_command_template,
    }


def run_cli(args: argparse.Namespace) -> int:
    max_runs = 1 if args.run_once else args.max_runs
    templates = _resolve_command_templates(args)
    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=args.output_dir,
            pre_pipeline_refresh_command_template=templates.get("pre_pipeline_refresh", ""),
            pipeline_command_template=templates["pipeline"],
            committee_preset_policy_command_template=templates.get("committee_preset_policy", ""),
            scheduler_policy_command_template=templates.get("scheduler_policy", ""),
            account_refresh_command_template=templates.get("account_refresh", ""),
            rules_path=args.rules_path,
            summary_output=args.summary_output or None,
        ),
        PipelineSchedulerConfig(
            max_runs=max_runs,
            interval_seconds=args.interval_seconds,
            stop_on_error=not args.continue_on_error,
            print_plan_only=args.print_plan_only,
        ),
    )
    payload = asdict(summary)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Pipeline scheduler {summary.status}: {summary.run_count} run(s) recorded.")
        print(f"Output: {summary.output_dir}")
        print("No paper or live orders were submitted.")
    return 0 if summary.status in {"completed", "planned"} else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
