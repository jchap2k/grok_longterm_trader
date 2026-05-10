"""No-submit scheduler launch packet for operator review."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SELL_REBALANCE_INTENTS = {"SELL", "REDUCE", "REBALANCE"}
REVIEW_INTENTS = {"SELL", "REDUCE", "REBALANCE", "REVIEW", "HOLD"}
PARKING_INTENTS = {"PARK_IDLE_CASH", "PARK_DEFENSIVE_CASH"}
APPROVED_PARKING_SYMBOLS = {"SPY", "SGOV", "BIL", "IEF", "TLT"}


@dataclass(frozen=True)
class SchedulerLaunchPacketInputs:
    """Saved artifacts used to decide whether no-submit scheduler launch is review-ready."""

    scheduler_config_validation: str | Path
    scheduler_task_plan: str | Path
    scheduler_handoff: str | Path
    scheduler_task_registration: str | Path
    dashboard_manifest: str | Path
    action_plan: str | Path = ""
    stage6b_candidate_plan: str | Path = ""
    position_review_queue: str | Path = ""
    market_regime: str | Path = ""
    portfolio_news_monitor: str | Path = ""
    api_usage: str | Path = ""
    pipeline_summary: str | Path = ""
    research_queue_summary: str | Path = ""
    scheduler_soak_plan: str | Path = ""
    pipeline_scheduler_summary: str | Path = ""
    post_run_verification: str | Path = ""
    scheduler_review_bundle: str | Path = ""


def build_scheduler_launch_packet(inputs: SchedulerLaunchPacketInputs) -> dict[str, Any]:
    """Build one read-only launch packet from existing no-submit scheduler artifacts."""
    artifacts = {
        "scheduler_config_validation": _load_json_optional(inputs.scheduler_config_validation),
        "scheduler_task_plan": _load_json_optional(inputs.scheduler_task_plan),
        "scheduler_handoff": _load_json_optional(inputs.scheduler_handoff),
        "scheduler_task_registration": _load_json_optional(inputs.scheduler_task_registration),
        "dashboard_manifest": _load_json_optional(inputs.dashboard_manifest),
        "action_plan": _load_json_optional(inputs.action_plan),
        "stage6b_candidate_plan": _load_json_optional(inputs.stage6b_candidate_plan),
        "position_review_queue": _load_json_optional(inputs.position_review_queue),
        "market_regime": _load_json_optional(inputs.market_regime),
        "portfolio_news_monitor": _load_json_optional(inputs.portfolio_news_monitor),
        "api_usage": _load_json_optional(inputs.api_usage),
        "pipeline_summary": _load_json_optional(inputs.pipeline_summary),
        "research_queue_summary": _load_json_optional(inputs.research_queue_summary),
        "scheduler_soak_plan": _load_json_optional(inputs.scheduler_soak_plan),
        "pipeline_scheduler_summary": _load_json_optional(inputs.pipeline_scheduler_summary),
        "post_run_verification": _load_json_optional(inputs.post_run_verification),
        "scheduler_review_bundle": _load_json_optional(inputs.scheduler_review_bundle),
    }
    blockers: list[str] = []
    warnings: list[str] = []
    chain = _scheduler_chain(artifacts, blockers=blockers, warnings=warnings)
    sell_rebalance = _sell_rebalance_review(
        artifacts["action_plan"],
        artifacts["stage6b_candidate_plan"],
        blockers=blockers,
    )
    parking = _parking_review(artifacts["action_plan"], artifacts["stage6b_candidate_plan"], blockers=blockers)
    panic = _panic_monitor(
        artifacts["market_regime"],
        artifacts["portfolio_news_monitor"],
        artifacts["position_review_queue"],
        warnings=warnings,
    )
    provider_usage = _provider_usage_review(
        artifacts["api_usage"],
        artifacts["pipeline_summary"],
        warnings=warnings,
    )
    research_queue = _research_queue_review(
        artifacts["research_queue_summary"],
        artifacts["portfolio_news_monitor"],
        blockers=blockers,
        warnings=warnings,
    )
    scheduler_soak = _scheduler_soak_review(artifacts["scheduler_soak_plan"], blockers=blockers, warnings=warnings)
    _check_optional_scheduler_run_artifacts(artifacts, blockers=blockers, warnings=warnings)
    status = "ready_for_no_submit_launch_review" if not blockers else "blocked"
    registration_readiness = _registration_readiness(
        artifacts["scheduler_task_registration"],
        blockers=blockers,
        packet_status=status,
    )
    return {
        "schema_version": 1,
        "mode": "scheduler_launch_packet",
        "status": status,
        "generated_at": _now_iso(),
        "chain": chain,
        "sell_rebalance_review": sell_rebalance,
        "parking_review": parking,
        "panic_monitor": panic,
        "provider_usage_review": provider_usage,
        "research_queue_review": research_queue,
        "scheduler_soak_review": scheduler_soak,
        "registration_readiness": registration_readiness,
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "artifact_paths": _artifact_paths(inputs),
        "order_submission_enabled": False,
        "windows_task_registration_executed": bool(
            artifacts["scheduler_task_registration"].get("registration_executed")
        ),
        "autonomous_broker_actions_enabled": False,
        "next_safe_action": (
            "review_no_submit_launch_packet_then_optionally_register_scheduler_task"
            if not blockers
            else "resolve_no_submit_launch_packet_blockers"
        ),
        "notes": [
            "This packet is an operator review artifact only.",
            "It never submits broker orders, registers Windows tasks, or launches LLM review.",
            "Scheduler auto-start later means recurring no-submit monitoring/research, not autonomous broker actions.",
        ],
    }


def build_scheduler_launch_packet_markdown(packet: Mapping[str, Any]) -> str:
    """Render a compact human-readable packet."""
    lines = [
        "# Scheduler No-Submit Launch Packet",
        "",
        f"- Status: {packet.get('status', 'unknown')}",
        f"- Next safe action: {packet.get('next_safe_action', 'unknown')}",
        f"- Broker actions enabled: {packet.get('autonomous_broker_actions_enabled', False)}",
        "",
        "## Chain",
    ]
    for step in packet.get("chain", {}).get("steps", []):
        if not isinstance(step, Mapping):
            continue
        lines.append(f"- {step.get('name')}: {step.get('status')}")
    blockers = packet.get("blockers") or []
    lines.extend(["", "## Blockers"])
    lines.extend([f"- {item}" for item in blockers] or ["- none"])
    warnings = packet.get("warnings") or []
    lines.extend(["", "## Warnings"])
    lines.extend([f"- {item}" for item in warnings] or ["- none"])
    lines.extend(["", "## Readiness"])
    for label, key in (
        ("Provider usage", "provider_usage_review"),
        ("Research queue", "research_queue_review"),
        ("Scheduler soak", "scheduler_soak_review"),
        ("Registration readiness", "registration_readiness"),
    ):
        section = packet.get(key) if isinstance(packet.get(key), Mapping) else {}
        lines.append(f"- {label}: {section.get('status', 'unavailable')}")
    return "\n".join(lines) + "\n"


def _scheduler_chain(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    validation = artifacts["scheduler_config_validation"]
    task_plan = artifacts["scheduler_task_plan"]
    handoff = artifacts["scheduler_handoff"]
    registration = artifacts["scheduler_task_registration"]
    manifest = artifacts["dashboard_manifest"]
    steps = [
        _step("scheduler_config_validation", _ready_status(validation), bool(validation)),
        _step("scheduler_task_plan", _ready_status(task_plan), bool(task_plan)),
        _step("scheduler_handoff", _ready_status(handoff), bool(handoff)),
        _step("scheduler_task_registration", _registration_status(registration), bool(registration)),
        _step("dashboard_manifest", _manifest_status(manifest), bool(manifest)),
    ]
    if not _is_ready(validation):
        blockers.append("scheduler_config_validation_not_ready")
    if not bool(validation.get("recurring_no_submit_ready")):
        blockers.append("recurring_no_submit_readiness_not_confirmed")
    summary = validation.get("operating_mode_summary") if isinstance(validation.get("operating_mode_summary"), Mapping) else {}
    if summary.get("ready_for_unattended_no_submit") is not True:
        blockers.append("unattended_no_submit_summary_not_ready")
    if summary.get("broker_submit_boundary") not in {"blocked_by_no_submit_scheduler", None, ""}:
        blockers.append("broker_submit_boundary_not_blocked")
    if not _is_ready(task_plan):
        blockers.append("scheduler_task_plan_not_ready")
    if str(task_plan.get("profile_run_mode") or "no-submit") != "no-submit":
        blockers.append("scheduler_task_plan_not_no_submit")
    if not _is_ready(handoff) or handoff.get("ready") is not True:
        blockers.append("scheduler_handoff_not_ready")
    checks = handoff.get("checks") if isinstance(handoff.get("checks"), Mapping) else {}
    for key in (
        "scheduler_config_validation",
        "recurring_no_submit_readiness",
        "scheduler_task_plan",
        "dashboard_manifest",
        "order_submission_boundary",
    ):
        if checks.get(key) not in {"ready", None}:
            blockers.append(f"scheduler_handoff_{key}_blocked")
    if _registration_status(registration) not in {"ready_for_registration_review", "registered"}:
        blockers.append("scheduler_task_registration_not_review_ready")
    if not manifest:
        blockers.append("dashboard_manifest_missing")
    _check_submit_boundary(artifacts, blockers=blockers)
    _check_protected_symbol_leakage(artifacts, blockers=blockers)
    if registration.get("registration_executed") is True:
        warnings.append("windows_task_already_registered_review_no_submit_only")
    return {"ready": not blockers, "steps": steps}


def _sell_rebalance_review(
    action_plan: Mapping[str, Any],
    stage6b_candidate_plan: Mapping[str, Any],
    *,
    blockers: list[str],
) -> dict[str, Any]:
    source_review = [
        item for item in _intents(action_plan) if _intent_type(item) in REVIEW_INTENTS
    ]
    stage6b_leaks = [
        item for item in _intents(stage6b_candidate_plan) if _intent_type(item) in SELL_REBALANCE_INTENTS
    ]
    if stage6b_leaks:
        blockers.append("stage6b_plan_contains_sell_or_rebalance_intent")
    return {
        "source_review_intent_count": len(source_review),
        "stage6b_leak_count": len(stage6b_leaks),
        "review_only": True,
        "symbols": sorted({_symbol(item) for item in source_review if _symbol(item)}),
    }


def _parking_review(
    action_plan: Mapping[str, Any],
    stage6b_candidate_plan: Mapping[str, Any],
    *,
    blockers: list[str],
) -> dict[str, Any]:
    parking = [item for item in _intents(action_plan) if _intent_type(item) in PARKING_INTENTS]
    stage6b_parking = [item for item in _intents(stage6b_candidate_plan) if _intent_type(item) in PARKING_INTENTS]
    symbols = sorted({_symbol(item) for item in parking if _symbol(item)})
    for item in parking:
        symbol = _symbol(item)
        if symbol not in APPROVED_PARKING_SYMBOLS:
            blockers.append("parking_symbol_not_in_review_allowlist")
        risk = item.get("risk_review") if isinstance(item.get("risk_review"), Mapping) else {}
        reason = str(item.get("reason") or "")
        if not risk.get("market_regime") and "Regime=" not in reason:
            blockers.append("parking_intent_missing_market_regime_context")
        if _number(item.get("trade_value") or item.get("target_value")) <= 0:
            blockers.append("parking_intent_missing_trade_value")
    return {
        "parking_intent_count": len(parking),
        "stage6b_parking_count": len(stage6b_parking),
        "symbols": symbols,
        "review_only": True,
    }


def _panic_monitor(
    market_regime: Mapping[str, Any],
    portfolio_news_monitor: Mapping[str, Any],
    position_review_queue: Mapping[str, Any],
    *,
    warnings: list[str],
) -> dict[str, Any]:
    vix = _number(market_regime.get("vix_level"))
    regime = str(market_regime.get("risk_regime") or "").lower()
    news_triggers = int(_number(portfolio_news_monitor.get("review_trigger_count")))
    high_impact = int(_number(portfolio_news_monitor.get("high_impact_count")))
    review_count = int(_number(position_review_queue.get("review_count")))
    panic = vix >= 30 or regime in {"equity_panic_falling_rates", "inflation_rate_shock"}
    recommended = panic or news_triggers > 0 or high_impact > 0 or review_count > 0
    if panic:
        warnings.append("panic_or_high_volatility_regime_requires_review_only")
    return {
        "market_regime": regime or "unknown",
        "vix_level": vix if vix > 0 else None,
        "news_review_trigger_count": news_triggers,
        "high_impact_news_count": high_impact,
        "position_review_count": review_count,
        "off_schedule_review_recommended": recommended,
        "review_only": True,
    }


def _provider_usage_review(
    api_usage: Mapping[str, Any],
    pipeline_summary: Mapping[str, Any],
    *,
    warnings: list[str],
) -> dict[str, Any]:
    usage = api_usage or _usage_from_pipeline_summary(pipeline_summary)
    if not usage:
        warnings.append("api_usage_not_supplied_for_launch_packet")
        return {
            "status": "unavailable",
            "providers": [],
            "total_request_count": 0,
            "estimated_total_cost_usd": 0.0,
            "tier_tracking": {},
            "next_safe_action": "review_api_usage_before_large_paid_enrichment_runs",
        }
    providers = _usage_providers(usage)
    request_count = int(sum(_number(item.get("request_count")) for item in providers))
    estimated_cost = round(sum(_usage_cost(item) for item in providers), 4)
    totals = usage.get("totals") if isinstance(usage.get("totals"), Mapping) else {}
    if totals:
        request_count = int(_number(totals.get("request_count")) or request_count)
        estimated_cost = round(_usage_cost(totals) or estimated_cost, 4)
    tier_tracking = _tier_tracking(providers, usage)
    return {
        "status": "tracked",
        "providers": [str(item.get("provider") or item.get("model_provider") or "unknown") for item in providers],
        "total_request_count": request_count,
        "estimated_total_cost_usd": estimated_cost,
        "tier_tracking": tier_tracking,
        "order_submission_enabled": False,
        "next_safe_action": "review_provider_usage_and_tier_progress_before_large_runs",
    }


def _usage_from_pipeline_summary(pipeline_summary: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("api_usage", "research_model_usage"):
        value = pipeline_summary.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _usage_providers(usage: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    providers = usage.get("providers")
    if isinstance(providers, list):
        return [item for item in providers if isinstance(item, Mapping)]
    if any(
        key in usage
        for key in ("provider", "model_provider", "request_count", "estimated_cost_usd", "estimated_total_cost_usd")
    ):
        return [usage]
    return []


def _usage_cost(usage: Mapping[str, Any]) -> float:
    explicit = _number(usage.get("estimated_total_cost_usd") or usage.get("estimated_cost_usd"))
    if explicit > 0:
        return explicit
    return (
        _number(usage.get("request_fees_usd") or usage.get("request_fee_usd"))
        + _number(usage.get("input_token_cost_usd") or usage.get("input_cost_usd"))
        + _number(usage.get("output_token_cost_usd") or usage.get("output_cost_usd"))
        + _number(
            usage.get("tool_cost_usd")
            or usage.get("tool_invocation_cost_usd")
            or usage.get("server_side_tool_cost_usd")
        )
    )


def _tier_tracking(providers: list[Mapping[str, Any]], usage: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [usage, *providers]
    for item in candidates:
        purchased = _number(item.get("credits_purchased_to_date_usd"))
        threshold = _number(item.get("tier_1_threshold_usd") or item.get("tier1_threshold_usd"))
        if threshold > 0:
            return {
                "credits_purchased_to_date_usd": purchased,
                "tier_1_threshold_usd": threshold,
                "remaining_to_tier_1_usd": round(max(threshold - purchased, 0.0), 4),
            }
    return {}


def _research_queue_review(
    research_queue_summary: Mapping[str, Any],
    portfolio_news_monitor: Mapping[str, Any],
    *,
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    news_followups = _followup_count(portfolio_news_monitor)
    reviewed = int(_number(portfolio_news_monitor.get("followup_reviewed_count")))
    high_impact_unreviewed = int(_number(portfolio_news_monitor.get("high_impact_unreviewed_count")))
    if high_impact_unreviewed > 0:
        blockers.append("portfolio_news_high_impact_followups_unreviewed")
    if not research_queue_summary:
        warnings.append("research_queue_summary_not_supplied_for_launch_packet")
        return {
            "status": "unavailable",
            "selected_count": 0,
            "ranked_all_count": 0,
            "source_count": 0,
            "top_symbols": [],
            "portfolio_news_followup_count": news_followups,
            "followup_reviewed_count": reviewed,
            "high_impact_unreviewed_count": high_impact_unreviewed,
            "next_safe_action": "run_research_queue_selection_or_confirm_existing_queue",
        }
    raw_status = str(research_queue_summary.get("status") or "unknown")
    if raw_status.lower() in {"blocked", "failed", "error"}:
        blockers.append("research_queue_summary_not_ready")
    selected_count = int(_number(_first_present(research_queue_summary, "selected_count", "selection_count")))
    ranked_all_count = int(_number(_first_present(research_queue_summary, "ranked_all_count", "ranked_count", "candidate_count")))
    source_count = int(_number(_first_present(research_queue_summary, "source_count", "input_count", "universe_count")))
    top_symbols = _top_symbols(research_queue_summary)
    if selected_count <= 0 and top_symbols:
        selected_count = len(top_symbols)
    if selected_count <= 0:
        warnings.append("research_queue_summary_empty")
    return {
        "status": "ready" if raw_status.lower() not in {"blocked", "failed", "error"} else "blocked",
        "source_status": raw_status,
        "selected_count": selected_count,
        "ranked_all_count": ranked_all_count,
        "source_count": source_count,
        "top_symbols": top_symbols[:10],
        "portfolio_news_followup_count": news_followups,
        "followup_reviewed_count": reviewed,
        "high_impact_unreviewed_count": high_impact_unreviewed,
        "order_submission_enabled": False,
        "next_safe_action": "review_queue_then_run_deeper_research_for_selected_names",
    }


def _scheduler_soak_review(
    scheduler_soak_plan: Mapping[str, Any],
    *,
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not scheduler_soak_plan:
        warnings.append("scheduler_soak_plan_not_supplied_for_launch_packet")
        return {
            "status": "unavailable",
            "preview_only": True,
            "scheduler_executed": False,
            "order_submission_enabled": False,
            "next_safe_action": "generate_no_submit_scheduler_soak_plan",
        }
    status = str(scheduler_soak_plan.get("status") or "unknown")
    if status != "ready_for_no_submit_soak_review":
        blockers.append("scheduler_soak_plan_not_ready")
    if bool(scheduler_soak_plan.get("scheduler_executed")):
        warnings.append("scheduler_soak_plan_reports_scheduler_already_executed")
    return {
        "status": status,
        "preview_only": not bool(scheduler_soak_plan.get("scheduler_executed")),
        "scheduler_executed": bool(scheduler_soak_plan.get("scheduler_executed")),
        "order_submission_enabled": bool(scheduler_soak_plan.get("order_submission_enabled")),
        "next_safe_action": "review_no_submit_soak_plan_before_task_registration",
    }


def _registration_readiness(
    registration: Mapping[str, Any],
    *,
    blockers: list[str],
    packet_status: str,
) -> dict[str, Any]:
    if blockers or packet_status != "ready_for_no_submit_launch_review":
        status = "blocked_by_launch_packet"
        next_action = "resolve_no_submit_launch_packet_blockers_before_registration"
    elif registration.get("registration_executed") is True or _registration_status(registration) == "registered":
        status = "registered_no_submit_task"
        next_action = "monitor_registered_no_submit_task_outputs"
    else:
        status = "ready_for_guarded_no_submit_registration"
        next_action = "optionally_run_guarded_scheduler_registration_with_explicit_confirmation"
    return {
        "status": status,
        "task_name": str(registration.get("task_name") or ""),
        "registration_requested": bool(registration.get("registration_requested")),
        "registration_executed": bool(registration.get("registration_executed")),
        "autostart_scope": "no_submit_monitoring_and_research_only",
        "order_submission_enabled": False,
        "next_safe_action": next_action,
    }


def _check_optional_scheduler_run_artifacts(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    blockers: list[str],
    warnings: list[str],
) -> None:
    summary = artifacts["pipeline_scheduler_summary"]
    if summary:
        if summary.get("status") != "completed":
            blockers.append("pipeline_scheduler_summary_not_completed")
        if bool(summary.get("order_submission_enabled")):
            blockers.append("pipeline_scheduler_summary_order_submission_enabled")
    verification = artifacts["post_run_verification"]
    if verification:
        if verification.get("status") != "ready":
            blockers.append("post_run_verification_not_ready")
        controls = verification.get("resource_controls") if isinstance(verification.get("resource_controls"), Mapping) else {}
        if controls.get("bounded") is False:
            blockers.append("post_run_resource_controls_unbounded")
    review_bundle = artifacts["scheduler_review_bundle"]
    if review_bundle and review_bundle.get("status") not in {"ready_for_manual_review", "ready"}:
        blockers.append("scheduler_review_bundle_not_ready")
    if not summary:
        warnings.append("pipeline_scheduler_summary_not_supplied_for_launch_packet")


def _check_submit_boundary(artifacts: Mapping[str, Mapping[str, Any]], *, blockers: list[str]) -> None:
    for name, payload in artifacts.items():
        if bool(payload.get("order_submission_enabled")):
            blockers.append(f"{name}_order_submission_enabled")
        text = json.dumps(payload, sort_keys=True)
        if "--submit-paper-orders" in text or "--confirm-paper-submit" in text:
            blockers.append(f"{name}_contains_submit_command_fragment")


def _check_protected_symbol_leakage(artifacts: Mapping[str, Mapping[str, Any]], *, blockers: list[str]) -> None:
    for name in ("action_plan", "stage6b_candidate_plan"):
        for item in _intents(artifacts[name]):
            symbol = _symbol(item)
            if symbol == "FXAIX" and _intent_type(item) in {"BUY", "ADD", "SELL", "REDUCE", "REBALANCE"}:
                blockers.append(f"{name}_contains_protected_symbol_intent")


def _registration_status(registration: Mapping[str, Any]) -> str:
    status = str(registration.get("status") or "").strip()
    return status or "missing"


def _manifest_status(manifest: Mapping[str, Any]) -> str:
    if not manifest:
        return "missing"
    return "ready" if manifest.get("schema_version") else "unknown"


def _ready_status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or "missing").strip()


def _is_ready(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status") or "").lower().strip() == "ready"


def _step(name: str, status: str, present: bool) -> dict[str, Any]:
    return {"name": name, "status": status, "present": present}


def _intents(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in payload.get("intents") or [] if isinstance(item, Mapping)]


def _intent_type(intent: Mapping[str, Any]) -> str:
    return str(intent.get("intent_type") or "").upper().strip()


def _symbol(intent: Mapping[str, Any]) -> str:
    return str(intent.get("symbol") or "").upper().strip()


def _first_present(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _top_symbols(payload: Mapping[str, Any]) -> list[str]:
    symbols = payload.get("selected_symbols")
    if isinstance(symbols, list):
        return [str(item).upper().strip() for item in symbols if str(item).strip()]
    for key in ("selected", "research_queue", "items", "candidates"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        result = []
        for row in rows:
            if isinstance(row, Mapping):
                symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
                if symbol:
                    result.append(symbol)
            elif str(row).strip():
                result.append(str(row).upper().strip())
        if result:
            return result
    return []


def _followup_count(payload: Mapping[str, Any]) -> int:
    for key in (
        "portfolio_news_followup_count",
        "followup_count",
        "queue_count",
        "review_trigger_count",
    ):
        if key in payload:
            return int(_number(payload.get(key)))
    followups = payload.get("portfolio_news_followup_ideas")
    if isinstance(followups, list):
        return len(followups)
    return 0


def _load_json_optional(path: str | Path) -> dict[str, Any]:
    raw = str(path or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(Path(raw).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_paths(inputs: SchedulerLaunchPacketInputs) -> dict[str, str]:
    return {
        key: str(value or "")
        for key, value in {
            "scheduler_config_validation": inputs.scheduler_config_validation,
            "scheduler_task_plan": inputs.scheduler_task_plan,
            "scheduler_handoff": inputs.scheduler_handoff,
            "scheduler_task_registration": inputs.scheduler_task_registration,
            "dashboard_manifest": inputs.dashboard_manifest,
            "action_plan": inputs.action_plan,
            "stage6b_candidate_plan": inputs.stage6b_candidate_plan,
            "position_review_queue": inputs.position_review_queue,
            "market_regime": inputs.market_regime,
            "portfolio_news_monitor": inputs.portfolio_news_monitor,
            "api_usage": inputs.api_usage,
            "pipeline_summary": inputs.pipeline_summary,
            "research_queue_summary": inputs.research_queue_summary,
            "scheduler_soak_plan": inputs.scheduler_soak_plan,
            "pipeline_scheduler_summary": inputs.pipeline_scheduler_summary,
            "post_run_verification": inputs.post_run_verification,
            "scheduler_review_bundle": inputs.scheduler_review_bundle,
        }.items()
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "SchedulerLaunchPacketInputs",
    "build_scheduler_launch_packet",
    "build_scheduler_launch_packet_markdown",
]
