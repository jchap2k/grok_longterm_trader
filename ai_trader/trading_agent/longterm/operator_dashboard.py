"""Static dashboard summaries and pages for long-term trader operator artifacts."""

from __future__ import annotations

import json
from html import escape
from typing import Any, Iterable, Mapping


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


def build_operator_dashboard_site(
    *,
    dashboard: Mapping[str, Any],
    action_plan: Mapping[str, Any] | None = None,
    evidence_items: Iterable[Mapping[str, Any]] | None = None,
    price_history_by_symbol: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build a static dashboard package with index and ticker pages."""
    action_plan = action_plan or {}
    evidence_by_symbol = {
        _symbol(item): dict(item)
        for item in (evidence_items or [])
        if isinstance(item, Mapping) and _symbol(item)
    }
    price_history_by_symbol = price_history_by_symbol or {}
    symbols = _ordered_site_symbols(dashboard, action_plan, evidence_by_symbol)
    pages: dict[str, str] = {
        "index.html": _site_index_html(
            dashboard=dashboard,
            action_plan=action_plan,
            symbols=symbols,
            evidence_by_symbol=evidence_by_symbol,
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


def _ordered_site_symbols(
    dashboard: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    ordered: list[str] = []
    for key in ("paper_submit_candidates", "parking_symbols"):
        for value in dashboard.get(key) or []:
            _append_unique_symbol(ordered, str(value))
    for intent in action_plan.get("intents") or []:
        if isinstance(intent, Mapping):
            _append_unique_symbol(ordered, _symbol(intent))
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
    symbols: list[str],
    evidence_by_symbol: Mapping[str, Mapping[str, Any]],
) -> str:
    regime = dashboard.get("market_regime") or {}
    advisory = dashboard.get("agent_advisory") or {}
    intents = [dict(item) for item in action_plan.get("intents") or [] if isinstance(item, Mapping)]
    buy_intents = [item for item in intents if _intent_type(item) == "BUY"]
    parking_intents = [item for item in intents if _intent_type(item) in PARKING_INTENTS]
    review_intents = [item for item in intents if _intent_type(item) not in {"BUY", *PARKING_INTENTS}]
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
            "<a class=\"ticker-card\" href=\"tickers/{symbol}.html\" data-search-text=\"{search_text}\">"
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
              <p class="eyebrow">Motley-Fool-style research surface</p>
              <h1>Long-Term Trader Dashboard</h1>
              <p class="lede">{escape(str(advisory.get("message") or "Review research, parking, and paper-readiness artifacts."))}</p>
              <div class="hero-grid">
                <div><span>Advisory</span><strong>{escape(str(advisory.get("state") or "unknown"))}</strong></div>
                <div><span>Market Regime</span><strong>{escape(str(regime.get("risk_regime") or "unknown"))}</strong></div>
                <div><span>Paper Candidates</span><strong>{int(dashboard.get("buy_intent_count") or 0)}</strong></div>
                <div><span>Parking</span><strong>{", ".join(escape(str(item)) for item in dashboard.get("parking_symbols") or []) or "none"}</strong></div>
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
                {_status_tile("Agent", advisory.get("state") or "unknown", advisory.get("message") or "")}
                {_status_tile("Order submission", "disabled", "Read-only dashboard. Stage 6B still requires explicit supervised confirmation.")}
                {_status_tile("Regime", regime.get("risk_regime") or "unknown", regime.get("reason") or "")}
                {_status_tile("VIX / 10Y", f"{regime.get('vix_level') if regime.get('vix_level') is not None else 'unknown'} / {regime.get('ten_year_yield_trend') or 'unknown'}", "Used for parking posture, not automatic trading.")}
              </div>
            </section>
            <section class="panel" id="coverage">
              <div class="section-heading">
                <p class="eyebrow">Coverage</p>
                <h2>Research Coverage Updates</h2>
              </div>
              <p>{len(symbols)} ticker tear sheets are available in this generated site.</p>
              <p>Coverage rows are generated from the current action plan, evidence files, and enrichment artifacts. Future versions can split this into analyst updates, latest news, and thesis-monitor notes.</p>
            </section>
            {_rankings_section(symbols=symbols, action_plan=action_plan, evidence_by_symbol=evidence_by_symbol)}
            {_placeholder_panel(
                section_id="scorecards",
                eyebrow="Scorecards",
                title="Scorecards Placeholder",
                body="Ticker scorecards are available on each tear sheet. This section is reserved for a portfolio-wide scorecard table.",
            )}
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
              {_holdings_placeholder_table()}
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
            </section>
            <section class="panel" id="research-board">
              <div class="section-heading">
                <p class="eyebrow">Research Board</p>
                <h2>All Ticker Tear Sheets</h2>
              </div>
              <div class="ticker-grid">{''.join(cards)}</div>
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
            <script>{_dashboard_search_script()}{_synced_table_scroller_script()}</script>
          </main>
        </div>
        """,
    )


def _dashboard_rail() -> str:
    items = [
        ("Dashboard", "#dashboard-overview"),
        ("Paper Candidates", "#paper-candidates"),
        ("All Tear Sheets", "#research-board"),
        ("Rankings", "#rankings"),
        ("Coverage", "#coverage"),
        ("Scorecards", "#scorecards"),
        ("Portfolio", "#portfolio"),
        ("Safety", "#safety"),
        ("Settings", "#settings"),
    ]
    links = "".join(f"<a href=\"{href}\"><span></span>{escape(label)}</a>" for label, href in items)
    return (
        "<aside class=\"dashboard-rail\">"
        "<div class=\"rail-brand\"><strong>LT Trader</strong><small>Autonomous long-term research</small></div>"
        f"<nav>{links}</nav>"
        "</aside>"
    )


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
    for symbol in symbols:
        intent = _intent_for_symbol(action_plan, symbol)
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
                "actionability": _actionability_for_intent(intent),
                "why_not_buy": _why_not_buy(intent),
                "trade_value": intent.get("trade_value") or intent.get("target_value") or 0,
                "quality": analysis.get("quality"),
                "growth": analysis.get("growth"),
                "valuation": analysis.get("valuation"),
                "safety": analysis.get("safety"),
                "reason": str(intent.get("reason") or evidence.get("business_summary") or ""),
            }
        )
    rows.sort(key=lambda item: (-float(item["score"]), str(item["symbol"])))
    if not rows:
        return _placeholder_panel(
            section_id="rankings",
            eyebrow="Rankings",
            title="Rankings Placeholder",
            body="Ranked stock lists will appear here once review scores or scorecards are supplied.",
        )
    body_rows = []
    for index, item in enumerate(rows, start=1):
        symbol = str(item["symbol"])
        body_rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td><a href=\"tickers/{escape(symbol)}.html\">{escape(symbol)}</a></td>"
            f"<td>{float(item['score']):g}</td>"
            f"<td>{escape(_actionability_label(str(item['actionability'])))}</td>"
            f"<td>{escape(_short_text(_humanize_reason(str(item['why_not_buy'])), 90))}</td>"
            f"<td>{escape(_money(item.get('trade_value')))}</td>"
            f"<td>{escape(_score_cell(item.get('quality')))}</td>"
            f"<td>{escape(_score_cell(item.get('growth')))}</td>"
            f"<td>{escape(_score_cell(item.get('valuation')))}</td>"
            f"<td>{escape(_score_cell(item.get('safety')))}</td>"
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
        "<p>Stock Details View: stocks are sorted by evidence score, while Actionability explains whether the name is actually cleared for a BUY.</p>"
        "<div class=\"table-scroll-top\" aria-hidden=\"true\"><div></div></div>"
        "<div class=\"table-scroll\"><table class=\"rankings-table\">"
        "<thead><tr><th>Rank</th><th>Symbol</th><th>Evidence Score</th><th>Actionability</th><th>Why Not Buy</th><th>Trade Value</th><th>Quality</th><th>Growth</th><th>Valuation</th><th>Safety</th><th>Context</th><th>Score Source</th></tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        "</section>"
    )


def _actionability_label(value: str) -> str:
    labels = {
        "ACTIONABLE_BUY": "Actionable buy",
        "WATCHLIST_PENDING_EVIDENCE": "Watchlist / needs evidence",
        "WATCHLIST_PENDING_CONFIRMATION": "Watchlist / needs confirmation",
        "PARKING_GUIDANCE": "Parking guidance",
        "RESEARCH_ONLY": "Research only",
    }
    return labels.get(value, value.replace("_", " ").title())


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
        (scorecard.get("superscore"), "Scorecard superscore"),
        (promotion.get("confidence"), "Promotion confidence"),
        (promotion.get("valuation_fit_score"), "Valuation fit"),
        (promotion.get("quality_score"), "Promotion quality score"),
    ]
    for value, label in candidates:
        score = _number(value)
        if score > 0:
            return score, label
    return 0.0, "No review score"


def _score_cell(value: Any) -> str:
    score = _number(value)
    return f"{score:g}" if score > 0 else "n/a"


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


def _holdings_placeholder_table() -> str:
    return (
        "<div class=\"holdings-table-wrap\">"
        "<h3>Current Portfolio Holdings</h3>"
        "<table class=\"holdings-table\">"
        "<thead><tr><th>Symbol</th><th>Shares</th><th>Original Purchase Total Cost</th><th>Current Total Value</th><th>% Gain</th><th>Status</th></tr></thead>"
        "<tbody><tr><td colspan=\"6\">No current portfolio holdings were supplied for this generated dashboard.</td></tr></tbody>"
        "</table>"
        "</div>"
    )


def _dashboard_search_script() -> str:
    return r"""
(function initDashboardSearch(){
  const input = document.querySelector(".dashboard-search");
  const cards = Array.from(document.querySelectorAll(".ticker-card[data-search-text]"));
  if (!input || !cards.length) return;
  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    cards.forEach(card => {
      const haystack = card.getAttribute("data-search-text") || "";
      card.hidden = Boolean(query) && !haystack.includes(query);
    });
  });
})();
"""


def _synced_table_scroller_script() -> str:
    return r"""
(function initSyncedTableScrollers(){
  const top = document.querySelector(".table-scroll-top");
  const bottom = document.querySelector(".table-scroll");
  const table = bottom ? bottom.querySelector("table") : null;
  const spacer = top ? top.querySelector("div") : null;
  if (!top || !bottom || !table || !spacer) return;
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
    earnings = evidence.get("latest_earnings") if isinstance(evidence.get("latest_earnings"), Mapping) else {}
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
          {_metric_tile("Valuation Fit", promotion.get("valuation_fit_score"))}
        </section>
        <section class="panel two-column">
          {_score_panel(scorecard)}
          {_earnings_panel(earnings)}
        </section>
        <section class="panel">
          <div class="section-heading"><p class="eyebrow">Financials</p><h2>Fool-like Metrics</h2></div>
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
      padding: 10px 8px 28px;
      border-bottom: 1px solid rgba(255,255,255,.12);
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
    .dashboard-rail a span {{
      width: 20px;
      height: 20px;
      border: 2px solid rgba(185,205,182,.8);
      border-radius: 6px;
      box-shadow: 12px 0 0 -5px rgba(185,205,182,.6), 0 12px 0 -5px rgba(185,205,182,.6);
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
      font-size: 24px;
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
    .placeholder-panel {{
      border-style: dashed;
      background:
        linear-gradient(135deg, rgba(15,107,86,.08), transparent 45%),
        var(--paper-2);
    }}
    .placeholder-panel p:last-child {{ max-width: 760px; color: var(--muted); }}
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
      min-width: 1120px;
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
    .rankings-table th:nth-child(2), .rankings-table td:nth-child(2) {{ width: 82px; }}
    .rankings-table th:nth-child(3), .rankings-table td:nth-child(3) {{ width: 92px; }}
    .rankings-table th:nth-child(4), .rankings-table td:nth-child(4) {{ width: 150px; }}
    .rankings-table th:nth-child(5), .rankings-table td:nth-child(5) {{ width: 165px; }}
    .rankings-table th:nth-child(6), .rankings-table td:nth-child(6) {{ width: 92px; }}
    .rankings-table th:nth-child(7), .rankings-table td:nth-child(7),
    .rankings-table th:nth-child(8), .rankings-table td:nth-child(8),
    .rankings-table th:nth-child(9), .rankings-table td:nth-child(9),
    .rankings-table th:nth-child(10), .rankings-table td:nth-child(10) {{ width: 78px; }}
    .rankings-table th:nth-child(11), .rankings-table td:nth-child(11) {{ width: 280px; }}
    .rankings-table th:nth-child(12), .rankings-table td:nth-child(12) {{ width: 120px; }}
    .rankings-table th:nth-child(1), .rankings-table td:nth-child(1),
    .rankings-table th:nth-child(2), .rankings-table td:nth-child(2) {{
      position: sticky;
      z-index: 2;
      background: var(--paper-2);
      box-shadow: 1px 0 0 var(--line);
    }}
    .rankings-table th:nth-child(1), .rankings-table td:nth-child(1) {{ left: 0; }}
    .rankings-table th:nth-child(2), .rankings-table td:nth-child(2) {{ left: 54px; }}
    .rankings-table thead th:nth-child(1), .rankings-table thead th:nth-child(2) {{ z-index: 3; }}
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


def _score_panel(scorecard: Mapping[str, Any]) -> str:
    analysis = scorecard.get("analysis") if isinstance(scorecard.get("analysis"), Mapping) else {}
    if not scorecard and not analysis:
        return "<div><div class=\"section-heading\"><p class=\"eyebrow\">Scores</p><h2>Scorecard</h2></div><p>No scorecard available yet.</p></div>"
    rows = []
    for key in ("quality", "growth", "valuation", "safety", "market_attention", "market_buzz"):
        if key in analysis:
            value = _number(analysis.get(key))
            rows.append(
                f"<li><strong>{escape(key.replace('_', ' ').title())}</strong><div class=\"bar\"><i style=\"width:{max(0, min(100, value)):.0f}%\"></i></div></li>"
            )
    return (
        "<div><div class=\"section-heading\"><p class=\"eyebrow\">Scores</p><h2>Scorecard</h2></div>"
        f"<p>Superscore: <strong>{escape(str(scorecard.get('superscore') or 'n/a'))}</strong></p>"
        f"<ul class=\"score-list\">{''.join(rows) or '<li>No analysis bars available.</li>'}</ul></div>"
    )


def _earnings_panel(earnings: Mapping[str, Any]) -> str:
    takeaways = earnings.get("key_takeaways") or earnings.get("positive_developments") or []
    items = "".join(f"<li>{escape(str(item))}</li>" for item in takeaways[:5])
    return (
        "<div><div class=\"section-heading\"><p class=\"eyebrow\">Earnings</p><h2>Latest Earnings</h2></div>"
        f"<p><strong>{escape(str(earnings.get('quarter') or 'Quarter pending'))}</strong></p>"
        f"<p>{escape(str(earnings.get('summary') or 'No earnings narrative available yet.'))}</p>"
        f"<ul class=\"article-list\">{items or '<li>No takeaways captured.</li>'}</ul></div>"
    )


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
    "build_operator_dashboard_html",
    "build_operator_dashboard_markdown",
    "build_operator_dashboard_site",
]
