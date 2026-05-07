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

from longterm.path_utils import (
    artifact_exists,
    artifact_is_dir,
    read_json_artifact,
    read_text_artifact,
    write_json_artifact,
)
from longterm.perplexity_research_enrichment import (
    DEFAULT_PERPLEXITY_API_URL,
    DEFAULT_PERPLEXITY_MAX_TOKENS,
    DEFAULT_PERPLEXITY_MODEL,
)


FORBIDDEN_COMMAND_FRAGMENTS = ("--submit-paper-orders",)


@dataclass(frozen=True)
class PipelineStage:
    """One executable or planned pipeline stage."""

    stage_id: str
    title: str
    command: str
    artifact_paths: dict[str, str] = field(default_factory=dict)
    stdout_artifact_path: str = ""
    timeout_seconds: float | None = None


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
    timeout_seconds: float | None = None


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
    artifact_rollup: dict[str, Any] = field(default_factory=dict)

    @property
    def stage_results(self) -> list[PipelineStageResult]:
        return [PipelineStageResult(**stage) for stage in self.stages]


CommandRunner = Callable[[str], tuple[int, str, str]]


def build_research_campaign_stages(
    *,
    output_dir: str | Path,
    source_file: str | Path | None = None,
    source_url: str | None = "",
    source: str,
    campaign_dir: str | Path,
    resume: bool = False,
    run_until: str = "research_queue_ready",
    watchlist_limit: int = 100,
    universe_batch_size: int = 50,
    top_percent: float = 10.0,
    min_pass_count: int = 10,
    max_pass_count: int = 300,
    min_coverage_percent_for_enrichment: float = 80.0,
    max_fundamental_fetches: int = 500,
    fundamental_fetch_chunk_size: int = 500,
    evidence_batch_size: int = 25,
    max_evidence_batches: int | None = None,
    rate_limit_batch_size: int = 5,
    rate_limit_pause_seconds: float = 66.0,
    campaign_batch_pause_seconds: float = 0.0,
    polygon_news: bool = False,
    news_cache_path: str | Path | None = None,
    xai_grok: bool = False,
    skip_grok: bool = True,
    perplexity_research: bool = False,
    perplexity_api_key_env: str = "PERPLEXITY_API_KEY",
    perplexity_model: str = DEFAULT_PERPLEXITY_MODEL,
    perplexity_api_url: str = DEFAULT_PERPLEXITY_API_URL,
    perplexity_timeout_seconds: float = 120.0,
    perplexity_max_tokens: int = DEFAULT_PERPLEXITY_MAX_TOKENS,
    perplexity_search_context_size: str = "low",
    perplexity_credits_purchased_to_date: float | None = None,
    selection_top_percent: float = 20.0,
    selection_min_count: int = 10,
    selection_max_count: int = 50,
    portfolio_state: str | Path | None = None,
    recent_research_symbols_file: str | Path | None = None,
    as_of_date: str = "",
    research_batch_size: int = 5,
) -> list[PipelineStage]:
    """Build upstream universe/evidence stages before committee research.

    These stages only prepare research artifacts. They do not run committee
    research, planning, broker reads, or order submission.
    """
    if bool(source_file) == bool(source_url):
        if source_file and source_url:
            raise ValueError("Provide source-file or source-url, not both.")
        raise ValueError("Provide exactly one of source-file or source-url.")
    if not source:
        raise ValueError("Research campaign source is required.")
    if xai_grok and perplexity_research:
        raise ValueError("Choose either xai_grok or perplexity_research, not both.")
    campaign_root = Path(campaign_dir)
    pipeline_root = Path(output_dir)
    selected_queue = campaign_root / "research_selection" / "research_queue_selected.json"
    committee_batch_dir = campaign_root / "committee_batches"
    campaign_state = campaign_root / "campaign_state.json"
    campaign_command = (
        "python scripts/longterm_research_automation_campaign.py "
        f"{_optional_path_arg('--source-file', source_file)}"
        f"{_optional_path_arg('--source-url', source_url)}"
        f" --source {source} "
        f"--campaign-dir {_quote(campaign_root)} "
        f"--run-until {run_until} "
        f"--watchlist-limit {int(watchlist_limit)} "
        f"--universe-batch-size {int(universe_batch_size)} "
        f"--top-percent {_format_number(top_percent)} "
        f"--min-pass-count {int(min_pass_count)} "
        f"--max-pass-count {int(max_pass_count)} "
        f"--min-coverage-percent-for-enrichment {_format_number(min_coverage_percent_for_enrichment)} "
        f"--max-fundamental-fetches {int(max_fundamental_fetches)} "
        f"--fundamental-fetch-chunk-size {int(fundamental_fetch_chunk_size)} "
        f"--evidence-batch-size {int(evidence_batch_size)} "
        f"--rate-limit-batch-size {int(rate_limit_batch_size)} "
        f"--rate-limit-pause-seconds {_format_number(rate_limit_pause_seconds)} "
        f"--campaign-batch-pause-seconds {_format_number(campaign_batch_pause_seconds)} "
        f"--selection-top-percent {_format_number(selection_top_percent)} "
        f"--selection-min-count {int(selection_min_count)} "
        f"--selection-max-count {int(selection_max_count)}"
        f"{_optional_number_arg('--max-evidence-batches', max_evidence_batches)}"
        f"{_optional_path_arg('--news-cache-path', news_cache_path)}"
        f"{_optional_path_arg('--portfolio-state', portfolio_state)}"
        f"{_optional_path_arg('--recent-research-symbols-file', recent_research_symbols_file)}"
        f"{_optional_text_arg('--as-of-date', as_of_date)}"
        f"{' --resume' if resume else ''}"
        f"{' --polygon-news' if polygon_news else ''}"
        f"{_research_provider_args(xai_grok=xai_grok, skip_grok=skip_grok, perplexity_research=perplexity_research)}"
        f"{_optional_text_arg('--perplexity-api-key-env', perplexity_api_key_env if perplexity_research else '')}"
        f"{_optional_text_arg('--perplexity-model', perplexity_model if perplexity_research else '')}"
        f"{_optional_text_arg('--perplexity-api-url', perplexity_api_url if perplexity_research else '')}"
        f"{_optional_number_arg('--perplexity-timeout-seconds', perplexity_timeout_seconds if perplexity_research else None)}"
        f"{_optional_number_arg('--perplexity-max-tokens', perplexity_max_tokens if perplexity_research else None)}"
        f"{_optional_text_arg('--perplexity-search-context-size', perplexity_search_context_size if perplexity_research else '')}"
        f"{_optional_number_arg('--perplexity-credits-purchased-to-date', perplexity_credits_purchased_to_date if perplexity_research else None)}"
    )
    split_output = pipeline_root / "research_batch_split.json"
    split_command = (
        "python scripts/longterm_research_universe.py "
        f"--research-ideas {_quote(selected_queue)} "
        f"--batch-size {int(research_batch_size)} "
        f"--output-dir {_quote(committee_batch_dir)}"
    )
    stages = [
        PipelineStage(
            stage_id="research_campaign",
            title="Prepare broad-universe research queue through evidence selection",
            command=campaign_command,
            artifact_paths={
                "research_campaign_dir": str(campaign_root),
                "research_campaign_state": str(campaign_state),
                "research_queue_selected": str(selected_queue),
            },
            stdout_artifact_path=str(campaign_root / "research_automation_campaign_stdout.json"),
        ),
        PipelineStage(
            stage_id="research_batch_split",
            title="Split selected research queue into committee batch files",
            command=split_command,
            artifact_paths={
                "committee_batch_dir": str(committee_batch_dir),
                "research_batch_split": str(split_output),
            },
            stdout_artifact_path=str(split_output),
        ),
    ]
    for stage in stages:
        validate_stage_command(stage)
    return stages


def build_generated_committee_batch_runner_stage(
    *,
    output_dir: str | Path,
    campaign_dir: str | Path | None = None,
    committee_batch_dir: str | Path | None = None,
    journal_db: str | Path,
    portfolio_state: str | Path,
    market_regime_file: str | Path | None = None,
    motley_fool_config: str | Path | None = None,
    agent_preset: str = "decision_4",
    profile_config: str | Path | None = None,
    resume: bool = True,
    max_batches: int | None = None,
) -> PipelineStage:
    """Build a runtime committee-batch runner for batches generated upstream."""
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be a positive integer when supplied.")
    pipeline_root = Path(output_dir)
    if bool(campaign_dir) == bool(committee_batch_dir):
        raise ValueError("Provide exactly one of campaign_dir or committee_batch_dir for generated committee runner.")
    campaign_root = Path(campaign_dir) if campaign_dir else Path(committee_batch_dir).parent
    batch_dir = Path(committee_batch_dir) if committee_batch_dir else campaign_root / "committee_batches"
    runner_output_dir = pipeline_root / "generated_committee_batches"
    summary_output = runner_output_dir / "committee_batch_run_summary.json"
    command = (
        "python scripts/longterm_committee_batch_runner.py "
        f"--committee-batch-dir {_quote(batch_dir)} "
        f"--output-dir {_quote(runner_output_dir)} "
        f"--journal-db {_quote(journal_db)} "
        f"--portfolio-state {_quote(portfolio_state)} "
        f"--campaign-id {_quote(campaign_root.name)} "
        f"--agent-preset {agent_preset} "
        f"--summary-output {_quote(summary_output)} "
        f"{_optional_path_arg('--market-regime-file', market_regime_file)}"
        f"{_optional_path_arg('--motley-fool-config', motley_fool_config)}"
        f"{_optional_path_arg('--profile-config', profile_config)}"
        f"{f' --max-batches {max_batches}' if max_batches is not None else ''}"
        f"{' --resume' if resume else ''} --json"
    )
    stage = PipelineStage(
        stage_id="generated_committee_batches",
        title="Run generated committee batches from selected research queue",
        command=command,
        artifact_paths={
            "committee_batch_dir": str(batch_dir),
            "generated_committee_batch_run_summary": str(summary_output),
        },
        stdout_artifact_path=str(runner_output_dir / "committee_batch_runner_stdout.json"),
    )
    validate_stage_command(stage)
    return stage


def build_committee_batch_stages(
    *,
    committee_batch_dir: str | Path,
    output_dir: str | Path,
    journal_db: str | Path,
    portfolio_state: str | Path,
    market_regime_file: str | Path | None = None,
    motley_fool_config: str | Path | None = None,
    agent_preset: str = "decision_4",
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
    agent_preset: str = "decision_4",
    profile_config: str = "",
    active_sleeve_value: float | None = None,
    available_cash: float | None = None,
    timeout_seconds: float | None = None,
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
        timeout_seconds=timeout_seconds,
    )
    validate_stage_command(stage)
    return stage


def build_final_planning_action_plan_extract_stage(
    *,
    output_dir: str | Path,
    action_plan: str | Path,
) -> PipelineStage:
    """Extract the generated account action plan for downstream preflight stages."""
    root = Path(output_dir)
    final_refresh = root / "final_planning_refresh.json"
    action_plan = Path(action_plan)
    code = (
        "import json, sys; from pathlib import Path; "
        "payload=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); "
        "plan=payload.get('account_action_plan'); "
        "assert isinstance(plan, dict), 'account_action_plan missing from final planning refresh'; "
        "out=Path(sys.argv[2]); out.parent.mkdir(parents=True, exist_ok=True); "
        "out.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding='utf-8')"
    )
    stage = PipelineStage(
        stage_id="extract_final_action_plan",
        title="Extract final planning account action plan for preflight",
        command=f"python -c {_quote(code)} {_quote(final_refresh)} {_quote(action_plan)}",
        artifact_paths={"action_plan": str(action_plan)},
    )
    validate_stage_command(stage)
    return stage


def build_portfolio_news_monitor_ingest_stage(
    *,
    portfolio_news_monitor: str | Path,
    output_dir: str | Path,
) -> PipelineStage:
    """Build a deterministic stage that validates and summarizes monitor output."""
    root = Path(output_dir)
    source = Path(portfolio_news_monitor)
    ingest = root / "portfolio_news_monitor_ingest.json"
    followup_ideas = root / "portfolio_news_followup_ideas.json"
    stage = PipelineStage(
        stage_id="ingest_portfolio_news_monitor",
        title="Ingest portfolio news monitor follow-up queue",
        command=(
            f"python {_quote(_script_path('longterm_portfolio_news_monitor_ingest.py'))} "
            f"--input {_quote(source)} "
            f"--output {_quote(ingest)} "
            f"--followup-ideas-output {_quote(followup_ideas)} "
            "--json"
        ),
        artifact_paths={
            "portfolio_news_monitor": str(source),
            "portfolio_news_monitor_ingest": str(ingest),
            "portfolio_news_followup_ideas": str(followup_ideas),
        },
        stdout_artifact_path=str(ingest),
    )
    validate_stage_command(stage)
    return stage


def build_portfolio_news_followup_batch_split_stage(
    *,
    output_dir: str | Path,
    followup_ideas: str | Path | None = None,
    batch_size: int = 3,
) -> PipelineStage:
    """Split validated portfolio-news follow-up ideas into committee batches."""
    if int(batch_size or 0) < 1:
        raise ValueError("batch_size must be a positive integer.")
    root = Path(output_dir)
    ideas = Path(followup_ideas) if followup_ideas else root / "portfolio_news_followup_ideas.json"
    batch_dir = root / "portfolio_news_followup_batches"
    split_output = root / "portfolio_news_followup_batch_split.json"
    stage = PipelineStage(
        stage_id="portfolio_news_followup_batch_split",
        title="Split portfolio news follow-up ideas into bounded committee batches",
        command=(
            f"python {_quote(_script_path('longterm_research_universe.py'))} "
            f"--research-ideas {_quote(ideas)} "
            f"--batch-size {int(batch_size)} "
            f"--output-dir {_quote(batch_dir)}"
        ),
        artifact_paths={
            "portfolio_news_followup_ideas": str(ideas),
            "portfolio_news_followup_batch_dir": str(batch_dir),
            "portfolio_news_followup_batch_split": str(split_output),
        },
        stdout_artifact_path=str(split_output),
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
    allow_existing_paper_positions: bool = False,
) -> list[PipelineStage]:
    """Build safe Stage 6B preflight commands from an existing action plan."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    action_plan = Path(action_plan)
    portfolio_state = Path(portfolio_state)
    journal_db = Path(journal_db)
    ledger_db = Path(ledger_db)
    rules_path = Path(rules_path)
    explicit_price_map = bool(price_map)
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
    existing_positions_arg = " --allow-existing-paper-positions" if allow_existing_paper_positions else ""
    existing_submissions_arg = " --allow-existing-submissions" if allow_existing_paper_positions else ""

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
                f"--action-plan {_quote(action_plan)} --output {_quote(candidate_plan)}{profile_arg} --json"
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
    elif not explicit_price_map:
        stages.append(
            PipelineStage(
                stage_id="empty_price_map",
                title="Create explicit empty price map for no-fetch execution",
                command=(
                    "python -c "
                    + _quote(
                        "from pathlib import Path; "
                        f"p=Path(r'{price_map_path}'); "
                        "p.parent.mkdir(parents=True, exist_ok=True); "
                        "p.write_text('{}', encoding='utf-8')"
                    )
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
                artifact_paths={
                    "paper_preview": str(preview),
                    "paper_ledger": str(ledger_db),
                    "price_map": str(price_map_path),
                },
                stdout_artifact_path=str(preview),
            ),
            PipelineStage(
                stage_id="workflow_smoke",
                title="Run audit-only paper workflow smoke",
                command=(
                    "python scripts/longterm_paper_workflow_smoke.py "
                    f"--journal-db {_quote(journal_db)} --ledger-db {_quote(ledger_db)} "
                    f"--portfolio-state {_quote(portfolio_state)} --action-plan {_quote(candidate_plan)} "
                    f"--price-map {_quote(price_map_path)} "
                    f"--report-output {_quote(workflow_smoke)}{profile_arg}{existing_submissions_arg} --json"
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
                    f"--report-output {_quote(smoke_readiness)}{existing_positions_arg} --json"
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
                    f"--report-output {_quote(monday_check)}{existing_positions_arg} --json"
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
                    timeout_seconds=stage.timeout_seconds,
                )
            )
            continue
        timed_out = False
        try:
            if command_runner is None:
                exit_code, stdout, stderr = _run_command(stage.command, timeout_seconds=stage.timeout_seconds)
            else:
                exit_code, stdout, stderr = command_runner(stage.command)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = _timeout_text(exc.output)
            stderr = _timeout_stderr(stage, exc)
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
        blocker = ""
        if exit_code != 0:
            blocker = f"stage_timeout:{stage.stage_id}" if timed_out else f"stage_failed:{stage.stage_id}"
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
                timeout_seconds=stage.timeout_seconds,
            )
        )
        if exit_code != 0:
            break
    artifact_rollup = build_pipeline_artifact_rollup(artifact_paths)
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
        artifact_rollup=artifact_rollup,
    )
    write_pipeline_summary(result, summary_output)
    return result


def build_pipeline_artifact_rollup(artifact_paths: Mapping[str, str]) -> dict[str, Any]:
    """Build scheduler/dashboard friendly counts from saved pipeline artifacts."""
    selected = _load_json_list(artifact_paths.get("research_queue_selected"))
    committee = _load_json_object(artifact_paths.get("generated_committee_batch_run_summary"))
    if not committee:
        committee = _load_json_object(artifact_paths.get("research_batch_split"))
    action_plan = _load_json_object(artifact_paths.get("candidate_action_plan"))
    preview = _load_json_object(artifact_paths.get("paper_preview"))
    workflow = _load_json_object(artifact_paths.get("workflow_smoke"))
    operator_status = _load_json_object(artifact_paths.get("operator_status_bundle"))
    portfolio_news_monitor = _load_json_object(artifact_paths.get("portfolio_news_monitor_ingest"))
    portfolio_news_followup_split = _load_json_object(artifact_paths.get("portfolio_news_followup_batch_split"))
    intents = [dict(item) for item in action_plan.get("intents") or [] if isinstance(item, Mapping)]
    intent_counts: dict[str, int] = {}
    allowed_count = 0
    blocked_count = 0
    for intent in intents:
        intent_type = str(intent.get("intent_type") or intent.get("action") or "UNKNOWN").upper()
        intent_counts[intent_type] = intent_counts.get(intent_type, 0) + 1
        if intent.get("allowed") is False:
            blocked_count += 1
        else:
            allowed_count += 1
    return {
        "research_selection": {
            "selected_count": len(selected),
            "selected_symbols": _symbols_from_rows(selected),
        },
        "committee_batches": {
            "batch_count": _int_value(committee.get("batch_count")),
            "completed_count": _int_value(committee.get("completed_count")),
            "failed_count": _int_value(committee.get("failed_count")),
            "skipped_count": _int_value(committee.get("skipped_count")),
            "planned_count": _int_value(committee.get("planned_count")),
            "remaining_count": _int_value(committee.get("remaining_count")),
            "status": str(committee.get("status") or "unknown"),
        },
        "action_plan": {
            "intent_count": len(intents),
            "intent_counts": intent_counts,
            "allowed_count": allowed_count,
            "blocked_count": blocked_count,
            "symbols": _symbols_from_rows(intents),
        },
        "paper_preview": {
            "preview_count": _int_value(preview.get("preview_count")),
            "ready_count": _int_value(preview.get("ready_count")),
            "blocked_count": _int_value(preview.get("blocked_count")),
        },
        "workflow_smoke": {
            "ready_count": _int_value(workflow.get("ready_count")),
            "blocked_count": _int_value(workflow.get("blocked_count")),
            "submitted_count": _int_value(workflow.get("submitted_count")),
            "excluded_count": _int_value(workflow.get("excluded_count")),
        },
        "portfolio_news_monitor": {
            "queue_count": _int_value(portfolio_news_monitor.get("queue_count")),
            "high_impact_count": _int_value(portfolio_news_monitor.get("high_impact_count")),
            "review_trigger_count": _int_value(portfolio_news_monitor.get("review_trigger_count")),
            "monitored_count": _int_value(portfolio_news_monitor.get("monitored_count")),
            "articles_checked": _int_value(portfolio_news_monitor.get("articles_checked")),
            "symbols": [
                str(symbol)
                for symbol in portfolio_news_monitor.get("symbols") or []
                if str(symbol)
            ],
            "high_impact_symbols_with_decisions": [
                str(symbol)
                for symbol in portfolio_news_monitor.get("high_impact_symbols_with_decisions") or []
                if str(symbol)
            ],
            "followup_idea_count": _int_value(portfolio_news_monitor.get("followup_idea_count")),
            "followup_symbols": [
                str(symbol)
                for symbol in portfolio_news_monitor.get("followup_symbols") or []
                if str(symbol)
            ],
            "followup_batch_count": _int_value(portfolio_news_followup_split.get("batch_count")),
            "followup_batch_total_ideas": _int_value(portfolio_news_followup_split.get("total_ideas")),
            "followup_batch_dir": str(artifact_paths.get("portfolio_news_followup_batch_dir") or ""),
            "warnings": [
                str(warning)
                for warning in portfolio_news_monitor.get("warnings") or []
                if str(warning)
            ],
            "top_triggers": [
                dict(row)
                for row in portfolio_news_monitor.get("top_triggers") or []
                if isinstance(row, Mapping)
            ],
            "status": str(portfolio_news_monitor.get("status") or "not_supplied"),
        },
        "operator": {
            "agent_next_step": operator_status.get("agent_next_step") or {},
        },
        "health": build_pipeline_artifact_health(artifact_paths),
    }


def build_pipeline_artifact_health(artifact_paths: Mapping[str, str]) -> dict[str, Any]:
    """Report whether known pipeline artifacts are present and parseable."""
    present: list[str] = []
    missing: list[str] = []
    malformed: list[dict[str, str]] = []
    empty_path: list[str] = []
    for key, raw_path in sorted(artifact_paths.items()):
        if not raw_path:
            empty_path.append(key)
            continue
        path = Path(raw_path)
        if not artifact_exists(path):
            missing.append(key)
            continue
        if artifact_is_dir(path) or path.suffix.lower() not in {".json", ".jsonl"}:
            present.append(key)
            continue
        try:
            json.loads(read_text_artifact(path))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            malformed.append({"artifact": key, "path": str(path), "error": str(exc)})
            continue
        present.append(key)
    issue_count = len(missing) + len(malformed) + len(empty_path)
    return {
        "status": "ready" if issue_count == 0 else "attention_required",
        "present_count": len(present),
        "missing_count": len(missing),
        "malformed_count": len(malformed),
        "empty_path_count": len(empty_path),
        "present": present,
        "missing": missing,
        "malformed": malformed,
        "empty_path": empty_path,
    }


def write_pipeline_summary(result: PipelineRunResult, path: str | Path) -> None:
    """Persist a pipeline summary JSON artifact."""
    write_json_artifact(path, asdict(result))


def _run_command(command: str, *, timeout_seconds: float | None = None) -> tuple[int, str, str]:
    completed = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=timeout_seconds)
    return completed.returncode, completed.stdout, completed.stderr


def _timeout_stderr(stage: PipelineStage, exc: subprocess.TimeoutExpired) -> str:
    stderr = _timeout_text(exc.stderr)
    timeout = stage.timeout_seconds if stage.timeout_seconds is not None else exc.timeout
    message = f"Stage {stage.stage_id} timed out after {_format_number(timeout)} seconds."
    return f"{stderr}\n{message}".strip()


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _next_safe_action(status: str) -> str:
    if status == "planned":
        return "review_pipeline_plan"
    if status == "completed":
        return "review_saved_preflight_artifacts_before_any_supervised_submit"
    return "inspect_failed_stage_log_before_continuing"


def _load_json_object(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not artifact_exists(target):
        return {}
    try:
        payload = read_json_artifact(target)
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _load_json_list(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    target = Path(path)
    if not artifact_exists(target):
        return []
    try:
        payload = read_json_artifact(target)
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(item) for item in payload if isinstance(item, Mapping)] if isinstance(payload, list) else []


def _symbols_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    symbols = []
    seen = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _quote(value: str | Path) -> str:
    return subprocess.list2cmdline([str(value)])


def _script_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / name


def _optional_path_arg(flag: str, value: str | Path | None) -> str:
    if not value:
        return ""
    return f" {flag} {_quote(value)}"


def _optional_number_arg(flag: str, value: float | None) -> str:
    if value is None:
        return ""
    return f" {flag} {_format_number(value)}"


def _optional_text_arg(flag: str, value: str | None) -> str:
    if not value:
        return ""
    return f" {flag} {_quote(value)}"


def _research_provider_args(*, xai_grok: bool, skip_grok: bool, perplexity_research: bool) -> str:
    if perplexity_research:
        return " --perplexity-research"
    if xai_grok:
        return " --xai-grok"
    if skip_grok:
        return " --skip-grok"
    return ""


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


__all__ = [
    "PipelineRunResult",
    "PipelineStage",
    "PipelineStageResult",
    "build_pipeline_artifact_health",
    "build_pipeline_artifact_rollup",
    "build_committee_batch_stages",
    "build_final_planning_action_plan_extract_stage",
    "build_final_planning_refresh_stage",
    "build_generated_committee_batch_runner_stage",
    "build_paper_preflight_stages",
    "build_portfolio_news_followup_batch_split_stage",
    "build_portfolio_news_monitor_ingest_stage",
    "build_research_campaign_stages",
    "run_pipeline_stages",
    "validate_stage_command",
    "write_pipeline_summary",
]
