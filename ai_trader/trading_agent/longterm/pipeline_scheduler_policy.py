"""Advisory cadence policy for the no-submit research-to-paper scheduler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.review_status import ReviewStatusBuilder, review_risk_bucket


PROTECTED_SYMBOLS = {"FXAIX"}
PANIC_REGIMES = {"equity_panic_falling_rates", "inflation_rate_shock"}


@dataclass(frozen=True)
class PipelineSchedulerPolicyConfig:
    """Configurable cadence thresholds for advisory scheduler decisions."""

    account_refresh_minutes: float = 30.0
    no_submit_preflight_hours: float = 6.0
    full_research_days: float = 7.0
    panic_min_vix: float = 30.0
    review_candidate_limit: int = 50


def build_pipeline_scheduler_policy_decision(
    *,
    rules_path: str | Path,
    now: datetime | None = None,
    market_regime: Mapping[str, Any] | None = None,
    policy_state: Mapping[str, Any] | None = None,
    pipeline_scheduler_summary: Mapping[str, Any] | None = None,
    journal_db: str | Path | None = None,
    config: PipelineSchedulerPolicyConfig | None = None,
) -> dict[str, Any]:
    """Return a read-only cadence recommendation for scheduler/dashboard review."""
    resolved_config = config or PipelineSchedulerPolicyConfig()
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    rules_hash = _rules_sha256(rules_path)
    state = dict(policy_state or {})
    regime = dict(market_regime or {})
    warnings: list[str] = []
    reasons: list[str] = []
    blockers: list[str] = []

    if state.get("active_rules_sha256") and state.get("active_rules_sha256") != rules_hash:
        warnings.append("active_rules_changed")

    review_summary = _build_review_summary(
        journal_db=journal_db,
        today=current_time,
        limit=resolved_config.review_candidate_limit,
    )
    benchmark_guard = _build_benchmark_guard(journal_db)

    recommended_mode = "account_refresh_only"
    urgency = "low"
    next_safe_action = "refresh_account_and_dashboard_artifacts"

    panic_reason = _panic_reason(regime, resolved_config)
    if panic_reason:
        recommended_mode = "panic_regime_reassessment"
        urgency = "high"
        next_safe_action = "rerun_market_regime_and_next_actions_no_submit"
        reasons.append(panic_reason)
    elif review_summary["actionable_review_count"]:
        recommended_mode = "thesis_review_refresh"
        urgency = "high" if review_summary["broken_count"] or review_summary["weakening_count"] else "medium"
        next_safe_action = "run_review_refresh_before_new_buys"
        reasons.append("review_pressure")
    elif benchmark_guard.get("should_pause_new_buys"):
        recommended_mode = "benchmark_reassessment"
        urgency = "medium"
        next_safe_action = "refresh_benchmark_outcomes_before_new_buys"
        reasons.append("benchmark_guard_paused")
    else:
        cadence_mode, cadence_reason, cadence_action = _cadence_mode(
            now=current_time,
            state=state,
            pipeline_scheduler_summary=pipeline_scheduler_summary or {},
            config=resolved_config,
        )
        recommended_mode = cadence_mode
        next_safe_action = cadence_action
        reasons.append(cadence_reason)

    return {
        "schema_version": 1,
        "mode": "pipeline_scheduler_policy",
        "order_submission_enabled": False,
        "generated_at": _format_timestamp(current_time),
        "recommended_mode": recommended_mode,
        "urgency": urgency,
        "reasons": reasons,
        "blockers": blockers,
        "warnings": warnings,
        "affected_symbols": review_summary["affected_symbols"],
        "review_summary": review_summary,
        "benchmark_guard": benchmark_guard,
        "market_regime": regime,
        "active_rules_path": str(Path(rules_path)),
        "active_rules_sha256": rules_hash,
        "policy_config": {
            "account_refresh_minutes": resolved_config.account_refresh_minutes,
            "no_submit_preflight_hours": resolved_config.no_submit_preflight_hours,
            "full_research_days": resolved_config.full_research_days,
            "panic_min_vix": resolved_config.panic_min_vix,
            "review_candidate_limit": resolved_config.review_candidate_limit,
        },
        "next_safe_action": next_safe_action,
        "notes": [
            "Advisory-only scheduler cadence policy. No broker calls or shell commands were made.",
            "Panic regime output is a reassessment trigger, not liquidation or order authorization.",
        ],
    }


def load_json_object(path: str | Path | None) -> dict[str, Any]:
    """Load an optional JSON object, returning an empty object for blank paths."""
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def write_pipeline_scheduler_policy_decision(payload: Mapping[str, Any], path: str | Path) -> None:
    """Persist a policy decision JSON artifact."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8")


def build_pipeline_scheduler_policy_state(
    decision: Mapping[str, Any],
    *,
    previous_state: Mapping[str, Any] | None = None,
    pipeline_scheduler_summary: Mapping[str, Any] | None = None,
    pipeline_summary: Mapping[str, Any] | None = None,
    mark_full_research_complete: bool = False,
) -> dict[str, Any]:
    """Build the next durable scheduler policy state artifact.

    This only records cadence metadata for future scheduler decisions. It does
    not choose or run commands, and it never authorizes broker submission.
    """
    state = dict(previous_state or {})
    generated_at = str(decision.get("generated_at") or _format_timestamp(datetime.now(timezone.utc)))
    state["schema_version"] = 1
    state["updated_at"] = generated_at
    state["active_rules_sha256"] = str(decision.get("active_rules_sha256") or state.get("active_rules_sha256") or "")

    scheduler_summary = pipeline_scheduler_summary or {}
    account_refresh_at = _latest_successful_account_refresh_at(scheduler_summary)
    if account_refresh_at:
        state["last_account_refresh_at"] = _format_timestamp(account_refresh_at)
    preflight_at = _latest_completed_run_finished_at(scheduler_summary)
    if preflight_at:
        state["last_no_submit_preflight_at"] = _format_timestamp(preflight_at)
    if mark_full_research_complete or _pipeline_summary_has_committee_research(pipeline_summary or {}):
        state["last_full_research_at"] = generated_at
    return state


def write_pipeline_scheduler_policy_state(payload: Mapping[str, Any], path: str | Path) -> None:
    """Persist the scheduler policy state JSON artifact."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8")


def _build_review_summary(
    *,
    journal_db: str | Path | None,
    today: datetime,
    limit: int,
) -> dict[str, Any]:
    empty = {
        "broken_count": 0,
        "weakening_count": 0,
        "stale_count": 0,
        "review_due_count": 0,
        "healthy_count": 0,
        "unreviewed_count": 0,
        "protected_excluded_count": 0,
        "actionable_review_count": 0,
        "affected_symbols": [],
        "items": [],
    }
    if not journal_db:
        return empty
    journal = LongTermDecisionJournal(journal_db)
    statuses = ReviewStatusBuilder(journal, today=today.date()).build(limit=limit)
    items: list[dict[str, Any]] = []
    affected_symbols: list[str] = []
    protected_excluded = 0
    counts = {
        "broken": 0,
        "weakening": 0,
        "stale": 0,
        "review_due": 0,
        "healthy": 0,
        "unreviewed": 0,
    }
    for symbol, status in sorted(statuses.items()):
        bucket = review_risk_bucket(status)
        counts[bucket] = counts.get(bucket, 0) + 1
        item = {
            "symbol": symbol,
            "bucket": bucket,
            "review_due": bool(status.get("review_due")),
            "thesis_state": status.get("thesis_state") or "",
            "review_reason": status.get("review_reason") or "",
        }
        items.append(item)
        if bucket in {"broken", "weakening", "stale", "review_due"}:
            if symbol.upper() in PROTECTED_SYMBOLS:
                protected_excluded += 1
            else:
                affected_symbols.append(symbol)
    return {
        "broken_count": counts.get("broken", 0),
        "weakening_count": counts.get("weakening", 0),
        "stale_count": counts.get("stale", 0),
        "review_due_count": counts.get("review_due", 0),
        "healthy_count": counts.get("healthy", 0),
        "unreviewed_count": counts.get("unreviewed", 0),
        "protected_excluded_count": protected_excluded,
        "actionable_review_count": len(affected_symbols),
        "affected_symbols": affected_symbols,
        "items": items,
    }


def _build_benchmark_guard(journal_db: str | Path | None) -> dict[str, Any]:
    if not journal_db:
        return {
            "evaluated": False,
            "should_pause_new_buys": False,
            "reason": "Benchmark guard not evaluated because no journal_db was supplied.",
        }
    journal = LongTermDecisionJournal(journal_db)
    summary = journal.summarize_benchmark_performance()
    result = BenchmarkGuard().evaluate(summary)
    return {
        "evaluated": True,
        "should_pause_new_buys": result.should_pause_new_buys,
        "reason": result.reason,
        "summary": summary,
    }


def _panic_reason(regime: Mapping[str, Any], config: PipelineSchedulerPolicyConfig) -> str:
    risk_regime = str(regime.get("risk_regime") or "").lower()
    if risk_regime in PANIC_REGIMES:
        return f"panic_regime:{risk_regime}"
    try:
        vix_level = float(regime.get("vix_level"))
    except (TypeError, ValueError):
        return ""
    if vix_level >= config.panic_min_vix:
        return "vix_panic_threshold"
    return ""


def _cadence_mode(
    *,
    now: datetime,
    state: Mapping[str, Any],
    pipeline_scheduler_summary: Mapping[str, Any],
    config: PipelineSchedulerPolicyConfig,
) -> tuple[str, str, str]:
    account_refresh_at = _parse_timestamp(state.get("last_account_refresh_at")) or _latest_successful_account_refresh_at(
        pipeline_scheduler_summary
    )
    preflight_at = _parse_timestamp(state.get("last_no_submit_preflight_at")) or _latest_completed_run_finished_at(
        pipeline_scheduler_summary
    )
    full_research_at = _parse_timestamp(state.get("last_full_research_at"))
    if _is_stale(account_refresh_at, now=now, max_age_seconds=config.account_refresh_minutes * 60):
        return "account_refresh_only", "account_refresh_stale", "refresh_account_and_dashboard_artifacts"
    if _is_stale(preflight_at, now=now, max_age_seconds=config.no_submit_preflight_hours * 3600):
        return "no_submit_preflight", "no_submit_preflight_stale", "run_no_submit_preflight_pipeline"
    if _is_stale(full_research_at, now=now, max_age_seconds=config.full_research_days * 86400):
        return "full_research_cycle", "full_research_stale", "run_full_research_cycle_no_submit"
    return "account_refresh_only", "dashboard_freshness_floor", "refresh_account_and_dashboard_artifacts"


def _latest_completed_run_finished_at(summary: Mapping[str, Any]) -> datetime | None:
    latest: datetime | None = None
    for run in summary.get("runs") or []:
        if not isinstance(run, Mapping) or run.get("status") != "completed":
            continue
        finished = _parse_timestamp(run.get("finished_at"))
        if finished and (latest is None or finished > latest):
            latest = finished
    return latest


def _latest_successful_account_refresh_at(summary: Mapping[str, Any]) -> datetime | None:
    latest: datetime | None = None
    for run in summary.get("runs") or []:
        if not isinstance(run, Mapping):
            continue
        if run.get("account_refresh_exit_code") != 0:
            continue
        finished = _parse_timestamp(run.get("finished_at"))
        if finished and (latest is None or finished > latest):
            latest = finished
    return latest


def _pipeline_summary_has_committee_research(summary: Mapping[str, Any]) -> bool:
    if str(summary.get("status") or "") != "completed":
        return False
    for stage in summary.get("stages") or []:
        if not isinstance(stage, Mapping) or str(stage.get("status") or "") not in {"passed", "completed"}:
            continue
        stage_id = str(stage.get("stage_id") or "")
        if stage_id == "generated_committee_batches":
            return _generated_committee_stage_completed(stage)
        if stage_id.startswith("committee_batch_"):
            return True
    return False


def _generated_committee_stage_completed(stage: Mapping[str, Any]) -> bool:
    artifact_paths = stage.get("artifact_paths") or {}
    if not isinstance(artifact_paths, Mapping):
        return False
    summary = _load_json_mapping(artifact_paths.get("generated_committee_batch_run_summary"))
    if not summary:
        return False
    if str(summary.get("status") or "") != "completed":
        return False
    if _int_value(summary.get("failed_count")) != 0:
        return False
    batch_count = _int_value(summary.get("batch_count"))
    completed_count = _int_value(summary.get("completed_count"))
    skipped_count = _int_value(summary.get("skipped_count"))
    remaining_count = _int_value(summary.get("remaining_count"))
    return batch_count > 0 and completed_count + skipped_count >= batch_count and remaining_count == 0


def _load_json_mapping(path_value: Any) -> Mapping[str, Any]:
    if not path_value:
        return {}
    try:
        path = Path(str(path_value))
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_stale(value: datetime | None, *, now: datetime, max_age_seconds: float) -> bool:
    if value is None:
        return True
    return (now - value).total_seconds() > max_age_seconds


def _rules_sha256(path: str | Path) -> str:
    rules = Path(path)
    if not rules.exists():
        raise ValueError(f"rules_path does not exist: {rules}")
    return hashlib.sha256(rules.read_bytes()).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return _normalize_datetime(value).isoformat().replace("+00:00", "Z")


__all__ = [
    "PipelineSchedulerPolicyConfig",
    "build_pipeline_scheduler_policy_decision",
    "build_pipeline_scheduler_policy_state",
    "load_json_object",
    "write_pipeline_scheduler_policy_decision",
    "write_pipeline_scheduler_policy_state",
]
