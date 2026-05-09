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
    _check_optional_scheduler_run_artifacts(artifacts, blockers=blockers, warnings=warnings)
    status = "ready_for_no_submit_launch_review" if not blockers else "blocked"
    return {
        "schema_version": 1,
        "mode": "scheduler_launch_packet",
        "status": status,
        "generated_at": _now_iso(),
        "chain": chain,
        "sell_rebalance_review": sell_rebalance,
        "parking_review": parking,
        "panic_monitor": panic,
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
