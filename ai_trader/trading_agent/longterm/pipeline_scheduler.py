"""Recurring no-submit scheduler for the research-to-paper pipeline."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class PipelineSchedulerInputs:
    """Static scheduler inputs."""

    output_dir: str | Path
    pipeline_command_template: str
    rules_path: str | Path
    pre_pipeline_refresh_command_template: str = ""
    committee_preset_policy_command_template: str = ""
    scheduler_policy_command_template: str = ""
    account_refresh_command_template: str = ""
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
    blocker: str = ""


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
    committee_preset_policy_path = run_dir / "committee_preset_policy.json"
    scheduler_policy_path = run_dir / "scheduler_policy.json"
    account_refresh_output_dir = run_dir / "paper_account_refresh"
    dashboard_manifest_path = run_dir / "dashboard_manifest.json"
    dashboard_site_output_dir = run_dir / "operator_dashboard_site"
    context = _render_context(
        run_dir=run_dir,
        pipeline_output_dir=pipeline_output_dir,
        pipeline_summary_path=pipeline_summary_path,
        pipeline_health_path=pipeline_health_path,
        portfolio_state_path=portfolio_state_path,
        committee_preset_policy_path=committee_preset_policy_path,
        scheduler_policy_path=scheduler_policy_path,
        account_refresh_output_dir=account_refresh_output_dir,
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
            pipeline_command=pipeline_command,
            committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
            committee_preset_policy_command=committee_command,
            scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
            scheduler_policy_command=policy_command,
            account_refresh_command=refresh_command,
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
                pipeline_command=pipeline_command,
                committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
                committee_preset_policy_command=committee_command,
                scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
                scheduler_policy_command=policy_command,
                account_refresh_command=refresh_command,
                blocker="pre_pipeline_refresh_command_failed",
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
            pipeline_command=pipeline_command,
            pipeline_exit_code=exit_code,
            pipeline_stdout_path=str(pipeline_stdout_path),
            pipeline_stderr_path=str(pipeline_stderr_path),
            committee_preset_policy_path=str(committee_preset_policy_path) if committee_command else "",
            committee_preset_policy_command=committee_command,
            scheduler_policy_path=str(scheduler_policy_path) if policy_command else "",
            scheduler_policy_command=policy_command,
            account_refresh_command=refresh_command,
            blocker="pipeline_command_failed",
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
                blocker="committee_preset_policy_command_failed",
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
                blocker="scheduler_policy_command_failed",
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
                blocker="account_refresh_command_failed",
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
    )


def _prepare_pipeline_command(command_template: str, context: dict[str, str]) -> str:
    command = _render_command(command_template, context)
    if "--summary-output" not in command:
        command = f"{command} --summary-output {context['pipeline_summary']}"
    if "--rules-path" not in command:
        command = f"{command} --rules-path {context['rules_path']}"
    if "{" in command or "}" in command:
        raise ValueError(f"Unresolved placeholder in scheduler command: {command}")
    return command


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
    committee_preset_policy_path: Path,
    scheduler_policy_path: Path,
    account_refresh_output_dir: Path,
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
        "committee_preset_policy": _quote(committee_preset_policy_path),
        "scheduler_policy": _quote(scheduler_policy_path),
        "account_refresh_output_dir": _quote(account_refresh_output_dir),
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
    completed = subprocess.run(command, shell=True, text=True, capture_output=True)
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
    "run_pipeline_scheduler",
    "validate_scheduler_command_template",
    "write_pipeline_scheduler_summary",
]
