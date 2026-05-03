"""Static dashboard summaries and pages for long-term trader operator artifacts."""

from __future__ import annotations

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
    cards = []
    for symbol in symbols:
        intent = _intent_for_symbol(action_plan, symbol)
        evidence = evidence_by_symbol.get(symbol, {})
        cards.append(
            "<a class=\"ticker-card\" href=\"tickers/{symbol}.html\">"
            "<span class=\"ticker-kicker\">{intent}</span>"
            "<strong>{symbol}</strong>"
            "<em>{summary}</em>"
            "<small>{value}</small>"
            "</a>".format(
                symbol=escape(symbol),
                intent=escape(str(intent.get("intent_type") or "RESEARCH")),
                summary=escape(_short_text(str(evidence.get("business_summary") or intent.get("reason") or "Open research page."), 105)),
                value=escape(_money(intent.get("trade_value") or intent.get("target_value") or 0)),
            )
        )
    return _html_shell(
        title="Long-Term Trader Dashboard",
        body=f"""
        <section class="hero">
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
        <section class="panel">
          <div class="section-heading">
            <p class="eyebrow">Research Board</p>
            <h2>Current Candidates and Parking</h2>
          </div>
          <div class="ticker-grid">{''.join(cards)}</div>
        </section>
        <section class="safety-strip">
          <strong>Read-only:</strong> this dashboard does not submit broker orders. Stage 6B still requires explicit supervised confirmation.
        </section>
        {_reference_footer()}
        """,
    )


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
    .hero, .ticker-hero, .panel, .safety-strip, .top-nav {{
      width: min(1180px, calc(100vw - 40px));
      margin: 22px auto;
    }}
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
      .hero, .ticker-hero, .panel {{ padding: 24px; }}
      .ticker-hero {{ grid-template-columns: 1fr; }}
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
    points = []
    for item in history:
        try:
            close = float(item.get("close"))
        except (TypeError, ValueError):
            continue
        points.append(close)
    if len(points) < 2:
        return "<div class=\"empty-chart\">Price history unavailable for this generated page.</div>"
    width, height, pad = 820, 260, 26
    low, high = min(points), max(points)
    spread = high - low or 1.0
    coords = []
    for index, value in enumerate(points):
        x = pad + (index / max(1, len(points) - 1)) * (width - pad * 2)
        y = height - pad - ((value - low) / spread) * (height - pad * 2)
        coords.append(f"{x:.1f},{y:.1f}")
    first, last = points[0], points[-1]
    change = ((last - first) / first) * 100 if first else 0.0
    color = "#0f6b56" if change >= 0 else "#7f2f25"
    return (
        f"<svg viewBox=\"0 0 {width} {height}\" role=\"img\" aria-label=\"Price chart\">"
        "<defs><linearGradient id=\"chartFill\" x1=\"0\" x2=\"0\" y1=\"0\" y2=\"1\">"
        f"<stop offset=\"0\" stop-color=\"{color}\" stop-opacity=\"0.22\"/>"
        f"<stop offset=\"1\" stop-color=\"{color}\" stop-opacity=\"0.02\"/>"
        "</linearGradient></defs>"
        f"<path d=\"M {pad},{height-pad} L {' L '.join(coords)} L {width-pad},{height-pad} Z\" fill=\"url(#chartFill)\"/>"
        f"<polyline points=\"{' '.join(coords)}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>"
        f"<text x=\"{pad}\" y=\"34\" fill=\"#6d6658\" font-size=\"16\">Last: {last:.2f} | Change: {change:+.1f}%</text>"
        "</svg>"
    )


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
