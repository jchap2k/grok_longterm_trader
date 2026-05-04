"""Dry-run research-to-paper pipeline command planner.

This module composes existing long-term trader scripts into an auditable stage
chain. It deliberately does not implement new research, planning, or broker
submission logic.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


FORBIDDEN_COMMAND_FRAGMENTS = ("--submit-paper-orders",)


@dataclass(frozen=True)
class PipelineStage:
    """One executable or planned pipeline stage."""

    stage_id: str
    title: str
    command: str
    artifact_paths: dict[str, str] = field(default_factory=dict)
    stdout_artifact_path: str = ""


@dataclass(frozen=True)
class PipelineStageResult:
    """Result for one stage."""

    stage_id: str
    title: str
    command: str
    status: str
    exit_code: int | None = None
    log_path: str = ""
    artifact_paths: dict[str, str] = field(default_factory=dict)
    blocker: str = ""


@dataclass(frozen=True)
class PipelineRunResult:
    """Full pipeline summary."""

    schema_version: int
    mode: str
    status: str
    order_submission_enabled: bool
    stage_count: int
    blocker_count: int
    warning_count: int
    output_dir: str
    stages: list[dict[str, Any]]
    artifact_paths: dict[str, str]
    next_safe_action: str

    @property
    def stage_results(self) -> list[PipelineStageResult]:
        return [PipelineStageResult(**stage) for stage in self.stages]


CommandRunner = Callable[[str], tuple[int, str, str]]


def build_committee_batch_stages(
    *,
    committee_batch_dir: str | Path,
    output_dir: str | Path,
    journal_db: str | Path,
    portfolio_state: str | Path,
    market_regime_file: str | Path | None = None,
    motley_fool_config: str | Path | None = None,
    agent_preset: str = "decision_6",
    profile_config: str = "",
) -> list[PipelineStage]:
    """Build optional committee batch stages using the existing cycle script."""
    batch_dir = Path(committee_batch_dir)
    root = Path(output_dir)
    batches = sorted(batch_dir.glob("*.json"))
    stages: list[PipelineStage] = []
    for index, batch in enumerate(batches, start=1):
        output = root / f"committee_batch_{index:03d}_cycle.json"
        command = (
            "python scripts/run_longterm_cycle.py "
            f"--idea-batch {_quote(batch)} "
            f"--journal-db {_quote(journal_db)} "
            f"--portfolio-state {_quote(portfolio_state)} "
            f"--agent-preset {agent_preset} --quiet"
            f"{_optional_path_arg('--market-regime-file', market_regime_file)}"
            f"{_optional_path_arg('--motley-fool-config', motley_fool_config)}"
            f"{_optional_path_arg('--profile-config', profile_config)}"
        )
        stages.append(
            PipelineStage(
                stage_id=f"committee_batch_{index:03d}",
                title=f"Run committee research batch {index:03d}",
                command=command,
                artifact_paths={f"committee_batch_{index:03d}": str(output)},
                stdout_artifact_path=str(output),
            )
        )
    for stage in stages:
        validate_stage_command(stage)
    return stages


def build_final_planning_refresh_stage(
    *,
    output_dir: str | Path,
    journal_db: str | Path,
    portfolio_state: str | Path,
    market_regime_file: str | Path | None = None,
    motley_fool_config: str | Path | None = None,
    agent_preset: str = "decision_6",
    profile_config: str = "",
    active_sleeve_value: float | None = None,
    available_cash: float | None = None,
) -> PipelineStage:
    """Build a final empty-cycle planning refresh stage."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    empty_batch = root / "empty_idea_batch.json"
    if not empty_batch.exists():
        empty_batch.write_text("[]", encoding="utf-8")
    output = root / "final_planning_refresh.json"
    command = (
        "python scripts/run_longterm_cycle.py "
        f"--idea-batch {_quote(empty_batch)} "
        f"--journal-db {_quote(journal_db)} "
        f"--portfolio-state {_quote(portfolio_state)} "
        f"--agent-preset {agent_preset} --quiet"
        f"{_optional_path_arg('--market-regime-file', market_regime_file)}"
        f"{_optional_path_arg('--motley-fool-config', motley_fool_config)}"
        f"{_optional_path_arg('--profile-config', profile_config)}"
        f"{_optional_number_arg('--active-sleeve-value', active_sleeve_value)}"
        f"{_optional_number_arg('--available-cash', available_cash)}"
    )
    stage = PipelineStage(
        stage_id="final_planning_refresh",
        title="Run final planning refresh through existing cycle orchestration",
        command=command,
        artifact_paths={"empty_idea_batch": str(empty_batch), "final_planning_refresh": str(output)},
        stdout_artifact_path=str(output),
    )
    validate_stage_command(stage)
    return stage


def build_paper_preflight_stages(
    *,
    output_dir: str | Path,
    rules_path: str | Path,
    action_plan: str | Path,
    portfolio_state: str | Path,
    journal_db: str | Path,
    ledger_db: str | Path,
    price_map: str | Path | None = None,
    expected_cash: float | None = None,
    profile_config: str = "",
    skip_price_map: bool = False,
) -> list[PipelineStage]:
    """Build safe Stage 6B preflight commands from an existing action plan."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    action_plan = Path(action_plan)
    portfolio_state = Path(portfolio_state)
    journal_db = Path(journal_db)
    ledger_db = Path(ledger_db)
    rules_path = Path(rules_path)
    price_map_path = Path(price_map) if price_map else root / "paper_price_map.json"
    candidate_plan = root / "stage6b_submit_candidates.json"
    preview = root / "paper_preview.json"
    workflow_smoke = root / "paper_workflow_smoke.json"
    smoke_readiness = root / "paper_smoke_readiness.json"
    runbook_dir = root / "paper_runbook"
    runbook_report = root / "paper_runbook.json"
    runbook_check = runbook_dir / "paper_runbook_check.json"
    monday_check = runbook_dir / "paper_monday_operator_check.json"
    status_refresh = runbook_dir / "paper_order_status_refresh.json"
    lifecycle = runbook_dir / "paper_lifecycle.json"
    observed = runbook_dir / "paper_trading_observed.json"
    live_bundle = runbook_dir / "live_readiness_bundle.json"
    operator_bundle = runbook_dir / "operator_status_bundle.json"
    profile_arg = f" --profile-config {_quote(profile_config)}" if profile_config else ""
    expected_cash_arg = f" --expected-cash {_format_number(expected_cash)}" if expected_cash is not None else ""

    stages = [
        PipelineStage(
            stage_id="preflight_rules",
            title="Verify active long-term rules file",
            command=(
                "python -c "
                + _quote(
                    "from pathlib import Path; import sys; "
                    f"p=Path(r'{rules_path}'); "
                    "print(p); sys.exit(0 if p.exists() else 2)"
                )
            ),
            artifact_paths={"rules_path": str(rules_path)},
        ),
        PipelineStage(
            stage_id="filter_action_plan",
            title="Filter action plan to Stage 6B simple BUY candidates",
            command=(
                "python scripts/longterm_action_plan_filter.py "
                f"--action-plan {_quote(action_plan)} --output {_quote(candidate_plan)} --json"
            ),
            artifact_paths={"candidate_action_plan": str(candidate_plan)},
        ),
    ]
    if not skip_price_map:
        stages.append(
            PipelineStage(
                stage_id="price_map",
                title="Build explicit paper price map",
                command=(
                    "python scripts/longterm_paper_price_map.py "
                    f"--action-plan {_quote(candidate_plan)} --price-map-output {_quote(price_map_path)}"
                    f"{profile_arg} --json"
                ),
                artifact_paths={"price_map": str(price_map_path)},
            )
        )
    stages.extend(
        [
            PipelineStage(
                stage_id="paper_preview",
                title="Build whole-share paper order preview",
                command=(
                    "python scripts/longterm_paper_order_preview.py "
                    f"--portfolio-state {_quote(portfolio_state)} --action-plan {_quote(candidate_plan)} "
                    f"--order-model whole_share --price-map {_quote(price_map_path)} "
                    f"--record-preview --ledger-db {_quote(ledger_db)}{profile_arg} --json"
                ),
                artifact_paths={"paper_preview": str(preview), "paper_ledger": str(ledger_db)},
                stdout_artifact_path=str(preview),
            ),
            PipelineStage(
                stage_id="workflow_smoke",
                title="Run audit-only paper workflow smoke",
                command=(
                    "python scripts/longterm_paper_workflow_smoke.py "
                    f"--journal-db {_quote(journal_db)} --ledger-db {_quote(ledger_db)} "
                    f"--portfolio-state {_quote(portfolio_state)} --action-plan {_quote(candidate_plan)} "
                    f"--report-output {_quote(workflow_smoke)}{profile_arg} --json"
                ),
                artifact_paths={"workflow_smoke": str(workflow_smoke)},
            ),
            PipelineStage(
                stage_id="paper_smoke_readiness",
                title="Build paper-smoke readiness report",
                command=(
                    "python scripts/longterm_paper_smoke_readiness.py "
                    f"--portfolio-state {_quote(portfolio_state)}{expected_cash_arg} "
                    "--required-order-model whole_share "
                    f"--workflow-smoke {_quote(workflow_smoke)} "
                    f"--report-output {_quote(smoke_readiness)} --json"
                ),
                artifact_paths={"paper_smoke_readiness": str(smoke_readiness)},
            ),
            PipelineStage(
                stage_id="paper_runbook",
                title="Generate redacted Monday paper runbook",
                command=(
                    "python scripts/longterm_paper_runbook.py "
                    f"--journal-db {_quote(journal_db)} --ledger-db {_quote(ledger_db)} "
                    f"--portfolio-state {_quote(portfolio_state)} --action-plan {_quote(candidate_plan)} "
                    f"--output-dir {_quote(runbook_dir)}{expected_cash_arg} "
                    f"--report-output {_quote(runbook_report)}{profile_arg} --json"
                ),
                artifact_paths={"paper_runbook": str(runbook_dir / "paper_runbook.json")},
            ),
            PipelineStage(
                stage_id="runbook_check",
                title="Check saved pre-submit runbook artifacts",
                command=(
                    "python scripts/longterm_paper_runbook_check.py "
                    f"--workflow-smoke {_quote(workflow_smoke)} "
                    f"--paper-smoke-readiness {_quote(smoke_readiness)} "
                    f"--action-plan {_quote(candidate_plan)} "
                    f"--report-output {_quote(runbook_check)} --json"
                ),
                artifact_paths={"runbook_check": str(runbook_check)},
            ),
            PipelineStage(
                stage_id="monday_operator_check",
                title="Build Monday operator artifact check",
                command=(
                    "python scripts/longterm_paper_monday_check.py "
                    f"--runbook {_quote(runbook_dir / 'paper_runbook.json')} "
                    f"--workflow-smoke {_quote(workflow_smoke)} "
                    f"--paper-smoke-readiness {_quote(smoke_readiness)} "
                    f"--runbook-check {_quote(runbook_check)} "
                    f"--report-output {_quote(monday_check)} --json"
                ),
                artifact_paths={"monday_operator_check": str(monday_check)},
            ),
            PipelineStage(
                stage_id="status_refresh",
                title="Refresh paper order statuses without submission",
                command=(
                    "python scripts/longterm_paper_order_status_refresh.py "
                    f"--ledger-db {_quote(ledger_db)} --report-output {_quote(status_refresh)} --json"
                ),
                artifact_paths={"status_refresh": str(status_refresh)},
            ),
            PipelineStage(
                stage_id="paper_lifecycle",
                title="Build paper lifecycle summary",
                command=(
                    "python scripts/longterm_paper_lifecycle.py "
                    f"--ledger-db {_quote(ledger_db)} --report-output {_quote(lifecycle)} --json"
                ),
                artifact_paths={"paper_lifecycle": str(lifecycle)},
            ),
            PipelineStage(
                stage_id="paper_trading_verification",
                title="Build paper trading verification evidence",
                command=(
                    "python scripts/longterm_paper_trading_verification.py "
                    f"--ledger-db {_quote(ledger_db)} --observed-output {_quote(observed)} --json"
                ),
                artifact_paths={"paper_trading_observed": str(observed)},
            ),
            PipelineStage(
                stage_id="live_readiness_bundle",
                title="Build evidence-only live readiness bundle",
                command=(
                    "python scripts/longterm_live_readiness_bundle.py "
                    f"--paper-ledger-db {_quote(ledger_db)} "
                    f"--paper-smoke-readiness {_quote(smoke_readiness)} "
                    "--required-order-model whole_share "
                    f"--report-output {_quote(live_bundle)} --json"
                ),
                artifact_paths={"live_readiness_bundle": str(live_bundle)},
            ),
            PipelineStage(
                stage_id="operator_status_bundle",
                title="Build final operator status bundle",
                command=(
                    "python scripts/longterm_operator_status_bundle.py "
                    f"--journal-db {_quote(journal_db)} "
                    f"--portfolio-state {_quote(portfolio_state)} "
                    f"--paper-ledger-db {_quote(ledger_db)} "
                    f"--action-plan {_quote(candidate_plan)} "
                    f"--price-map {_quote(price_map_path)} "
                    f"--monday-operator-check {_quote(monday_check)} "
                    f"--live-readiness-bundle {_quote(live_bundle)} "
                    f"--status-refresh {_quote(status_refresh)} "
                    f"--report-output {_quote(operator_bundle)} --json"
                ),
                artifact_paths={"operator_status_bundle": str(operator_bundle)},
            ),
        ]
    )
    for stage in stages:
        validate_stage_command(stage)
    return stages


def validate_stage_command(stage: PipelineStage) -> None:
    """Reject unsafe commands before they can be executed or printed as safe."""
    command = stage.command.lower()
    for fragment in FORBIDDEN_COMMAND_FRAGMENTS:
        if fragment in command:
            raise ValueError(f"Unsafe submit command in stage {stage.stage_id}: {fragment}")


def run_pipeline_stages(
    stages: Iterable[PipelineStage],
    *,
    output_dir: str | Path,
    summary_output: str | Path,
    print_plan_only: bool = False,
    command_runner: CommandRunner | None = None,
) -> PipelineRunResult:
    """Run or print an ordered pipeline stage list."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stage_list = list(stages)
    for stage in stage_list:
        validate_stage_command(stage)
    artifact_paths: dict[str, str] = {}
    results: list[PipelineStageResult] = []
    status = "planned" if print_plan_only else "completed"
    blocker_count = 0
    runner = command_runner or _run_command
    for index, stage in enumerate(stage_list, start=1):
        artifact_paths.update(stage.artifact_paths)
        if print_plan_only:
            results.append(
                PipelineStageResult(
                    stage_id=stage.stage_id,
                    title=stage.title,
                    command=stage.command,
                    status="planned",
                    artifact_paths=dict(stage.artifact_paths),
                )
            )
            continue
        exit_code, stdout, stderr = runner(stage.command)
        log_path = root / "logs" / f"{index:02d}_{stage.stage_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"COMMAND:\n{stage.command}\n\nEXIT_CODE:\n{exit_code}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )
        if stage.stdout_artifact_path and stdout:
            stdout_path = Path(stage.stdout_artifact_path)
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(stdout, encoding="utf-8")
        stage_status = "passed" if exit_code == 0 else "failed"
        blocker = "" if exit_code == 0 else f"stage_failed:{stage.stage_id}"
        if exit_code != 0:
            status = "failed"
            blocker_count += 1
        results.append(
            PipelineStageResult(
                stage_id=stage.stage_id,
                title=stage.title,
                command=stage.command,
                status=stage_status,
                exit_code=exit_code,
                log_path=str(log_path),
                artifact_paths=dict(stage.artifact_paths),
                blocker=blocker,
            )
        )
        if exit_code != 0:
            break
    result = PipelineRunResult(
        schema_version=1,
        mode="research_to_paper_pipeline",
        status=status,
        order_submission_enabled=False,
        stage_count=len(stage_list),
        blocker_count=blocker_count,
        warning_count=0,
        output_dir=str(root),
        stages=[asdict(item) for item in results],
        artifact_paths=artifact_paths,
        next_safe_action=_next_safe_action(status),
    )
    write_pipeline_summary(result, summary_output)
    return result


def write_pipeline_summary(result: PipelineRunResult, path: str | Path) -> None:
    """Persist a pipeline summary JSON artifact."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True), encoding="utf-8")


def _run_command(command: str) -> tuple[int, str, str]:
    completed = subprocess.run(command, shell=True, text=True, capture_output=True)
    return completed.returncode, completed.stdout, completed.stderr


def _next_safe_action(status: str) -> str:
    if status == "planned":
        return "review_pipeline_plan"
    if status == "completed":
        return "review_saved_preflight_artifacts_before_any_supervised_submit"
    return "inspect_failed_stage_log_before_continuing"


def _quote(value: str | Path) -> str:
    return subprocess.list2cmdline([str(value)])


def _optional_path_arg(flag: str, value: str | Path | None) -> str:
    if not value:
        return ""
    return f" {flag} {_quote(value)}"


def _optional_number_arg(flag: str, value: float | None) -> str:
    if value is None:
        return ""
    return f" {flag} {_format_number(value)}"


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


__all__ = [
    "PipelineRunResult",
    "PipelineStage",
    "PipelineStageResult",
    "build_committee_batch_stages",
    "build_final_planning_refresh_stage",
    "build_paper_preflight_stages",
    "run_pipeline_stages",
    "validate_stage_command",
    "write_pipeline_summary",
]
