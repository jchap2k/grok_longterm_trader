"""Static dashboard summaries and pages for long-term trader operator artifacts."""

from __future__ import annotations

import base64
import json
from html import escape
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping


PAPER_EXECUTABLE_INTENTS = {"BUY"}
PARKING_INTENTS = {"PARK_IDLE_CASH", "PARK_DEFENSIVE_CASH"}


def build_operator_dashboard(
    *,
    action_plan: Mapping[str, Any] | None = None,
    market_regime: Mapping[str, Any] | None = None,
    scheduler_policy: Mapping[str, Any] | None = None,
    operator_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact control-surface summary from saved JSON artifacts."""
    action_plan = action_plan or {}
    operator_status = operator_status or {}
    intents = [dict(item) for item in action_plan.get("intents") or [] if isinstance(item, Mapping)]
    buys = [item for item in intents if _intent_type(item) in PAPER_EXECUTABLE_INTENTS and bool(item.get("allowed"))]
    parking = [item for item in intents if _intent_type(item) in PARKING_INTENTS and bool(item.get("allowed"))]
    suppressed_reasons = _suppressed_reasons(action_plan)
    next_step = operator_status.get("agent_next_step") if isinstance(operator_status, Mapping) else {}
    next_step = next_step if isinstance(next_step, Mapping) else {}
    advisory = _agent_advisory(next_step=next_step, buys=buys, parking=parking)
    policy = _scheduler_policy_summary(scheduler_policy)
    return {
        "schema_version": 1,
        "mode": "operator_dashboard",
        "order_submission_enabled": bool(operator_status.get("order_submission_enabled")),
        "agent_state": str(next_step.get("state") or "unknown"),
        "agent_message": str(next_step.get("message") or ""),
        "agent_advisory": advisory,
        "market_regime": dict(market_regime or {}),
        "scheduler_policy": policy,
        "buy_intent_count": len(buys),
        "parking_intent_count": len(parking),
        "paper_submit_candidates": [_symbol(item) for item in buys if _symbol(item)],
        "parking_symbols": [_symbol(item) for item in parking if _symbol(item)],
        "buy_intents": [_intent_summary(item) for item in buys],
        "parking_intents": [_intent_summary(item) for item in parking],
        "suppressed_reasons": suppressed_reasons,
        "suppressed_count": len(suppressed_reasons),
        "notes": [
            "Dashboard is read-only. It does not submit or modify broker orders.",
            "Parking intents are capital-deployment guidance and remain excluded from Stage 6B V1 paper submission.",
        ],
    }


def build_operator_dashboard_markdown(dashboard: Mapping[str, Any]) -> str:
    """Render a compact markdown dashboard."""
    regime = dashboard.get("market_regime") or {}
    policy = dashboard.get("scheduler_policy") or {}
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
        f"- Scheduler policy: `{policy.get('recommended_mode') or 'unavailable'}`",
        f"- Next safe action: `{policy.get('next_safe_action') or 'unknown'}`",
        f"- Suppressed broad actions: `{int(dashboard.get('suppressed_count') or 0)}`",
        "",
        "## Paper Submit Candidates",
        "",
    ]
    lines.extend(_table_lines(dashboard.get("buy_intents") or []))
    lines.extend(["", "## Idle/Defensive Parking", ""])
    lines.extend(_table_lines(dashboard.get("parking_intents") or []))
    lines.extend(["", "## Safety Notes", ""])
    suppressed = dashboard.get("suppressed_reasons") or []
    if suppressed:
        lines.extend(["", "### Tax-Mode Suppressions", ""])
        for reason in suppressed:
            lines.append(f"- {_human_label(str(reason))} (`{reason}`)")
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
        "<div data-pipeline-health>"
        "<p>Follow-up reviews: <span data-pipeline-followup-reviewed>n/a</span></p>"
        "<p>Follow-up next step: <span data-pipeline-followup-next-step>n/a</span></p>"
        "</div>"
        f"{body}</body></html>\n"
    )


def build_operator_dashboard_site(
    *,
    dashboard: Mapping[str, Any],
    action_plan: Mapping[str, Any] | None = None,
    portfolio_state: Mapping[str, Any] | None = None,
    evidence_items: Iterable[Mapping[str, Any]] | None = None,
    price_history_by_symbol: Mapping[str, Any] | None = None,
    api_usage: Mapping[str, Any] | None = None,
    scheduler_config_validation: Mapping[str, Any] | None = None,
    scheduler_task_plan: Mapping[str, Any] | None = None,
    scheduler_handoff: Mapping[str, Any] | None = None,
    scheduler_task_registration: Mapping[str, Any] | None = None,
    scheduler_chain: Mapping[str, Any] | None = None,
    position_review_queue: Mapping[str, Any] | None = None,
    paper_submit_mode_plan: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build a static dashboard package with index and ticker pages."""
    action_plan = action_plan or {}
    evidence_by_symbol = {
        _symbol(item): dict(item)
        for item in (evidence_items or [])
        if isinstance(item, Mapping) and _symbol(item)
    }
    price_history_by_symbol = price_history_by_symbol or {}
    portfolio_state = portfolio_state or {}
    symbols = _ordered_site_symbols(dashboard, action_plan, evidence_by_symbol, portfolio_state)
    pages: dict[str, str] = {
        "index.html": _site_index_html(
            dashboard=dashboard,
            action_plan=action_plan,
            portfolio_state=portfolio_state,
            symbols=symbols,
            evidence_by_symbol=evidence_by_symbol,
            price_history_by_symbol=price_history_by_symbol,
            api_usage=api_usage or {},
            scheduler_config_validation=scheduler_config_validation or {},
            scheduler_task_plan=scheduler_task_plan or {},
            scheduler_handoff=scheduler_handoff or {},
            scheduler_task_registration=scheduler_task_registration or {},
            scheduler_chain=scheduler_chain or {},
            position_review_queue=position_review_queue or {},
            paper_submit_mode_plan=paper_submit_mode_plan or {},
        )
    }
    for symbol in symbols:
        intent = _intent_for_symbol(action_plan, symbol)
        evidence = evidence_by_symbol.get(symbol, {})
        raw_history = price_history_by_symbol.get(symbol) or []
        history = [dict(item) for item in raw_history if isinstance(item, Mapping)]
        pages[f"tickers/{symbol}.html"] = _ticker_page_html(
            symbol=symbol,
            intent=intent,
            evidence=evidence,
            price_history=history,
            dashboard=dashboard,
        )
    return pages


def build_operator_dashboard_evidence_gap_summary(
    *,
    dashboard: Mapping[str, Any] | None = None,
    action_plan: Mapping[str, Any] | None = None,
    portfolio_state: Mapping[str, Any] | None = None,
    evidence_items: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build structured research follow-up data from the same inputs as the site."""
    dashboard = dashboard or {}
    action_plan = action_plan or {}
    evidence_by_symbol = {
        _symbol(item): dict(item)
        for item in (evidence_items or [])
        if isinstance(item, Mapping) and _symbol(item)
    }
    symbols = _ordered_site_symbols(dashboard, action_plan, evidence_by_symbol, portfolio_state or {})
    items = []
    for symbol in symbols:
        intent = _intent_for_symbol(action_plan, symbol)
        evidence = evidence_by_symbol.get(symbol, {})
        gap_info = _evidence_gaps_for_symbol(intent=intent, evidence=evidence)
        gap_count = sum(len(gap_info[key]) for key in ("promotion", "missing", "warnings"))
        if gap_count <= 0:
            continue
        items.append(
            {
                "symbol": symbol,
                "gap_count": gap_count,
                "promotion_followups": list(gap_info["promotion"]),
                "missing_evidence": list(gap_info["missing"]),
                "warnings": list(gap_info["warnings"]),
                "suggested_next_step": _evidence_gap_next_step(gap_info),
            }
        )
    items.sort(key=lambda item: (-int(item["gap_count"]), str(item["symbol"])))
    return {
        "schema_version": 1,
        "mode": "operator_dashboard_evidence_gaps",
        "gap_count": len(items),
        "symbols_with_gaps": [str(item["symbol"]) for item in items],
        "items": items,
    }


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


def _advisory_display_label(state: Any) -> str:
    value = str(state or "unknown").strip()
    labels = {
        "ready_for_supervised_paper_review": "Paper Review Ready",
        "blocked_preflight": "Preflight Blocked",
        "collect_preflight_artifacts": "Collect Preflight",
        "parking_only_review": "Parking Review",
        "research_more": "Research More",
        "ready_to_reveal_submit_command": "Paper Review Ready",
        "submit_command_revealed_review_required": "Review Required",
    }
    if value in labels:
        return labels[value]
    return value.replace("_", " ").replace("-", " ").title() if value else "Unknown"


def _scheduler_policy_summary(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize advisory scheduler policy data for dashboard display."""
    if not isinstance(policy, Mapping) or not policy:
        return {
            "available": False,
            "recommended_mode": "unavailable",
            "urgency": "unknown",
            "reasons": [],
            "warnings": [],
            "blockers": [],
            "affected_symbols": [],
            "next_safe_action": "generate_scheduler_policy_artifact",
            "order_submission_enabled": False,
        }
    return {
        "available": True,
        "recommended_mode": str(policy.get("recommended_mode") or "unknown"),
        "urgency": str(policy.get("urgency") or "unknown"),
        "reasons": [str(item) for item in (policy.get("reasons") or [])],
        "warnings": [str(item) for item in (policy.get("warnings") or [])],
        "blockers": [str(item) for item in (policy.get("blockers") or [])],
        "affected_symbols": [str(item).upper() for item in (policy.get("affected_symbols") or []) if str(item).strip()],
        "next_safe_action": str(policy.get("next_safe_action") or "unknown"),
        "active_rules_sha256": str(policy.get("active_rules_sha256") or ""),
        "generated_at": str(policy.get("generated_at") or ""),
        "order_submission_enabled": False,
    }


def _suppressed_reasons(action_plan: Mapping[str, Any]) -> list[str]:
    return [
        str(reason).strip()
        for reason in (action_plan.get("suppressed_reasons") or [])
        if str(reason).strip()
    ]


def _scheduler_mode_label(value: Any) -> str:
    text = str(value or "unavailable").strip()
    if not text or text == "unavailable":
        return "Unavailable"
    return text.replace("_", " ").replace("-", " ").title()


def _scheduler_action_label(value: Any) -> str:
    text = str(value or "unknown").strip()
    if not text:
        return "unknown"
    return text.replace("_", " ").replace("-", " ")


def _scheduler_policy_tile(policy: Mapping[str, Any]) -> str:
    mode = _scheduler_mode_label(policy.get("recommended_mode"))
    urgency = _scheduler_mode_label(policy.get("urgency"))
    next_action = _scheduler_action_label(policy.get("next_safe_action"))
    reasons = ", ".join(_scheduler_action_label(item) for item in (policy.get("reasons") or [])) or "No reasons supplied"
    affected = ", ".join(str(item) for item in (policy.get("affected_symbols") or [])) or "none"
    warnings = ", ".join(_scheduler_action_label(item) for item in (policy.get("warnings") or [])) or "none"
    return (
        "<div class=\"status-tile scheduler-policy-tile\" id=\"scheduler-policy\">"
        "<span>Scheduler Policy</span>"
        f"<strong>{escape(mode)}</strong>"
        f"<small><b>{escape(urgency)}</b> urgency. Next safe action: {escape(next_action)}.</small>"
        f"<small>Reasons: {escape(reasons)}. Affected symbols: {escape(affected)}.</small>"
        f"<small>Warnings: {escape(warnings)}. Advisory only; order submission remains disabled.</small>"
        "</div>"
    )


def _intent_type(item: Mapping[str, Any]) -> str:
    return str(item.get("intent_type") or "").upper()


def _symbol(item: Mapping[str, Any]) -> str:
    return str(item.get("symbol") or "").upper()


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


def _ordered_site_symbols(
    dashboard: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
    portfolio_state: Mapping[str, Any] | None = None,
) -> list[str]:
    ordered: list[str] = []
    for key in ("paper_submit_candidates", "parking_symbols"):
        for value in dashboard.get(key) or []:
            _append_unique_symbol(ordered, str(value))
    for intent in action_plan.get("intents") or []:
        if isinstance(intent, Mapping):
            _append_unique_symbol(ordered, _symbol(intent))
    for holding in _portfolio_holdings(portfolio_state or {}):
        _append_unique_symbol(ordered, _holding_symbol(holding))
    for symbol in evidence_by_symbol:
        _append_unique_symbol(ordered, symbol)
    return ordered


def _append_unique_symbol(symbols: list[str], value: str) -> None:
    symbol = str(value or "").upper().strip()
    if symbol and symbol not in symbols:
        symbols.append(symbol)


def _intent_for_symbol(action_plan: Mapping[str, Any], symbol: str) -> dict[str, Any]:
    for intent in action_plan.get("intents") or []:
        if isinstance(intent, Mapping) and _symbol(intent) == symbol:
            return dict(intent)
    return {"symbol": symbol, "intent_type": "RESEARCH", "order_intent": "NONE", "allowed": False}


def _site_index_html(
    *,
    dashboard: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    portfolio_state: Mapping[str, Any],
    symbols: list[str],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
    price_history_by_symbol: Mapping[str, Any] | None = None,
    api_usage: Mapping[str, Any] | None = None,
    scheduler_config_validation: Mapping[str, Any] | None = None,
    scheduler_task_plan: Mapping[str, Any] | None = None,
    scheduler_handoff: Mapping[str, Any] | None = None,
    scheduler_task_registration: Mapping[str, Any] | None = None,
    scheduler_chain: Mapping[str, Any] | None = None,
    position_review_queue: Mapping[str, Any] | None = None,
    paper_submit_mode_plan: Mapping[str, Any] | None = None,
) -> str:
    regime = dashboard.get("market_regime") or {}
    advisory = dashboard.get("agent_advisory") or {}
    policy = dashboard.get("scheduler_policy") or {}
    intents = [dict(item) for item in action_plan.get("intents") or [] if isinstance(item, Mapping)]
    buy_intents = [item for item in intents if _intent_type(item) == "BUY"]
    parking_intents = [item for item in intents if _intent_type(item) in PARKING_INTENTS]
    review_intents = [item for item in intents if _intent_type(item) not in {"BUY", *PARKING_INTENTS}]
    advisory_label = _advisory_display_label(advisory.get("state"))
    cards = []
    for symbol in symbols:
        intent = _intent_for_symbol(action_plan, symbol)
        evidence = evidence_by_symbol.get(symbol, {})
        search_text = " ".join(
            [
                symbol,
                str(intent.get("intent_type") or ""),
                str(intent.get("reason") or ""),
                str(evidence.get("business_summary") or ""),
            ]
        ).lower()
        cards.append(
            "<a class=\"ticker-card\" href=\"tickers/{symbol}.html\" data-search-text=\"{search_text}\" data-paginated-item>"
            "<span class=\"ticker-kicker\">{intent}</span>"
            "<strong>{symbol}</strong>"
            "<em>{summary}</em>"
            "<small>{value}</small>"
            "</a>".format(
                symbol=escape(symbol),
                search_text=escape(search_text),
                intent=escape(str(intent.get("intent_type") or "RESEARCH")),
                summary=escape(_short_text(str(evidence.get("business_summary") or intent.get("reason") or "Open research page."), 105)),
                value=escape(_money(intent.get("trade_value") or intent.get("target_value") or 0)),
            )
        )
    return _html_shell(
        title="Long-Term Trader Dashboard",
        body=f"""
        <div class="dashboard-shell">
          {_dashboard_rail()}
          <main class="dashboard-main">
            {_dashboard_topbar(dashboard=dashboard, regime=regime, buy_intents=buy_intents)}
            <section class="hero" id="dashboard-overview">
              <p class="eyebrow">Autonomous Research Surface</p>
              <h1>Long-Term Trader Dashboard</h1>
              <p class="lede">{escape(str(advisory.get("message") or "Review research, parking, and paper-readiness artifacts."))}</p>
              <div class="hero-grid">
                <div><span>Advisory</span><strong>{escape(advisory_label)}</strong></div>
                <div><span>Market Regime</span><strong>{escape(str(regime.get("risk_regime") or "unknown"))}</strong></div>
                <div><span>Paper Candidates</span><strong>{int(dashboard.get("buy_intent_count") or 0)}</strong></div>
                <div><span>Parking</span><strong>{", ".join(escape(str(item)) for item in dashboard.get("parking_symbols") or []) or "none"}</strong></div>
                <div><span>Scheduler</span><strong>{escape(_scheduler_mode_label(policy.get("recommended_mode")))}</strong></div>
              </div>
            </section>
            {_dashboard_tabs()}
            <section class="panel overview-panel">
              <div class="section-heading">
                <p class="eyebrow">Overview Highlights</p>
                <h2>Performance Goal And Latest Recommendation</h2>
              </div>
              <div class="overview-grid">
                <div class="highlight-card">
                  <h3>Goal: Beat FXAIX over 5 years</h3>
                  <p>Active-sleeve decisions must earn their place against the protected benchmark/core holding.</p>
                  <dl>
                    <div><dt>Paper candidates</dt><dd>{len(buy_intents)}</dd></div>
                    <div><dt>Parking symbols</dt><dd>{", ".join(escape(str(item)) for item in dashboard.get("parking_symbols") or []) or "none"}</dd></div>
                    <div><dt>Regime</dt><dd>{escape(str(regime.get("risk_regime") or "unknown"))}</dd></div>
                  </dl>
                </div>
                <div class="highlight-card latest-rec">
                  <h3>Latest Recommendation</h3>
                  {_latest_recommendation_html(buy_intents)}
                </div>
                <div class="highlight-card coverage-updates">
                  <h3>Coverage Updates</h3>
                  <p>{len(symbols)} ticker tear sheets are available in this generated site.</p>
                  <p>Use the research board to open scorecards, earnings context, article evidence, and charts.</p>
                </div>
              </div>
            </section>
            <section class="panel command-center" id="command-center">
              <div class="section-heading">
                <p class="eyebrow">Command Center</p>
                <h2>Agent State And Market Posture</h2>
              </div>
              <div class="command-grid">
                {_status_tile("Agent", advisory_label, advisory.get("message") or "")}
                {_status_tile("Order submission", "disabled", "Read-only dashboard. Stage 6B still requires explicit supervised confirmation.")}
                {_status_tile("Regime", regime.get("risk_regime") or "unknown", regime.get("reason") or "")}
                {_status_tile("VIX / 10Y", f"{regime.get('vix_level') if regime.get('vix_level') is not None else 'unknown'} / {regime.get('ten_year_yield_trend') or 'unknown'}", "Used for parking posture, not automatic trading.")}
                {_scheduler_policy_tile(policy)}
              </div>
            </section>
            {_scheduler_readiness_section(
                scheduler_config_validation=scheduler_config_validation or {},
                scheduler_task_plan=scheduler_task_plan or {},
                scheduler_handoff=scheduler_handoff or {},
                scheduler_task_registration=scheduler_task_registration or {},
                scheduler_chain=scheduler_chain or {},
            )}
            {_api_usage_panel(api_usage or {})}
            <section class="panel" id="coverage">
              <div class="section-heading">
                <p class="eyebrow">Coverage</p>
                <h2>Research Coverage Updates</h2>
              </div>
              <p>{len(symbols)} ticker tear sheets are available in this generated site.</p>
              <p>Coverage rows are generated from the current action plan, evidence files, and enrichment artifacts. Future versions can split this into analyst updates, latest news, and thesis-monitor notes.</p>
            </section>
            {_rankings_section(symbols=symbols, action_plan=action_plan, evidence_by_symbol=evidence_by_symbol)}
            {_scorecards_section(symbols=symbols, evidence_by_symbol=evidence_by_symbol, price_history_by_symbol=price_history_by_symbol or {})}
            {_evidence_gaps_section(symbols=symbols, action_plan=action_plan, evidence_by_symbol=evidence_by_symbol)}
            {_placeholder_panel(
                section_id="foundational-core",
                eyebrow="Foundational Core",
                title="Foundational Core Placeholder",
                body="Protected benchmark/core holdings and approved index or defensive parking choices will appear here when portfolio holdings are supplied.",
            )}
            {_placeholder_panel(
                section_id="hold-review",
                eyebrow="Hold / Review",
                title="Hold / Review Placeholder",
                body="Held names that need thesis review, refreshed evidence, or sell/trim analysis will appear here once portfolio holdings are connected.",
            )}
            {_placeholder_panel(
                section_id="closed-positions",
                eyebrow="Closed Positions",
                title="Closed Positions Placeholder",
                body="No closed positions are available in this generated dashboard yet.",
            )}
            <section class="panel" id="paper-candidates">
              <div class="section-heading">
                <p class="eyebrow">Paper-Ready Candidates</p>
                <h2>Simple BUYs Cleared For Review</h2>
              </div>
              {_intent_rows(buy_intents, empty_label="No paper-ready BUY candidates.")}
            </section>
            <section class="panel" id="parking">
              <div class="section-heading">
                <p class="eyebrow">Capital Deployment / Parking</p>
                <h2>Idle Cash Posture</h2>
              </div>
              {_intent_rows(parking_intents, empty_label="No parking intent generated.")}
            </section>
            {_review_simulation_intents_panel(review_intents)}
            <section class="panel" id="portfolio">
              <div class="section-heading">
                <p class="eyebrow">Portfolio Snapshot</p>
                <h2>Exposure Surface</h2>
              </div>
              <p>Portfolio details are sourced from the current action-plan and operator artifacts. Protected/core holdings remain outside Stage 6B paper submission.</p>
              <ul class="summary-list">
                <li><strong>{len(buy_intents)}</strong><span>paper-review BUY intents</span></li>
                <li><strong>{len(parking_intents)}</strong><span>parking intents</span></li>
                <li><strong>{len(review_intents)}</strong><span>review / follow-up intents</span></li>
              </ul>
              {_portfolio_value_panel(portfolio_state)}
              {_holdings_table(portfolio_state)}
            </section>
            <section class="panel" id="safety">
              <div class="section-heading">
                <p class="eyebrow">Safety &amp; Preflight</p>
                <h2>Paper Boundary Guardrails</h2>
              </div>
              <div class="safety-grid">
                {_safety_chip("Broker submit", "off")}
                {_safety_chip("Allowed V1 order type", "simple BUY only")}
                {_safety_chip("Parking submit", "excluded")}
                {_safety_chip("Rebalance submit", "hard-blocked")}
              </div>
              {_tax_mode_suppressions_panel(dashboard.get("suppressed_reasons") or [])}
              <p class="safety-note">Scheduler launch evidence is tracked in the dedicated <a href="#scheduler-readiness">Scheduler Readiness</a> section above.</p>
              {_position_review_queue_panel(position_review_queue or {})}
              {_paper_submit_mode_plan_panel(paper_submit_mode_plan or {})}
              {_pipeline_health_panel()}
            </section>
            <section class="panel" id="research-board">
              <div class="section-heading">
                <p class="eyebrow">Research Board</p>
                <h2>All Ticker Tear Sheets</h2>
              </div>
              <div class="pagination-shell" data-paginated-list data-page-size="24" data-pagination-label="tear sheets">
                <div class="ticker-grid">{''.join(cards)}</div>
                {_pagination_controls()}
              </div>
            </section>
            <section class="safety-strip">
              <strong>Read-only:</strong> this dashboard does not submit broker orders. Stage 6B still requires explicit supervised confirmation.
            </section>
            {_placeholder_panel(
                section_id="about",
                eyebrow="About",
                title="About This Dashboard",
                body="This is a generated, read-only operator surface for the autonomous long-term trader. It organizes evidence, paper-candidate review, parking posture, and safety state before any supervised paper action.",
            )}
            {_placeholder_panel(
                section_id="settings",
                eyebrow="Settings",
                title="Settings Placeholder",
                body="Runtime configuration, source toggles, and scheduler controls are intentionally not editable from this static dashboard yet.",
            )}
            {_reference_footer()}
            {_agent_chat_placeholder()}
            <script>{_dashboard_search_script()}{_paginated_lists_script()}{_synced_table_scroller_script()}{_portfolio_live_refresh_script()}{_api_usage_refresh_script()}{_pipeline_health_refresh_script()}{_agent_chat_placeholder_script()}</script>
          </main>
        </div>
        """,
    )


def _dashboard_rail() -> str:
    items = [
        ("dashboard", "Dashboard", "#dashboard-overview"),
        ("paper-candidates", "Paper Candidates", "#paper-candidates"),
        ("all-tear-sheets", "All Tear Sheets", "#research-board"),
        ("rankings", "Rankings", "#rankings"),
        ("coverage", "Coverage", "#coverage"),
        ("scorecards", "Scorecards", "#scorecards"),
        ("evidence-gaps", "Evidence Gaps", "#evidence-gaps"),
        ("portfolio", "Portfolio", "#portfolio"),
        ("scheduler", "Scheduler", "#scheduler-readiness"),
        ("api-usage", "API Usage", "#api-usage"),
        ("safety", "Safety", "#safety"),
        ("settings", "Settings", "#settings"),
    ]
    links = "".join(
        f"<a href=\"{href}\">{_nav_icon(icon)}<span class=\"nav-label\">{escape(label)}</span></a>"
        for icon, label, href in items
    )
    logo = _brand_logo_html()
    return (
        "<aside class=\"dashboard-rail\">"
        f"<div class=\"rail-brand\">{logo}</div>"
        f"<nav>{links}</nav>"
        "</aside>"
    )


def _nav_icon(name: str) -> str:
    icons = {
        "dashboard": (
            '<rect x="4" y="4" width="6" height="6" rx="1.4"/>'
            '<rect x="14" y="4" width="6" height="6" rx="1.4"/>'
            '<rect x="4" y="14" width="6" height="6" rx="1.4"/>'
            '<rect x="14" y="14" width="6" height="6" rx="1.4"/>'
        ),
        "paper-candidates": (
            '<path d="M7 4.5h7.2L18 8.3V19a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V6A1.5 1.5 0 0 1 7 4.5Z"/>'
            '<path d="M14 4.5V8h3.8"/>'
            '<path d="m8.4 13 2.2 2.2 4.8-5"/>'
        ),
        "all-tear-sheets": (
            '<path d="M7.5 5.5h9A1.5 1.5 0 0 1 18 7v11.5H7.5A1.5 1.5 0 0 1 6 17V7a1.5 1.5 0 0 1 1.5-1.5Z"/>'
            '<path d="M9 8.5h6"/>'
            '<path d="M9 12h6"/>'
            '<path d="M9 15.5h4.2"/>'
            '<path d="M18 8.5h1.1A1.4 1.4 0 0 1 20.5 10v9.5H9"/>'
        ),
        "rankings": (
            '<path d="M5 19V9"/>'
            '<path d="M11 19V5"/>'
            '<path d="M17 19v-7"/>'
            '<path d="M4 19h16"/>'
            '<path d="m15.4 5.8 1.1-2.2 1.1 2.2 2.4.4-1.7 1.7.4 2.4-2.2-1.1-2.1 1.1.4-2.4-1.8-1.7 2.4-.4Z"/>'
        ),
        "coverage": (
            '<path d="M4.5 8.5h5l1.8 2h8.2v7A1.5 1.5 0 0 1 18 19H6A1.5 1.5 0 0 1 4.5 17.5v-9Z"/>'
            '<path d="M4.5 8.5V7A1.5 1.5 0 0 1 6 5.5h4.4l1.6 1.7"/>'
            '<path d="M16 12.5c.8.6 1.3 1.4 1.3 2.5"/>'
            '<path d="M14.2 14c.3.3.5.6.5 1"/>'
        ),
        "scorecards": (
            '<path d="M6 18.5V10a6 6 0 0 1 12 0v8.5"/>'
            '<path d="M8.5 17h7"/>'
            '<path d="m12 10 3.2-3.2"/>'
            '<circle cx="12" cy="10" r="1.6"/>'
            '<path d="M7.3 11.2h2"/>'
            '<path d="M14.7 11.2h2"/>'
        ),
        "scheduler": (
            '<rect x="4.5" y="5" width="15" height="13.5" rx="2.2"/>'
            '<path d="M8 3.8v3.4"/>'
            '<path d="M16 3.8v3.4"/>'
            '<path d="M4.8 9h14.4"/>'
            '<path d="m8 14 2.3 2.2 5.7-5.5"/>'
        ),
        "evidence-gaps": (
            '<circle cx="10.5" cy="10.5" r="5.5"/>'
            '<path d="m15 15 4.5 4.5"/>'
            '<path d="M10.5 7.5v3.6"/>'
            '<path d="M10.5 14h.1"/>'
            '<path d="M18 5.2v3"/>'
            '<path d="M18 11.3h.1"/>'
        ),
        "portfolio": (
            '<path d="M8.5 7V5.8A1.8 1.8 0 0 1 10.3 4h3.4a1.8 1.8 0 0 1 1.8 1.8V7"/>'
            '<rect x="4.5" y="7" width="15" height="12.5" rx="2"/>'
            '<path d="M4.8 11.5h14.4"/>'
            '<path d="M12 10.8v2.4"/>'
            '<path d="M8 16.5h2.3"/>'
            '<path d="M13.7 16.5H16"/>'
        ),
        "api-usage": (
            '<path d="M5 18.5V6.8A1.8 1.8 0 0 1 6.8 5h10.4A1.8 1.8 0 0 1 19 6.8v11.7"/>'
            '<path d="M7.5 9h9"/>'
            '<path d="M8.2 13.5h2.2"/>'
            '<path d="M12 13.5h3.8"/>'
            '<path d="M8.2 16.5h5.2"/>'
            '<path d="M4 20h16"/>'
            '<path d="m15.8 9 1.4-1.4 1.4 1.4"/>'
        ),
        "safety": (
            '<path d="M12 3.8 18.5 6v5.2c0 4.1-2.6 7.4-6.5 9-3.9-1.6-6.5-4.9-6.5-9V6L12 3.8Z"/>'
            '<path d="m8.8 11.8 2.1 2.1 4.4-4.6"/>'
        ),
        "settings": (
            '<circle cx="12" cy="12" r="3"/>'
            '<path d="M12 4.5v2"/>'
            '<path d="M12 17.5v2"/>'
            '<path d="M4.5 12h2"/>'
            '<path d="M17.5 12h2"/>'
            '<path d="m6.7 6.7 1.4 1.4"/>'
            '<path d="m15.9 15.9 1.4 1.4"/>'
            '<path d="m17.3 6.7-1.4 1.4"/>'
            '<path d="m8.1 15.9-1.4 1.4"/>'
        ),
    }
    body = icons.get(name, icons["dashboard"])
    safe_name = "".join(ch for ch in name if ch.isalnum() or ch == "-")
    return (
        f'<svg class="nav-icon nav-icon-{safe_name}" viewBox="0 0 24 24" '
        'aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    )


def _brand_logo_html() -> str:
    data_uri = _logo_data_uri()
    if not data_uri:
        return "<strong>Long-Term Trader Agent</strong>"
    return (
        f"<img class=\"rail-logo\" src=\"{escape(data_uri)}\" "
        "alt=\"Long-Term Trader Agent logo\" loading=\"lazy\">"
    )


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    logo_dir = Path(__file__).resolve().parents[1] / "agent" / "utils"
    svg_path = logo_dir / "logo.svg"
    if svg_path.exists():
        encoded = ""
        try:
            svg = svg_path.read_text(encoding="utf-8")
        except OSError:
            svg = ""
        if svg:
            svg = svg.replace('viewBox="0 0 320 220"', 'viewBox="0 42 320 178"')
            # The source logo uses dark navy for the bull; lighten it for the dark rail.
            svg = svg.replace("#0F2A5E", "#CFEFFF")
            svg = svg.replace("paint-order: stroke fill; stroke: #CFEFFF; stroke-width: 1.6px;", "")
            svg = svg.replace("paint-order: stroke fill; stroke: #CFEFFF; stroke-width: 1.2px;", "")
            # The source text sits close to the bull hooves; lower it in the rail variant.
            svg = svg.replace('y="172"', 'y="194"').replace('y="195"', 'y="220"')
            svg = svg.replace('font-size="19.8"', 'font-size="31.5"')
            svg = svg.replace('letter-spacing="4.8px"', 'letter-spacing="2.4px"')
            svg = svg.replace('letter-spacing="3.9px"', 'letter-spacing="1.8px"')
            svg = svg.replace("system-ui, Arial Black, sans-serif", "Bahnschrift, Aptos Display, Arial Narrow, Arial, sans-serif")
            svg = svg.replace('fill="#10B981" text-anchor="middle"', 'fill="#F8FAE8" text-anchor="middle"')
            svg = svg.replace("LONG TERM", "Long-Term")
            svg = svg.replace("TRADER AGENT", "TRADING AGENT")
            svg = svg.replace("TRADING AGENT", "Trading Agent")
            encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        if encoded:
            return f"data:image/svg+xml;base64,{encoded}"
    logo_path = logo_dir / "ltta_logo.jpg"
    try:
        encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/jpeg;base64,{encoded}"


def _dashboard_topbar(
    *,
    dashboard: Mapping[str, Any],
    regime: Mapping[str, Any],
    buy_intents: list[Mapping[str, Any]],
) -> str:
    best_buys = ", ".join(_symbol(item) for item in buy_intents[:3]) or "none"
    return (
        "<header class=\"dashboard-topbar\">"
        "<div class=\"topbar-links\"><a href=\"#dashboard-overview\">Long-Term Advisor</a><a href=\"#portfolio\">My Stocks</a><a href=\"#coverage\">My Reports</a></div>"
        "<label class=\"search-box\"><span>Search research universe</span><input class=\"dashboard-search\" aria-label=\"Search research universe\" placeholder=\"Search research universe\"></label>"
        "<div class=\"best-buys\"><span>Best Buys For Review</span><strong>{best_buys}</strong></div>"
        "<div class=\"market-tape\"><span>S&amp;P 500</span><strong>{regime}</strong><span>VIX</span><strong>{vix}</strong></div>"
        "</header>"
    ).format(
        best_buys=escape(best_buys),
        regime=escape(str(regime.get("risk_regime") or "unknown")),
        vix=escape(str(regime.get("vix_level") if regime.get("vix_level") is not None else "unknown")),
    )


def _dashboard_tabs() -> str:
    tabs = [
        ("Overview", "#dashboard-overview"),
        ("Scorecard", "#scorecards"),
        ("Foundational Core", "#foundational-core"),
        ("Hold / Review", "#hold-review"),
        ("Closed Positions", "#closed-positions"),
        ("About", "#about"),
    ]
    return "<nav class=\"dashboard-tabs\">" + "".join(
        f"<a class=\"{'is-active' if index == 0 else ''}\" href=\"{href}\">{escape(tab)}</a>"
        for index, (tab, href) in enumerate(tabs)
    ) + "</nav>"


def _placeholder_panel(*, section_id: str, eyebrow: str, title: str, body: str) -> str:
    return (
        f"<section class=\"panel placeholder-panel\" id=\"{escape(section_id)}\">"
        "<div class=\"section-heading\">"
        f"<p class=\"eyebrow\">{escape(eyebrow)}</p>"
        f"<h2>{escape(title)}</h2>"
        "</div>"
        f"<p>{escape(body)}</p>"
        "</section>"
    )


def _rankings_section(
    *,
    symbols: Iterable[str],
    action_plan: Mapping[str, Any],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
) -> str:
    rows = []
    for symbol in _action_plan_symbols(action_plan, symbols):
        intent = _intent_for_symbol(action_plan, symbol)
        if not intent or _intent_type(intent) in PARKING_INTENTS:
            continue
        evidence = evidence_by_symbol.get(symbol, {})
        score, score_source = _review_score_for_symbol(intent=intent, evidence=evidence)
        if score <= 0:
            continue
        scorecard = evidence.get("quality_growth_scorecard") if isinstance(evidence.get("quality_growth_scorecard"), Mapping) else {}
        analysis = scorecard.get("analysis") if isinstance(scorecard.get("analysis"), Mapping) else {}
        rows.append(
            {
                "symbol": symbol,
                "score": score,
                "score_source": score_source,
                "sort_priority": _actionability_sort_priority(_actionability_for_intent(intent)),
                "actionability": _actionability_for_intent(intent),
                "why_not_buy": _why_not_buy(intent),
                "trade_value": intent.get("trade_value") or intent.get("target_value") or 0,
                "quality": analysis.get("quality"),
                "growth": analysis.get("growth"),
                "valuation": analysis.get("valuation"),
                "reason": str(intent.get("reason") or evidence.get("business_summary") or ""),
            }
        )
    rows.sort(key=lambda item: (int(item["sort_priority"]), -float(item["score"]), str(item["symbol"])))
    if not rows:
        return _placeholder_panel(
            section_id="rankings",
            eyebrow="Rankings",
            title="Rankings Placeholder",
            body="Ranked action-plan stocks will appear here once promotion-review scores are supplied.",
        )
    body_rows = []
    for index, item in enumerate(rows, start=1):
        symbol = str(item["symbol"])
        search_text = " ".join(
            [
                symbol,
                str(item["actionability"]),
                str(item["why_not_buy"]),
                str(item["reason"]),
                str(item["score_source"]),
            ]
        ).lower()
        body_rows.append(
            f"<tr data-paginated-item data-search-text=\"{escape(search_text)}\">"
            f"<td>{index}</td>"
            f"<td><a href=\"tickers/{escape(symbol)}.html\">{escape(symbol)}</a></td>"
            f"<td>{float(item['score']):g}</td>"
            f"<td>{escape(_actionability_label(str(item['actionability'])))}</td>"
            f"<td>{escape(_short_text(_humanize_reason(str(item['why_not_buy'])), 90))}</td>"
            f"<td>{escape(_money(item.get('trade_value')))}</td>"
            f"<td>{escape(_score_cell(item.get('quality')))}</td>"
            f"<td>{escape(_score_cell(item.get('growth')))}</td>"
            f"<td>{escape(_score_cell(item.get('valuation')))}</td>"
            f"<td>{escape(_short_text(str(item['reason']), 110))}</td>"
            f"<td>{escape(str(item['score_source']))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"panel\" id=\"rankings\">"
        "<div class=\"section-heading\">"
        "<p class=\"eyebrow\">Rankings</p>"
        "<h2>Ranked Stock List</h2>"
        "</div>"
        "<p>Operator Action View: stocks are sorted by promotion/actionability state first, then review confidence. Scorecards below remain the broad evidence matrix.</p>"
        "<div class=\"pagination-shell\" data-paginated-list data-page-size=\"25\" data-pagination-label=\"ranked stocks\">"
        "<div class=\"table-scroll-top\" aria-hidden=\"true\"><div></div></div>"
        "<div class=\"table-scroll\"><table class=\"rankings-table\">"
        "<thead><tr><th title=\"Rank\">#</th><th>Symbol</th><th title=\"Operator Score\">Score</th><th>Action</th><th>Why Not Buy</th><th title=\"Trade Value\">Value</th><th title=\"Quality\">Qual</th><th title=\"Growth\">Grow</th><th title=\"Valuation\">Val</th><th>Context</th><th title=\"Score Source\">Source</th></tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        f"{_pagination_controls()}"
        "</div>"
        "</section>"
    )


def _scorecards_section(
    *,
    symbols: Iterable[str],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
    price_history_by_symbol: Mapping[str, Any],
) -> str:
    rows = []
    for symbol in symbols:
        evidence = evidence_by_symbol.get(symbol, {})
        scorecard = evidence.get("quality_growth_scorecard") if isinstance(evidence.get("quality_growth_scorecard"), Mapping) else {}
        analysis = scorecard.get("analysis") if isinstance(scorecard.get("analysis"), Mapping) else {}
        superscore = _number(scorecard.get("superscore"))
        has_metric = superscore > 0 or bool(analysis) or any(
            _number(scorecard.get(key)) > 0
            for key in ("quality_score", "growth_score", "valuation_score", "safety_score", "market_attention_score")
        )
        if not has_metric:
            continue
        rows.append(
            {
                "symbol": symbol,
                "superscore": superscore,
                "quality": _scorecard_metric(scorecard, analysis, "quality_score", "quality"),
                "growth": _scorecard_metric(scorecard, analysis, "growth_score", "growth"),
                "valuation": _scorecard_metric(scorecard, analysis, "valuation_score", "valuation"),
                "safety": _scorecard_metric(scorecard, analysis, "safety_score", "safety"),
                "market": _scorecard_metric(
                    scorecard,
                    analysis,
                    "market_attention_score",
                    "market_buzz_score",
                    "market_attention",
                    "market_buzz",
                ),
                "investing_type": scorecard.get("investing_type") or "n/a",
                "max_drawdown": scorecard.get("estimated_drawdown_band") or scorecard.get("est_max_drawdown") or "n/a",
                "historical_drawdown": _historical_max_drawdown_pct(price_history_by_symbol.get(symbol) or []),
                "reasons": [str(item) for item in scorecard.get("score_reasons") or [] if str(item)],
            }
        )
    rows.sort(key=lambda item: (-float(item["superscore"]), str(item["symbol"])))
    if not rows:
        return _placeholder_panel(
            section_id="scorecards",
            eyebrow="Scorecards",
            title="Scorecards Placeholder",
            body="Ticker scorecards are available on each tear sheet. This section is reserved for a portfolio-wide scorecard table.",
        )
    body_rows = []
    for item in rows:
        symbol = str(item["symbol"])
        top_reasons = "; ".join(item["reasons"][:3]) if item["reasons"] else "n/a"
        search_text = " ".join(
            [
                symbol,
                str(item["superscore"]),
                str(item["quality"]),
                str(item["growth"]),
                str(item["valuation"]),
                str(item["safety"]),
                str(item["market"]),
                str(item["investing_type"]),
                str(item["max_drawdown"]),
                _drawdown_cell(item["historical_drawdown"]),
                top_reasons,
            ]
        ).lower()
        body_rows.append(
            f"<tr data-paginated-item data-search-text=\"{escape(search_text)}\">"
            f"<td><a href=\"tickers/{escape(symbol)}.html\">{escape(symbol)}</a></td>"
            f"<td>{escape(_score_cell(item.get('superscore')))}</td>"
            f"<td>{escape(_score_cell(item.get('quality')))}</td>"
            f"<td>{escape(_score_cell(item.get('growth')))}</td>"
            f"<td>{escape(_score_cell(item.get('valuation')))}</td>"
            f"<td>{escape(_score_cell(item.get('safety')))}</td>"
            f"<td>{escape(_score_cell(item.get('market')))}</td>"
            f"<td>{escape(_short_text(str(item['investing_type']), 44))}</td>"
            f"<td>{escape(_short_text(str(item['max_drawdown']), 44))}</td>"
            f"<td>{escape(_drawdown_cell(item['historical_drawdown']))}</td>"
            f"<td>{escape(_short_text(top_reasons, 150))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"panel\" id=\"scorecards\">"
        "<div class=\"section-heading\">"
        "<p class=\"eyebrow\">Scorecards</p>"
        "<h2>Universe Scorecards</h2>"
        "</div>"
        "<p>Scorecards condense deterministic quality, growth, valuation, safety, and attention signals before deeper agent research makes final portfolio decisions.</p>"
        "<div class=\"pagination-shell\" data-paginated-list data-page-size=\"25\" data-pagination-label=\"scorecards\">"
        "<div class=\"table-scroll-top\" aria-hidden=\"true\"><div></div></div>"
        "<div class=\"table-scroll\"><table class=\"scorecards-table\">"
        "<thead><tr><th>Symbol</th><th title=\"Superscore\">Super</th><th title=\"Quality\">Qual</th><th title=\"Growth\">Grow</th><th title=\"Valuation\">Val</th><th title=\"Safety\">Safe</th><th title=\"Market Buzz\">Buzz</th><th title=\"Investing Type\">Type</th><th title=\"Estimated Drawdown\">Drawdown</th><th title=\"Historical Max Drawdown\">Hist DD</th><th>Top Reasons</th></tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        f"{_pagination_controls()}"
        "</div>"
        "</section>"
    )


def _evidence_gaps_section(
    *,
    symbols: Iterable[str],
    action_plan: Mapping[str, Any],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
) -> str:
    rows = []
    for symbol in symbols:
        intent = _intent_for_symbol(action_plan, symbol)
        evidence = evidence_by_symbol.get(symbol, {})
        gap_info = _evidence_gaps_for_symbol(intent=intent, evidence=evidence)
        if not any(gap_info.values()):
            continue
        gaps = list(gap_info["missing"]) + list(gap_info["promotion"]) + list(gap_info["warnings"])
        rows.append(
            {
                "symbol": symbol,
                "gap_count": len(gaps),
                "promotion": gap_info["promotion"],
                "missing": gap_info["missing"],
                "warnings": gap_info["warnings"],
                "next_step": _evidence_gap_next_step(gap_info),
            }
        )
    rows.sort(key=lambda item: (-int(item["gap_count"]), str(item["symbol"])))
    if not rows:
        return _placeholder_panel(
            section_id="evidence-gaps",
            eyebrow="Evidence Gaps",
            title="Evidence Gaps Placeholder",
            body="No evidence gaps were detected in the generated dashboard inputs.",
        )
    body_rows = []
    for item in rows:
        symbol = str(item["symbol"])
        promotion = "; ".join(str(value) for value in item["promotion"]) or "none"
        missing = "; ".join(str(value) for value in item["missing"]) or "none"
        warnings = "; ".join(str(value) for value in item["warnings"]) or "none"
        search_text = " ".join([symbol, promotion, missing, warnings, str(item["next_step"])]).lower()
        body_rows.append(
            f"<tr data-paginated-item data-search-text=\"{escape(search_text)}\">"
            f"<td><a href=\"tickers/{escape(symbol)}.html\">{escape(symbol)}</a></td>"
            f"<td>{int(item['gap_count'])}</td>"
            f"<td>{escape(_short_text(promotion, 120))}</td>"
            f"<td>{escape(_short_text(missing, 150))}</td>"
            f"<td>{escape(_short_text(warnings, 150))}</td>"
            f"<td>{escape(str(item['next_step']))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"panel\" id=\"evidence-gaps\">"
        "<div class=\"section-heading\">"
        "<p class=\"eyebrow\">Evidence Gaps</p>"
        "<h2>Research Follow-Up Queue</h2>"
        "</div>"
        "<p>Evidence gaps show what the next enrichment/research loop should fix before a symbol is trusted for stronger portfolio decisions.</p>"
        "<div class=\"pagination-shell\" data-paginated-list data-page-size=\"25\" data-pagination-label=\"evidence gaps\">"
        "<div class=\"table-scroll\"><table class=\"evidence-gaps-table\">"
        "<thead><tr><th>Symbol</th><th>Gaps</th><th>Promotion Follow-Up</th><th>Missing Evidence</th><th>Warnings</th><th>Suggested Next Step</th></tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        f"{_pagination_controls()}"
        "</div>"
        "</section>"
    )


def _evidence_gaps_for_symbol(*, intent: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, list[str]]:
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    promotion_gaps = [
        _humanize_reason(str(item))
        for item in [*(promotion.get("followups") or []), *(promotion.get("blockers") or [])]
        if str(item).strip()
    ]
    missing: list[str] = []
    if not evidence:
        missing.append("No evidence packet")
    if not str(evidence.get("business_summary") or "").strip():
        missing.append("Missing business summary")
    if not isinstance(evidence.get("fundamental_metrics"), Mapping):
        missing.append("Missing fundamentals")
    if not isinstance(evidence.get("quality_growth_scorecard"), Mapping):
        missing.append("Missing scorecard")
    if not _latest_earnings_for_evidence(evidence):
        missing.append("Missing latest earnings")
    if not _article_evidence_present(evidence):
        missing.append("Missing article evidence")
    warnings = _evidence_warnings(evidence)
    return {"promotion": promotion_gaps, "missing": missing, "warnings": warnings}


def _article_evidence_present(evidence: Mapping[str, Any]) -> bool:
    for key in ("article_evidence_summaries", "relevant_news"):
        value = evidence.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _evidence_warnings(evidence: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for key in ("warnings", "enrichment_warnings", "evidence_warnings"):
        value = evidence.get(key)
        if isinstance(value, list):
            warnings.extend(str(item) for item in value if str(item).strip())
        elif str(value or "").strip():
            warnings.append(str(value))
    for nested_key in ("quality_growth_scorecard", "latest_earnings", "latest_earnings_enrichment", "fundamental_metrics"):
        nested = evidence.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        value = nested.get("warnings")
        if isinstance(value, list):
            warnings.extend(str(item) for item in value if str(item).strip())
        elif str(value or "").strip():
            warnings.append(str(value))
    deduped: list[str] = []
    for item in warnings:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _evidence_gap_next_step(gap_info: Mapping[str, list[str]]) -> str:
    missing = " ".join(gap_info.get("missing") or []).lower()
    promotion = " ".join(gap_info.get("promotion") or []).lower()
    if "earnings" in promotion or "article" in promotion:
        return "Run news/earnings enrichment or capture company-page evidence."
    if "promotion" in promotion or promotion:
        return "Resolve promotion follow-up before paper planning."
    if "fundamentals" in missing or "scorecard" in missing:
        return "Run fundamentals and scorecard enrichment."
    if "earnings" in missing or "article" in missing:
        return "Run news/earnings enrichment or capture company-page evidence."
    if "business summary" in missing:
        return "Add company summary before committee research."
    return "Review enrichment warnings."


def _action_plan_symbols(action_plan: Mapping[str, Any], fallback_symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in action_plan.get("intents") or []:
        if not isinstance(item, Mapping):
            continue
        symbol = _symbol(item)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        ordered.append(symbol)
    if ordered:
        return ordered
    for symbol in fallback_symbols:
        normalized = str(symbol).upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _scorecard_metric(scorecard: Mapping[str, Any], analysis: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = scorecard.get(key)
        if _number(value) > 0:
            return value
        value = analysis.get(key)
        if _number(value) > 0:
            return value
    return None


def _actionability_label(value: str) -> str:
    labels = {
        "ACTIONABLE_BUY": "Actionable buy",
        "WATCHLIST_PENDING_EVIDENCE": "Watchlist / needs evidence",
        "WATCHLIST_PENDING_CONFIRMATION": "Watchlist / needs confirmation",
        "PARKING_GUIDANCE": "Parking guidance",
        "RESEARCH_ONLY": "Research only",
    }
    return labels.get(value, value.replace("_", " ").title())


def _actionability_sort_priority(value: str) -> int:
    priority = {
        "ACTIONABLE_BUY": 0,
        "WATCHLIST_PENDING_CONFIRMATION": 1,
        "WATCHLIST_PENDING_EVIDENCE": 2,
        "PARKING_GUIDANCE": 3,
        "RESEARCH_ONLY": 4,
    }
    return priority.get(value, 9)


def _actionability_for_intent(intent: Mapping[str, Any]) -> str:
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    decision = str(promotion.get("promotion_decision") or "").strip()
    if decision:
        return decision
    if _intent_type(intent) in PAPER_EXECUTABLE_INTENTS and bool(intent.get("allowed")):
        return "ACTIONABLE_BUY"
    if _intent_type(intent) in PARKING_INTENTS:
        return "PARKING_GUIDANCE"
    return "RESEARCH_ONLY"


def _why_not_buy(intent: Mapping[str, Any]) -> str:
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    followups = [str(item) for item in promotion.get("followups") or [] if str(item)]
    blockers = [str(item) for item in promotion.get("blockers") or [] if str(item)]
    reasons = followups + blockers
    decision = str(promotion.get("promotion_decision") or "")
    if _intent_type(intent) == "BUY" and decision == "ACTIONABLE_BUY" and not reasons:
        return "Cleared for staged BUY review."
    if reasons:
        return "; ".join(reasons)
    if _intent_type(intent) in PARKING_INTENTS:
        return "Parking guidance, not a stock BUY candidate."
    if _intent_type(intent) != "BUY":
        return str(intent.get("reason") or "Research-only candidate.")
    return str(intent.get("reason") or "Not cleared for BUY.")


def _humanize_reason(value: str) -> str:
    known = {
        "missing_earnings_article": "Missing earnings article",
        "confidence_below_actionable_threshold": "Confidence below actionable threshold",
    }
    parts = [part.strip() for part in str(value or "").split(";") if part.strip()]
    if not parts:
        return ""
    return "; ".join(known.get(part, part.replace("_", " ").strip().capitalize()) for part in parts)


def _review_score_for_symbol(*, intent: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[float, str]:
    scorecard = evidence.get("quality_growth_scorecard") if isinstance(evidence.get("quality_growth_scorecard"), Mapping) else {}
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    candidates = [
        (promotion.get("confidence"), "Promotion confidence"),
        (promotion.get("portfolio_fit_score"), "Portfolio fit"),
        (promotion.get("valuation_fit_score"), "Valuation fit"),
        (promotion.get("evidence_score"), "Promotion evidence score"),
        (promotion.get("quality_score"), "Promotion quality score"),
        (scorecard.get("superscore"), "Scorecard superscore"),
    ]
    for value, label in candidates:
        score = _number(value)
        if score > 0:
            return score, label
    return 0.0, "No review score"


def _score_cell(value: Any) -> str:
    score = _number(value)
    return f"{score:g}" if score > 0 else "n/a"


def _historical_max_drawdown_pct(history: Any) -> float | None:
    if not isinstance(history, list):
        return None
    peak: float | None = None
    max_drawdown = 0.0
    for item in history:
        if not isinstance(item, Mapping):
            continue
        close = _number(item.get("close") or item.get("current_price") or item.get("price"))
        if close <= 0:
            continue
        if peak is None or close > peak:
            peak = close
            continue
        drawdown = ((close - peak) / peak) * 100.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    return max_drawdown if peak is not None else None


def _drawdown_cell(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _latest_recommendation_html(buy_intents: list[Mapping[str, Any]]) -> str:
    if not buy_intents:
        return "<p>No active BUY recommendation cleared promotion review.</p>"
    item = buy_intents[0]
    promotion = item.get("promotion_review") if isinstance(item.get("promotion_review"), Mapping) else {}
    return (
        f"<p><strong>{escape(_symbol(item))}</strong> cleared as "
        f"{escape(str(promotion.get('promotion_decision') or 'BUY review'))}.</p>"
        f"<p>{escape(_short_text(str(item.get('reason') or ''), 160))}</p>"
        f"<a class=\"read-rec\" href=\"tickers/{escape(_symbol(item))}.html\">Read Recommendation</a>"
    )


def _holdings_table(portfolio_state: Mapping[str, Any] | None) -> str:
    holdings = _portfolio_holdings(portfolio_state or {})
    protected = {
        str(symbol).upper()
        for symbol in _mapping_get(portfolio_state or {}, "protected_symbols", default=[]) or []
    }
    if not holdings:
        return (
            "<div class=\"holdings-table-wrap\">"
            "<h3>Current Portfolio Holdings</h3>"
            "<table class=\"holdings-table\">"
            "<thead><tr><th>Symbol</th><th>Shares</th><th>Original Purchase Total Cost</th><th>Current Total Value</th><th>% Gain</th><th>Status</th></tr></thead>"
            "<tbody data-portfolio-holdings><tr><td colspan=\"6\">No current portfolio holdings were supplied for this generated dashboard.</td></tr></tbody>"
            "</table>"
            "</div>"
        )
    rows = []
    for holding in sorted(holdings, key=lambda item: _holding_symbol(item)):
        symbol = _holding_symbol(holding)
        quantity = _holding_number(holding, "quantity", "shares")
        current_value = _holding_number(holding, "market_value", "current_total_value", "current_value")
        cost = _holding_total_cost(holding, quantity=quantity)
        status = str(_mapping_get(holding, "status", default="") or "").strip()
        if not status:
            status = "Protected / core" if symbol in protected else "Active holding"
        rows.append(
            "<tr>"
            f"<td><a href=\"tickers/{escape(symbol)}.html\">{escape(symbol)}</a></td>"
            f"<td>{escape(_shares(quantity))}</td>"
            f"<td>{escape(_money_cents(cost))}</td>"
            f"<td>{escape(_money_cents(current_value))}</td>"
            f"<td>{escape(_gain_percent(current_value, cost))}</td>"
            f"<td>{escape(status)}</td>"
            "</tr>"
        )
    return (
        "<div class=\"holdings-table-wrap\">"
        "<h3>Current Portfolio Holdings</h3>"
        "<table class=\"holdings-table\">"
        "<thead><tr><th>Symbol</th><th>Shares</th><th>Original Purchase Total Cost</th><th>Current Total Value</th><th>% Gain</th><th>Status</th></tr></thead>"
        f"<tbody data-portfolio-holdings>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )


def _portfolio_value_panel(portfolio_state: Mapping[str, Any] | None) -> str:
    summary = _portfolio_summary(portfolio_state or {})
    totals = summary["totals"]
    return (
        "<div class=\"portfolio-live-card\" data-portfolio-summary>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Portfolio Totals</p>"
        "<h3>Current Value And Gain</h3>"
        "</div>"
        "<div class=\"portfolio-total-grid\">"
        f"<div><span>Total Current Value</span><strong data-portfolio-total=\"current_total_value\">{escape(_money_cents(totals['current_total_value']))}</strong></div>"
        f"<div><span>Total Cost</span><strong data-portfolio-total=\"original_purchase_total_cost\">{escape(_money_cents(totals['original_purchase_total_cost']))}</strong></div>"
        f"<div><span>Total Gain</span><strong data-portfolio-total=\"gain_amount\">{escape(_signed_money(totals['gain_amount']))}</strong></div>"
        f"<div><span>% Gain</span><strong data-portfolio-total=\"gain_percent\">{escape(_signed_percent(totals['gain_percent']))}</strong></div>"
        f"<div><span>Cash</span><strong data-portfolio-total=\"cash\">{escape(_money_cents(totals['cash']))}</strong></div>"
        "</div>"
        f"<div class=\"portfolio-gain-chart\" data-portfolio-gain-chart>{_portfolio_gain_chart(summary['holdings'])}</div>"
        "<p class=\"portfolio-live-note\">Dashboard polls the local read-only portfolio endpoint when served from localhost. Scheduler refreshes keep this fed from broker/account artifacts.</p>"
        "<small data-portfolio-last-updated>Local artifact snapshot</small>"
        "</div>"
    )


def _pipeline_health_panel() -> str:
    return (
        "<div class=\"pipeline-health-card\" data-pipeline-health>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Pipeline Artifact Health</p>"
        "<h3>Saved Run Integrity</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        "<div><span>Status</span><strong data-pipeline-health-status>Static Snapshot</strong></div>"
        "<div><span>Missing</span><strong data-pipeline-health-missing>n/a</strong></div>"
        "<div><span>Malformed</span><strong data-pipeline-health-malformed>n/a</strong></div>"
        "<div><span>Selected</span><strong data-pipeline-health-selected>n/a</strong></div>"
        "<div><span>Provider</span><strong data-pipeline-resource-provider>n/a</strong></div>"
        "<div><span>Research Cap</span><strong data-pipeline-resource-research-cap>n/a</strong></div>"
        "<div><span>Committee Cap</span><strong data-pipeline-resource-committee-cap>n/a</strong></div>"
        "<div><span>Bounded</span><strong data-pipeline-resource-bounded>n/a</strong></div>"
        "<div><span>Follow-Up Reviewed</span><strong data-pipeline-followup-reviewed>n/a</strong></div>"
        "<div><span>Follow-Up Step</span><strong data-pipeline-followup-next-step>n/a</strong></div>"
        "</div>"
        "<p data-pipeline-health-message>Serve the dashboard locally to check saved pipeline artifacts from "
        "<code>/api/pipeline-health.json</code>.</p>"
        "<small data-pipeline-health-updated>Static snapshot shown.</small>"
        "</div>"
    )


def _scheduler_config_validation_panel(validation: Mapping[str, Any]) -> str:
    status = _display_label(validation.get("status") or "unavailable")
    preset = _display_label(validation.get("preset") or "unavailable")
    config_file = str(validation.get("config_file") or "").strip()
    config_label = Path(config_file).name if config_file else "No profile validation artifact"
    mode = validation.get("operating_mode_summary") if isinstance(validation.get("operating_mode_summary"), Mapping) else {}
    mode_name = _display_label(mode.get("name") or "unavailable")
    recurring_ready = bool(validation.get("recurring_no_submit_ready"))
    recurring_ready_label = "Yes" if recurring_ready else "No"
    broker_boundary = _display_label(mode.get("broker_submit_boundary") or "unavailable")
    controls = validation.get("resource_controls") if isinstance(validation.get("resource_controls"), Mapping) else {}
    provider = _display_label(controls.get("provider_mode") or "unavailable")
    bounded = controls.get("bounded")
    bounded_label = "Yes" if bounded is True else "No" if bounded is False else "n/a"
    research_cap = controls.get("research_max_pass_count")
    committee_cap = controls.get("generated_committee_max_batches")
    next_action = _display_label(validation.get("next_safe_action") or "run_scheduler_config_validation_before_recurring_launch")
    return (
        "<div class=\"scheduler-validation-card\" data-scheduler-config-validation>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Scheduler Profile</p>"
        "<h3>Config Validation</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong data-scheduler-validation-status>{escape(status)}</strong></div>"
        f"<div><span>Mode</span><strong data-scheduler-validation-mode>{escape(mode_name)}</strong></div>"
        f"<div><span>Ready For Unattended No Submit</span><strong data-scheduler-validation-recurring-ready>{escape(recurring_ready_label)}</strong></div>"
        f"<div><span>Preset</span><strong data-scheduler-validation-preset>{escape(preset)}</strong></div>"
        f"<div><span>Profile</span><strong data-scheduler-validation-config>{escape(config_label)}</strong></div>"
        f"<div><span>Broker Boundary</span><strong data-scheduler-validation-boundary>{escape(broker_boundary)}</strong></div>"
        f"<div><span>Provider</span><strong data-scheduler-validation-provider>{escape(provider)}</strong></div>"
        f"<div><span>Research Cap</span><strong data-scheduler-validation-research-cap>{escape(str(research_cap if research_cap is not None else 'n/a'))}</strong></div>"
        f"<div><span>Committee Cap</span><strong data-scheduler-validation-committee-cap>{escape(str(committee_cap if committee_cap is not None else 'n/a'))}</strong></div>"
        f"<div><span>Bounded</span><strong data-scheduler-validation-bounded>{escape(bounded_label)}</strong></div>"
        "</div>"
        f"<p data-scheduler-validation-message>{escape(next_action)}</p>"
        "<small>Validation is read-only and does not create scheduler run folders or submit orders.</small>"
        "</div>"
    )


def _scheduler_task_plan_panel(task_plan: Mapping[str, Any]) -> str:
    status = _display_label(task_plan.get("status") or "unavailable")
    task_name = str(task_plan.get("task_name") or "No task plan artifact").strip()
    profile_file = str(task_plan.get("profile_file") or "").strip()
    profile_label = Path(profile_file).name if profile_file else "n/a"
    schedule = task_plan.get("schedule") if isinstance(task_plan.get("schedule"), Mapping) else {}
    schedule_label = " ".join(
        part
        for part in [
            _display_label(schedule.get("type") or ""),
            str(schedule.get("start_time") or "").strip(),
        ]
        if part
    ) or "n/a"
    next_action = _display_label(task_plan.get("next_safe_action") or "generate_windows_task_scheduler_plan")
    return (
        "<div class=\"scheduler-validation-card\" data-scheduler-task-plan>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Windows Task Scheduler</p>"
        "<h3>Registration Plan</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong data-scheduler-task-status>{escape(status)}</strong></div>"
        f"<div><span>Task</span><strong data-scheduler-task-name>{escape(task_name)}</strong></div>"
        f"<div><span>Profile</span><strong data-scheduler-task-profile>{escape(profile_label)}</strong></div>"
        f"<div><span>Schedule</span><strong data-scheduler-task-schedule>{escape(schedule_label)}</strong></div>"
        "</div>"
        f"<p data-scheduler-task-message>{escape(next_action)}</p>"
        "<small>This is a review artifact only; the dashboard does not register Windows tasks.</small>"
        "</div>"
    )


def _review_simulation_intents_panel(items: list[Mapping[str, Any]]) -> str:
    visible = [
        item
        for item in items
        if str(item.get("intent_type") or "").upper() in {"SELL", "REDUCE", "REBALANCE", "REVIEW", "HOLD"}
    ]
    if not visible:
        return ""
    return (
        "<section class=\"panel\" id=\"review-simulation-intents\">"
        "<div class=\"section-heading\">"
        "<p class=\"eyebrow\">Review / Simulation</p>"
        "<h2>Review / Simulation Intents</h2>"
        "</div>"
        "<p>Sell and rebalance candidates remain visible for operator review but are never Stage 6B V1 paper-submit candidates.</p>"
        f"{_intent_rows(visible, empty_label='No review/simulation intents.')}"
        "</section>"
    )


def _scheduler_readiness_section(
    *,
    scheduler_config_validation: Mapping[str, Any],
    scheduler_task_plan: Mapping[str, Any],
    scheduler_handoff: Mapping[str, Any],
    scheduler_task_registration: Mapping[str, Any],
    scheduler_chain: Mapping[str, Any],
) -> str:
    chain_status = _display_label(scheduler_chain.get("status") or "unavailable")
    registration = (
        scheduler_chain.get("registration_readiness")
        if isinstance(scheduler_chain.get("registration_readiness"), Mapping)
        else {}
    )
    registration_status = _display_label(registration.get("status") or scheduler_task_registration.get("status") or "unavailable")
    blockers = [str(item) for item in (scheduler_chain.get("blockers") or []) if str(item).strip()]
    blocker_label = ", ".join(_display_label(item) for item in blockers) or "none"
    submit_label = "off" if scheduler_chain.get("order_submission_enabled") is False else "unknown"
    return (
        "<section class=\"panel scheduler-readiness-panel\" id=\"scheduler-readiness\">"
        "<div class=\"section-heading\">"
        "<p class=\"eyebrow\">Scheduler Readiness</p>"
        "<h2>No-Submit Launch Review</h2>"
        "</div>"
        "<p>This is the operator checkpoint for unattended monitoring/research. It is separate from broker authorization and keeps order submission disabled.</p>"
        "<div class=\"scheduler-readiness-strip\">"
        f"<div><span>Launch Packet</span><strong>{escape(_scheduler_brief_status(chain_status))}</strong><small>{escape(chain_status)}</small></div>"
        f"<div><span>Registration</span><strong>{escape(_scheduler_brief_status(registration_status))}</strong><small>{escape(registration_status)}</small></div>"
        f"<div><span>Blockers</span><strong>{escape(_scheduler_brief_status(blocker_label))}</strong><small>{escape(blocker_label)}</small></div>"
        f"<div><span>Broker Submit</span><strong>{escape(_scheduler_brief_status(submit_label))}</strong><small>Order submission remains disabled.</small></div>"
        "</div>"
        "<p class=\"scheduler-readiness-note\"><strong>Expected:</strong> Launch Packet = Ready, Registration = Ready, Blockers = None, Broker Submit = Off. That means it is safe to register the no-submit monitoring task, not that paper orders are authorized.</p>"
        "<div class=\"scheduler-card-stack\">"
        f"{_scheduler_chain_panel(scheduler_chain)}"
        f"{_scheduler_config_validation_panel(scheduler_config_validation)}"
        f"{_scheduler_task_plan_panel(scheduler_task_plan)}"
        f"{_scheduler_handoff_panel(scheduler_handoff)}"
        f"{_scheduler_task_registration_panel(scheduler_task_registration)}"
        "</div>"
        "</section>"
    )


def _scheduler_brief_status(value: object) -> str:
    text = _display_label(value)
    normalized = text.lower()
    if normalized in {"none", "no blockers"}:
        return "None"
    if normalized in {"off", "disabled", "false"}:
        return "Off"
    if normalized.startswith("ready"):
        return "Ready"
    if "ready" in normalized and "not" not in normalized:
        return "Ready"
    if normalized in {"unavailable", "unknown", "n/a"}:
        return text
    return text


def _scheduler_handoff_panel(handoff: Mapping[str, Any]) -> str:
    status = _display_label(handoff.get("status") or "unavailable")
    next_action = _display_label(handoff.get("next_safe_action") or "generate_scheduler_handoff_check")
    checks = handoff.get("checks") if isinstance(handoff.get("checks"), Mapping) else {}
    blockers = [str(item) for item in (handoff.get("blockers") or []) if str(item).strip()]
    validation = _display_label(checks.get("scheduler_config_validation") or "n/a")
    task = _display_label(checks.get("scheduler_task_plan") or "n/a")
    manifest = _display_label(checks.get("dashboard_manifest") or "n/a")
    boundary = _display_label(checks.get("order_submission_boundary") or "n/a")
    blocker_text = ", ".join(_display_label(item) for item in blockers) or "none"
    return (
        "<div class=\"scheduler-validation-card\" data-scheduler-handoff>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Scheduler Handoff</p>"
        "<h3>Launch Readiness Packet</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong data-scheduler-handoff-status>{escape(status)}</strong></div>"
        f"<div><span>Profile</span><strong>{escape(validation)}</strong></div>"
        f"<div><span>Task Plan</span><strong>{escape(task)}</strong></div>"
        f"<div><span>Manifest</span><strong>{escape(manifest)}</strong></div>"
        f"<div><span>Submit Boundary</span><strong>{escape(boundary)}</strong></div>"
        f"<div><span>Blockers</span><strong>{escape(blocker_text)}</strong></div>"
        "</div>"
        f"<p data-scheduler-handoff-message>{escape(next_action)}</p>"
        "<small>Handoff is advisory and read-only; recurring task registration remains a separate operator action.</small>"
        "</div>"
    )


def _scheduler_task_registration_panel(registration: Mapping[str, Any]) -> str:
    status = _display_label(registration.get("status") or "unavailable")
    task_name = str(registration.get("task_name") or "No registration artifact").strip()
    requested = "Yes" if bool(registration.get("registration_requested")) else "No"
    executed = "Yes" if bool(registration.get("registration_executed")) else "No"
    command = str(registration.get("registration_command") or "").strip()
    command_label = command if command else "n/a"
    next_action = _display_label(registration.get("next_safe_action") or "run_scheduler_task_registration_review")
    return (
        "<div class=\"scheduler-validation-card\" data-scheduler-task-registration>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Task Registration Review</p>"
        "<h3>Guarded Register Step</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong data-scheduler-registration-status>{escape(status)}</strong></div>"
        f"<div><span>Task</span><strong data-scheduler-registration-task>{escape(task_name)}</strong></div>"
        f"<div><span>Requested</span><strong>{escape(requested)}</strong></div>"
        f"<div><span>Executed</span><strong>{escape(executed)}</strong></div>"
        "</div>"
        f"<p data-scheduler-registration-message>{escape(next_action)}</p>"
        f"<small>Review command: {escape(command_label)}. Dashboard is read-only; actual registration requires the guarded CLI confirmation.</small>"
        "</div>"
    )


def _scheduler_chain_panel(chain: Mapping[str, Any]) -> str:
    status = _display_label(chain.get("status") or "unavailable")
    next_action = _display_label(chain.get("next_safe_action") or "build_scheduler_launch_packet")
    provider_usage = chain.get("provider_usage_review") if isinstance(chain.get("provider_usage_review"), Mapping) else {}
    research_queue = chain.get("research_queue_review") if isinstance(chain.get("research_queue_review"), Mapping) else {}
    soak_review = chain.get("scheduler_soak_review") if isinstance(chain.get("scheduler_soak_review"), Mapping) else {}
    registration = chain.get("registration_readiness") if isinstance(chain.get("registration_readiness"), Mapping) else {}
    providers = ", ".join(str(item) for item in provider_usage.get("providers") or []) or "n/a"
    selected_count = int(_number(research_queue.get("selected_count")))
    rows = []
    for step in chain.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        rows.append(
            "<li>"
            f"<strong>{escape(_display_label(step.get('name') or 'step'))}</strong>"
            f"<span>{escape(_display_label(step.get('status') or 'unknown'))}</span>"
            "</li>"
        )
    if not rows:
        rows.append("<li><strong>No chain</strong><span>unavailable</span></li>")
    blockers = ", ".join(_display_label(item) for item in chain.get("blockers") or []) or "none"
    return (
        "<div class=\"scheduler-validation-card\" data-scheduler-chain>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Scheduler Chain</p>"
        "<h3>No-Submit Timeline</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong data-scheduler-chain-status>{escape(status)}</strong></div>"
        f"<div><span>Blockers</span><strong>{escape(blockers)}</strong></div>"
        f"<div><span>Provider Usage</span><strong>{escape(_display_label(provider_usage.get('status') or 'unavailable'))}</strong><small>{escape(providers)}</small></div>"
        f"<div><span>Research Queue</span><strong>{selected_count}</strong><small>{escape(_display_label(research_queue.get('status') or 'unavailable'))}</small></div>"
        f"<div><span>Soak Plan</span><strong>{escape(_display_label(soak_review.get('status') or 'unavailable'))}</strong></div>"
        f"<div><span>Registration Readiness</span><strong>{escape(_display_label(registration.get('status') or 'unavailable'))}</strong></div>"
        "</div>"
        f"<ul class=\"compact-list\">{''.join(rows)}</ul>"
        f"<p data-scheduler-chain-message>{escape(next_action)}</p>"
        "<small>Timeline is read-only and never starts the scheduler or submits broker orders.</small>"
        "</div>"
    )


def _position_review_queue_panel(queue: Mapping[str, Any]) -> str:
    status = _display_label(queue.get("status") or "unavailable")
    review_count = int(_number(queue.get("review_count")))
    counts = queue.get("counts_by_review_type") if isinstance(queue.get("counts_by_review_type"), Mapping) else {}
    rows = []
    for item in (queue.get("review_queue") or [])[:5]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            "<li>"
            f"<strong>{escape(str(item.get('symbol') or 'n/a'))}</strong>"
            f"<span>{escape(_display_label(item.get('review_type') or 'review'))}</span>"
            f"<em>{escape(_display_label(item.get('severity') or 'unknown'))}</em>"
            "</li>"
        )
    counts_label = ", ".join(
        f"{_display_label(key)}: {value}"
        for key, value in sorted(counts.items())
    ) or "none"
    excluded = ", ".join(str(item) for item in (queue.get("excluded_protected_symbols") or [])) or "none"
    return (
        "<div class=\"scheduler-validation-card\" data-position-review-queue>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Position Review Queue</p>"
        "<h3>Sell / Rebalance / News Review</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong>{escape(status)}</strong></div>"
        f"<div><span>Rows</span><strong>{review_count}</strong></div>"
        f"<div><span>Types</span><strong>{escape(counts_label)}</strong></div>"
        f"<div><span>Protected Excluded</span><strong>{escape(excluded)}</strong></div>"
        "</div>"
        f"<ul class=\"review-queue-list\">{''.join(rows) or '<li>No current review rows.</li>'}</ul>"
        "<small>No-submit queue only; rows are not authorization to sell, rebalance, or submit orders.</small>"
        "</div>"
    )


def _paper_submit_mode_plan_panel(plan: Mapping[str, Any]) -> str:
    status = _display_label(plan.get("status") or "unavailable")
    next_action = _display_label(plan.get("next_safe_action") or "generate_paper_submit_mode_plan")
    checks = plan.get("checks") if isinstance(plan.get("checks"), Mapping) else {}
    blockers = [str(item) for item in (plan.get("blockers") or []) if str(item).strip()]
    blocker_label = ", ".join(_display_label(item) for item in blockers) or "none"
    check_rows = "".join(
        f"<div><span>{escape(_display_label(key))}</span><strong>{escape(_display_label(value))}</strong></div>"
        for key, value in sorted(checks.items())
    )
    return (
        "<div class=\"scheduler-validation-card\" data-paper-submit-mode-plan>"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Paper Submit Mode Plan</p>"
        "<h3>Disabled Submit-Profile Readiness</h3>"
        "</div>"
        "<div class=\"pipeline-health-grid\">"
        f"<div><span>Status</span><strong>{escape(status)}</strong></div>"
        f"<div><span>Blockers</span><strong>{escape(blocker_label)}</strong></div>"
        f"{check_rows}"
        "</div>"
        f"<p>{escape(next_action)}</p>"
        "<small>Checklist only; no submit profile is enabled and no runnable submit command is emitted.</small>"
        "</div>"
    )


def _tax_mode_suppressions_panel(reasons: Iterable[Any]) -> str:
    items = [str(reason).strip() for reason in reasons if str(reason).strip()]
    if not items:
        return ""
    rows = "".join(
        "<li>"
        f"<strong>{escape(_display_label(reason))}</strong>"
        f"<code>{escape(reason)}</code>"
        "</li>"
        for reason in items
    )
    return (
        "<div class=\"tax-suppression-card\">"
        "<div class=\"section-heading compact-heading\">"
        "<p class=\"eyebrow\">Tax-Mode Suppressions</p>"
        "<h3>Broad Actions Held Back</h3>"
        "</div>"
        "<p>These are advisory suppressions, not broker-submit blockers. Stock-specific sells can still be reviewed separately.</p>"
        f"<ul>{rows}</ul>"
        "</div>"
    )


def _api_usage_panel(api_usage: Mapping[str, Any] | None) -> str:
    usage = api_usage or {}
    providers = [dict(item) for item in usage.get("providers") or [] if isinstance(item, Mapping)]
    totals = usage.get("totals") if isinstance(usage.get("totals"), Mapping) else {}
    tier = usage.get("tier_tracking") if isinstance(usage.get("tier_tracking"), Mapping) else {}
    if not providers:
        provider_cards = (
            "<div class=\"usage-provider-card usage-provider-empty\">"
            "<span>No Paid Usage Yet</span>"
            "<strong>$0.00</strong>"
            "<small>Run a Perplexity or Grok enrichment artifact with usage tracking to populate this panel.</small>"
            "</div>"
        )
    else:
        provider_cards = "".join(_api_usage_provider_card(provider) for provider in providers)
    progress = max(0.0, min(100.0, _number(tier.get("progress_percent"))))
    remaining = tier.get("estimated_remaining_to_tier_1_usd")
    remaining_text = _money_cents(remaining) if remaining not in (None, "") else "n/a"
    return (
        "<section class=\"panel\" id=\"api-usage\">"
        "<div class=\"section-heading\">"
        "<p class=\"eyebrow\">API Usage</p>"
        "<h2>Model Spend And Tier Progress</h2>"
        "</div>"
        "<p>Broad enrichment should prefer Python, Finnhub, Polygon, and cached artifacts first; paid model calls are tracked here so Perplexity and Grok 4.3 usage stays deliberate.</p>"
        "<div class=\"api-usage-card\" data-api-usage>"
        "<div class=\"api-usage-total-grid\">"
        f"<div><span>Estimated Cost</span><strong data-api-usage-total=\"estimated_total_cost_usd\">{escape(_money_cents(totals.get('estimated_total_cost_usd')))}</strong></div>"
        f"<div><span>Requests</span><strong data-api-usage-total=\"request_count\">{int(_number(totals.get('request_count')))}</strong></div>"
        f"<div><span>Tokens</span><strong data-api-usage-total=\"total_tokens\">{int(_number(totals.get('total_tokens'))):,}</strong></div>"
        f"<div><span>Tier 1 Remaining</span><strong data-api-usage-tier=\"remaining\">{escape(remaining_text)}</strong></div>"
        "</div>"
        f"<div class=\"usage-progress\" aria-label=\"Perplexity tier progress\"><i data-api-usage-progress style=\"width:{progress:.1f}%\"></i></div>"
        "<div class=\"usage-provider-grid\" data-api-usage-providers>"
        f"{provider_cards}"
        "</div>"
        f"<small data-api-usage-updated>{escape(_api_usage_status_text(usage))}</small>"
        "</div>"
        "</section>"
    )


def _api_usage_provider_card(provider: Mapping[str, Any]) -> str:
    provider_label = _provider_display_label(provider.get("provider"))
    model = str(provider.get("model") or "unknown")
    cost = _money_cents(provider.get("estimated_total_cost_usd"))
    requests = int(_number(provider.get("request_count")))
    tokens = int(_number(provider.get("total_tokens")))
    context = str(provider.get("search_context_size") or "").strip()
    detail = f"{requests} requests / {tokens:,} tokens"
    if context:
        detail = f"{detail} / {context} context"
    return (
        "<div class=\"usage-provider-card\">"
        f"<span>{escape(provider_label)}</span>"
        f"<strong>{escape(cost)}</strong>"
        f"<small>{escape(model)} - {escape(detail)}</small>"
        "</div>"
    )


def _provider_display_label(value: Any) -> str:
    text = str(value or "unknown").strip()
    labels = {
        "perplexity": "Perplexity",
        "xai": "xAI / Grok",
        "grok": "xAI / Grok",
        "PerplexityResearchClient": "Perplexity",
    }
    return labels.get(text, _display_label(text))


def _api_usage_status_text(usage: Mapping[str, Any]) -> str:
    status = str(usage.get("status") or "unavailable")
    source = str(usage.get("source_path") or "").strip()
    if source:
        return f"Static usage snapshot from {Path(source).name}: {_display_label(status)}."
    return f"Static usage snapshot: {_display_label(status)}."


def _portfolio_summary(portfolio_state: Mapping[str, Any] | None) -> dict[str, Any]:
    protected = {
        str(symbol).upper()
        for symbol in _mapping_get(portfolio_state or {}, "protected_symbols", default=[]) or []
    }
    holdings = []
    totals = {
        "original_purchase_total_cost": 0.0,
        "current_total_value": 0.0,
        "gain_amount": 0.0,
        "gain_percent": 0.0,
        "cash": _number(_mapping_get(portfolio_state or {}, "cash", default=0.0)),
    }
    for holding in _portfolio_holdings(portfolio_state or {}):
        symbol = _holding_symbol(holding)
        quantity = _holding_number(holding, "quantity", "shares")
        current_price = _holding_number(holding, "current_price")
        current_value = _holding_number(holding, "market_value", "current_total_value", "current_value")
        if current_value <= 0 and current_price > 0 and quantity > 0:
            current_value = current_price * quantity
        cost = _holding_total_cost(holding, quantity=quantity)
        gain_amount = current_value - cost if cost > 0 else 0.0
        gain_percent = (gain_amount / cost) * 100.0 if cost > 0 else 0.0
        status = str(_mapping_get(holding, "status", default="") or "").strip()
        if not status:
            status = "Protected / core" if symbol in protected else "Active holding"
        holdings.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "current_price": current_price,
                "original_purchase_total_cost": cost,
                "current_total_value": current_value,
                "gain_amount": gain_amount,
                "gain_percent": gain_percent,
                "status": status,
            }
        )
        totals["original_purchase_total_cost"] += cost
        totals["current_total_value"] += current_value
    totals["gain_amount"] = totals["current_total_value"] - totals["original_purchase_total_cost"]
    if totals["original_purchase_total_cost"] > 0:
        totals["gain_percent"] = (totals["gain_amount"] / totals["original_purchase_total_cost"]) * 100.0
    holdings.sort(key=lambda item: str(item["symbol"]))
    return {"holdings": holdings, "totals": totals}


def _portfolio_gain_chart(holdings: list[Mapping[str, Any]]) -> str:
    if not holdings:
        return "<p>No holdings to chart yet.</p>"
    max_abs = max(abs(_number(item.get("gain_percent"))) for item in holdings) or 1.0
    rows = []
    for item in holdings[:12]:
        symbol = str(item.get("symbol") or "")
        gain = _number(item.get("gain_percent"))
        width = max(3.0, min(100.0, (abs(gain) / max_abs) * 100.0))
        tone = "positive" if gain >= 0 else "negative"
        rows.append(
            "<div class=\"portfolio-gain-row\">"
            f"<span>{escape(symbol)}</span>"
            f"<i class=\"{tone}\" style=\"width:{width:.1f}%\"></i>"
            f"<strong>{escape(_signed_percent(gain))}</strong>"
            "</div>"
        )
    return "".join(rows)


def _portfolio_holdings(portfolio_state: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    raw_holdings = _mapping_get(portfolio_state or {}, "holdings", default=[]) or []
    if not isinstance(raw_holdings, Iterable) or isinstance(raw_holdings, (str, bytes)):
        return []
    holdings = []
    for item in raw_holdings:
        if isinstance(item, Mapping) and _holding_symbol(item):
            holdings.append(item)
    return holdings


def _holding_symbol(holding: Mapping[str, Any]) -> str:
    return str(_mapping_get(holding, "symbol", default="") or "").upper().strip()


def _holding_number(holding: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = _mapping_get(holding, key, default=None)
        if value not in (None, ""):
            return _number(value)
    return 0.0


def _holding_total_cost(holding: Mapping[str, Any], *, quantity: float) -> float:
    explicit = _holding_number(
        holding,
        "original_purchase_total_cost",
        "purchase_total_cost",
        "total_cost",
        "cost_basis_total",
        "cost_basis",
    )
    if explicit > 0:
        return explicit
    avg_entry = _holding_number(holding, "avg_entry_price", "average_entry_price", "purchase_price")
    return round(quantity * avg_entry, 2) if quantity > 0 and avg_entry > 0 else 0.0


def _mapping_get(value: Any, key: str, *, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _pagination_controls() -> str:
    return (
        "<div class=\"pagination-controls\">"
        "<button type=\"button\" class=\"pagination-prev\">Previous</button>"
        "<span class=\"pagination-status\">Showing all</span>"
        "<button type=\"button\" class=\"pagination-next\">Next</button>"
        "</div>"
    )


def _dashboard_search_script() -> str:
    return r"""
(function initDashboardSearch(){
  const input = document.querySelector(".dashboard-search");
  const searchable = Array.from(document.querySelectorAll("[data-search-text]"));
  if (!input || !searchable.length) return;
  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    searchable.forEach(item => {
      const haystack = item.getAttribute("data-search-text") || "";
      item.dataset.searchHidden = Boolean(query) && !haystack.includes(query) ? "true" : "false";
    });
    if (window.refreshPaginatedLists) {
      window.refreshPaginatedLists({ resetPage: true });
    }
  });
})();
"""


def _paginated_lists_script() -> str:
    return r"""
(function initPaginatedLists(){
  const applyVisibility = (item) => {
    item.hidden = item.dataset.searchHidden === "true" || item.dataset.pageHidden === "true";
  };
  const refresh = ({ resetPage = false } = {}) => {
    document.querySelectorAll("[data-paginated-list]").forEach((list) => {
      const items = Array.from(list.querySelectorAll("[data-paginated-item]"));
      const pageSize = Math.max(1, parseInt(list.dataset.pageSize || "25", 10));
      const label = list.dataset.paginationLabel || "items";
      const candidates = items.filter((item) => item.dataset.searchHidden !== "true");
      const pageCount = Math.max(1, Math.ceil(candidates.length / pageSize));
      let page = resetPage ? 1 : parseInt(list.dataset.page || "1", 10);
      if (!Number.isFinite(page) || page < 1) page = 1;
      if (page > pageCount) page = pageCount;
      list.dataset.page = String(page);
      const start = (page - 1) * pageSize;
      const end = start + pageSize;
      candidates.forEach((item, index) => {
        item.dataset.pageHidden = index >= start && index < end ? "false" : "true";
      });
      items.filter((item) => item.dataset.searchHidden === "true").forEach((item) => {
        item.dataset.pageHidden = "false";
      });
      items.forEach(applyVisibility);
      const status = list.querySelector(".pagination-status");
      const prev = list.querySelector(".pagination-prev");
      const next = list.querySelector(".pagination-next");
      const visibleStart = candidates.length ? start + 1 : 0;
      const visibleEnd = Math.min(end, candidates.length);
      if (status) status.textContent = `Showing ${visibleStart}-${visibleEnd} of ${candidates.length} ${label}`;
      if (prev) {
        prev.disabled = page <= 1;
        prev.onclick = () => {
          list.dataset.page = String(Math.max(1, page - 1));
          refresh();
        };
      }
      if (next) {
        next.disabled = page >= pageCount;
        next.onclick = () => {
          list.dataset.page = String(Math.min(pageCount, page + 1));
          refresh();
        };
      }
    });
  };
  window.refreshPaginatedLists = refresh;
  refresh();
})();
"""


def _synced_table_scroller_script() -> str:
    return r"""
(function initSyncedTableScrollers(){
  document.querySelectorAll(".table-scroll-top").forEach((top) => {
    const bottom = top.nextElementSibling;
    const table = bottom ? bottom.querySelector("table") : null;
    const spacer = top.querySelector("div");
    if (!bottom || !bottom.classList.contains("table-scroll") || !table || !spacer) return;
    spacer.style.width = `${table.scrollWidth}px`;
    let syncing = false;
    const sync = (source, target) => {
      if (syncing) return;
      syncing = true;
      target.scrollLeft = source.scrollLeft;
      requestAnimationFrame(() => { syncing = false; });
    };
    top.addEventListener("scroll", () => sync(top, bottom), { passive: true });
    bottom.addEventListener("scroll", () => sync(bottom, top), { passive: true });
  });
})();
"""


def _portfolio_live_refresh_script() -> str:
    return r"""
(function initPortfolioLiveRefresh(){
  const summary = document.querySelector("[data-portfolio-summary]");
  const body = document.querySelector("[data-portfolio-holdings]");
  if (!summary || !body) return;
  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const shares = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });
  const signedMoney = (value) => {
    const amount = Number(value || 0);
    return `${amount >= 0 ? "+" : "-"}${money.format(Math.abs(amount))}`;
  };
  const signedPercent = (value) => {
    const amount = Number(value || 0);
    return `${amount >= 0 ? "+" : ""}${amount.toFixed(2)}%`;
  };
  const text = (value) => String(value ?? "");
  const escapeHtml = (value) => text(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
  const chart = document.querySelector("[data-portfolio-gain-chart]");
  const updated = document.querySelector("[data-portfolio-last-updated]");
  function renderChart(holdings) {
    if (!chart) return;
    if (!holdings.length) {
      chart.innerHTML = "<p>No holdings to chart yet.</p>";
      return;
    }
    const maxAbs = Math.max(...holdings.map((item) => Math.abs(Number(item.gain_percent || 0))), 1);
    chart.innerHTML = holdings.slice(0, 12).map((item) => {
      const gain = Number(item.gain_percent || 0);
      const width = Math.max(3, Math.min(100, (Math.abs(gain) / maxAbs) * 100));
      const tone = gain >= 0 ? "positive" : "negative";
      return `<div class="portfolio-gain-row"><span>${escapeHtml(item.symbol)}</span><i class="${tone}" style="width:${width.toFixed(1)}%"></i><strong>${signedPercent(gain)}</strong></div>`;
    }).join("");
  }
  function renderHoldings(holdings) {
    if (!holdings.length) {
      body.innerHTML = '<tr><td colspan="6">No holdings are currently available from the live portfolio endpoint.</td></tr>';
      return;
    }
    body.innerHTML = holdings.map((item) => {
      const symbol = escapeHtml(item.symbol);
      return `<tr><td><a href="tickers/${symbol}.html">${symbol}</a></td><td>${shares.format(Number(item.quantity || 0))}</td><td>${money.format(Number(item.original_purchase_total_cost || 0))}</td><td>${money.format(Number(item.current_total_value || 0))}</td><td>${signedPercent(item.gain_percent)}</td><td>${escapeHtml(item.status || "Active holding")}</td></tr>`;
    }).join("");
  }
  function render(payload) {
    const totals = payload.totals || {};
    const fields = {
      current_total_value: money.format(Number(totals.current_total_value || 0)),
      original_purchase_total_cost: money.format(Number(totals.original_purchase_total_cost || 0)),
      gain_amount: signedMoney(totals.gain_amount),
      gain_percent: signedPercent(totals.gain_percent),
      cash: money.format(Number(totals.cash || 0))
    };
    Object.entries(fields).forEach(([key, value]) => {
      document.querySelectorAll(`[data-portfolio-total="${key}"]`).forEach((node) => { node.textContent = value; });
    });
    const holdings = Array.isArray(payload.holdings) ? payload.holdings : [];
    renderHoldings(holdings);
    renderChart(holdings);
    if (updated) updated.textContent = `Updated from local portfolio endpoint: ${payload.generated_at || "now"}`;
  }
  async function refresh() {
    try {
      const response = await fetch("/api/portfolio.json", { cache: "no-store" });
      if (!response.ok) return;
      render(await response.json());
    } catch (error) {
      if (updated) updated.textContent = "Static snapshot shown; live local endpoint unavailable.";
    }
  }
  refresh();
  window.setInterval(refresh, 30000);
})();
"""


def _pipeline_health_refresh_script() -> str:
    return r"""
(function initPipelineHealthRefresh(){
  const card = document.querySelector("[data-pipeline-health]");
  if (!card) return;
  const statusNode = card.querySelector("[data-pipeline-health-status]");
  const missingNode = card.querySelector("[data-pipeline-health-missing]");
  const malformedNode = card.querySelector("[data-pipeline-health-malformed]");
  const selectedNode = card.querySelector("[data-pipeline-health-selected]");
  const providerNode = card.querySelector("[data-pipeline-resource-provider]");
  const researchCapNode = card.querySelector("[data-pipeline-resource-research-cap]");
  const committeeCapNode = card.querySelector("[data-pipeline-resource-committee-cap]");
  const boundedNode = card.querySelector("[data-pipeline-resource-bounded]");
  const followupReviewedNode = card.querySelector("[data-pipeline-followup-reviewed]");
  const followupNextStepNode = card.querySelector("[data-pipeline-followup-next-step]");
  const messageNode = card.querySelector("[data-pipeline-health-message]");
  const updatedNode = card.querySelector("[data-pipeline-health-updated]");
  const titleCase = (value) => String(value || "unknown").replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  function render(payload) {
    const health = payload.health || {};
    const rollup = payload.rollup || {};
    const selection = rollup.research_selection || {};
    const portfolioNews = rollup.portfolio_news_monitor || {};
    const controls = payload.resource_controls || {};
    if (statusNode) statusNode.textContent = titleCase(payload.status || health.status);
    if (missingNode) missingNode.textContent = String(health.missing_count ?? 0);
    if (malformedNode) malformedNode.textContent = String(health.malformed_count ?? 0);
    if (selectedNode) selectedNode.textContent = String(selection.selected_count ?? 0);
    if (providerNode) providerNode.textContent = titleCase(controls.provider_mode || "unavailable");
    if (researchCapNode) researchCapNode.textContent = controls.research_max_pass_count == null ? "n/a" : String(controls.research_max_pass_count);
    if (committeeCapNode) committeeCapNode.textContent = controls.generated_committee_max_batches == null ? "n/a" : String(controls.generated_committee_max_batches);
    if (boundedNode) boundedNode.textContent = controls.bounded == null ? "n/a" : (controls.bounded ? "Yes" : "No");
    if (followupReviewedNode) followupReviewedNode.textContent = String(portfolioNews.followup_reviewed_count ?? 0);
    if (followupNextStepNode) followupNextStepNode.textContent = portfolioNews.followup_review_next_action ? titleCase(portfolioNews.followup_review_next_action) : "n/a";
    if (messageNode) messageNode.textContent = payload.next_safe_action ? titleCase(payload.next_safe_action) : "Pipeline health loaded.";
    if (updatedNode) updatedNode.textContent = `Checked from local pipeline endpoint: ${payload.pipeline_status || "unknown"}`;
    card.dataset.pipelineHealthState = String(payload.status || health.status || "unknown");
  }
  async function refresh() {
    try {
      const response = await fetch("/api/pipeline-health.json", { cache: "no-store" });
      if (!response.ok) return;
      render(await response.json());
    } catch (error) {
      if (updatedNode) updatedNode.textContent = "Static snapshot shown; live local pipeline endpoint unavailable.";
    }
  }
  refresh();
  window.setInterval(refresh, 45000);
})();
"""


def _api_usage_refresh_script() -> str:
    return r"""
(function initApiUsageRefresh(){
  const card = document.querySelector("[data-api-usage]");
  if (!card) return;
  const totalCost = card.querySelector('[data-api-usage-total="estimated_total_cost_usd"]');
  const requestCount = card.querySelector('[data-api-usage-total="request_count"]');
  const totalTokens = card.querySelector('[data-api-usage-total="total_tokens"]');
  const remaining = card.querySelector('[data-api-usage-tier="remaining"]');
  const progress = card.querySelector("[data-api-usage-progress]");
  const providerGrid = card.querySelector("[data-api-usage-providers]");
  const updated = card.querySelector("[data-api-usage-updated]");
  const money = (value) => {
    const amount = Number(value || 0);
    return amount ? `$${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "$0.00";
  };
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const titleCase = (value) => String(value || "unknown").replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  const providerLabel = (value) => {
    const text = String(value || "unknown");
    if (text === "perplexity" || text === "PerplexityResearchClient") return "Perplexity";
    if (text === "xai" || text === "grok") return "xAI / Grok";
    return titleCase(text);
  };
  function providerCard(provider) {
    const requests = Number(provider.request_count || 0);
    const tokens = Number(provider.total_tokens || 0);
    const context = provider.search_context_size ? ` / ${escapeHtml(provider.search_context_size)} context` : "";
    return `<div class="usage-provider-card"><span>${escapeHtml(providerLabel(provider.provider))}</span><strong>${money(provider.estimated_total_cost_usd)}</strong><small>${escapeHtml(provider.model || "unknown")} - ${requests} requests / ${tokens.toLocaleString()} tokens${context}</small></div>`;
  }
  function render(payload) {
    const totals = payload.totals || {};
    const tier = payload.tier_tracking || {};
    if (totalCost) totalCost.textContent = money(totals.estimated_total_cost_usd);
    if (requestCount) requestCount.textContent = String(totals.request_count || 0);
    if (totalTokens) totalTokens.textContent = Number(totals.total_tokens || 0).toLocaleString();
    if (remaining) remaining.textContent = tier.estimated_remaining_to_tier_1_usd == null ? "n/a" : money(tier.estimated_remaining_to_tier_1_usd);
    if (progress) progress.style.width = `${Math.max(0, Math.min(100, Number(tier.progress_percent || 0)))}%`;
    if (providerGrid) {
      const providers = Array.isArray(payload.providers) ? payload.providers : [];
      providerGrid.innerHTML = providers.length ? providers.map(providerCard).join("") : '<div class="usage-provider-card usage-provider-empty"><span>No Paid Usage Yet</span><strong>$0.00</strong><small>Run enrichment with usage tracking to populate this panel.</small></div>';
    }
    if (updated) updated.textContent = `Checked from local API usage endpoint: ${titleCase(payload.status || "unknown")}.`;
  }
  async function refresh() {
    try {
      const response = await fetch("/api/api-usage.json", { cache: "no-store" });
      if (!response.ok) return;
      render(await response.json());
    } catch (error) {
      if (updated) updated.textContent = "Static snapshot shown; live local API usage endpoint unavailable.";
    }
  }
  refresh();
  window.setInterval(refresh, 45000);
})();
"""


def _agent_chat_placeholder() -> str:
    return """
      <aside class="agent-chat" aria-label="Agent chat placeholder">
        <button type="button" class="agent-chat-bubble" aria-controls="agent-chat-panel" aria-expanded="false">
          <span class="agent-chat-pulse"></span>
          <strong>Agent Desk</strong>
          <small>Questions &amp; commands</small>
        </button>
        <section class="agent-chat-panel" id="agent-chat-panel" hidden>
          <div class="agent-chat-head">
            <div>
              <p class="eyebrow">Agent Desk</p>
              <h2>Ask Or Draft A Command</h2>
            </div>
            <button type="button" class="agent-chat-close" aria-label="Close agent chat">Close</button>
          </div>
          <p class="agent-chat-note">Placeholder only. Future versions can send questions or supervised commands into the active long-term agent context after authentication, audit logging, and safety gates.</p>
          <div class="agent-chat-prompts" aria-label="Example prompts">
            <button type="button">Why is MSFT a buy?</button>
            <button type="button">Compare MA vs AMZN</button>
            <button type="button">Draft sell review for TSLA</button>
            <button type="button">Explain parking choice</button>
          </div>
          <label class="agent-chat-compose">
            <span>Message</span>
            <textarea rows="4" placeholder="Example: Ask the agent why MA cleared paper review..." disabled></textarea>
          </label>
          <button type="button" class="agent-chat-send" disabled>Send disabled until agent chat is wired</button>
        </section>
      </aside>
    """


def _agent_chat_placeholder_script() -> str:
    return r"""
(function initAgentChatPlaceholder(){
  const chat = document.querySelector(".agent-chat");
  if (!chat) return;
  const bubble = chat.querySelector(".agent-chat-bubble");
  const panel = chat.querySelector(".agent-chat-panel");
  const close = chat.querySelector(".agent-chat-close");
  if (!bubble || !panel) return;
  const setOpen = (open) => {
    panel.hidden = !open;
    bubble.setAttribute("aria-expanded", open ? "true" : "false");
    chat.classList.toggle("is-open", open);
  };
  bubble.addEventListener("click", () => setOpen(panel.hidden));
  if (close) close.addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
})();
"""


def _status_tile(label: str, value: Any, detail: Any = "") -> str:
    return (
        "<div class=\"status-tile\">"
        f"<span>{escape(str(label))}</span>"
        f"<strong>{escape(_short_text(str(value), 72))}</strong>"
        f"<small>{escape(_short_text(str(detail or ''), 150))}</small>"
        "</div>"
    )


def _intent_rows(intents: list[Mapping[str, Any]], *, empty_label: str) -> str:
    if not intents:
        return f"<p>{escape(empty_label)}</p>"
    rows = []
    for item in intents[:8]:
        promotion = item.get("promotion_review") if isinstance(item.get("promotion_review"), Mapping) else {}
        decision = str(promotion.get("promotion_decision") or item.get("order_intent") or item.get("intent_type") or "")
        rows.append(
            "<a class=\"intent-row\" href=\"tickers/{symbol}.html\">"
            "<strong>{symbol}</strong>"
            "<span>{intent}</span>"
            "<em>{decision}</em>"
            "<small>{value}</small>"
            "<p>{reason}</p>"
            "</a>".format(
                symbol=escape(_symbol(item)),
                intent=escape(_intent_type(item)),
                decision=escape(_short_text(decision, 48)),
                value=escape(_money(item.get("trade_value") or item.get("target_value") or 0)),
                reason=escape(_short_text(str(item.get("reason") or ""), 150)),
            )
        )
    return f"<div class=\"intent-list\">{''.join(rows)}</div>"


def _safety_chip(label: str, value: str) -> str:
    return f"<div class=\"safety-chip\"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"


def _ticker_page_html(
    *,
    symbol: str,
    intent: Mapping[str, Any],
    evidence: Mapping[str, Any],
    price_history: list[Mapping[str, Any]],
    dashboard: Mapping[str, Any],
) -> str:
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    scorecard = evidence.get("quality_growth_scorecard") if isinstance(evidence.get("quality_growth_scorecard"), Mapping) else {}
    fundamentals = evidence.get("fundamental_metrics") if isinstance(evidence.get("fundamental_metrics"), Mapping) else {}
    earnings = _latest_earnings_for_evidence(evidence)
    first_pass_scan = evidence.get("python_first_pass_scan") if isinstance(evidence.get("python_first_pass_scan"), Mapping) else {}
    articles = evidence.get("article_evidence_summaries") or evidence.get("relevant_news") or []
    return _html_shell(
        title=f"{symbol} Research Tear Sheet",
        body=f"""
        <nav class="top-nav"><a href="../index.html">Back to dashboard</a><span>{escape(str((dashboard.get("agent_advisory") or {}).get("state") or ""))}</span></nav>
        <section class="ticker-hero">
          <div>
            <p class="eyebrow">Ticker Tear Sheet</p>
            <h1>{escape(symbol)}</h1>
            <p class="lede">{escape(_short_text(str(evidence.get("business_summary") or intent.get("reason") or "Research context pending."), 220))}</p>
          </div>
          <div class="verdict-card">
            <span>{escape(str(intent.get("intent_type") or "RESEARCH"))}</span>
            <strong>{escape(str(promotion.get("promotion_decision") or intent.get("order_intent") or "NONE"))}</strong>
            <small>{escape(_money(intent.get("trade_value") or intent.get("target_value") or 0))}</small>
          </div>
        </section>
        <section class="price-chart panel">
          <div class="section-heading"><p class="eyebrow">Price</p><h2>Chart Snapshot</h2></div>
          {_price_chart_svg(price_history)}
        </section>
        <section class="metric-ribbon">
          {_metric_tile("Confidence", promotion.get("confidence"))}
          {_metric_tile("Suggested Size", _percentish(promotion.get("suggested_size_pct")))}
          {_metric_tile("Superscore", scorecard.get("superscore"))}
          {_metric_tile("Scan Score", first_pass_scan.get("rank_score") or first_pass_scan.get("score"))}
          {_metric_tile("Moneyball", first_pass_scan.get("moneyball_score"))}
          {_metric_tile("Quant", first_pass_scan.get("quant_score"))}
          {_metric_tile("Valuation Fit", promotion.get("valuation_fit_score"))}
          {_metric_tile("Margin of Safety", promotion.get("margin_of_safety_score"))}
          {_metric_tile("Permanent Loss", promotion.get("permanent_loss_score"))}
          {_metric_tile("Historical Max Drawdown", _drawdown_cell(_historical_max_drawdown_pct(price_history)))}
        </section>
        {_graham_panel(promotion)}
        <section class="panel two-column">
          {_score_panel(scorecard, first_pass_scan=first_pass_scan)}
          {_earnings_panel(earnings)}
        </section>
        <section class="panel">
          <div class="section-heading"><p class="eyebrow">Financials</p><h2>Financial Metrics</h2></div>
          {_fundamental_sections(fundamentals)}
        </section>
        <section class="panel">
          <div class="section-heading"><p class="eyebrow">Evidence</p><h2>Research Notes and Sources</h2></div>
          <p>{escape(str(intent.get("reason") or ""))}</p>
          {_article_list(articles)}
        </section>
        <section class="safety-strip"><strong>Safety:</strong> read-only research page. No broker order can be placed from this file.</section>
        {_reference_footer(ticker_page=True)}
        """,
    )


def _graham_panel(promotion: Mapping[str, Any]) -> str:
    if not promotion:
        return ""
    flags = promotion.get("permanent_loss_flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]
    flag_text = ", ".join(str(flag) for flag in flags if str(flag).strip()) or "none"
    rows = [
        ("Mode", promotion.get("defensive_enterprising_mode") or "n/a"),
        ("Entry Plan", promotion.get("staged_entry_label") or "n/a"),
        ("Staged Size", _percentish(promotion.get("staged_entry_size_pct"))),
        ("Normalized Earnings", promotion.get("normalized_earnings_quality") or "n/a"),
        ("Permanent Loss Flags", flag_text),
    ]
    return (
        "<section class=\"panel\">"
        "<div class=\"section-heading\"><p class=\"eyebrow\">Graham Discipline</p><h2>Margin of Safety and Permanent Loss</h2></div>"
        "<div class=\"metric-grid\">"
        + "".join(
            f"<div class=\"metric-tile\"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>"
            for label, value in rows
        )
        + "</div>"
        "</section>"
    )


def _html_shell(*, title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --ink: #1d241f;
      --muted: #6d6658;
      --paper: #f4eddf;
      --paper-2: #fffaf0;
      --line: #d6c5a8;
      --accent: #0f6b56;
      --accent-2: #b66a2c;
      --danger: #7f2f25;
      --shadow: 0 24px 70px rgba(69, 47, 20, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    [hidden] {{ display: none !important; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 0%, rgba(15,107,86,.16), transparent 29rem),
        radial-gradient(circle at 85% 12%, rgba(182,106,44,.14), transparent 24rem),
        linear-gradient(135deg, #f7efe0, #efe2c9);
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    }}
    body:before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: .26;
      background-image: linear-gradient(rgba(29,36,31,.05) 1px, transparent 1px), linear-gradient(90deg, rgba(29,36,31,.035) 1px, transparent 1px);
      background-size: 34px 34px;
    }}
    a {{ color: inherit; }}
    .dashboard-shell {{
      display: grid;
      grid-template-columns: 252px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .dashboard-rail {{
      position: sticky;
      top: 0;
      align-self: start;
      min-height: 100vh;
      padding: 30px 22px;
      background:
        radial-gradient(circle at 25% 4%, rgba(40,147,186,.34), transparent 10rem),
        linear-gradient(180deg, #111947, #10173f 58%, #0f1536);
      color: rgba(245,241,226,.82);
      box-shadow: inset -1px 0 rgba(255,255,255,.06);
    }}
    .rail-brand {{
      padding: 0 0 18px;
      border-bottom: 1px solid rgba(255,255,255,.12);
    }}
    .rail-logo {{
      display: block;
      width: 100%;
      max-width: 208px;
      height: 92px;
      object-fit: contain;
      object-position: center center;
      margin: 0 auto;
      background: transparent;
      border-radius: 0;
      box-shadow: none;
    }}
    .rail-brand strong {{
      display: block;
      font-size: 31px;
      line-height: .95;
      letter-spacing: -.05em;
    }}
    .rail-brand small {{
      display: block;
      margin-top: 9px;
      color: rgba(245,241,226,.62);
      font-weight: 800;
    }}
    .dashboard-rail nav {{
      display: grid;
      gap: 7px;
      margin-top: 24px;
    }}
    .dashboard-rail a {{
      display: flex;
      align-items: center;
      gap: 13px;
      min-height: 48px;
      padding: 11px 12px;
      border-radius: 16px;
      color: inherit;
      text-decoration: none;
      font-size: 18px;
      transition: background .18s ease, transform .18s ease;
    }}
    .dashboard-rail a:first-child, .dashboard-rail a:hover {{
      background: rgba(255,255,255,.1);
      transform: translateX(3px);
    }}
    .dashboard-rail .nav-icon {{
      width: 25px;
      height: 25px;
      flex: 0 0 25px;
      color: rgba(185,205,182,.88);
      filter: drop-shadow(0 5px 8px rgba(0,0,0,.18));
      transition: color .18s ease, transform .18s ease;
    }}
    .dashboard-rail a:first-child .nav-icon, .dashboard-rail a:hover .nav-icon {{
      color: #7df0d0;
      transform: translateX(1px) scale(1.04);
    }}
    .dashboard-rail .nav-label {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}
    .dashboard-main {{ min-width: 0; padding-bottom: 34px; }}
    .dashboard-topbar {{
      position: sticky;
      top: 0;
      z-index: 4;
      display: grid;
      grid-template-columns: minmax(240px, 1fr) minmax(250px, 360px) minmax(190px, 240px) minmax(190px, 260px);
      gap: 18px;
      align-items: center;
      padding: 20px 30px;
      background: rgba(29,36,31,.92);
      color: rgba(255,250,240,.86);
      backdrop-filter: blur(18px);
      border-bottom: 1px solid rgba(255,250,240,.1);
    }}
    .topbar-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 26px;
      font-weight: 800;
    }}
    .search-box {{
      display: grid;
      gap: 4px;
      padding: 9px 12px;
      border: 1px solid rgba(255,250,240,.34);
      border-radius: 10px;
      background: rgba(255,255,255,.04);
    }}
    .search-box span {{
      color: rgba(255,250,240,.52);
      font-size: 12px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 800;
    }}
    .search-box input {{
      width: 100%;
      border: 0;
      outline: 0;
      color: rgba(255,250,240,.8);
      background: transparent;
      font: inherit;
    }}
    .best-buys {{
      padding: 13px 16px;
      border-radius: 14px;
      background: #0b43b8;
      text-align: center;
      box-shadow: 0 14px 32px rgba(11,67,184,.24);
    }}
    .best-buys span, .market-tape span {{
      display: block;
      color: rgba(255,250,240,.68);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .1em;
      text-transform: uppercase;
    }}
    .best-buys strong {{ display: block; margin-top: 5px; }}
    .market-tape {{
      display: grid;
      grid-template-columns: auto auto;
      gap: 5px 12px;
      align-items: baseline;
      color: rgba(255,250,240,.78);
    }}
    .dashboard-tabs {{
      display: flex;
      gap: 22px;
      width: min(1180px, calc(100vw - 40px - 252px));
      margin: 0 auto 20px;
      padding: 0 4px;
      border-bottom: 1px solid var(--line);
    }}
    .dashboard-tabs a {{
      padding: 18px 0;
      color: var(--muted);
      text-decoration: none;
      font-size: 17px;
      font-weight: 800;
      border-bottom: 3px solid transparent;
    }}
    .dashboard-tabs a.is-active {{
      color: var(--accent);
      border-color: var(--accent);
    }}
    .overview-grid {{
      display: grid;
      grid-template-columns: 1.1fr .9fr .9fr;
      gap: 18px;
      margin-top: 20px;
    }}
    .highlight-card {{
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: rgba(244,237,223,.68);
    }}
    .highlight-card h3 {{ margin: 0 0 10px; font-size: 27px; letter-spacing: -.04em; }}
    .highlight-card p {{ color: var(--muted); line-height: 1.45; }}
    .highlight-card dl {{ margin: 22px 0 0; }}
    .highlight-card dl div {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      padding: 11px 0;
      border-top: 1px solid var(--line);
    }}
    .highlight-card dt {{ color: var(--muted); }}
    .highlight-card dd {{ margin: 0; color: var(--accent); font-weight: 900; }}
    .read-rec {{
      display: inline-block;
      margin-top: 14px;
      padding: 11px 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 900;
    }}
    .hero, .ticker-hero, .panel, .safety-strip, .top-nav {{
      width: min(1180px, calc(100vw - 40px));
      margin: 22px auto;
    }}
    [id] {{ scroll-margin-top: 96px; }}
    .hero, .ticker-hero {{
      padding: 42px;
      border: 1px solid var(--line);
      background: rgba(255,250,240,.78);
      box-shadow: var(--shadow);
      border-radius: 30px;
      position: relative;
      overflow: hidden;
    }}
    .hero:after, .ticker-hero:after {{
      content: "";
      position: absolute;
      right: -80px;
      top: -80px;
      width: 240px;
      height: 240px;
      border: 1px solid rgba(15,107,86,.25);
      border-radius: 50%;
    }}
    .eyebrow {{
      margin: 0 0 8px;
      color: var(--accent-2);
      font-size: 12px;
      letter-spacing: .18em;
      text-transform: uppercase;
      font-weight: 800;
    }}
    h1, h2 {{ margin: 0; letter-spacing: -.04em; }}
    h1 {{ font-size: clamp(42px, 8vw, 92px); line-height: .9; max-width: 850px; }}
    h2 {{ font-size: clamp(26px, 4vw, 42px); }}
    .lede {{ color: var(--muted); font-size: 20px; line-height: 1.45; max-width: 760px; }}
    .hero-grid, .metric-ribbon {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-top: 30px;
    }}
    .hero-grid div, .metric-tile, .verdict-card {{
      padding: 18px;
      background: rgba(244,237,223,.9);
      border: 1px solid var(--line);
      border-radius: 20px;
    }}
    .hero-grid span, .metric-tile span, .verdict-card span, .ticker-card span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 800;
    }}
    .hero-grid strong, .metric-tile strong, .verdict-card strong {{
      display: block;
      margin-top: 8px;
      font-size: clamp(20px, 2vw, 24px);
      line-height: 1.08;
      overflow-wrap: anywhere;
    }}
    .panel {{
      padding: 30px;
      border-radius: 26px;
      border: 1px solid var(--line);
      background: rgba(255,250,240,.74);
      box-shadow: 0 14px 35px rgba(69, 47, 20, 0.08);
    }}
    .command-grid, .safety-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 14px;
      margin-top: 20px;
    }}
    .status-tile, .safety-chip {{
      min-height: 132px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: linear-gradient(145deg, rgba(255,250,240,.95), rgba(237,224,198,.62));
    }}
    .status-tile span, .safety-chip span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 800;
    }}
    .status-tile strong, .safety-chip strong {{
      display: block;
      margin-top: 9px;
      font-size: 23px;
      letter-spacing: -.03em;
    }}
    .status-tile small {{
      display: block;
      margin-top: 10px;
      color: var(--muted);
      line-height: 1.35;
    }}
    .scheduler-readiness-panel {{
      border-color: rgba(15,107,86,.28);
      background:
        radial-gradient(circle at 96% 0%, rgba(125,240,208,.22), transparent 18rem),
        linear-gradient(145deg, rgba(255,250,240,.88), rgba(237,224,198,.72));
    }}
    .scheduler-readiness-strip {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 12px;
      margin: 20px 0 4px;
    }}
    .scheduler-readiness-panel .scheduler-readiness-strip > div {{
      padding: 16px;
      border: 1px solid rgba(15,107,86,.22);
      border-radius: 18px;
      color: var(--ink);
      background:
        radial-gradient(circle at 100% 0%, rgba(125,240,208,.16), transparent 8rem),
        rgba(255,250,240,.86);
    }}
    .scheduler-readiness-panel .scheduler-readiness-strip span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 900;
    }}
    .scheduler-readiness-panel .scheduler-readiness-strip strong {{
      display: block;
      margin-top: 8px;
      color: var(--ink);
      font-size: 24px;
      line-height: 1.1;
      overflow-wrap: anywhere;
    }}
    .scheduler-readiness-panel .scheduler-readiness-strip small {{
      display: block;
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.25;
    }}
    .scheduler-readiness-note {{
      margin: 16px 0 0;
      padding: 14px 16px;
      border: 1px solid rgba(15,107,86,.2);
      border-radius: 16px;
      color: var(--muted);
      background: rgba(255,250,240,.62);
      line-height: 1.4;
    }}
    .scheduler-readiness-note strong {{
      color: var(--ink);
    }}
    .scheduler-card-stack {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
      margin-top: 18px;
    }}
    .scheduler-card-stack .scheduler-validation-card {{
      margin-top: 0;
    }}
    .scheduler-readiness-panel .scheduler-validation-card {{
      color: var(--ink);
      background:
        radial-gradient(circle at 96% 8%, rgba(15,107,86,.1), transparent 16rem),
        linear-gradient(145deg, rgba(255,250,240,.96), rgba(237,224,198,.68));
    }}
    .scheduler-readiness-panel .pipeline-health-grid {{
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    }}
    .scheduler-readiness-panel .pipeline-health-grid div {{
      color: var(--ink);
      background: rgba(255,250,240,.72);
    }}
    .scheduler-readiness-panel .pipeline-health-grid strong {{
      color: var(--ink);
      font-size: 20px;
    }}
    .safety-note {{
      margin-top: 18px;
      color: var(--muted);
    }}
    .safety-note a {{
      color: var(--accent);
      font-weight: 800;
    }}
    .intent-list {{
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }}
    .intent-row {{
      display: grid;
      grid-template-columns: 100px 120px minmax(160px, 1fr) 100px;
      gap: 12px;
      align-items: start;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(244,237,223,.72);
      color: inherit;
      text-decoration: none;
    }}
    .intent-row strong {{ font-size: 26px; letter-spacing: -.04em; }}
    .intent-row span, .intent-row small {{
      color: var(--muted);
      font-size: 12px;
      letter-spacing: .1em;
      text-transform: uppercase;
      font-weight: 800;
    }}
    .intent-row em {{ color: var(--accent); font-style: normal; font-weight: 800; }}
    .intent-row p {{
      grid-column: 1 / -1;
      margin: 0;
      color: var(--muted);
      line-height: 1.35;
    }}
    .summary-list {{
      display: grid;
      gap: 12px;
      padding: 0;
      margin: 18px 0 0;
      list-style: none;
    }}
    .summary-list li {{
      display: flex;
      align-items: baseline;
      gap: 12px;
      padding: 12px 0;
      border-top: 1px solid var(--line);
    }}
    .summary-list strong {{ font-size: 34px; color: var(--accent); }}
    .summary-list span {{ color: var(--muted); }}
    .holdings-table-wrap {{ margin-top: 24px; }}
    .holdings-table-wrap h3 {{ margin: 0 0 8px; font-size: 22px; letter-spacing: -.03em; }}
    .holdings-table td[colspan] {{ color: var(--muted); font-style: italic; }}
    .portfolio-live-card, .pipeline-health-card, .scheduler-validation-card, .api-usage-card, .tax-suppression-card {{
      margin-top: 22px;
      padding: 22px;
      border: 1px solid rgba(15,107,86,.22);
      border-radius: 24px;
      background:
        radial-gradient(circle at 96% 8%, rgba(15,107,86,.14), transparent 16rem),
        linear-gradient(145deg, rgba(255,250,240,.95), rgba(237,224,198,.62));
    }}
    .compact-heading h3 {{ margin: 0; font-size: 28px; letter-spacing: -.04em; }}
    .portfolio-total-grid, .pipeline-health-grid, .api-usage-total-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .portfolio-total-grid div, .pipeline-health-grid div, .api-usage-total-grid div {{
      padding: 15px;
      border: 1px solid var(--line);
      border-radius: 17px;
      background: rgba(255,250,240,.66);
    }}
    .portfolio-total-grid span, .pipeline-health-grid span, .api-usage-total-grid span, .usage-provider-card span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 900;
    }}
    .portfolio-total-grid strong, .pipeline-health-grid strong, .api-usage-total-grid strong, .usage-provider-card strong {{
      display: block;
      margin-top: 8px;
      font-size: 23px;
      letter-spacing: -.03em;
      overflow-wrap: anywhere;
    }}
    .usage-progress {{
      height: 12px;
      margin: 18px 0 0;
      border: 1px solid rgba(15,107,86,.18);
      border-radius: 999px;
      background: rgba(255,250,240,.7);
      overflow: hidden;
    }}
    .usage-progress i {{
      display: block;
      height: 100%;
      width: 0%;
      border-radius: inherit;
      background: linear-gradient(90deg, #0f6b56, #7df0d0);
      transition: width .35s ease;
    }}
    .usage-provider-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .usage-provider-card {{
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,250,240,.66);
    }}
    .usage-provider-card small {{
      display: block;
      margin-top: 9px;
      color: var(--muted);
      line-height: 1.35;
    }}
    .usage-provider-empty {{
      border-style: dashed;
    }}
    .pipeline-health-card[data-pipeline-health-state="attention_required"],
    .scheduler-validation-card[data-scheduler-validation-state="attention_required"] {{
      border-color: rgba(176,75,45,.34);
    }}
    .scheduler-validation-card p, .tax-suppression-card p {{ margin: 12px 0 0; color: var(--muted); }}
    .tax-suppression-card ul {{
      display: grid;
      gap: 10px;
      padding: 0;
      margin: 16px 0 0;
      list-style: none;
    }}
    .tax-suppression-card li {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,250,240,.66);
    }}
    .tax-suppression-card code {{
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .portfolio-gain-chart {{
      display: grid;
      gap: 9px;
      margin-top: 18px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
    }}
    .portfolio-gain-row {{
      display: grid;
      grid-template-columns: 70px minmax(120px, 1fr) 74px;
      gap: 10px;
      align-items: center;
    }}
    .portfolio-gain-row span, .portfolio-gain-row strong {{
      font-weight: 900;
      font-size: 13px;
    }}
    .portfolio-gain-row i {{
      display: block;
      height: 12px;
      min-width: 4px;
      border-radius: 999px;
      background: var(--accent);
    }}
    .portfolio-gain-row i.negative {{ background: var(--danger); }}
    .portfolio-live-note, [data-portfolio-last-updated] {{
      display: block;
      margin: 12px 0 0;
      color: var(--muted);
      line-height: 1.35;
    }}
    .placeholder-panel {{
      border-style: dashed;
      background:
        linear-gradient(135deg, rgba(15,107,86,.08), transparent 45%),
        var(--paper-2);
    }}
    .placeholder-panel p:last-child {{ max-width: 760px; color: var(--muted); }}
    .pagination-shell {{
      margin-top: 18px;
    }}
    .pagination-controls {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 12px;
      margin-top: 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    .pagination-controls button {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 13px;
      background: rgba(255,250,240,.72);
      color: var(--ink);
      font: inherit;
      font-weight: 900;
      cursor: pointer;
    }}
    .pagination-controls button:disabled {{
      cursor: not-allowed;
      opacity: .38;
    }}
    .pagination-status {{
      min-width: 170px;
      text-align: center;
      font-weight: 800;
    }}
    .table-scroll-top, .table-scroll {{
      width: 100%;
      overflow-x: auto;
    }}
    .table-scroll-top {{
      height: 18px;
      margin-top: 18px;
    }}
    .table-scroll-top div {{
      height: 1px;
    }}
    .table-scroll {{
      padding-bottom: 8px;
    }}
    .rankings-table {{
      min-width: 1080px;
      table-layout: fixed;
    }}
    .rankings-table th, .rankings-table td {{
      font-size: 15px;
      vertical-align: top;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: normal;
    }}
    .rankings-table th:nth-child(1), .rankings-table td:nth-child(1) {{ width: 54px; }}
    .rankings-table th:nth-child(2), .rankings-table td:nth-child(2) {{ width: 110px; }}
    .rankings-table th:nth-child(3), .rankings-table td:nth-child(3) {{ width: 82px; }}
    .rankings-table th:nth-child(4), .rankings-table td:nth-child(4) {{ width: 132px; }}
    .rankings-table th:nth-child(5), .rankings-table td:nth-child(5) {{ width: 185px; }}
    .rankings-table th:nth-child(6), .rankings-table td:nth-child(6) {{ width: 92px; }}
    .rankings-table th:nth-child(7), .rankings-table td:nth-child(7),
    .rankings-table th:nth-child(8), .rankings-table td:nth-child(8),
    .rankings-table th:nth-child(9), .rankings-table td:nth-child(9) {{ width: 82px; }}
    .rankings-table th:nth-child(10), .rankings-table td:nth-child(10) {{ width: 300px; }}
    .rankings-table th:nth-child(11), .rankings-table td:nth-child(11) {{ width: 120px; }}
    .rankings-table th:nth-child(1), .rankings-table td:nth-child(1),
    .rankings-table th:nth-child(2), .rankings-table td:nth-child(2),
    .rankings-table th:nth-child(3), .rankings-table td:nth-child(3) {{
      position: sticky;
      z-index: 2;
      background: var(--paper-2);
      box-shadow: 1px 0 0 var(--line);
    }}
    .rankings-table th:nth-child(1), .rankings-table td:nth-child(1) {{ left: 0; }}
    .rankings-table th:nth-child(2), .rankings-table td:nth-child(2) {{ left: 54px; }}
    .rankings-table th:nth-child(3), .rankings-table td:nth-child(3) {{ left: 164px; }}
    .rankings-table th:nth-child(3), .rankings-table td:nth-child(3) {{ z-index: 1; }}
    .rankings-table thead th:nth-child(1), .rankings-table thead th:nth-child(2) {{ z-index: 3; }}
    .scorecards-table {{
      min-width: 1090px;
      table-layout: fixed;
    }}
    .scorecards-table th, .scorecards-table td {{
      font-size: 15px;
      vertical-align: top;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: normal;
    }}
    .scorecards-table th:nth-child(1), .scorecards-table td:nth-child(1) {{ width: 96px; }}
    .scorecards-table th:nth-child(2), .scorecards-table td:nth-child(2),
    .scorecards-table th:nth-child(3), .scorecards-table td:nth-child(3),
    .scorecards-table th:nth-child(4), .scorecards-table td:nth-child(4),
    .scorecards-table th:nth-child(5), .scorecards-table td:nth-child(5),
    .scorecards-table th:nth-child(6), .scorecards-table td:nth-child(6),
    .scorecards-table th:nth-child(7), .scorecards-table td:nth-child(7) {{ width: 78px; }}
    .scorecards-table th:nth-child(8), .scorecards-table td:nth-child(8) {{ width: 140px; }}
    .scorecards-table th:nth-child(9), .scorecards-table td:nth-child(9) {{ width: 120px; }}
    .scorecards-table th:nth-child(10), .scorecards-table td:nth-child(10) {{ width: 86px; }}
    .scorecards-table th:nth-child(11), .scorecards-table td:nth-child(11) {{ width: 250px; }}
    .scorecards-table th:nth-child(1), .scorecards-table td:nth-child(1) {{
      position: sticky;
      left: 0;
      z-index: 2;
      background: var(--paper-2);
      box-shadow: 1px 0 0 var(--line);
    }}
    .scorecards-table thead th:nth-child(1) {{ z-index: 3; }}
    .evidence-gaps-table {{
      min-width: 1080px;
      table-layout: fixed;
    }}
    .evidence-gaps-table th, .evidence-gaps-table td {{
      font-size: 15px;
      vertical-align: top;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: normal;
    }}
    .evidence-gaps-table th:nth-child(1), .evidence-gaps-table td:nth-child(1) {{ width: 105px; }}
    .evidence-gaps-table th:nth-child(2), .evidence-gaps-table td:nth-child(2) {{ width: 72px; }}
    .evidence-gaps-table th:nth-child(3), .evidence-gaps-table td:nth-child(3),
    .evidence-gaps-table th:nth-child(4), .evidence-gaps-table td:nth-child(4),
    .evidence-gaps-table th:nth-child(5), .evidence-gaps-table td:nth-child(5) {{ width: 230px; }}
    .evidence-gaps-table th:nth-child(6), .evidence-gaps-table td:nth-child(6) {{ width: 210px; }}
    .ticker-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-top: 20px;
    }}
    .ticker-card {{
      display: block;
      min-height: 190px;
      padding: 22px;
      text-decoration: none;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: linear-gradient(145deg, rgba(255,250,240,.95), rgba(237,224,198,.75));
      transition: transform .18s ease, box-shadow .18s ease;
    }}
    .ticker-card:hover {{ transform: translateY(-5px) rotate(-.35deg); box-shadow: var(--shadow); }}
    .ticker-card strong {{ display: block; margin: 12px 0; font-size: 42px; letter-spacing: -.05em; }}
    .ticker-card em {{ display: block; color: var(--muted); font-style: normal; line-height: 1.35; }}
    .ticker-card small {{ display: block; margin-top: 18px; color: var(--accent); font-weight: 800; }}
    .ticker-hero {{ display: grid; grid-template-columns: 1fr minmax(220px, 320px); gap: 24px; align-items: end; }}
    .top-nav {{ display: flex; justify-content: space-between; padding: 16px 4px; color: var(--muted); }}
    .price-chart svg {{ width: 100%; height: auto; display: block; }}
    .chart-workbench {{ position: relative; }}
    .chart-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 16px;
    }}
    .chart-toolbar button {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,250,240,.9);
      color: var(--muted);
      cursor: pointer;
      font: inherit;
      font-size: 13px;
      font-weight: 800;
      padding: 8px 13px;
      transition: transform .15s ease, background .15s ease, color .15s ease;
    }}
    .chart-toolbar button:hover, .chart-toolbar button.is-active {{
      background: var(--accent);
      color: #fffaf0;
      transform: translateY(-1px);
    }}
    .chart-stage {{
      position: relative;
      border: 1px solid rgba(214,197,168,.72);
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(255,250,240,.72), rgba(235,223,198,.42));
      overflow: hidden;
    }}
    .chart-tooltip {{
      position: absolute;
      top: 18px;
      left: 18px;
      z-index: 2;
      min-width: 148px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(29,36,31,.92);
      color: #fffaf0;
      box-shadow: 0 16px 32px rgba(29,36,31,.18);
      font-size: 13px;
      opacity: 0;
      transform: translateY(4px);
      pointer-events: none;
      transition: opacity .12s ease, transform .12s ease;
    }}
    .chart-tooltip.is-visible {{ opacity: 1; transform: translateY(0); }}
    .chart-tooltip strong {{ display: block; font-size: 16px; margin-bottom: 3px; }}
    .chart-caption {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }}
    .two-column {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 22px; }}
    .score-list, .data-table, .article-list {{ margin: 16px 0 0; padding: 0; list-style: none; }}
    .score-list li, .article-list li {{ padding: 12px 0; border-top: 1px solid var(--line); }}
    .bar {{ height: 10px; background: #e5d4b5; border-radius: 99px; overflow: hidden; margin-top: 7px; }}
    .bar i {{ display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #91b56d); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 18px; }}
    th, td {{ border-top: 1px solid var(--line); padding: 11px 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }}
    .section-heading {{ display: flex; justify-content: space-between; gap: 16px; align-items: end; }}
    .safety-strip {{
      padding: 18px 22px;
      border-left: 5px solid var(--danger);
      background: rgba(255,250,240,.72);
      border-radius: 18px;
      color: var(--muted);
    }}
    .reference-footer {{
      width: min(1180px, calc(100vw - 40px));
      margin: 18px auto 42px;
      color: var(--muted);
      font-size: 13px;
    }}
    .reference-footer a {{ color: var(--accent); font-weight: 800; }}
    .agent-chat {{
      position: fixed;
      right: 26px;
      bottom: 24px;
      z-index: 20;
      display: grid;
      justify-items: end;
      gap: 12px;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    }}
    .agent-chat-bubble {{
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 2px 11px;
      align-items: center;
      min-width: 190px;
      padding: 13px 16px;
      border: 1px solid rgba(207,239,255,.34);
      border-radius: 999px;
      color: #fffaf0;
      background:
        radial-gradient(circle at 18% 14%, rgba(23,211,176,.34), transparent 42%),
        linear-gradient(135deg, #122052, #0b153d 62%, #142b61);
      box-shadow: 0 18px 50px rgba(15,21,54,.32);
      cursor: pointer;
      text-align: left;
      transition: transform .18s ease, box-shadow .18s ease;
    }}
    .agent-chat-bubble:hover, .agent-chat.is-open .agent-chat-bubble {{
      transform: translateY(-3px);
      box-shadow: 0 22px 64px rgba(15,21,54,.42);
    }}
    .agent-chat-pulse {{
      grid-row: span 2;
      width: 38px;
      height: 38px;
      border-radius: 50%;
      background: linear-gradient(135deg, #18d7b2, #cfefff);
      box-shadow: 0 0 0 8px rgba(24,215,178,.11);
    }}
    .agent-chat-bubble strong {{ display: block; font-size: 17px; letter-spacing: -.02em; }}
    .agent-chat-bubble small {{ color: rgba(255,250,240,.68); font-weight: 800; }}
    .agent-chat-panel {{
      width: min(380px, calc(100vw - 32px));
      padding: 20px;
      border: 1px solid rgba(214,197,168,.9);
      border-radius: 24px;
      background: rgba(255,250,240,.96);
      box-shadow: 0 26px 80px rgba(29,36,31,.22);
    }}
    .agent-chat-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
    }}
    .agent-chat-head h2 {{ font-size: 27px; }}
    .agent-chat-close, .agent-chat-send, .agent-chat-prompts button {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(244,237,223,.86);
      color: var(--ink);
      font: inherit;
      font-weight: 900;
      cursor: pointer;
    }}
    .agent-chat-close {{ padding: 8px 11px; font-size: 13px; }}
    .agent-chat-note {{
      color: var(--muted);
      line-height: 1.4;
      margin: 14px 0;
    }}
    .agent-chat-prompts {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .agent-chat-prompts button {{
      padding: 8px 11px;
      color: var(--accent);
      font-size: 13px;
    }}
    .agent-chat-compose span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 900;
      margin-bottom: 7px;
    }}
    .agent-chat-compose textarea {{
      width: 100%;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px;
      color: var(--ink);
      background: rgba(244,237,223,.62);
      font: inherit;
      line-height: 1.35;
    }}
    .agent-chat-send {{
      width: 100%;
      margin-top: 12px;
      padding: 12px 14px;
      color: rgba(29,36,31,.54);
      cursor: not-allowed;
    }}
    @media (max-width: 760px) {{
      .dashboard-shell {{ display: block; }}
      .dashboard-rail {{ position: static; min-height: auto; }}
      .dashboard-topbar {{ position: static; grid-template-columns: 1fr; }}
      .dashboard-tabs {{ width: min(1180px, calc(100vw - 40px)); overflow-x: auto; }}
      .overview-grid {{ grid-template-columns: 1fr; }}
      .hero, .ticker-hero, .panel {{ padding: 24px; }}
      .ticker-hero {{ grid-template-columns: 1fr; }}
      .intent-row {{ grid-template-columns: 1fr; }}
      .intent-row p {{ grid-column: auto; }}
      h1 {{ font-size: 48px; }}
      .agent-chat {{ right: 16px; bottom: 16px; }}
      .agent-chat-bubble {{ min-width: 162px; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def _price_chart_svg(history: list[Mapping[str, Any]]) -> str:
    points: list[dict[str, Any]] = []
    for item in history:
        try:
            close = float(item.get("close"))
        except (TypeError, ValueError):
            continue
        points.append({"date": str(item.get("date") or ""), "close": close})
    if len(points) < 2:
        return "<div class=\"empty-chart\">Price history unavailable for this generated page.</div>"
    data = json.dumps(points, sort_keys=True).replace("</", "<\\/")
    return (
        "<div class=\"chart-workbench\" data-active-range=\"MAX\">"
        "<div class=\"chart-toolbar\" aria-label=\"Chart range controls\">"
        "<button type=\"button\" data-range=\"1M\">1M</button>"
        "<button type=\"button\" data-range=\"3M\">3M</button>"
        "<button type=\"button\" data-range=\"6M\">6M</button>"
        "<button type=\"button\" data-range=\"1Y\">1Y</button>"
        "<button type=\"button\" data-range=\"MAX\" class=\"is-active\">MAX</button>"
        "</div>"
        "<div class=\"chart-stage\">"
        f"{_static_price_chart_svg(points)}"
        "<div class=\"chart-tooltip\" role=\"status\" aria-live=\"polite\"></div>"
        "</div>"
        "<div class=\"chart-caption\"><span>Hover the line for date and close.</span><span>Use range chips to zoom.</span></div>"
        f"<script type=\"application/json\" class=\"chart-data\">{data}</script>"
        f"<script>{_interactive_chart_script()}</script>"
        "</div>"
    )


def _static_price_chart_svg(points: list[Mapping[str, Any]]) -> str:
    closes = [float(item.get("close") or 0) for item in points]
    width, height, pad = 820, 260, 26
    low, high = min(closes), max(closes)
    spread = high - low or 1.0
    coords = []
    for index, value in enumerate(closes):
        x = pad + (index / max(1, len(closes) - 1)) * (width - pad * 2)
        y = height - pad - ((value - low) / spread) * (height - pad * 2)
        coords.append(f"{x:.1f},{y:.1f}")
    first, last = closes[0], closes[-1]
    change = ((last - first) / first) * 100 if first else 0.0
    color = "#0f6b56" if change >= 0 else "#7f2f25"
    return (
        f"<svg class=\"interactive-chart\" viewBox=\"0 0 {width} {height}\" role=\"img\" aria-label=\"Interactive price chart\">"
        "<defs><linearGradient id=\"chartFill\" x1=\"0\" x2=\"0\" y1=\"0\" y2=\"1\">"
        f"<stop offset=\"0\" stop-color=\"{color}\" stop-opacity=\"0.22\"/>"
        f"<stop offset=\"1\" stop-color=\"{color}\" stop-opacity=\"0.02\"/>"
        "</linearGradient></defs>"
        f"<path d=\"M {pad},{height-pad} L {' L '.join(coords)} L {width-pad},{height-pad} Z\" fill=\"url(#chartFill)\"/>"
        f"<polyline points=\"{' '.join(coords)}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>"
        f"<text x=\"{pad}\" y=\"34\" fill=\"#6d6658\" font-size=\"16\">Last: {last:.2f} | Change: {change:+.1f}%</text>"
        "</svg>"
    )


def _interactive_chart_script() -> str:
    return r"""
(function initInteractiveCharts(){
  if (window.__longtermInteractiveChartsReady) return;
  window.__longtermInteractiveChartsReady = true;
  const width = 820, height = 260, pad = 26;
  const ranges = { "1M": 22, "3M": 66, "6M": 132, "1Y": 252, "MAX": Infinity };
  const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });

  function visiblePoints(points, range) {
    const take = ranges[range] || Infinity;
    return take === Infinity ? points.slice() : points.slice(Math.max(0, points.length - take));
  }

  function toCoords(points) {
    const closes = points.map(point => Number(point.close)).filter(Number.isFinite);
    const low = Math.min(...closes);
    const high = Math.max(...closes);
    const spread = high - low || 1;
    return points.map((point, index) => {
      const x = pad + (index / Math.max(1, points.length - 1)) * (width - pad * 2);
      const y = height - pad - ((Number(point.close) - low) / spread) * (height - pad * 2);
      return { ...point, x, y };
    });
  }

  function draw(workbench, points, range) {
    const svg = workbench.querySelector("svg.interactive-chart");
    const tooltip = workbench.querySelector(".chart-tooltip");
    const subset = visiblePoints(points, range);
    if (!svg || subset.length < 2) return;
    const coords = toCoords(subset);
    const first = Number(subset[0].close);
    const last = Number(subset[subset.length - 1].close);
    const change = first ? ((last - first) / first) * 100 : 0;
    const color = change >= 0 ? "#0f6b56" : "#7f2f25";
    const polyline = coords.map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
    const area = `M ${pad},${height - pad} L ${coords.map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" L ")} L ${width - pad},${height - pad} Z`;
    svg.innerHTML = `
      <defs><linearGradient id="chartFillLive" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity="0.24"/><stop offset="1" stop-color="${color}" stop-opacity="0.03"/></linearGradient></defs>
      <path d="${area}" fill="url(#chartFillLive)"></path>
      <line class="chart-crosshair" x1="${coords[coords.length - 1].x}" x2="${coords[coords.length - 1].x}" y1="${pad}" y2="${height - pad}" stroke="#6d6658" stroke-width="1.5" stroke-dasharray="5 7" opacity="0"></line>
      <polyline points="${polyline}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"></polyline>
      <circle class="chart-focus" cx="${coords[coords.length - 1].x}" cy="${coords[coords.length - 1].y}" r="6" fill="${color}" stroke="#fffaf0" stroke-width="3" opacity="0"></circle>
      <rect class="chart-hit-area" x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      <text x="${pad}" y="34" fill="#6d6658" font-size="16">Last: ${last.toFixed(2)} | Change: ${change.toFixed(1)}%</text>
    `;
    const focus = svg.querySelector(".chart-focus");
    const crosshair = svg.querySelector(".chart-crosshair");
    const hit = svg.querySelector(".chart-hit-area");
    function show(event) {
      const box = svg.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * width;
      const nearest = coords.reduce((best, point) => Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best, coords[0]);
      if (!nearest || !focus || !crosshair || !tooltip) return;
      focus.setAttribute("cx", nearest.x);
      focus.setAttribute("cy", nearest.y);
      focus.setAttribute("opacity", "1");
      crosshair.setAttribute("x1", nearest.x);
      crosshair.setAttribute("x2", nearest.x);
      crosshair.setAttribute("opacity", ".72");
      tooltip.innerHTML = `<strong>${money.format(Number(nearest.close))}</strong><span>${nearest.date || "date pending"}</span>`;
      tooltip.style.left = `${Math.min(Math.max(14, (nearest.x / width) * box.width - 74), box.width - 170)}px`;
      tooltip.classList.add("is-visible");
    }
    function hide() {
      if (focus) focus.setAttribute("opacity", "0");
      if (crosshair) crosshair.setAttribute("opacity", "0");
      if (tooltip) tooltip.classList.remove("is-visible");
    }
    if (hit) {
      hit.addEventListener("mousemove", show);
      hit.addEventListener("mouseleave", hide);
      hit.addEventListener("touchstart", event => show(event.touches[0]), { passive: true });
      hit.addEventListener("touchmove", event => show(event.touches[0]), { passive: true });
    }
  }

  function boot() {
    document.querySelectorAll(".chart-workbench").forEach(workbench => {
      const data = workbench.querySelector(".chart-data");
      const points = JSON.parse(data ? data.textContent : "[]");
      workbench.querySelectorAll("[data-range]").forEach(button => {
        button.addEventListener("click", () => {
          const range = button.getAttribute("data-range") || "MAX";
          workbench.querySelectorAll("[data-range]").forEach(other => other.classList.toggle("is-active", other === button));
          draw(workbench, points, range);
        });
      });
      draw(workbench, points, workbench.getAttribute("data-active-range") || "MAX");
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
"""


def _metric_tile(label: str, value: Any) -> str:
    return f"<div class=\"metric-tile\"><span>{escape(label)}</span><strong>{escape(str(value if value not in (None, '') else 'n/a'))}</strong></div>"


def _score_panel(scorecard: Mapping[str, Any], *, first_pass_scan: Mapping[str, Any] | None = None) -> str:
    analysis = scorecard.get("analysis") if isinstance(scorecard.get("analysis"), Mapping) else {}
    if not scorecard and not analysis:
        return "<div><div class=\"section-heading\"><p class=\"eyebrow\">Scores</p><h2>Scorecard</h2></div><p>No scorecard available yet.</p></div>"
    rows = []
    for label, value in _scorecard_bar_values(scorecard, analysis):
        if value is not None:
            numeric = _number(value)
            rows.append(
                f"<li><strong>{escape(label)}</strong><span>{numeric:g}</span><div class=\"bar\"><i style=\"width:{max(0, min(100, numeric)):.0f}%\"></i></div></li>"
            )
    first_pass_scan = first_pass_scan or {}
    scan_html = ""
    if first_pass_scan:
        scan_bits = [
            ("Rank Score", first_pass_scan.get("rank_score") or first_pass_scan.get("score")),
            ("Moneyball", first_pass_scan.get("moneyball_score")),
            ("Quant", first_pass_scan.get("quant_score")),
            ("Rank", first_pass_scan.get("rank")),
        ]
        scan_items = "".join(
            f"<li><strong>{escape(label)}</strong> {escape(str(value))}</li>"
            for label, value in scan_bits
            if value not in (None, "")
        )
        reason = str(first_pass_scan.get("reason") or first_pass_scan.get("rank_reason") or "")
        scan_html = (
            "<div class=\"scan-card\"><h3>First-Pass Scan</h3>"
            f"<ul>{scan_items}</ul>"
            f"<p>{escape(_short_text(reason, 180))}</p>"
            "</div>"
        )
    return (
        "<div><div class=\"section-heading\"><p class=\"eyebrow\">Scores</p><h2>Scorecard</h2></div>"
        f"<p>Superscore: <strong>{escape(str(scorecard.get('superscore') or 'n/a'))}</strong></p>"
        f"<ul class=\"score-list\">{''.join(rows) or '<li>No analysis bars available.</li>'}</ul>"
        f"{scan_html}</div>"
    )


def _earnings_panel(earnings: Mapping[str, Any]) -> str:
    takeaways = (
        earnings.get("key_takeaways")
        or earnings.get("key_financial_takeaways")
        or earnings.get("positive_developments")
        or []
    )
    items = "".join(f"<li>{escape(str(item))}</li>" for item in takeaways[:5])
    return (
        "<div><div class=\"section-heading\"><p class=\"eyebrow\">Earnings</p><h2>Latest Earnings</h2></div>"
        f"<p><strong>{escape(_display_label(earnings.get('quarter') or 'Quarter pending'))}</strong></p>"
        f"<p>{escape(str(earnings.get('summary') or 'No earnings narrative available yet.'))}</p>"
        f"<ul class=\"article-list\">{items or '<li>No takeaways captured.</li>'}</ul></div>"
    )


def _latest_earnings_for_evidence(evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("latest_earnings", "latest_earnings_enrichment", "recent_earnings"):
        value = evidence.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _scorecard_bar_values(scorecard: Mapping[str, Any], analysis: Mapping[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("Quality", _first_present(analysis.get("quality"), scorecard.get("quality_score"))),
        ("Growth", _first_present(analysis.get("growth"), scorecard.get("growth_score"))),
        ("Valuation", _first_present(analysis.get("valuation"), scorecard.get("valuation_score"))),
        ("Safety", _first_present(analysis.get("safety"), scorecard.get("safety_score"))),
        (
            "Market Buzz",
            _first_present(
                analysis.get("market_attention"),
                analysis.get("market_buzz"),
                scorecard.get("market_attention_score"),
                scorecard.get("market_buzz_score"),
            ),
        ),
    ]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _fundamental_sections(fundamentals: Mapping[str, Any]) -> str:
    if not fundamentals:
        return "<p>No structured fundamentals available yet.</p>"
    sections = [
        ("Revenue Growth (CAGR)", "revenue_growth_cagr"),
        ("Valuation (TTM)", "valuation_ttm"),
        ("Profitability (TTM)", "profitability_ttm"),
        ("Financials (TTM)", "financials_ttm"),
    ]
    html = []
    for title, key in sections:
        values = fundamentals.get(key) if isinstance(fundamentals.get(key), Mapping) else {}
        if not values:
            continue
        html.append(f"<h3>{escape(title)}</h3><table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>")
        for metric, value in values.items():
            html.append(f"<tr><td>{escape(_metric_label(str(metric)))}</td><td>{escape(str(value))}</td></tr>")
        html.append("</tbody></table>")
    return "".join(html) or "<p>No structured fundamentals available yet.</p>"


def _article_list(articles: Any) -> str:
    if not isinstance(articles, list) or not articles:
        return "<ul class=\"article-list\"><li>No article evidence captured yet.</li></ul>"
    rows = []
    for item in articles[:8]:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or item.get("source") or "Source")
        summary = str(item.get("summary") or item.get("description") or "")
        url = str(item.get("url") or "")
        link = f" <a href=\"{escape(url)}\">source</a>" if url else ""
        rows.append(f"<li><strong>{escape(title)}</strong>{link}<br>{escape(summary)}</li>")
    return f"<ul class=\"article-list\">{''.join(rows) or '<li>No article evidence captured yet.</li>'}</ul>"


def _reference_footer(*, ticker_page: bool = False) -> str:
    extra = (
        ' Ticker-page layout reference: <a href="https://www.fool.com/premium/company/NASDAQ/AAPL/financials/summary">'
        "Fool company financial summary example</a>."
        if ticker_page
        else ""
    )
    return (
        '<footer class="reference-footer">'
        'Design reference: <a href="https://www.fool.com/premium">Motley Fool Premium</a>. '
        "This generated dashboard is original, read-only, and not affiliated with Motley Fool."
        f"{extra}</footer>"
    )


def _metric_label(value: str) -> str:
    value = value.replace("3_yr", "3-Yr").replace("ttm", "TTM")
    return value.replace("_", " ").title().replace("Cagr", "CAGR").replace("Eps", "EPS")


def _display_label(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "latest_available": "Latest Available",
        "latest_quarter": "Latest Quarter",
        "quarter_pending": "Quarter Pending",
    }
    lowered = text.lower()
    if lowered in labels:
        return labels[lowered]
    if "_" in text or "-" in text:
        return text.replace("_", " ").replace("-", " ").title()
    return text


def _short_text(value: str, limit: int) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"${amount:,.0f}" if amount else "$0"


def _money_cents(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"${amount:,.2f}" if amount else "$0.00"


def _signed_money(value: Any) -> str:
    amount = _number(value)
    sign = "+" if amount >= 0 else "-"
    return f"{sign}${abs(amount):,.2f}"


def _signed_percent(value: Any) -> str:
    amount = _number(value)
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:.2f}%"


def _shares(value: Any) -> str:
    amount = _number(value)
    if amount <= 0:
        return "0"
    return f"{amount:,.6f}".rstrip("0").rstrip(".")


def _gain_percent(current_value: float, cost: float) -> str:
    if cost <= 0:
        return "n/a"
    gain = ((float(current_value or 0.0) - cost) / cost) * 100.0
    return f"{gain:+.2f}%"


def _percentish(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value):g}%"
    except (TypeError, ValueError):
        return str(value)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "build_operator_dashboard",
    "build_operator_dashboard_evidence_gap_summary",
    "build_operator_dashboard_html",
    "build_operator_dashboard_markdown",
    "build_operator_dashboard_site",
]
