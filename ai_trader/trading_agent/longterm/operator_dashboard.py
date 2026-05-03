"""Static dashboard summaries for long-term trader operator artifacts."""

from __future__ import annotations

from html import escape
from typing import Any, Mapping


PAPER_EXECUTABLE_INTENTS = {"BUY"}
PARKING_INTENTS = {"PARK_IDLE_CASH", "PARK_DEFENSIVE_CASH"}


def build_operator_dashboard(
    *,
    action_plan: Mapping[str, Any] | None = None,
    market_regime: Mapping[str, Any] | None = None,
    operator_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact control-surface summary from saved JSON artifacts."""
    action_plan = action_plan or {}
    operator_status = operator_status or {}
    intents = [dict(item) for item in action_plan.get("intents") or [] if isinstance(item, Mapping)]
    buys = [item for item in intents if _intent_type(item) in PAPER_EXECUTABLE_INTENTS and bool(item.get("allowed"))]
    parking = [item for item in intents if _intent_type(item) in PARKING_INTENTS and bool(item.get("allowed"))]
    next_step = operator_status.get("agent_next_step") if isinstance(operator_status, Mapping) else {}
    next_step = next_step if isinstance(next_step, Mapping) else {}
    advisory = _agent_advisory(next_step=next_step, buys=buys, parking=parking)
    return {
        "schema_version": 1,
        "mode": "operator_dashboard",
        "order_submission_enabled": bool(operator_status.get("order_submission_enabled")),
        "agent_state": str(next_step.get("state") or "unknown"),
        "agent_message": str(next_step.get("message") or ""),
        "agent_advisory": advisory,
        "market_regime": dict(market_regime or {}),
        "buy_intent_count": len(buys),
        "parking_intent_count": len(parking),
        "paper_submit_candidates": [_symbol(item) for item in buys if _symbol(item)],
        "parking_symbols": [_symbol(item) for item in parking if _symbol(item)],
        "buy_intents": [_intent_summary(item) for item in buys],
        "parking_intents": [_intent_summary(item) for item in parking],
        "notes": [
            "Dashboard is read-only. It does not submit or modify broker orders.",
            "Parking intents are capital-deployment guidance and remain excluded from Stage 6B V1 paper submission.",
        ],
    }


def build_operator_dashboard_markdown(dashboard: Mapping[str, Any]) -> str:
    """Render a compact markdown dashboard."""
    regime = dashboard.get("market_regime") or {}
    lines = [
        "# Long-Term Trader Dashboard",
        "",
        f"- Agent state: `{dashboard.get('agent_state') or 'unknown'}`",
        f"- Agent message: {dashboard.get('agent_message') or ''}",
        f"- Advisory: `{(dashboard.get('agent_advisory') or {}).get('state') or 'unknown'}`",
        f"- Order submission enabled: `{str(bool(dashboard.get('order_submission_enabled'))).lower()}`",
        f"- Market regime: `{regime.get('risk_regime') or 'unknown'}`",
        f"- VIX: `{regime.get('vix_level') if regime.get('vix_level') is not None else 'unknown'}`",
        f"- 10Y yield trend: `{regime.get('ten_year_yield_trend') or 'unknown'}`",
        "",
        "## Paper Submit Candidates",
        "",
    ]
    lines.extend(_table_lines(dashboard.get("buy_intents") or []))
    lines.extend(["", "## Idle/Defensive Parking", ""])
    lines.extend(_table_lines(dashboard.get("parking_intents") or []))
    lines.extend(["", "## Safety Notes", ""])
    for note in dashboard.get("notes") or []:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def build_operator_dashboard_html(dashboard: Mapping[str, Any]) -> str:
    """Render a static HTML dashboard suitable for local preview."""
    markdown = build_operator_dashboard_markdown(dashboard)
    body = "\n".join(f"<p>{escape(line)}</p>" if line else "<br>" for line in markdown.splitlines())
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\"><title>Long-Term Trader Dashboard</title>"
        "<style>"
        "body{font-family:Georgia,serif;background:#f6f1e8;color:#1f2933;margin:32px;}"
        "p{margin:0.35rem 0;white-space:pre-wrap;}"
        "</style></head><body>"
        "<h1>Long-Term Trader Dashboard</h1>"
        f"<p>Order Submission Enabled: {str(bool(dashboard.get('order_submission_enabled'))).lower()}</p>"
        f"<p>Advisory: {escape(str((dashboard.get('agent_advisory') or {}).get('state') or 'unknown'))}</p>"
        f"{body}</body></html>\n"
    )


def _table_lines(items: list[Mapping[str, Any]]) -> list[str]:
    lines = ["| Intent | Symbol | Value | Allowed | Reason |", "|---|---|---:|---|---|"]
    if not items:
        lines.append("| none |  |  |  |  |")
        return lines
    for item in items:
        lines.append(
            "| "
            f"{_cell(str(item.get('intent_type') or ''))} | "
            f"{_cell(str(item.get('symbol') or ''))} | "
            f"{float(item.get('trade_value') or 0):.2f} | "
            f"{str(bool(item.get('allowed'))).lower()} | "
            f"{_cell(str(item.get('reason') or ''))} |"
        )
    return lines


def _intent_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "intent_type": _intent_type(item),
        "symbol": _symbol(item),
        "trade_value": float(item.get("trade_value") or item.get("target_value") or 0.0),
        "allowed": bool(item.get("allowed")),
        "reason": str(item.get("reason") or ""),
    }


def _agent_advisory(
    *,
    next_step: Mapping[str, Any],
    buys: list[Mapping[str, Any]],
    parking: list[Mapping[str, Any]],
) -> dict[str, Any]:
    next_state = str(next_step.get("state") or "")
    blockers = [str(item) for item in (next_step.get("blockers") or [])]
    if next_state.startswith("blocked") or blockers:
        state = "blocked_preflight"
        message = "Resolve blockers before any paper submit review."
    elif buys and next_state in {"ready_to_reveal_submit_command", "submit_command_revealed_review_required"}:
        state = "ready_for_supervised_paper_review"
        message = "Review saved artifacts and reveal/run the supervised paper submit command only during the approved window."
    elif buys:
        state = "collect_preflight_artifacts"
        message = "Buy candidates exist, but paper preflight artifacts are not yet ready for review."
    elif parking:
        state = "parking_only_review"
        message = "No stock BUY candidates are paper-ready; review parking guidance and continue research."
    else:
        state = "research_more"
        message = "No paper-ready buys or parking intents are available; continue enrichment and research."
    return {
        "state": state,
        "message": message,
        "submit_candidate_count": len(buys),
        "parking_intent_count": len(parking),
        "blockers": blockers,
        "order_submission_enabled": False,
    }


def _intent_type(item: Mapping[str, Any]) -> str:
    return str(item.get("intent_type") or "").upper()


def _symbol(item: Mapping[str, Any]) -> str:
    return str(item.get("symbol") or "").upper()


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


__all__ = [
    "build_operator_dashboard",
    "build_operator_dashboard_html",
    "build_operator_dashboard_markdown",
]
