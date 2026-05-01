"""CLI helpers for the dry-run long-term scheduler."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.scheduler import (
    LongTermSchedulerConfig,
    LongTermSchedulerInputs,
    run_longterm_scheduler,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run recurring dry-run long-term research cycles.")
    parser.add_argument("--idea-file", default="")
    parser.add_argument("--idea-batch", default="")
    parser.add_argument("--discovery-candidates", default="")
    parser.add_argument("--discovery-source-file", default="")
    parser.add_argument("--discovery-source", default="")
    parser.add_argument("--discovery-enrichment-file", default="")
    parser.add_argument("--discovery-enrichment-source", default="local_enrichment")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--motley-fool-config", default=None)
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--agent-config", default=None)
    parser.add_argument("--agent-preset", default="decision_4")
    parser.add_argument("--launch-login-if-needed", action="store_true")
    parser.add_argument("--active-sleeve-value", type=float, default=None)
    parser.add_argument("--available-cash", type=float, default=None)
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--max-runs", type=int, default=1)
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--quiet", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    scheduler_func=run_longterm_scheduler,
) -> int:
    inputs = LongTermSchedulerInputs(
        profile_config=Path(args.profile_config),
        idea_file=Path(args.idea_file) if args.idea_file else None,
        idea_batch=Path(args.idea_batch) if args.idea_batch else None,
        discovery_candidates=Path(args.discovery_candidates) if args.discovery_candidates else None,
        discovery_source_file=Path(args.discovery_source_file) if args.discovery_source_file else None,
        discovery_source=args.discovery_source,
        discovery_enrichment_file=Path(args.discovery_enrichment_file) if args.discovery_enrichment_file else None,
        discovery_enrichment_source=args.discovery_enrichment_source,
        motley_fool_config=Path(args.motley_fool_config) if args.motley_fool_config else None,
        journal_db=Path(args.journal_db) if args.journal_db else None,
        portfolio_state=Path(args.portfolio_state) if args.portfolio_state else None,
        agent_config=Path(args.agent_config) if args.agent_config else None,
        agent_preset=args.agent_preset,
        launch_login_if_needed=args.launch_login_if_needed,
        active_sleeve_value=args.active_sleeve_value,
        available_cash=args.available_cash,
        quiet=args.quiet,
    )
    config = LongTermSchedulerConfig(
        max_runs=1 if args.run_once else args.max_runs,
        interval_seconds=args.interval_seconds,
        stop_on_error=not args.continue_on_error,
    )
    kwargs = {"inputs": inputs, "config": config}
    if args.summary_output:
        kwargs["summary_output_path"] = Path(args.summary_output)
    result = scheduler_func(**kwargs)
    payload = asdict(result) if is_dataclass(result) else result
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
