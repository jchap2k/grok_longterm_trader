"""Read-only Monday launch packet for supervised long-term paper testing."""

from __future__ import annotations

from typing import Any, Mapping


def build_operator_launch_packet(
    *,
    dashboard: Mapping[str, Any] | None = None,
    candidate_plan: Mapping[str, Any] | None = None,
    monday_check: Mapping[str, Any] | None = None,
    workflow_smoke: Mapping[str, Any] | None = None,
    runbook: Mapping[str, Any] | None = None,
    site_index: str = "",
) -> dict[str, Any]:
    """Combine saved Monday artifacts into one no-submit operator packet."""
    dashboard = dashboard or {}
    candidate_plan = candidate_plan or {}
    monday_check = monday_check or {}
    workflow_smoke = workflow_smoke or {}
    runbook = runbook or {}
    candidates = [
        dict(item)
        for item in candidate_plan.get("intents") or []
        if isinstance(item, Mapping) and _intent_type(item) == "BUY" and bool(item.get("allowed"))
    ]
    blockers = [str(item) for item in monday_check.get("blockers") or []]
    ready = bool(monday_check.get("ready_for_review")) and not blockers and bool(candidates)
    launch_state = "ready_for_supervised_review" if ready else "blocked"
    if not candidates and not blockers:
        launch_state = "research_or_parking_only"

    return {
        "schema_version": 1,
        "mode": "operator_launch_packet",
        "launch_state": launch_state,
        "ready_for_supervised_review": ready,
        "order_submission_enabled": False,
        "agent_advisory_state": str((dashboard.get("agent_advisory") or {}).get("state") or "unknown"),
        "market_regime": dict(dashboard.get("market_regime") or {}),
        "candidate_count": len(candidates),
        "candidate_symbols": [_symbol(item) for item in candidates if _symbol(item)],
        "paper_submit_candidates": [
            _candidate_summary(item, _preview_by_symbol(workflow_smoke).get(_symbol(item), {})) for item in candidates
        ],
        "parking_symbols": [str(item).upper() for item in dashboard.get("parking_symbols") or [] if str(item).strip()],
        "excluded_intent_count": _excluded_intent_count(candidate_plan),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "submit_command_revealed": _submit_command_revealed(runbook),
        "required_conditions": _required_conditions(),
        "do_not_do": [
            "Do not submit live broker orders from this packet.",
            "Do not submit parking, review, rebalance, or sell intents in Stage 6B V1.",
            "Do not use stale artifacts if the action-plan hash or Monday check changes.",
            "Do not run the supervised submit command outside market hours.",
        ],
        "artifacts": {
            "site_index": site_index,
        },
        "notes": [
            "This packet is a review artifact only. It never authorizes broker automation.",
            "Stage 6B remains limited to explicit, supervised Alpaca paper BUY tests.",
        ],
    }


def build_operator_launch_packet_markdown(packet: Mapping[str, Any]) -> str:
    """Render the launch packet as a compact operator-facing markdown file."""
    regime = packet.get("market_regime") or {}
    lines = [
        "# Monday Launch Packet",
        "",
        "Read-only review packet for the supervised Stage 6B paper boundary.",
        "",
        "## State Summary",
        "",
        f"- Launch state: `{packet.get('launch_state') or 'unknown'}`",
        f"- Ready for supervised review: `{str(bool(packet.get('ready_for_supervised_review'))).lower()}`",
        f"- Order submission enabled: `{str(bool(packet.get('order_submission_enabled'))).lower()}`",
        f"- Agent advisory: `{packet.get('agent_advisory_state') or 'unknown'}`",
        f"- Market regime: `{regime.get('risk_regime') or 'unknown'}`",
        f"- Submit command revealed: `{str(bool(packet.get('submit_command_revealed'))).lower()}`",
        "",
        "## Paper Submit Candidates",
        "",
        "| Intent | Symbol | Shares | Est. Value | Promotion |",
        "|---|---|---:|---:|---|",
    ]
    candidates = packet.get("paper_submit_candidates") or []
    if candidates:
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "| "
                f"{_cell(str(item.get('intent_type') or 'BUY'))} | "
                f"{_cell(str(item.get('symbol') or ''))} | "
                f"{_shares(item.get('quantity'))} | "
                f"{_money(item.get('trade_value'))} | "
                f"{_cell(str(item.get('promotion_decision') or ''))} |"
            )
    else:
        lines.append("| none |  |  |  |  |")
    lines.extend(["", "## Parking Guidance", ""])
    parking = [str(item) for item in packet.get("parking_symbols") or []]
    lines.append(f"- Symbols: {', '.join(parking) if parking else 'none'}")
    lines.append(f"- Excluded non-submit intents: {int(packet.get('excluded_intent_count') or 0)}")
    lines.extend(["", "## Readiness And Blockers", ""])
    blockers = [str(item) for item in packet.get("blockers") or []]
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    lines.extend(["", "## Required Conditions", ""])
    lines.extend(f"- {item}" for item in packet.get("required_conditions") or [])
    artifacts = packet.get("artifacts") or {}
    lines.extend(["", "## Artifact Links", ""])
    for label, value in artifacts.items():
        if value:
            lines.append(f"- {label}: `{value}`")
    lines.extend(["", "## Do Not Do", ""])
    lines.extend(f"- {item}" for item in packet.get("do_not_do") or [])
    return "\n".join(lines) + "\n"


def _candidate_summary(item: Mapping[str, Any], preview: Mapping[str, Any] | None = None) -> dict[str, Any]:
    preview = preview or {}
    promotion = item.get("promotion_review") if isinstance(item.get("promotion_review"), Mapping) else {}
    return {
        "intent_type": _intent_type(item),
        "symbol": _symbol(item),
        "quantity": preview.get("quantity") or item.get("quantity") or item.get("shares") or "",
        "trade_value": float(item.get("trade_value") or item.get("target_value") or 0.0),
        "notional": _optional_float(preview.get("notional")),
        "estimated_price": _optional_float(preview.get("estimated_price")),
        "promotion_decision": str(promotion.get("promotion_decision") or item.get("buy_promotion_decision") or ""),
        "reason": str(item.get("reason") or ""),
    }


def _required_conditions() -> list[str]:
    return [
        "Review the static dashboard and ticker pages before revealing or running any submit command.",
        "Confirm the Monday operator check is ready and has zero blockers.",
        "Confirm the Alpaca paper account is clean immediately before submission.",
        "Confirm the market is open; Stage 6B paper submit blocks when the Alpaca clock is closed.",
        "Use only the explicit supervised confirmation token for simple paper BUYs.",
        "Refresh paper order status after any supervised submit attempt.",
        "Clean up any temporary paper position created during smoke testing.",
    ]


def _excluded_intent_count(candidate_plan: Mapping[str, Any]) -> int:
    summary = candidate_plan.get("filter_summary") if isinstance(candidate_plan.get("filter_summary"), Mapping) else {}
    try:
        return int(
            candidate_plan.get("excluded_count")
            or candidate_plan.get("excluded_intent_count")
            or summary.get("excluded_count")
            or summary.get("excluded_intent_count")
            or 0
        )
    except (TypeError, ValueError):
        return 0


def _submit_command_revealed(runbook: Mapping[str, Any]) -> bool:
    for step in runbook.get("steps") or []:
        if not isinstance(step, Mapping) or step.get("step_id") != "supervised_submit":
            continue
        command = str(step.get("command") or "")
        return bool("--submit-paper-orders" in command or step.get("requires_explicit_reveal") is False)
    return False


def _preview_by_symbol(workflow_smoke: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    preview = workflow_smoke.get("preview") if isinstance(workflow_smoke.get("preview"), Mapping) else {}
    rows: dict[str, dict[str, Any]] = {}
    for item in preview.get("previews") or []:
        if isinstance(item, Mapping):
            symbol = _symbol(item)
            if symbol:
                rows[symbol] = dict(item)
    return rows


def _intent_type(item: Mapping[str, Any]) -> str:
    return str(item.get("intent_type") or "").upper()


def _symbol(item: Mapping[str, Any]) -> str:
    return str(item.get("symbol") or "").upper()


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


def _money(value: Any) -> str:
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shares(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(amount)) if amount.is_integer() else f"{amount:g}"


__all__ = ["build_operator_launch_packet", "build_operator_launch_packet_markdown"]
