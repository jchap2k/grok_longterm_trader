"""Recurring no-submit scheduler for the research-to-paper pipeline."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from longterm.pipeline_health_cli import build_pipeline_health_report


CommandRunner = Callable[[str], tuple[int, str, str]]
NowFunc = Callable[[], datetime]
SleepFunc = Callable[[float], None]


FORBIDDEN_SCHEDULER_FRAGMENTS = (
    "--submit-paper-orders",
    "--confirm-paper-submit",
    "supervised_paper",
    "longterm_paper_execution.py",
    "paper_execution.py",
)
FORBIDDEN_COMMAND_SEPARATORS = ("&&", "|", ";", "`", "\n", "\r")
TRADING_AGENT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PipelineSchedulerInputs:
    """Static scheduler inputs."""

    output_dir: str | Path
    pipeline_command_template: str
    rules_path: str | Path
    pre_pipeline_refresh_command_template: str = ""
    portfolio_news_monitor_command_template: str = ""
    position_review_queue_command_template: str = ""
    committee_preset_policy_command_template: str = ""
    scheduler_policy_command_template: str = ""
    account_refresh_command_template: str = ""
    post_run_verification_command_template: str = ""
    scheduler_review_bundle_command_template: str = ""
    summary_output: str | Path | None = None


@dataclass(frozen=True)
class PipelineSchedulerConfig:
    """Runtime scheduler controls."""

    max_runs: int = 1
    interval_seconds: float = 3600.0
    stop_on_error: bool = True
    print_plan_only: bool = False


@dataclass(frozen=True)
class PipelineSchedulerRunRecord:
    """Audit record for one scheduler run."""

    run_number: int
    scheduler_run_id: str
    started_at: str
    finished_at: str
    status: str
    run_dir: str
    pipeline_output_dir: str
    pipeline_summary_path: str
    pipeline_health_path: str
    pipeline_command: str
    pre_pipeline_refresh_command: str = ""
    pre_pipeline_refresh_exit_code: int | None = None
    pre_pipeline_refresh_stdout_path: str = ""
    pre_pipeline_refresh_stderr_path: str = ""
    portfolio_news_monitor_path: str = ""
    portfolio_news_monitor_command: str = ""
    portfolio_news_monitor_exit_code: int | None = None
    portfolio_news_monitor_stdout_path: str = ""
    portfolio_news_monitor_stderr_path: str = ""
    position_review_queue_path: str = ""
    position_review_queue_command: str = ""
    position_review_queue_exit_code: int | None = None
    position_review_queue_stdout_path: str = ""
    position_review_queue_stderr_path: str = ""
    pipeline_exit_code: int | None = None
    pipeline_stdout_path: str = ""
    pipeline_stderr_path: str = ""
    committee_preset_policy_path: str = ""
    committee_preset_policy_command: str = ""
    committee_preset_policy_exit_code: int | None = None
    committee_preset_policy_stdout_path: str = ""
    committee_preset_policy_stderr_path: str = ""
    scheduler_policy_path: str = ""
    scheduler_policy_command: str = ""
    scheduler_policy_exit_code: int | None = None
    scheduler_policy_stdout_path: str = ""
    scheduler_policy_stderr_path: str = ""
    account_refresh_command: str = ""
    account_refresh_exit_code: int | None = None
    account_refresh_stdout_path: str = ""
    account_refresh_stderr_path: str = ""
    post_run_verification_path: str = ""
    post_run_verification_command: str = ""
    post_run_verification_exit_code: int | None = None
    post_run_verification_stdout_path: str = ""
    post_run_verification_stderr_path: str = ""
    scheduler_review_bundle_path: str = ""
    scheduler_review_bundle_output_dir: str = ""
    scheduler_review_bundle_command: str = ""
    scheduler_review_bundle_exit_code: int | None = None
    scheduler_review_bundle_stdout_path: str = ""
    scheduler_review_bundle_stderr_path: str = ""
    blocker: str = ""
    resource_controls: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineSchedulerSummary:
    """Full recurring scheduler summary."""

    schema_version: int
    mode: str
    status: str
    order_submission_enabled: bool
    output_dir: str
    run_count: int
    success_count: int
    error_count: int
    runs: list[PipelineSchedulerRunRecord] = field(default_factory=list)
    next_safe_action: str = ""


def run_pipeline_scheduler(
    inputs: PipelineSchedulerInputs,
    config: PipelineSchedulerConfig | None = None,
    *,
    command_runner: CommandRunner | None = None,
    now_func: NowFunc | None = None,
    sleep_func: SleepFunc | None = None,
) -> PipelineSchedulerSummary:
    """Run a safe recurring pipeline command loop and persist an audit summary."""
    scheduler_config = config or PipelineSchedulerConfig()
    if scheduler_config.max_runs < 1:
        raise ValueError("max_runs must be at least 1.")
    output_dir = Path(inputs.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rules_path = Path(inputs.rules_path)
    summary_output_path = _summary_output_path(inputs, output_dir)
    scheduler_policy_state_path = output_dir / "scheduler_policy_state.json"
    runner = command_runner or _run_command
    now = now_func or _utc_now
    sleeper = sleep_func or time.sleep
    validate_scheduler_command_template(
        inputs.pipeline_command_template,
        command_kind="pipeline",
        rules_path=rules_path,
    )
    if inputs.pre_pipeline_refresh_command_template:
        validate_scheduler_command_template(
            inputs.pre_pipeline_refresh_command_template,
            command_kind="pre_pipeline_refresh",
            rules_path=rules_path,
        )
    if inputs.portfolio_news_monitor_command_template:
        validate_scheduler_command_template(
            inputs.portfolio_news_monitor_command_template,
            command_kind="portfolio_news_monitor",
            rules_path=rules_path,
        )
    if inputs.position_review_queue_command_template:
        validate_scheduler_command_template(
            inputs.position_review_queue_command_template,
            command_kind="position_review_queue",
            rules_path=rules_path,
        )
    if inputs.account_refresh_command_template:
        validate_scheduler_command_template(
            inputs.account_refresh_command_template,
            command_kind="account_refresh",
            rules_path=rules_path,
        )
    if inputs.committee_preset_policy_command_template:
        validate_scheduler_command_template(
            inputs.committee_preset_policy_command_template,
            command_kind="committee_preset_policy",
            rules_path=rules_path,
        )
    if inputs.scheduler_policy_command_template:
        validate_scheduler_command_template(
            inputs.scheduler_policy_command_template,
            command_kind="scheduler_policy",
            rules_path=rules_path,
        )
    if inputs.post_run_verification_command_template:
        validate_scheduler_command_template(
            inputs.post_run_verification_command_template,
            command_kind="post_run_verification",
            rules_path=rules_path,
        )
    if inputs.scheduler_review_bundle_command_template:
        if not inputs.post_run_verification_command_template:
            raise ValueError(
                "scheduler_review_bundle_command_template requires post_run_verification_command_template "
                "so the bundle can be built only after verification passes."
            )
        validate_scheduler_command_template(
            inputs.scheduler_review_bundle_command_template,
            command_kind="scheduler_review_bundle",
            rules_path=rules_path,
        )

    records: list[PipelineSchedulerRunRecord] = []
    for run_number in range(1, scheduler_config.max_runs + 1):
        started = now()
        run_dir = output_dir / f"run_{run_number:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = run_dir / ".pipeline_scheduler.lock"
        if lock_path.exists():
            record = _blocked_record(
                run_number=run_number,
                started=started,
                finished=now(),
                run_dir=run_dir,
                rules_path=rules_path,
                blocker="scheduler_lock_exists",
            )
            records.append(record)
            if scheduler_config.stop_on_error:
                break
            continue
        lock_path.write_text(_format_timestamp(started), encoding="utf-8")
        try:
            record = _run_one_scheduler_cycle(
                inputs=inputs,
                config=scheduler_config,
                run_number=run_number,
                started=started,
                run_dir=run_dir,
                rules_path=rules_path,
                scheduler_summary_path=summary_output_path,
                scheduler_policy_state_path=scheduler_policy_state_path,
                command_runner=runner,
                now_func=now,
            )
            records.append(record)
            _update_scheduler_policy_state_after_record(
                scheduler_policy_state_path,
                record=record,
                rules_path=rules_path,
            )
            write_pipeline_scheduler_summary(_build_summary(output_dir=output_dir, records=records), summary_output_path)
            verified_record = _run_post_run_verification_after_summary(
                record,
                command_runner=runner,
                now_func=now,
            )
            if verified_record != record:
                records[-1] = verified_record
                write_pipeline_scheduler_summary(
                    _build_summary(output_dir=output_dir, records=records),
                    summary_output_path,
                )
            bundled_record = _run_scheduler_review_bundle_after_verification(
                records[-1],
                command_runner=runner,
                now_func=now,
            )
            if bundled_record != records[-1]:
                records[-1] = bundled_record
                write_pipeline_scheduler_summary(
                    _build_summary(output_dir=output_dir, records=records),
                    summary_output_path,
                )
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass
        if records[-1].status == "failed" and scheduler_config.stop_on_error:
            break
        if run_number < scheduler_config.max_runs:
            sleeper(scheduler_config.interval_seconds)

    summary = _build_summary(output_dir=output_dir, records=records)
    write_pipeline_scheduler_summary(summary, summary_output_path)
    return summary


def validate_scheduler_command_template(
    command_template: str,
    *,
    command_kind: str,
    rules_path: str | Path,
) -> None:
    """Validate that a scheduler command cannot submit orders or lose context."""
    if not command_template.strip():
        raise ValueError(f"{command_kind} command template is required.")
    if not Path(rules_path).exists():
        raise ValueError(f"rules_path does not exist: {rules_path}")
    lowered = command_template.lower()
    for fragment in FORBIDDEN_SCHEDULER_FRAGMENTS:
        if fragment in lowered:
            raise ValueError(f"Unsafe scheduler command contains forbidden fragment: {fragment}")
    for separator in FORBIDDEN_COMMAND_SEPARATORS:
        if separator in command_template:
            raise ValueError(f"Unsafe scheduler command contains forbidden separator: {separator!r}")
    if command_kind == "pipeline":
        if "longterm_research_to_paper_pipeline.py" not in lowered:
            raise ValueError("Pipeline command must call scripts/longterm_research_to_paper_pipeline.py.")
        _require_flag(command_template, "--journal-db")
        _require_flag(command_template, "--portfolio-state")
    elif command_kind == "account_refresh":
        if "longterm_paper_account_refresh.py" not in lowered:
            raise ValueError("Account refresh command must call scripts/longterm_paper_account_refresh.py.")
        _require_flag(command_template, "--pipeline-summary")
    elif command_kind == "scheduler_policy":
        if "longterm_pipeline_scheduler_policy.py" not in lowered:
            raise ValueError("Scheduler policy command must call scripts/longterm_pipeline_scheduler_policy.py.")
        _require_flag(command_template, "--rules-path")
        _require_flag(command_template, "--report-output")
    elif command_kind == "committee_preset_policy":
        if "longterm_committee_preset_policy.py" not in lowered:
            raise ValueError("Committee preset policy command must call scripts/longterm_committee_preset_policy.py.")
        _require_flag(command_template, "--report-output")
    elif command_kind == "pre_pipeline_refresh":
        if "longterm_alpaca_paper_snapshot.py" not in lowered:
            raise ValueError("Pre-pipeline refresh command must call scripts/longterm_alpaca_paper_snapshot.py.")
        _require_flag(command_template, "--portfolio-state-output")
    elif command_kind == "portfolio_news_monitor":
        if "longterm_portfolio_news_monitor.py" not in lowered:
            raise ValueError("Portfolio news monitor command must call scripts/longterm_portfolio_news_monitor.py.")
        _require_flag(command_template, "--output")
    elif command_kind == "position_review_queue":
        if "longterm_position_review_queue.py" not in lowered:
            raise ValueError("Position review queue command must call scripts/longterm_position_review_queue.py.")
        _require_flag(command_template, "--output")
        _require_flag(command_template, "--portfolio-state")
    elif command_kind == "post_run_verification":
        if "longterm_pipeline_scheduler_verify.py" not in lowered:
            raise ValueError("Post-run verification command must call scripts/longterm_pipeline_scheduler_verify.py.")
        _require_flag(command_template, "--pipeline-scheduler-summary")
        _require_flag(command_template, "--report-output")
    elif command_kind == "scheduler_review_bundle":
        if "longterm_scheduler_review_bundle.py" not in lowered:
            raise ValueError("Scheduler review bundle command must call scripts/longterm_scheduler_review_bundle.py.")
        _require_flag(command_template, "--dashboard-manifest")
        _require_flag(command_template, "--scheduler-handoff")
        _require_flag(command_template, "--pipeline-scheduler-summary")
        _require_flag(command_template, "--position-review-queue")
        _require_flag(command_template, "--post-run-verification")
        _require_flag(command_template, "--output-dir")
    else:
        raise ValueError(f"Unknown scheduler command kind: {command_kind}")


def write_pipeline_scheduler_summary(summary: PipelineSchedulerSummary, path: str | Path) -> None:
    """Persist the scheduler summary JSON artifact."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True), encoding="utf-8")


def _run_one_scheduler_cycle(
    *,
    inputs: PipelineSchedulerInputs,
    config: PipelineSchedulerConfig,
    run_number: int,
    started: datetime,
    run_dir: Path,
    rules_path: Path,
    scheduler_summary_path: Path,
    scheduler_policy_state_path: Path,
    command_runner: CommandRunner,
    now_func: NowFunc,
) -> PipelineSchedulerRunRecord:
    scheduler_run_id = _scheduler_run_id(started, run_number)
    pipeline_output_dir = run_dir / "pipeline"
    pipeline_summary_path = run_dir / "pipeline_summary.json"
    pipeline_health_path = run_dir / "pipeline_artifact_health.json"
    portfolio_state_path = run_dir / "paper_portfolio_state.json"
    portfolio_news_monitor_path = run_dir / "portfolio_news_monitor.json"
    position_review_queue_path = run_dir / "position_review_queue.json"
    committee_preset_policy_path = run_dir / "committee_preset_policy.json"
    scheduler_policy_path = run_dir / "scheduler_policy.json"
    account_refresh_output_dir = run_dir / "paper_account_refresh"
    post_run_verification_path = run_dir / "scheduler_cadence_verification.json"
    scheduler_review_bundle_output_dir = run_dir / "scheduler_review_bundle"
    scheduler_review_bundle_path = scheduler_review_bundle_output_dir / "scheduler_review_bundle.json"
    dashboard_review_gates_manifest_path = scheduler_review_bundle_output_dir / "dashboard_review_gates_manifest.json"
    paper_submit_mode_plan_path = scheduler_review_bundle_output_dir / "paper_submit_mode_plan.json"
    dashboard_manifest_path = run_dir / "dashboard_manifest.json"
    dashboard_site_output_dir = run_dir / "operator_dashboard_site"
    context = _render_context(
        run_dir=run_dir,
        pipeline_output_dir=pipeline_output_dir,
        pipeline_summary_path=pipeline_summary_path,
        pipeline_health_path=pipeline_health_path,
        portfolio_state_path=portfolio_state_path,
        portfolio_news_monitor_path=portfolio_news_monitor_path,
        position_review_queue_path=position_review_queue_path,
        committee_preset_policy_path=committee_preset_policy_path,
        scheduler_policy_path=scheduler_policy_path,
        account_refresh_output_dir=account_refresh_output_dir,
        post_run_verification_path=post_run_verification_path,
        scheduler_review_bundle_output_dir=scheduler_review_bundle_output_dir,
        scheduler_review_bundle_path=scheduler_review_bundle_path,
        dashboard_review_gates_manifest_path=dashboard_review_gates_manifest_path,
        paper_submit_mode_plan_path=paper_submit_mode_plan_path,
        dashboard_manifest_path=dashboard_manifest_path,
        dashboard_site_output_dir=dashboard_site_output_dir,
        scheduler_run_id=scheduler_run_id,
        rules_path=rules_path,
        scheduler_summary_path=scheduler_summary_path,
        scheduler_policy_state_path=scheduler_policy_state_path,
    )
    pre_pipeline_refresh_command = (
        _render_command(inputs.pre_pipeline_refresh_command_template, context)
        if inputs.pre_pipeline_refresh_command_template
        else ""
    )
    portfolio_news_monitor_command = (
        _render_command(inputs.portfolio_news_monitor_command_template, context)
        if inputs.portfolio_news_monitor_command_template
        else ""
    )
    position_review_queue_command = (
        _render_command(inputs.position_review_queue_command_template, context)
        if inputs.position_review_queue_command_template
        else ""
    )
    monitor_fields = {
        "portfolio_news_monitor_path": str(portfolio_news_monitor_path) if portfolio_news_monitor_command else "",
        "portfolio_news_monitor_command": portfolio_news_monitor_command,
        "portfolio_news_monitor_exit_code": None,
        "portfolio_news_monitor_stdout_path": "",
        "portfolio_news_monitor_stderr_path": "",
    }
    position_review_fields = {
        "position_review_queue_path": str(position_review_queue_path) if position_review_queue_command else "",
        "position_review_queue_command": position_review_queue_command,
        "position_review_queue_exit_code": None,
        "position_review_queue_stdout_path": "",
        "position_review_queue_stderr_path": "",
    }
    pipeline_command = _prepare_pipeline_command(inputs.pipeline_command_template, context)
    policy_command = (
        _render_command(inputs.scheduler_policy_command_template, context)
        if inputs.scheduler_policy_command_template
        else ""
    )
    committee_command = (
        _render_command(inputs.committee_preset_policy_command_template, context)
        if inputs.committee_preset_policy_command_template
        else ""
    )
    refresh_command = (
        _prepare_account_refresh_command(
            inputs.account_refresh_command_template,
            context,
            include_scheduler_policy=bool(policy_command),
            include_committee_preset_policy=bool(committee_command),
        )
        if inputs.account_refresh_command_template
        else ""
    )
    post_run_verification_command = (
        _render_command(inputs.post_run_verification_command_template, context)
        if inputs.post_run_verification_command_template
        else ""
    )
    scheduler_review_bundle_command = (
        _render_command(inputs.scheduler_review_bundle_command_template, context)
        if inputs.scheduler_review_bundle_command_template
        else ""
    )
    scheduler_review_bundle_fields = {
        "scheduler_review_bundle_path": str(scheduler_review_bundle_path)
        if scheduler_review_bundle_command
        else "",
        "scheduler_review_bundle_output_dir": str(scheduler_review_bundle_output_dir)
        if scheduler_review_bundle_command
        else "",
        "scheduler_review_bundle_command": scheduler_review_bundle_command,
        "scheduler_review_bundle_exit_code": None,
        "scheduler_review_bundle_stdout_path": "",
        "scheduler_review_bundle_stderr_path": "",
    }
    resource_controls = derive_scheduler_resource_controls(pipeline_command)
    if config.print_plan_only:
        return PipelineSchedulerRunRecord(
            run_number=run_number,
            scheduler_run_id=scheduler_run_id,
            started_at=_format_timestamp(started),
            finished_at=_format_timestamp(now_func()),
            status="planned",
            run_dir=str(run_dir),
            pipeline_output_dir=str(pipeline_output_dir),
            pipeline_summary_path=str(pipeline_summary_path),
            pipeline_health_path=str(pipeline_health_path),
            pre_pipeline_refresh_command=pre_pipeline_refresh_command,
            **monitor_fields,
            **position_review_fields,
            pipeline_command=pipeline_command,
            committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
            committee_preset_policy_command=committee_command,
            scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
            scheduler_policy_command=policy_command,
            account_refresh_command=refresh_command,
            post_run_verification_path=str(post_run_verification_path) if post_run_verification_command else "",
            post_run_verification_command=post_run_verification_command,
            **scheduler_review_bundle_fields,
            resource_controls=resource_controls,
        )

    pre_refresh_exit_code: int | None = None
    pre_refresh_stdout_path = ""
    pre_refresh_stderr_path = ""
    if pre_pipeline_refresh_command:
        pre_refresh_stdout = run_dir / "pre_pipeline_refresh_stdout.txt"
        pre_refresh_stderr = run_dir / "pre_pipeline_refresh_stderr.txt"
        pre_refresh_exit_code, stdout, stderr = command_runner(pre_pipeline_refresh_command)
        _write_text(pre_refresh_stdout, stdout)
        _write_text(pre_refresh_stderr, stderr)
        pre_refresh_stdout_path = str(pre_refresh_stdout)
        pre_refresh_stderr_path = str(pre_refresh_stderr)
        if pre_refresh_exit_code != 0:
            return PipelineSchedulerRunRecord(
                run_number=run_number,
                scheduler_run_id=scheduler_run_id,
                started_at=_format_timestamp(started),
                finished_at=_format_timestamp(now_func()),
                status="failed",
                run_dir=str(run_dir),
                pipeline_output_dir=str(pipeline_output_dir),
                pipeline_summary_path=str(pipeline_summary_path),
                pipeline_health_path=str(pipeline_health_path),
                pre_pipeline_refresh_command=pre_pipeline_refresh_command,
                pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
                pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
                pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
                **monitor_fields,
                **position_review_fields,
                pipeline_command=pipeline_command,
                committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
                committee_preset_policy_command=committee_command,
                scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
                scheduler_policy_command=policy_command,
                account_refresh_command=refresh_command,
                **scheduler_review_bundle_fields,
                blocker="pre_pipeline_refresh_command_failed",
                resource_controls=resource_controls,
            )

    if portfolio_news_monitor_command:
        monitor_stdout = run_dir / "portfolio_news_monitor_stdout.txt"
        monitor_stderr = run_dir / "portfolio_news_monitor_stderr.txt"
        monitor_exit_code, stdout, stderr = command_runner(portfolio_news_monitor_command)
        _write_text(monitor_stdout, stdout)
        _write_text(monitor_stderr, stderr)
        monitor_fields = {
            "portfolio_news_monitor_path": str(portfolio_news_monitor_path),
            "portfolio_news_monitor_command": portfolio_news_monitor_command,
            "portfolio_news_monitor_exit_code": monitor_exit_code,
            "portfolio_news_monitor_stdout_path": str(monitor_stdout),
            "portfolio_news_monitor_stderr_path": str(monitor_stderr),
        }
        if monitor_exit_code != 0:
            return PipelineSchedulerRunRecord(
                run_number=run_number,
                scheduler_run_id=scheduler_run_id,
                started_at=_format_timestamp(started),
                finished_at=_format_timestamp(now_func()),
                status="failed",
                run_dir=str(run_dir),
                pipeline_output_dir=str(pipeline_output_dir),
                pipeline_summary_path=str(pipeline_summary_path),
                pipeline_health_path=str(pipeline_health_path),
                pre_pipeline_refresh_command=pre_pipeline_refresh_command,
                pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
                pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
                pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
                **monitor_fields,
                pipeline_command=pipeline_command,
                committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
                committee_preset_policy_command=committee_command,
                scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
                scheduler_policy_command=policy_command,
                account_refresh_command=refresh_command,
                **scheduler_review_bundle_fields,
                blocker="portfolio_news_monitor_command_failed",
                resource_controls=resource_controls,
            )

    if position_review_queue_command:
        review_stdout = run_dir / "position_review_queue_stdout.txt"
        review_stderr = run_dir / "position_review_queue_stderr.txt"
        review_exit_code, stdout, stderr = command_runner(position_review_queue_command)
        _write_text(review_stdout, stdout)
        _write_text(review_stderr, stderr)
        position_review_fields = {
            "position_review_queue_path": str(position_review_queue_path),
            "position_review_queue_command": position_review_queue_command,
            "position_review_queue_exit_code": review_exit_code,
            "position_review_queue_stdout_path": str(review_stdout),
            "position_review_queue_stderr_path": str(review_stderr),
        }
        if review_exit_code != 0:
            return PipelineSchedulerRunRecord(
                run_number=run_number,
                scheduler_run_id=scheduler_run_id,
                started_at=_format_timestamp(started),
                finished_at=_format_timestamp(now_func()),
                status="failed",
                run_dir=str(run_dir),
                pipeline_output_dir=str(pipeline_output_dir),
                pipeline_summary_path=str(pipeline_summary_path),
                pipeline_health_path=str(pipeline_health_path),
                pre_pipeline_refresh_command=pre_pipeline_refresh_command,
                pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
                pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
                pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
                **monitor_fields,
                **position_review_fields,
                pipeline_command=pipeline_command,
                committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
                committee_preset_policy_command=committee_command,
                scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
                scheduler_policy_command=policy_command,
                account_refresh_command=refresh_command,
                **scheduler_review_bundle_fields,
                blocker="position_review_queue_command_failed",
                resource_controls=resource_controls,
            )

    pipeline_stdout_path = run_dir / "pipeline_command_stdout.txt"
    pipeline_stderr_path = run_dir / "pipeline_command_stderr.txt"
    exit_code, stdout, stderr = command_runner(pipeline_command)
    _write_text(pipeline_stdout_path, stdout)
    _write_text(pipeline_stderr_path, stderr)
    health_report = build_pipeline_health_report(pipeline_summary=pipeline_summary_path)
    _write_json(pipeline_health_path, health_report)
    if exit_code != 0:
        return PipelineSchedulerRunRecord(
            run_number=run_number,
            scheduler_run_id=scheduler_run_id,
            started_at=_format_timestamp(started),
            finished_at=_format_timestamp(now_func()),
            status="failed",
            run_dir=str(run_dir),
            pipeline_output_dir=str(pipeline_output_dir),
            pipeline_summary_path=str(pipeline_summary_path),
            pipeline_health_path=str(pipeline_health_path),
            pre_pipeline_refresh_command=pre_pipeline_refresh_command,
            pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
            pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
            pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
            **monitor_fields,
            **position_review_fields,
            pipeline_command=pipeline_command,
            pipeline_exit_code=exit_code,
            pipeline_stdout_path=str(pipeline_stdout_path),
            pipeline_stderr_path=str(pipeline_stderr_path),
            committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
            committee_preset_policy_command=committee_command,
            scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
            scheduler_policy_command=policy_command,
            account_refresh_command=refresh_command,
            **scheduler_review_bundle_fields,
            blocker="pipeline_command_failed",
            resource_controls=resource_controls,
        )

    committee_exit_code: int | None = None
    committee_stdout_path = ""
    committee_stderr_path = ""
    if committee_command:
        committee_stdout = run_dir / "committee_preset_policy_stdout.txt"
        committee_stderr = run_dir / "committee_preset_policy_stderr.txt"
        committee_exit_code, stdout, stderr = command_runner(committee_command)
        _write_text(committee_stdout, stdout)
        _write_text(committee_stderr, stderr)
        committee_stdout_path = str(committee_stdout)
        committee_stderr_path = str(committee_stderr)
        if committee_exit_code != 0:
            return PipelineSchedulerRunRecord(
                run_number=run_number,
                scheduler_run_id=scheduler_run_id,
                started_at=_format_timestamp(started),
                finished_at=_format_timestamp(now_func()),
                status="failed",
                run_dir=str(run_dir),
                pipeline_output_dir=str(pipeline_output_dir),
                pipeline_summary_path=str(pipeline_summary_path),
                pipeline_health_path=str(pipeline_health_path),
                pre_pipeline_refresh_command=pre_pipeline_refresh_command,
                pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
                pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
                pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
                **monitor_fields,
                **position_review_fields,
                pipeline_command=pipeline_command,
                pipeline_exit_code=exit_code,
                pipeline_stdout_path=str(pipeline_stdout_path),
                pipeline_stderr_path=str(pipeline_stderr_path),
                committee_preset_policy_path=str(committee_preset_policy_path),
                committee_preset_policy_command=committee_command,
                committee_preset_policy_exit_code=committee_exit_code,
                committee_preset_policy_stdout_path=committee_stdout_path,
                committee_preset_policy_stderr_path=committee_stderr_path,
                scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
                scheduler_policy_command=policy_command,
                account_refresh_command=refresh_command,
                **scheduler_review_bundle_fields,
                blocker="committee_preset_policy_command_failed",
                resource_controls=resource_controls,
            )

    policy_exit_code: int | None = None
    policy_stdout_path = ""
    policy_stderr_path = ""
    if policy_command:
        policy_stdout = run_dir / "scheduler_policy_stdout.txt"
        policy_stderr = run_dir / "scheduler_policy_stderr.txt"
        policy_exit_code, stdout, stderr = command_runner(policy_command)
        _write_text(policy_stdout, stdout)
        _write_text(policy_stderr, stderr)
        policy_stdout_path = str(policy_stdout)
        policy_stderr_path = str(policy_stderr)
        if policy_exit_code != 0:
            return PipelineSchedulerRunRecord(
                run_number=run_number,
                scheduler_run_id=scheduler_run_id,
                started_at=_format_timestamp(started),
                finished_at=_format_timestamp(now_func()),
                status="failed",
                run_dir=str(run_dir),
                pipeline_output_dir=str(pipeline_output_dir),
                pipeline_summary_path=str(pipeline_summary_path),
                pipeline_health_path=str(pipeline_health_path),
                pre_pipeline_refresh_command=pre_pipeline_refresh_command,
                pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
                pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
                pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
                **monitor_fields,
                **position_review_fields,
                pipeline_command=pipeline_command,
                pipeline_exit_code=exit_code,
                pipeline_stdout_path=str(pipeline_stdout_path),
                pipeline_stderr_path=str(pipeline_stderr_path),
                committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
                committee_preset_policy_command=committee_command,
                committee_preset_policy_exit_code=committee_exit_code,
                committee_preset_policy_stdout_path=committee_stdout_path,
                committee_preset_policy_stderr_path=committee_stderr_path,
                scheduler_policy_path=str(scheduler_policy_path),
                scheduler_policy_command=policy_command,
                scheduler_policy_exit_code=policy_exit_code,
                scheduler_policy_stdout_path=policy_stdout_path,
                scheduler_policy_stderr_path=policy_stderr_path,
                account_refresh_command=refresh_command,
                **scheduler_review_bundle_fields,
                blocker="scheduler_policy_command_failed",
                resource_controls=resource_controls,
            )

    refresh_exit_code: int | None = None
    refresh_stdout_path = ""
    refresh_stderr_path = ""
    if refresh_command:
        refresh_stdout = run_dir / "account_refresh_stdout.txt"
        refresh_stderr = run_dir / "account_refresh_stderr.txt"
        refresh_exit_code, stdout, stderr = command_runner(refresh_command)
        _write_text(refresh_stdout, stdout)
        _write_text(refresh_stderr, stderr)
        refresh_stdout_path = str(refresh_stdout)
        refresh_stderr_path = str(refresh_stderr)
        if refresh_exit_code != 0:
            return PipelineSchedulerRunRecord(
                run_number=run_number,
                scheduler_run_id=scheduler_run_id,
                started_at=_format_timestamp(started),
                finished_at=_format_timestamp(now_func()),
                status="failed",
                run_dir=str(run_dir),
                pipeline_output_dir=str(pipeline_output_dir),
                pipeline_summary_path=str(pipeline_summary_path),
                pipeline_health_path=str(pipeline_health_path),
                pre_pipeline_refresh_command=pre_pipeline_refresh_command,
                pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
                pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
                pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
                **monitor_fields,
                **position_review_fields,
                pipeline_command=pipeline_command,
                pipeline_exit_code=exit_code,
                pipeline_stdout_path=str(pipeline_stdout_path),
                pipeline_stderr_path=str(pipeline_stderr_path),
                committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
                committee_preset_policy_command=committee_command,
                committee_preset_policy_exit_code=committee_exit_code,
                committee_preset_policy_stdout_path=committee_stdout_path,
                committee_preset_policy_stderr_path=committee_stderr_path,
                scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
                scheduler_policy_command=policy_command,
                scheduler_policy_exit_code=policy_exit_code,
                scheduler_policy_stdout_path=policy_stdout_path,
                scheduler_policy_stderr_path=policy_stderr_path,
                account_refresh_command=refresh_command,
                account_refresh_exit_code=refresh_exit_code,
                account_refresh_stdout_path=refresh_stdout_path,
                account_refresh_stderr_path=refresh_stderr_path,
                **scheduler_review_bundle_fields,
                blocker="account_refresh_command_failed",
                resource_controls=resource_controls,
            )

    return PipelineSchedulerRunRecord(
        run_number=run_number,
        scheduler_run_id=scheduler_run_id,
        started_at=_format_timestamp(started),
        finished_at=_format_timestamp(now_func()),
        status="completed",
        run_dir=str(run_dir),
        pipeline_output_dir=str(pipeline_output_dir),
        pipeline_summary_path=str(pipeline_summary_path),
        pipeline_health_path=str(pipeline_health_path),
        pre_pipeline_refresh_command=pre_pipeline_refresh_command,
        pre_pipeline_refresh_exit_code=pre_refresh_exit_code,
        pre_pipeline_refresh_stdout_path=pre_refresh_stdout_path,
        pre_pipeline_refresh_stderr_path=pre_refresh_stderr_path,
        **monitor_fields,
        **position_review_fields,
        pipeline_command=pipeline_command,
        pipeline_exit_code=exit_code,
        pipeline_stdout_path=str(pipeline_stdout_path),
        pipeline_stderr_path=str(pipeline_stderr_path),
        committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
        committee_preset_policy_command=committee_command,
        committee_preset_policy_exit_code=committee_exit_code,
        committee_preset_policy_stdout_path=committee_stdout_path,
        committee_preset_policy_stderr_path=committee_stderr_path,
        scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
        scheduler_policy_command=policy_command,
        scheduler_policy_exit_code=policy_exit_code,
        scheduler_policy_stdout_path=policy_stdout_path,
        scheduler_policy_stderr_path=policy_stderr_path,
        account_refresh_command=refresh_command,
        account_refresh_exit_code=refresh_exit_code,
        account_refresh_stdout_path=refresh_stdout_path,
        account_refresh_stderr_path=refresh_stderr_path,
        post_run_verification_path=str(post_run_verification_path) if post_run_verification_command else "",
        post_run_verification_command=post_run_verification_command,
        **scheduler_review_bundle_fields,
        resource_controls=resource_controls,
    )


def derive_scheduler_resource_controls(pipeline_command: str) -> dict[str, object]:
    """Summarize provider and batch caps visible in a rendered pipeline command."""
    tokens = _split_command(pipeline_command)
    provider_mode = "free_or_skip_grok"
    if "--perplexity-research" in tokens:
        provider_mode = "perplexity"
    elif "--xai-grok" in tokens:
        provider_mode = "xai_grok"
    elif "--skip-grok" in tokens:
        provider_mode = "free_or_skip_grok"

    research_max_pass_count = _int_flag(tokens, "--research-max-pass-count")
    generated_committee_batches = "--run-generated-committee-batches" in tokens
    generated_committee_max_batches = _int_flag(tokens, "--generated-committee-max-batches")
    final_planning_refresh = "--final-planning-refresh" in tokens
    final_planning_timeout_seconds = _float_flag(tokens, "--final-planning-timeout-seconds")
    portfolio_news_followup_batches = "--portfolio-news-followup-batches" in tokens
    portfolio_news_followup_batch_size = _int_flag(tokens, "--portfolio-news-followup-batch-size")
    portfolio_news_followup_committee_batches = "--run-portfolio-news-followup-committee-batches" in tokens
    portfolio_news_followup_max_batches = _int_flag(tokens, "--portfolio-news-followup-max-batches")
    paid_provider_enabled = provider_mode in {"perplexity", "xai_grok"}
    missing_bounds: list[str] = []
    if paid_provider_enabled and research_max_pass_count is None:
        missing_bounds.append("research_max_pass_count")
    if generated_committee_batches and generated_committee_max_batches is None:
        missing_bounds.append("generated_committee_max_batches")
    if final_planning_refresh and final_planning_timeout_seconds is None:
        missing_bounds.append("final_planning_timeout_seconds")
    if portfolio_news_followup_committee_batches and portfolio_news_followup_max_batches is None:
        missing_bounds.append("portfolio_news_followup_max_batches")

    return {
        "schema_version": 1,
        "provider_mode": provider_mode,
        "paid_provider_enabled": paid_provider_enabled,
        "research_source": _string_flag(tokens, "--research-source") or "",
        "research_source_file_present": "--research-source-file" in tokens,
        "research_source_url_present": "--research-source-url" in tokens,
        "research_campaign_dir_present": "--research-campaign-dir" in tokens,
        "research_resume": "--research-resume" in tokens,
        "research_run_until": _string_flag(tokens, "--research-run-until") or "",
        "research_max_pass_count": research_max_pass_count,
        "research_evidence_batch_size": _int_flag(tokens, "--research-evidence-batch-size"),
        "research_max_evidence_batches": _int_flag(tokens, "--research-max-evidence-batches"),
        "research_rate_limit_batch_size": _int_flag(tokens, "--research-rate-limit-batch-size"),
        "research_rate_limit_pause_seconds": _float_flag(tokens, "--research-rate-limit-pause-seconds"),
        "polygon_news": "--polygon-news" in tokens,
        "perplexity_search_context_size": (
            _string_flag(tokens, "--perplexity-search-context-size") if provider_mode == "perplexity" else ""
        ),
        "perplexity_max_tokens": _int_flag(tokens, "--perplexity-max-tokens") if provider_mode == "perplexity" else None,
        "perplexity_credits_purchased_to_date": (
            _float_flag(tokens, "--perplexity-credits-purchased-to-date") if provider_mode == "perplexity" else None
        ),
        "generated_committee_batches": generated_committee_batches,
        "generated_committee_max_batches": generated_committee_max_batches,
        "portfolio_news_followup_batches": portfolio_news_followup_batches,
        "portfolio_news_followup_batch_size": portfolio_news_followup_batch_size,
        "portfolio_news_followup_committee_batches": portfolio_news_followup_committee_batches,
        "portfolio_news_followup_max_batches": portfolio_news_followup_max_batches,
        "final_planning_refresh": final_planning_refresh,
        "final_planning_timeout_seconds": final_planning_timeout_seconds,
        "bounded": not missing_bounds,
        "bounded_reason": "explicit_caps_present" if not missing_bounds else "missing_" + "_and_".join(missing_bounds),
        "estimated_cost_usd": "unknown",
    }


def _prepare_pipeline_command(command_template: str, context: dict[str, str]) -> str:
    command = _render_command(command_template, context)
    if "--summary-output" not in command:
        command = f"{command} --summary-output {context['pipeline_summary']}"
    if "--rules-path" not in command:
        command = f"{command} --rules-path {context['rules_path']}"
    if "{" in command or "}" in command:
        raise ValueError(f"Unresolved placeholder in scheduler command: {command}")
    return command


def _split_command(command: str) -> list[str]:
    try:
        return [token.strip('"') for token in shlex.split(command, posix=False)]
    except ValueError:
        return command.split()


def _string_flag(tokens: list[str], flag: str) -> str | None:
    try:
        index = tokens.index(flag)
    except ValueError:
        return None
    next_index = index + 1
    if next_index >= len(tokens):
        return None
    value = tokens[next_index]
    if value.startswith("--"):
        return None
    return value


def _int_flag(tokens: list[str], flag: str) -> int | None:
    value = _string_flag(tokens, flag)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _float_flag(tokens: list[str], flag: str) -> float | None:
    value = _string_flag(tokens, flag)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _prepare_account_refresh_command(
    command_template: str,
    context: dict[str, str],
    *,
    include_scheduler_policy: bool,
    include_committee_preset_policy: bool,
) -> str:
    command = _render_command(command_template, context)
    if include_scheduler_policy and "--scheduler-policy" not in command:
        command = f"{command} --scheduler-policy {context['scheduler_policy']}"
    if include_committee_preset_policy and "--committee-preset-policy" not in command:
        command = f"{command} --committee-preset-policy {context['committee_preset_policy']}"
    if "{" in command or "}" in command:
        raise ValueError(f"Unresolved placeholder in scheduler command: {command}")
    return command


def _render_command(command_template: str, context: dict[str, str]) -> str:
    try:
        command = command_template.format_map(context)
    except KeyError as exc:
        raise ValueError(f"Unknown scheduler command placeholder: {exc.args[0]}") from exc
    if "{" in command or "}" in command:
        raise ValueError(f"Unresolved placeholder in scheduler command: {command}")
    return command


def _render_context(
    *,
    run_dir: Path,
    pipeline_output_dir: Path,
    pipeline_summary_path: Path,
    pipeline_health_path: Path,
    portfolio_state_path: Path,
    portfolio_news_monitor_path: Path,
    position_review_queue_path: Path,
    committee_preset_policy_path: Path,
    scheduler_policy_path: Path,
    account_refresh_output_dir: Path,
    post_run_verification_path: Path,
    scheduler_review_bundle_output_dir: Path,
    scheduler_review_bundle_path: Path,
    dashboard_review_gates_manifest_path: Path,
    paper_submit_mode_plan_path: Path,
    dashboard_manifest_path: Path,
    dashboard_site_output_dir: Path,
    scheduler_run_id: str,
    rules_path: Path,
    scheduler_summary_path: Path,
    scheduler_policy_state_path: Path,
) -> dict[str, str]:
    return {
        "run_dir": _quote(run_dir),
        "pipeline_output_dir": _quote(pipeline_output_dir),
        "pipeline_summary": _quote(pipeline_summary_path),
        "pipeline_health": _quote(pipeline_health_path),
        "portfolio_state": _quote(portfolio_state_path),
        "portfolio_news_monitor": _quote(portfolio_news_monitor_path),
        "position_review_queue": _quote(position_review_queue_path),
        "committee_preset_policy": _quote(committee_preset_policy_path),
        "scheduler_policy": _quote(scheduler_policy_path),
        "account_refresh_output_dir": _quote(account_refresh_output_dir),
        "post_run_verification": _quote(post_run_verification_path),
        "scheduler_review_bundle_output_dir": _quote(scheduler_review_bundle_output_dir),
        "scheduler_review_bundle": _quote(scheduler_review_bundle_path),
        "dashboard_review_gates_manifest": _quote(dashboard_review_gates_manifest_path),
        "paper_submit_mode_plan": _quote(paper_submit_mode_plan_path),
        "dashboard_manifest": _quote(dashboard_manifest_path),
        "dashboard_site_output_dir": _quote(dashboard_site_output_dir),
        "scheduler_run_id": scheduler_run_id,
        "rules_path": _quote(rules_path),
        "scheduler_summary": _quote(scheduler_summary_path),
        "scheduler_policy_state": _quote(scheduler_policy_state_path),
    }


def _blocked_record(
    *,
    run_number: int,
    started: datetime,
    finished: datetime,
    run_dir: Path,
    rules_path: Path,
    blocker: str,
) -> PipelineSchedulerRunRecord:
    scheduler_run_id = _scheduler_run_id(started, run_number)
    return PipelineSchedulerRunRecord(
        run_number=run_number,
        scheduler_run_id=scheduler_run_id,
        started_at=_format_timestamp(started),
        finished_at=_format_timestamp(finished),
        status="failed",
        run_dir=str(run_dir),
        pipeline_output_dir=str(run_dir / "pipeline"),
        pipeline_summary_path=str(run_dir / "pipeline_summary.json"),
        pipeline_health_path=str(run_dir / "pipeline_artifact_health.json"),
        pipeline_command=f"blocked_before_render rules_path={rules_path}",
        blocker=blocker,
    )


def _build_summary(output_dir: Path, records: list[PipelineSchedulerRunRecord]) -> PipelineSchedulerSummary:
    error_count = len([record for record in records if record.status == "failed"])
    success_count = len([record for record in records if record.status == "completed"])
    if records and all(record.status == "planned" for record in records):
        status = "planned"
        next_safe_action = "review_scheduler_plan_before_running_pipeline"
    elif error_count:
        status = "failed"
        next_safe_action = "inspect_failed_scheduler_run_before_continuing"
    else:
        status = "completed"
        next_safe_action = "review_pipeline_and_dashboard_artifacts_before_any_supervised_submit"
    return PipelineSchedulerSummary(
        schema_version=1,
        mode="research_to_paper_pipeline_scheduler",
        status=status,
        order_submission_enabled=False,
        output_dir=str(output_dir),
        run_count=len(records),
        success_count=success_count,
        error_count=error_count,
        runs=records,
        next_safe_action=next_safe_action,
    )


def _update_scheduler_policy_state_after_record(
    path: Path,
    *,
    record: PipelineSchedulerRunRecord,
    rules_path: Path,
) -> None:
    if record.status != "completed" and record.portfolio_news_monitor_exit_code != 0:
        return
    state = _load_json_dict(path)
    state["schema_version"] = 1
    state["updated_at"] = record.finished_at
    state["active_rules_sha256"] = hashlib.sha256(rules_path.read_bytes()).hexdigest()
    if record.portfolio_news_monitor_exit_code == 0:
        state["last_news_monitor_at"] = record.finished_at
    if record.position_review_queue_exit_code == 0:
        state["last_position_review_at"] = record.finished_at
    if record.status != "completed":
        _write_json(path, state)
        return
    if record.pipeline_exit_code == 0:
        state["last_no_submit_preflight_at"] = record.finished_at
    if record.account_refresh_exit_code == 0:
        state["last_account_refresh_at"] = record.finished_at
    if _pipeline_summary_has_successful_final_planning(record.pipeline_summary_path):
        state["last_final_planning_at"] = record.finished_at
    if _pipeline_summary_has_successful_followup_batch_split(record.pipeline_summary_path):
        state["last_followup_batch_split_at"] = record.finished_at
    if _pipeline_summary_has_successful_followup_committee(record.pipeline_summary_path):
        state["last_followup_committee_at"] = record.finished_at
    _write_json(path, state)


def _run_post_run_verification_after_summary(
    record: PipelineSchedulerRunRecord,
    *,
    command_runner: CommandRunner,
    now_func: NowFunc,
) -> PipelineSchedulerRunRecord:
    if record.status != "completed" or not record.post_run_verification_command:
        return record
    run_dir = Path(record.run_dir)
    stdout_path = run_dir / "post_run_verification_stdout.txt"
    stderr_path = run_dir / "post_run_verification_stderr.txt"
    exit_code, stdout, stderr = command_runner(record.post_run_verification_command)
    _write_text(stdout_path, stdout)
    _write_text(stderr_path, stderr)
    status = "completed" if exit_code == 0 else "failed"
    blocker = "" if exit_code == 0 else "post_run_verification_command_failed"
    return replace(
        record,
        finished_at=_format_timestamp(now_func()),
        status=status,
        blocker=blocker,
        post_run_verification_exit_code=exit_code,
        post_run_verification_stdout_path=str(stdout_path),
        post_run_verification_stderr_path=str(stderr_path),
    )


def _run_scheduler_review_bundle_after_verification(
    record: PipelineSchedulerRunRecord,
    *,
    command_runner: CommandRunner,
    now_func: NowFunc,
) -> PipelineSchedulerRunRecord:
    if record.status != "completed" or not record.scheduler_review_bundle_command:
        return record
    if record.post_run_verification_exit_code != 0:
        return record
    run_dir = Path(record.run_dir)
    stdout_path = run_dir / "scheduler_review_bundle_stdout.txt"
    stderr_path = run_dir / "scheduler_review_bundle_stderr.txt"
    exit_code, stdout, stderr = command_runner(record.scheduler_review_bundle_command)
    _write_text(stdout_path, stdout)
    _write_text(stderr_path, stderr)
    status = "completed" if exit_code == 0 else "failed"
    blocker = "" if exit_code == 0 else "scheduler_review_bundle_command_failed"
    return replace(
        record,
        finished_at=_format_timestamp(now_func()),
        status=status,
        blocker=blocker,
        scheduler_review_bundle_exit_code=exit_code,
        scheduler_review_bundle_stdout_path=str(stdout_path),
        scheduler_review_bundle_stderr_path=str(stderr_path),
    )


def _pipeline_summary_has_successful_final_planning(path_value: str) -> bool:
    payload = _load_json_dict(Path(path_value))
    if str(payload.get("status") or "") != "completed":
        return False
    if _int_value(payload.get("blocker_count")) != 0:
        return False
    passed_stage_ids = {
        str(stage.get("stage_id") or "")
        for stage in payload.get("stages") or []
        if isinstance(stage, dict) and str(stage.get("status") or "") in {"passed", "completed"}
    }
    return {"final_planning_refresh", "extract_final_action_plan"}.issubset(passed_stage_ids)


def _pipeline_summary_has_successful_followup_batch_split(path_value: str) -> bool:
    payload = _load_json_dict(Path(path_value))
    if str(payload.get("status") or "") != "completed":
        return False
    if _int_value(payload.get("blocker_count")) != 0:
        return False
    return any(
        isinstance(stage, dict)
        and str(stage.get("stage_id") or "") == "portfolio_news_followup_batch_split"
        and str(stage.get("status") or "") in {"passed", "completed"}
        for stage in payload.get("stages") or []
    )


def _pipeline_summary_has_successful_followup_committee(path_value: str) -> bool:
    payload = _load_json_dict(Path(path_value))
    if str(payload.get("status") or "") != "completed":
        return False
    if _int_value(payload.get("blocker_count")) != 0:
        return False
    for stage in payload.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("stage_id") or "") != "portfolio_news_followup_committee_batches":
            continue
        if str(stage.get("status") or "") not in {"passed", "completed"}:
            continue
        artifact_paths = stage.get("artifact_paths") or {}
        if not isinstance(artifact_paths, dict):
            return False
        summary = _load_json_dict(
            Path(str(artifact_paths.get("portfolio_news_followup_committee_batch_run_summary") or ""))
        )
        if not summary:
            return False
        if str(summary.get("status") or "") not in {"completed", "partial"}:
            return False
        if _int_value(summary.get("failed_count")) != 0:
            return False
        processed = _int_value(summary.get("completed_count")) + _int_value(summary.get("skipped_count"))
        return processed > 0
    return False


def _load_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _int_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summary_output_path(inputs: PipelineSchedulerInputs, output_dir: Path) -> Path:
    return Path(inputs.summary_output) if inputs.summary_output else output_dir / "pipeline_scheduler_summary.json"


def _scheduler_run_id(started: datetime, run_number: int) -> str:
    return f"pipeline_scheduler_{started.strftime('%Y%m%dT%H%M%SZ')}_run{run_number:03d}"


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_flag(command: str, flag: str) -> None:
    if flag not in command:
        raise ValueError(f"Scheduler command must include {flag}.")


def _run_command(command: str) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        cwd=TRADING_AGENT_DIR,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _quote(value: str | Path) -> str:
    return subprocess.list2cmdline([str(value)])


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


__all__ = [
    "PipelineSchedulerConfig",
    "PipelineSchedulerInputs",
    "PipelineSchedulerRunRecord",
    "PipelineSchedulerSummary",
    "derive_scheduler_resource_controls",
    "run_pipeline_scheduler",
    "validate_scheduler_command_template",
    "write_pipeline_scheduler_summary",
]
