"""Dry-run recurring scheduler for long-term research cycles."""

from __future__ import annotations

import time
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from longterm.discovery_enrichment import apply_discovery_enrichment, load_discovery_enrichment_file
from longterm.idle_cash_policy import load_market_regime_snapshot
from longterm.market_regime_snapshot import fetch_yfinance_history, build_market_regime_snapshot, market_regime_to_dict
from longterm.motley_fool_settings import load_motley_fool_capture_settings
from longterm.orchestration import run_longterm_cycle
from longterm.orchestration_cli import _load_manual_ideas
from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_url
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


@dataclass(frozen=True)
class LongTermSchedulerInputs:
    profile_config: str | Path
    idea_file: str | Path | None = None
    idea_batch: str | Path | None = None
    discovery_candidates: str | Path | None = None
    discovery_source_file: str | Path | None = None
    discovery_source_url: str = ""
    discovery_source: str = ""
    discovery_enrichment_file: str | Path | None = None
    discovery_enrichment_source: str = "local_enrichment"
    motley_fool_config: str | Path | None = None
    journal_db: str | Path | None = None
    portfolio_state: str | Path | None = None
    market_regime_file: str | Path | None = None
    auto_market_regime_snapshot: bool = False
    market_regime_output: str | Path | None = None
    agent_config: str | Path | None = None
    agent_preset: str = "decision_4"
    launch_login_if_needed: bool = False
    active_sleeve_value: float | None = None
    available_cash: float | None = None
    quiet: bool = False


@dataclass(frozen=True)
class LongTermSchedulerConfig:
    max_runs: int = 1
    interval_seconds: int = 3600
    stop_on_error: bool = True


@dataclass(frozen=True)
class LongTermSchedulerRunRecord:
    run_number: int
    started_at: str
    finished_at: str
    status: str
    capture_status: str = ""
    setup_status: str = ""
    total_idea_count: int = 0
    skipped_idea_count: int = 0
    skipped_ideas: list[dict[str, str]] = field(default_factory=list)
    deferred_research_queue: list[dict[str, Any]] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    recommendation_report_markdown: str = ""
    buy_promotion_markdown: str = ""
    next_actions_markdown: str = ""
    capital_alert_markdown: str = ""
    rebalance_markdown: str = ""
    account_action_plan: dict[str, Any] = field(default_factory=dict)
    idea_provenance_summary: dict[str, int] = field(default_factory=dict)
    packet_completeness_warnings: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class LongTermSchedulerSummary:
    status: str
    run_count: int
    success_count: int
    error_count: int
    runs: list[LongTermSchedulerRunRecord] = field(default_factory=list)


def build_cycle_kwargs(
    inputs: LongTermSchedulerInputs,
    *,
    market_regime_fetcher: Callable[[str, str], list[dict[str, Any]]] = fetch_yfinance_history,
) -> dict[str, Any]:
    """Build fresh one-cycle kwargs so portfolio/config files reload each run."""
    if inputs.market_regime_file and inputs.auto_market_regime_snapshot:
        raise ValueError("Use either market_regime_file or auto_market_regime_snapshot, not both.")
    profile = PortfolioProfile.from_file(inputs.profile_config)
    manual_ideas = _load_manual_ideas(
        str(inputs.idea_file or ""),
        str(inputs.idea_batch or ""),
    )
    discovery_candidates = _load_discovery_candidates(
        inputs.discovery_candidates,
        source_file=inputs.discovery_source_file,
        source_url=inputs.discovery_source_url,
        source=inputs.discovery_source,
        enrichment_file=inputs.discovery_enrichment_file,
        enrichment_source=inputs.discovery_enrichment_source,
    )
    settings = load_motley_fool_capture_settings(inputs.motley_fool_config)
    portfolio_state = (
        PortfolioState.from_file(inputs.portfolio_state, profile=profile)
        if inputs.portfolio_state
        else None
    )
    market_regime = _load_or_generate_market_regime(
        inputs,
        market_regime_fetcher=market_regime_fetcher,
    )
    kwargs: dict[str, Any] = {
        "profile": profile,
        "manual_ideas": manual_ideas,
        "discovery_candidates": discovery_candidates,
        "motley_fool_settings": settings,
        "journal_db_path": inputs.journal_db,
        "portfolio_state": portfolio_state,
        "market_regime": market_regime,
        "agent_preset": inputs.agent_preset,
        "launch_login_if_needed": inputs.launch_login_if_needed,
        "active_sleeve_value": inputs.active_sleeve_value,
        "available_cash": inputs.available_cash,
        "verbose": not inputs.quiet,
    }
    if inputs.agent_config:
        kwargs["agent_config_path"] = inputs.agent_config
    return kwargs


def _load_or_generate_market_regime(
    inputs: LongTermSchedulerInputs,
    *,
    market_regime_fetcher: Callable[[str, str], list[dict[str, Any]]],
) -> Any:
    if inputs.market_regime_file:
        return load_market_regime_snapshot(inputs.market_regime_file)
    if not inputs.auto_market_regime_snapshot:
        return None
    snapshot = build_market_regime_snapshot(fetch_history=market_regime_fetcher)
    if inputs.market_regime_output:
        output_path = Path(inputs.market_regime_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(market_regime_to_dict(snapshot), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def _load_discovery_candidates(
    path: str | Path | None,
    *,
    source_file: str | Path | None = None,
    source_url: str = "",
    source: str = "",
    enrichment_file: str | Path | None = None,
    enrichment_source: str = "local_enrichment",
) -> list[dict[str, Any]]:
    source_count = sum(1 for value in (path, source_file, source_url) if value)
    if source_count > 1:
        raise ValueError("Use only one of discovery_candidates, discovery_source_file, or discovery_source_url.")
    if source_file:
        if not source:
            raise ValueError("discovery_source is required when using discovery_source_file.")
        candidates = load_candidate_source_file(source_file, source=source)
    elif source_url:
        if not source:
            raise ValueError("discovery_source is required when using discovery_source_url.")
        candidates = load_candidate_source_url(source_url, source=source)
    elif path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Discovery candidates file must contain a JSON list.")
        candidates = [dict(item) for item in payload]
    else:
        candidates = []
    if enrichment_file:
        candidates = apply_discovery_enrichment(
            candidates,
            load_discovery_enrichment_file(enrichment_file),
            source=enrichment_source,
        )
    return candidates


def run_longterm_scheduler(
    *,
    inputs: LongTermSchedulerInputs,
    config: LongTermSchedulerConfig,
    cycle_func: Callable[..., Any] = run_longterm_cycle,
    sleep_func: Callable[[int], Any] = time.sleep,
    summary_output_path: str | Path | None = None,
) -> LongTermSchedulerSummary:
    """Run recurring dry-run long-term cycles with bounded, testable control."""
    records: list[LongTermSchedulerRunRecord] = []
    max_runs = max(1, int(config.max_runs or 1))

    for run_number in range(1, max_runs + 1):
        started_at = _now_iso()
        try:
            result = cycle_func(**build_cycle_kwargs(inputs))
            record = _record_from_result(run_number, started_at, result)
        except Exception as exc:
            record = LongTermSchedulerRunRecord(
                run_number=run_number,
                started_at=started_at,
                finished_at=_now_iso(),
                status="error",
                error=str(exc),
            )
            records.append(record)
            if config.stop_on_error:
                summary = _summary_from_records(records, stopped_on_error=True)
                _write_summary_output(summary, summary_output_path)
                return summary
        else:
            records.append(record)

        if run_number < max_runs:
            sleep_func(max(0, int(config.interval_seconds or 0)))

    summary = _summary_from_records(records, stopped_on_error=False)
    _write_summary_output(summary, summary_output_path)
    return summary


def _record_from_result(
    run_number: int,
    started_at: str,
    result: Any,
) -> LongTermSchedulerRunRecord:
    payload = asdict(result) if is_dataclass(result) else dict(result)
    return LongTermSchedulerRunRecord(
        run_number=run_number,
        started_at=started_at,
        finished_at=_now_iso(),
        status=str(payload.get("status") or "completed"),
        capture_status=str(payload.get("capture_status") or ""),
        setup_status=str(payload.get("setup_status") or ""),
        total_idea_count=int(payload.get("total_idea_count") or 0),
        skipped_idea_count=int(payload.get("skipped_idea_count") or 0),
        skipped_ideas=[dict(item) for item in (payload.get("skipped_ideas") or [])],
        deferred_research_queue=[dict(item) for item in (payload.get("deferred_research_queue") or [])],
        decision_ids=list(payload.get("decision_ids") or []),
        recommendation_report_markdown=str(payload.get("recommendation_report_markdown") or ""),
        buy_promotion_markdown=str(payload.get("buy_promotion_markdown") or ""),
        next_actions_markdown=str(payload.get("next_actions_markdown") or ""),
        capital_alert_markdown=str(payload.get("capital_alert_markdown") or ""),
        rebalance_markdown=str(payload.get("rebalance_markdown") or ""),
        account_action_plan=dict(payload.get("account_action_plan") or {}),
        idea_provenance_summary=dict(payload.get("idea_provenance_summary") or {}),
        packet_completeness_warnings=list(payload.get("packet_completeness_warnings") or []),
    )


def _summary_from_records(
    records: list[LongTermSchedulerRunRecord],
    *,
    stopped_on_error: bool,
) -> LongTermSchedulerSummary:
    success_count = sum(1 for record in records if record.status != "error")
    error_count = sum(1 for record in records if record.status == "error")
    if stopped_on_error and error_count:
        status = "stopped_on_error"
    elif error_count:
        status = "completed_with_errors"
    else:
        status = "completed"
    return LongTermSchedulerSummary(
        status=status,
        run_count=len(records),
        success_count=success_count,
        error_count=error_count,
        runs=records,
    )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_summary_output(
    summary: LongTermSchedulerSummary,
    output_path: str | Path | None,
) -> None:
    if not output_path:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8")
