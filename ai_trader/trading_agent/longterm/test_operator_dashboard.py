import sys
import base64
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.operator_dashboard import (
    _logo_data_uri,
    build_operator_dashboard,
    build_operator_dashboard_evidence_gap_summary,
    build_operator_dashboard_html,
    build_operator_dashboard_site,
)
from longterm.operator_dashboard_cli import build_parser, run_cli


def test_operator_dashboard_summarizes_capital_deployment_and_next_step():
    dashboard = build_operator_dashboard(
        action_plan={
            "suppressed_reasons": ["taxable_broad_parking_suppressed"],
            "intents": [
                {
                    "intent_type": "BUY",
                    "symbol": "MSFT",
                    "allowed": True,
                    "trade_value": 1613.02,
                    "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                },
                {
                    "intent_type": "PARK_IDLE_CASH",
                    "symbol": "SPY",
                    "allowed": True,
                    "trade_value": 30940.0,
                    "risk_review": {"market_regime": "normal"},
                },
            ]
        },
        market_regime={"risk_regime": "normal", "vix_level": 16.99, "ten_year_yield_trend": "stable"},
        operator_status={
            "agent_next_step": {
                "state": "ready_to_reveal_submit_command",
                "message": "Saved preflight artifacts are reviewable.",
            },
            "order_submission_enabled": False,
        },
    )

    assert dashboard["agent_state"] == "ready_to_reveal_submit_command"
    assert dashboard["market_regime"]["risk_regime"] == "normal"
    assert dashboard["buy_intent_count"] == 1
    assert dashboard["parking_intent_count"] == 1
    assert dashboard["paper_submit_candidates"] == ["MSFT"]
    assert dashboard["parking_symbols"] == ["SPY"]
    assert dashboard["suppressed_reasons"] == ["taxable_broad_parking_suppressed"]
    assert dashboard["suppressed_count"] == 1
    assert dashboard["agent_advisory"]["state"] == "ready_for_supervised_paper_review"
    assert dashboard["agent_advisory"]["submit_candidate_count"] == 1
    assert dashboard["order_submission_enabled"] is False


def test_operator_dashboard_html_renders_human_control_surface():
    dashboard = build_operator_dashboard(
        action_plan={
            "intents": [
                {"intent_type": "BUY", "symbol": "MA", "allowed": True, "trade_value": 991.05},
                {"intent_type": "PARK_IDLE_CASH", "symbol": "SPY", "allowed": True, "trade_value": 30940.0},
            ]
        },
        market_regime={"risk_regime": "normal", "vix_level": 17},
        operator_status={"agent_next_step": {"state": "ready_to_reveal_submit_command"}},
    )

    html = build_operator_dashboard_html(dashboard)

    assert "<!doctype html>" in html.lower()
    assert "Long-Term Trader Dashboard" in html
    assert "ready_to_reveal_submit_command" in html
    assert "MA" in html
    assert "SPY" in html
    assert "Order Submission Enabled: false" in html
    assert "data-pipeline-followup-reviewed" in html
    assert "data-pipeline-followup-next-step" in html


def test_operator_dashboard_svg_logo_uses_rail_contrast_variant():
    data_uri = _logo_data_uri()
    assert data_uri.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(data_uri.split(",", 1)[1]).decode("utf-8")
    assert 'viewBox="0 42 320 178"' in svg
    assert "#0F2A5E" not in svg
    assert "#CFEFFF" in svg
    assert 'y="194"' in svg
    assert 'y="220"' in svg
    assert 'font-size="31.5"' in svg
    assert 'fill="#F8FAE8"' in svg
    assert 'font-family="Bahnschrift, Aptos Display, Arial Narrow, Arial, sans-serif"' in svg
    assert "paint-order: stroke fill" not in svg
    assert "Long-Term" in svg
    assert "Trading Agent" in svg
    assert "LONG TERM" not in svg
    assert "TRADING AGENT" not in svg
    assert "TRADER AGENT" not in svg


def test_operator_dashboard_advisory_distinguishes_parking_only_from_blocked():
    parking_only = build_operator_dashboard(
        action_plan={
            "intents": [
                {"intent_type": "PARK_IDLE_CASH", "symbol": "SPY", "allowed": True, "trade_value": 10000}
            ]
        },
        operator_status={"agent_next_step": {"state": "collect_preflight_artifacts"}},
    )
    blocked = build_operator_dashboard(
        action_plan={"intents": [{"intent_type": "BUY", "symbol": "MSFT", "allowed": True}]},
        operator_status={
            "agent_next_step": {
                "state": "blocked_preflight",
                "blockers": ["paper account not clean"],
            }
        },
    )

    assert parking_only["agent_advisory"]["state"] == "parking_only_review"
    assert parking_only["agent_advisory"]["submit_candidate_count"] == 0
    assert blocked["agent_advisory"]["state"] == "blocked_preflight"
    assert "paper account not clean" in blocked["agent_advisory"]["blockers"]


def test_operator_dashboard_cli_writes_json_and_html(tmp_path, capsys):
    action_plan = tmp_path / "action_plan.json"
    market_regime = tmp_path / "market_regime.json"
    operator_status = tmp_path / "operator_status.json"
    scheduler_policy = tmp_path / "scheduler_policy.json"
    output = tmp_path / "dashboard.json"
    html_output = tmp_path / "dashboard.html"
    action_plan.write_text(
        json.dumps({"intents": [{"intent_type": "BUY", "symbol": "MSFT", "allowed": True, "trade_value": 1000}]}),
        encoding="utf-8",
    )
    market_regime.write_text(json.dumps({"risk_regime": "normal"}), encoding="utf-8")
    operator_status.write_text(
        json.dumps({"order_submission_enabled": False, "agent_next_step": {"state": "ready_to_reveal_submit_command"}}),
        encoding="utf-8",
    )
    scheduler_policy.write_text(
        json.dumps(
            {
                "recommended_mode": "account_refresh_only",
                "urgency": "low",
                "next_safe_action": "refresh_account_and_dashboard_artifacts",
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--action-plan",
                str(action_plan),
                "--market-regime",
                str(market_regime),
                "--operator-status",
                str(operator_status),
                "--scheduler-policy",
                str(scheduler_policy),
                "--report-output",
                str(output),
                "--html-output",
                str(html_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["paper_submit_candidates"] == ["MSFT"]
    assert printed["scheduler_policy"]["recommended_mode"] == "account_refresh_only"
    assert saved["agent_state"] == "ready_to_reveal_submit_command"
    assert saved["scheduler_policy"]["next_safe_action"] == "refresh_account_and_dashboard_artifacts"
    assert "Long-Term Trader Dashboard" in html_output.read_text(encoding="utf-8")


def test_operator_dashboard_site_builds_index_and_ticker_pages_with_chart():
    action_plan = {
        "suppressed_reasons": ["taxable_broad_parking_suppressed"],
        "intents": [
            {
                "intent_type": "BUY",
                "symbol": "MSFT",
                "allowed": True,
                "trade_value": 1700,
                "reason": "Cash is sufficient.",
                "promotion_review": {
                    "promotion_decision": "ACTIONABLE_BUY",
                    "confidence": 75,
                    "suggested_size_pct": 5,
                    "valuation_fit_score": 72,
                },
            },
            {
                "intent_type": "REVIEW",
                "symbol": "NVDA",
                "allowed": True,
                "trade_value": 0,
                "reason": "Missing earnings article.",
                "promotion_review": {
                    "promotion_decision": "WATCHLIST_PENDING_EVIDENCE",
                    "followups": ["missing_earnings_article"],
                },
            },
            {
                "intent_type": "PARK_IDLE_CASH",
                "symbol": "SPY",
                "allowed": True,
                "trade_value": 30940,
                "reason": "Normal regime parking.",
            },
            {
                "intent_type": "SELL",
                "order_intent": "SELL",
                "symbol": "AAPL",
                "allowed": True,
                "trade_value": 1200,
                "reason": "Thesis broken; review-only sell simulation.",
            },
            {
                "intent_type": "REBALANCE",
                "order_intent": "SELL_TO_FUND_BUY",
                "symbol": "MSFT",
                "source_symbol": "AAPL",
                "allowed": True,
                "trade_value": 900,
                "reason": "Rebalance candidate; hard-blocked from Stage 6B V1 submission.",
            },
        ]
    }
    dashboard = build_operator_dashboard(
        action_plan=action_plan,
        market_regime={"risk_regime": "normal", "vix_level": 17},
        scheduler_policy={
            "recommended_mode": "account_refresh_only",
            "urgency": "low",
            "reasons": ["account_refresh_stale"],
            "affected_symbols": [],
            "next_safe_action": "refresh_account_and_dashboard_artifacts",
            "order_submission_enabled": False,
        },
        operator_status={"agent_next_step": {"state": "ready_to_reveal_submit_command"}},
    )
    site = build_operator_dashboard_site(
        dashboard=dashboard,
        action_plan=action_plan,
        portfolio_state={
            "cash": 2500,
            "protected_symbols": ["FXAIX"],
            "holdings": [
                {
                    "symbol": "FXAIX",
                    "quantity": 100,
                    "original_purchase_total_cost": 30000,
                    "market_value": 34000,
                },
                {
                    "symbol": "MSFT",
                    "quantity": 4,
                    "avg_entry_price": 325,
                    "market_value": 1700,
                },
            ],
        },
        evidence_items=[
            {
                "symbol": "MSFT",
                "business_summary": "Microsoft is a cloud and productivity platform.",
                "quality_growth_scorecard": {
                    "superscore": 86,
                    "analysis": {"quality": 91, "growth": 84, "valuation": 61, "safety": 78},
                },
                "fundamental_metrics": {
                    "revenue_growth_cagr": {"3_yr_revenue_growth": "15.2%"},
                    "valuation_ttm": {"price_earnings": "33.0x"},
                    "profitability_ttm": {"gross_margin": "69.0%"},
                    "financials_ttm": {"revenue": "$280.0B"},
                },
                "latest_earnings": {
                    "quarter": "Q3 FY2026",
                    "summary": "Cloud demand and AI attach rates supported growth.",
                    "key_takeaways": ["Azure growth remained durable."],
                },
                "article_evidence_summaries": [
                    {"title": "Microsoft expands AI cloud capacity", "summary": "Capex supports cloud demand.", "url": "https://example.com/msft"}
                ],
            },
            {
                "symbol": "NVDA",
                "business_summary": "Nvidia is an accelerated computing platform.",
                "quality_growth_scorecard": {
                    "superscore": 73,
                    "investing_type": "Aggressive Growth",
                    "estimated_drawdown_band": "-40% to -60%",
                    "analysis": {"quality": 80, "growth": 88, "valuation": 42, "safety": 58},
                },
            }
        ],
        price_history_by_symbol={
            "MSFT": [
                {"date": "2026-01-01", "close": 100},
                {"date": "2026-01-02", "close": 112},
                {"date": "2026-01-03", "close": 84},
            ],
            "NVDA": [{"date": "2026-01-01", "close": 90}, {"date": "2026-01-02", "close": 95}],
        },
    )

    assert "index.html" in site
    assert "tickers/MSFT.html" in site
    assert "tickers/NVDA.html" in site
    assert 'href="tickers/MSFT.html"' in site["index.html"]
    assert "Autonomous Research Surface" in site["index.html"]
    assert "Motley-Fool-style research surface" not in site["index.html"]
    assert "Paper Review Ready" in site["index.html"]
    assert "Scheduler Policy" in site["index.html"]
    assert "Account Refresh Only" in site["index.html"]
    assert "refresh account and dashboard artifacts" in site["index.html"]
    assert "Advisory only" in site["index.html"]
    assert "Agent Desk" in site["index.html"]
    assert "Ask Or Draft A Command" in site["index.html"]
    assert "Send disabled until agent chat is wired" in site["index.html"]
    assert "initAgentChatPlaceholder" in site["index.html"]
    assert "agent-chat-bubble" in site["index.html"]
    assert "aria-expanded=\"false\"" in site["index.html"]
    assert "Future versions can send questions or supervised commands into the active long-term agent context" in site["index.html"]
    assert "Why is MSFT a buy?" in site["index.html"]
    assert "dashboard-shell" in site["index.html"]
    assert "dashboard-rail" in site["index.html"]
    assert 'class="nav-icon nav-icon-dashboard"' in site["index.html"]
    assert 'class="nav-icon nav-icon-paper-candidates"' in site["index.html"]
    assert 'class="nav-icon nav-icon-all-tear-sheets"' in site["index.html"]
    assert 'class="nav-icon nav-icon-rankings"' in site["index.html"]
    assert 'class="nav-icon nav-icon-coverage"' in site["index.html"]
    assert 'class="nav-icon nav-icon-scorecards"' in site["index.html"]
    assert 'class="nav-icon nav-icon-evidence-gaps"' in site["index.html"]
    assert 'class="nav-icon nav-icon-portfolio"' in site["index.html"]
    assert 'class="nav-icon nav-icon-safety"' in site["index.html"]
    assert 'class="nav-icon nav-icon-settings"' in site["index.html"]
    assert "nav-icon-fallback" not in site["index.html"]
    assert "Long-Term Trader Agent logo" in site["index.html"]
    assert "data:image/svg+xml;base64," in site["index.html"]
    assert "Autonomous long-term research" not in site["index.html"]
    assert "height: 92px" in site["index.html"]
    assert "max-width: 208px" in site["index.html"]
    assert "margin: 0 auto" in site["index.html"]
    assert "object-position: center center" in site["index.html"]
    assert "object-fit: contain" in site["index.html"]
    assert 'href="#dashboard-overview"' in site["index.html"]
    assert 'href="#paper-candidates"' in site["index.html"]
    assert 'href="#research-board"' in site["index.html"]
    assert 'href="#rankings"' in site["index.html"]
    assert 'href="#coverage"' in site["index.html"]
    assert 'href="#scorecards"' in site["index.html"]
    assert 'href="#evidence-gaps"' in site["index.html"]
    assert 'href="#portfolio"' in site["index.html"]
    assert 'href="#safety"' in site["index.html"]
    assert 'href="#settings"' in site["index.html"]
    assert 'href="#foundational-core"' in site["index.html"]
    assert 'href="#hold-review"' in site["index.html"]
    assert 'href="#closed-positions"' in site["index.html"]
    assert 'href="#about"' in site["index.html"]
    assert "Paper Candidates" in site["index.html"]
    assert "Research Board" in site["index.html"]
    assert "All Tear Sheets" in site["index.html"]
    assert "Scorecards" in site["index.html"]
    assert "Portfolio" in site["index.html"]
    assert "Safety" in site["index.html"]
    assert "Review / Simulation Intents" in site["index.html"]
    assert "Sell and rebalance candidates remain visible for operator review but are never Stage 6B V1 paper-submit candidates." in site["index.html"]
    assert "Thesis broken; review-only sell simulation." in site["index.html"]
    assert "Rebalance candidate; hard-blocked from Stage 6B V1 submission." in site["index.html"]
    assert "dashboard-topbar" in site["index.html"]
    assert 'href="#dashboard-overview">Long-Term Advisor</a>' in site["index.html"]
    assert 'href="#portfolio">My Stocks</a>' in site["index.html"]
    assert 'href="#coverage">My Reports</a>' in site["index.html"]
    assert "Search research universe" in site["index.html"]
    assert "dashboard-search" in site["index.html"]
    assert "initDashboardSearch" in site["index.html"]
    assert "initPaginatedLists" in site["index.html"]
    assert "data-paginated-list" in site["index.html"]
    assert "data-paginated-item" in site["index.html"]
    assert "data-page-size" in site["index.html"]
    assert "pagination-controls" in site["index.html"]
    assert "Showing" in site["index.html"]
    assert "[hidden]" in site["index.html"]
    assert "display: none !important" in site["index.html"]
    assert "data-search-text" in site["index.html"]
    assert 'class="rankings-table"' in site["index.html"]
    assert 'class="scorecards-table"' in site["index.html"]
    assert 'class="evidence-gaps-table"' in site["index.html"]
    assert 'data-search-text="msft' in site["index.html"]
    assert 'data-search-text="nvda' in site["index.html"]
    assert 'document.querySelectorAll("[data-search-text]")' in site["index.html"]
    assert "Best Buys For Review" in site["index.html"]
    assert "S&amp;P 500" in site["index.html"]
    assert "dashboard-tabs" in site["index.html"]
    assert "Overview" in site["index.html"]
    assert "Scorecard" in site["index.html"]
    assert "Foundational Core" in site["index.html"]
    assert "Hold / Review" in site["index.html"]
    assert "Closed Positions" in site["index.html"]
    assert "Overview Highlights" in site["index.html"]
    assert "Latest Recommendation" in site["index.html"]
    assert "Coverage Updates" in site["index.html"]
    assert 'id="dashboard-overview"' in site["index.html"]
    assert 'id="paper-candidates"' in site["index.html"]
    assert 'id="parking"' in site["index.html"]
    assert 'id="portfolio"' in site["index.html"]
    assert 'id="safety"' in site["index.html"]
    assert 'id="research-board"' in site["index.html"]
    assert 'id="rankings"' in site["index.html"]
    assert 'id="scorecards"' in site["index.html"]
    assert 'id="evidence-gaps"' in site["index.html"]
    assert 'id="foundational-core"' in site["index.html"]
    assert 'id="hold-review"' in site["index.html"]
    assert 'id="closed-positions"' in site["index.html"]
    assert 'id="about"' in site["index.html"]
    assert 'id="settings"' in site["index.html"]
    assert "Ranked Stock List" in site["index.html"]
    assert "Operator Action View" in site["index.html"]
    assert "Operator Score" in site["index.html"]
    assert "Action" in site["index.html"]
    assert "Actionability</th>" not in site["index.html"]
    assert "<th>Intent</th>" not in site["index.html"]
    assert "Why Not Buy" in site["index.html"]
    assert "Scorecards below remain the broad evidence matrix" in site["index.html"]
    assert "table-scroll" in site["index.html"]
    assert "table-scroll-top" in site["index.html"]
    assert "initSyncedTableScrollers" in site["index.html"]
    assert "position: sticky" in site["index.html"]
    assert "overflow-wrap: anywhere" in site["index.html"]
    assert "Financial Metrics" in site["tickers/MSFT.html"]
    assert "Fool-like Metrics" not in site["tickers/MSFT.html"]
    assert "Research Follow-Up Queue" in site["index.html"]
    assert "Evidence gaps show what the next enrichment/research loop should fix" in site["index.html"]
    assert "Missing fundamentals" in site["index.html"]
    assert "Missing latest earnings" in site["index.html"]
    assert "Missing article evidence" in site["index.html"]
    assert "Run news/earnings enrichment or capture company-page evidence." in site["index.html"]
    assert "Watchlist / needs evidence" in site["index.html"]
    assert "Missing earnings article" in site["index.html"]
    assert "Quality" in site["index.html"]
    assert "Growth" in site["index.html"]
    assert "Valuation" in site["index.html"]
    assert "Safety" in site["index.html"]
    assert "Trade Value" in site["index.html"]
    assert site["index.html"].index('tickers/MSFT.html">MSFT</a>') < site["index.html"].index('tickers/NVDA.html">NVDA</a>')
    assert "Promotion confidence" in site["index.html"]
    assert "Scorecard superscore" in site["index.html"]
    assert "scroll-margin-top" in site["index.html"]
    assert '<td><a href="tickers/MSFT.html">MSFT</a></td>' in site["index.html"]
    assert "<th>Page</th>" not in site["index.html"]
    assert ">Open</a>" not in site["index.html"]
    assert "Universe Scorecards" in site["index.html"]
    assert "Superscore" in site["index.html"]
    assert ">Super</th>" in site["index.html"]
    assert ">Qual</th>" in site["index.html"]
    assert ">Drawdown</th>" in site["index.html"]
    assert ">Hist DD</th>" in site["index.html"]
    assert "-25.00%" in site["index.html"]
    assert "Investing Type" in site["index.html"]
    assert "Max Drawdown" in site["index.html"]
    assert "Aggressive Growth" in site["index.html"]
    assert "Scorecards Placeholder" not in site["index.html"]
    assert "Foundational Core Placeholder" in site["index.html"]
    assert "Hold / Review Placeholder" in site["index.html"]
    assert "No closed positions are available in this generated dashboard yet." in site["index.html"]
    assert "About This Dashboard" in site["index.html"]
    assert "Settings Placeholder" in site["index.html"]
    assert "Command Center" in site["index.html"]
    assert "Paper-Ready Candidates" in site["index.html"]
    assert "Capital Deployment / Parking" in site["index.html"]
    assert "Portfolio Snapshot" in site["index.html"]
    assert "Current Portfolio Holdings" in site["index.html"]
    assert "Portfolio Totals" in site["index.html"]
    assert "Total Current Value" in site["index.html"]
    assert "Total Gain" in site["index.html"]
    assert "portfolio-gain-chart" in site["index.html"]
    assert "data-portfolio-summary" in site["index.html"]
    assert "initPortfolioLiveRefresh" in site["index.html"]
    assert 'fetch("/api/portfolio.json"' in site["index.html"]
    assert 'data-portfolio-total="current_total_value"' in site["index.html"]
    assert 'data-portfolio-total="gain_percent"' in site["index.html"]
    assert 'data-portfolio-holdings' in site["index.html"]
    assert "Original Purchase Total Cost" in site["index.html"]
    assert "Current Total Value" in site["index.html"]
    assert "% Gain" in site["index.html"]
    assert "FXAIX" in site["index.html"]
    assert "Protected / core" in site["index.html"]
    assert "$30,000.00" in site["index.html"]
    assert "$34,000.00" in site["index.html"]
    assert "+13.33%" in site["index.html"]
    assert "MSFT" in site["index.html"]
    assert "$1,300.00" in site["index.html"]
    assert "+30.77%" in site["index.html"]
    assert "No current portfolio holdings were supplied for this generated dashboard." not in site["index.html"]
    assert "Safety &amp; Preflight" in site["index.html"]
    assert "Tax-Mode Suppressions" in site["index.html"]
    assert "Taxable Broad Parking Suppressed" in site["index.html"]
    assert "taxable_broad_parking_suppressed" in site["index.html"]
    assert "Pipeline Artifact Health" in site["index.html"]
    assert "data-pipeline-health-status" in site["index.html"]
    assert "data-pipeline-resource-provider" in site["index.html"]
    assert "data-pipeline-resource-research-cap" in site["index.html"]
    assert "data-pipeline-resource-committee-cap" in site["index.html"]
    assert "data-pipeline-resource-bounded" in site["index.html"]
    assert "initPipelineHealthRefresh" in site["index.html"]
    assert 'fetch("/api/pipeline-health.json"' in site["index.html"]
    assert "Research Board" in site["index.html"]
    assert "Order submission" in site["index.html"]
    assert "ACTIONABLE_BUY" in site["index.html"]
    assert "Normal regime parking." in site["index.html"]
    assert "https://www.fool.com/premium" in site["index.html"]
    assert "https://www.fool.com/premium/company/NASDAQ/AAPL/financials/summary" in site["tickers/MSFT.html"]
    assert "<svg" in site["tickers/MSFT.html"]
    assert "price-chart" in site["tickers/MSFT.html"]
    assert "chart-workbench" in site["tickers/MSFT.html"]
    assert "data-range=\"1M\"" in site["tickers/MSFT.html"]
    assert "chart-tooltip" in site["tickers/MSFT.html"]
    assert "initInteractiveCharts" in site["tickers/MSFT.html"]
    assert "\"close\": 112.0" in site["tickers/MSFT.html"]
    assert "Historical Max Drawdown" in site["tickers/MSFT.html"]
    assert "-25.00%" in site["tickers/MSFT.html"]
    assert "Q3 FY2026" in site["tickers/MSFT.html"]
    assert "3-Yr Revenue Growth" in site["tickers/MSFT.html"]
    assert "Microsoft is a cloud and productivity platform." in site["tickers/MSFT.html"]
    assert "Missing earnings article." in site["tickers/NVDA.html"]


def test_operator_dashboard_evidence_gap_summary_is_structured():
    summary = build_operator_dashboard_evidence_gap_summary(
        dashboard={"paper_submit_candidates": ["MSFT"]},
        action_plan={
            "intents": [
                {
                    "intent_type": "REVIEW",
                    "symbol": "NVDA",
                    "promotion_review": {
                        "promotion_decision": "WATCHLIST_PENDING_EVIDENCE",
                        "followups": ["missing_earnings_article"],
                    },
                }
            ]
        },
        evidence_items=[
            {
                "symbol": "MSFT",
                "business_summary": "Complete enough.",
                "fundamental_metrics": {"financials_ttm": {"revenue": "$1"}},
                "quality_growth_scorecard": {"superscore": 80},
                "latest_earnings": {"summary": "ok"},
                "article_evidence_summaries": [{"title": "source"}],
            },
            {"symbol": "NVDA", "business_summary": "Accelerated computing platform."},
        ],
    )

    assert summary["mode"] == "operator_dashboard_evidence_gaps"
    assert summary["gap_count"] == 1
    assert summary["symbols_with_gaps"] == ["NVDA"]
    item = summary["items"][0]
    assert item["symbol"] == "NVDA"
    assert "Missing earnings article" in item["promotion_followups"]
    assert "Missing fundamentals" in item["missing_evidence"]
    assert item["suggested_next_step"] == "Run news/earnings enrichment or capture company-page evidence."


def test_operator_dashboard_cli_writes_static_site(tmp_path, capsys):
    action_plan = tmp_path / "action_plan.json"
    dashboard = tmp_path / "dashboard.json"
    evidence = tmp_path / "evidence.json"
    prices = tmp_path / "prices.json"
    portfolio_path = tmp_path / "portfolio.json"
    site_dir = tmp_path / "site"
    action_plan.write_text(
        json.dumps({"intents": [{"intent_type": "BUY", "symbol": "MA", "allowed": True, "trade_value": 1000}]}),
        encoding="utf-8",
    )
    dashboard.write_text(
        json.dumps({"agent_advisory": {"state": "ready_for_supervised_paper_review"}, "paper_submit_candidates": ["MA"]}),
        encoding="utf-8",
    )
    evidence.write_text(json.dumps([{"symbol": "MA", "business_summary": "Payments network."}]), encoding="utf-8")
    prices.write_text(json.dumps({"MA": [{"date": "2026-01-01", "close": 10}, {"date": "2026-01-02", "close": 12}]}), encoding="utf-8")
    portfolio_path.write_text(
        json.dumps(
            {
                "cash": 1000,
                "holdings": [
                    {
                        "symbol": "MA",
                        "quantity": 2,
                        "avg_entry_price": 400,
                        "market_value": 991,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--dashboard-file",
                str(dashboard),
                "--action-plan",
                str(action_plan),
                "--evidence-file",
                str(evidence),
                "--portfolio-state",
                str(portfolio_path),
                "--price-history-file",
                str(prices),
                "--site-output-dir",
                str(site_dir),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["site_output_dir"] == str(site_dir)
    assert printed["evidence_gap_summary"]["gap_count"] == 1
    assert printed["evidence_gap_summary"]["symbols_with_gaps"] == ["MA"]
    assert (site_dir / "index.html").exists()
    assert (site_dir / "tickers" / "MA.html").exists()
    assert "Payments network." in (site_dir / "tickers" / "MA.html").read_text(encoding="utf-8")
    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert "$800.00" in index_html
    assert "+23.88%" in index_html


def test_operator_dashboard_ticker_page_shows_python_scorecard_and_earnings_alias():
    site = build_operator_dashboard_site(
        dashboard={"agent_advisory": {"state": "ready_for_supervised_paper_review"}},
        action_plan={
            "intents": [
                {
                    "intent_type": "BUY",
                    "symbol": "ADBE",
                    "allowed": True,
                    "trade_value": 750,
                    "promotion_review": {
                        "confidence": 72,
                        "valuation_fit_score": 88,
                        "margin_of_safety_score": 71,
                        "permanent_loss_score": 86,
                        "permanent_loss_flags": ["overpayment"],
                        "defensive_enterprising_mode": "enterprising_candidate",
                        "staged_entry_label": "starter_position",
                        "staged_entry_size_pct": 2.0,
                        "normalized_earnings_quality": "normalized_support",
                    },
                }
            ]
        },
        evidence_items=[
            {
                "symbol": "ADBE",
                "business_summary": "Adobe is a creative and document cloud platform.",
                "quality_growth_scorecard": {
                    "superscore": 78.5,
                    "quality_score": 100.0,
                    "growth_score": 57.0,
                    "valuation_score": 100.0,
                    "safety_score": 68.0,
                    "market_attention_score": 56.6,
                    "investing_type": "Moderate Compounder",
                    "score_reasons": ["strong gross margin", "reasonable P/E"],
                },
                "python_first_pass_scan": {
                    "rank_score": 76.6,
                    "moneyball_score": 74.1,
                    "quant_score": 82.3,
                    "rank": 23,
                    "reason": "Advanced by relative top 10.0% Python scan.",
                },
                "latest_earnings_enrichment": {
                    "quarter": "latest_available",
                    "summary": "AI-driven earnings growth and resilient subscription revenue supported the thesis.",
                    "key_financial_takeaways": ["Revenue: $23.77B (+10.53%)"],
                },
            }
        ],
    )

    html = site["tickers/ADBE.html"]
    assert "Superscore" in html
    assert "78.5" in html
    assert "Quality" in html
    assert "100" in html
    assert "Growth" in html
    assert "57" in html
    assert "First-Pass Scan" in html
    assert "Moneyball" in html
    assert "74.1" in html
    assert "Quant" in html
    assert "82.3" in html
    assert "latest_available" not in html
    assert "Latest Available" in html
    assert "AI-driven earnings growth" in html
    assert "Revenue: $23.77B" in html
    assert "Margin of Safety" in html
    assert "Permanent Loss" in html
    assert "starter_position" in html
    assert "overpayment" in html


def test_operator_dashboard_cli_can_fetch_price_history_for_site(tmp_path, capsys):
    action_plan = tmp_path / "action_plan.json"
    dashboard = tmp_path / "dashboard.json"
    portfolio_path = tmp_path / "portfolio.json"
    site_dir = tmp_path / "site"
    action_plan.write_text(
        json.dumps({"intents": [{"intent_type": "BUY", "symbol": "MSFT", "allowed": True, "trade_value": 1000}]}),
        encoding="utf-8",
    )
    dashboard.write_text(json.dumps({"paper_submit_candidates": ["MSFT"]}), encoding="utf-8")
    portfolio_path.write_text(
        json.dumps({"cash": 1000, "holdings": [{"symbol": "FXAIX", "quantity": 100, "market_value": 34000}]}),
        encoding="utf-8",
    )

    def fetcher(symbol, period):
        assert symbol in {"MSFT", "FXAIX"}
        assert period == "1y"
        return [{"date": "2026-01-01", "close": 100}, {"date": "2026-01-02", "close": 110}]

    code = run_cli(
        build_parser().parse_args(
            [
                "--dashboard-file",
                str(dashboard),
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio_path),
                "--site-output-dir",
                str(site_dir),
                "--fetch-price-history",
                "--json",
            ]
        ),
        price_history_fetcher=fetcher,
    )

    printed = json.loads(capsys.readouterr().out)
    ticker_html = (site_dir / "tickers" / "MSFT.html").read_text(encoding="utf-8")
    protected_html = (site_dir / "tickers" / "FXAIX.html").read_text(encoding="utf-8")
    assert code == 0
    assert printed["site_output_dir"] == str(site_dir)
    assert "<svg" in ticker_html
    assert "<svg" in protected_html
