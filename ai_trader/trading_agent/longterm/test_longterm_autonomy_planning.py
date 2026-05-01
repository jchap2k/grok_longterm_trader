import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.action_planner import ActionPlanner
from longterm.action_planner_cli import build_parser, run_cli
from longterm.portfolio_state import PortfolioState
from longterm.thesis_monitor import ThesisMonitor
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def test_portfolio_state_loads_cash_holdings_and_protected_symbols(tmp_path):
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "cash": 1250,
                "holdings": [
                    {"symbol": "FXAIX", "market_value": 34000, "quantity": 120.5},
                    {"symbol": "AAPL", "market_value": 3000, "quantity": 12},
                ],
            }
        ),
        encoding="utf-8",
    )
    profile = PortfolioProfile(protected_symbols=["FXAIX"])

    state = PortfolioState.from_file(path, profile=profile)

    assert state.cash == 1250.0
    assert state.holding_value("AAPL") == 3000.0
    assert state.holding_value("FXAIX") == 34000.0
    assert state.active_market_value == 3000.0
    assert state.protected_market_value == 34000.0


def test_action_planner_builds_buy_plan_when_cash_is_available():
    planner = ActionPlanner()
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000)
    packet = create_research_packet_from_idea({"symbol": "NVDA"})

    plan = planner.plan(
        packet,
        profile=profile,
        portfolio_state=state,
        decision={"recommendation": "BUY", "confidence": 86, "suggested_size_pct": 6},
    )

    assert plan.action == "BUY"
    assert plan.order_intent == "BUY"
    assert plan.target_value == 2040.0
    assert plan.cash_shortfall == 0.0
    assert plan.allowed is True


def test_action_planner_marks_capital_needed_when_cash_is_short():
    planner = ActionPlanner()
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=500)
    packet = create_research_packet_from_idea({"symbol": "NVDA"})

    plan = planner.plan(
        packet,
        profile=profile,
        portfolio_state=state,
        decision={"recommendation": "BUY", "confidence": 91, "suggested_size_pct": 8},
    )

    assert plan.action == "BUY"
    assert plan.allowed is False
    assert plan.cash_shortfall == 2220.0
    assert plan.capital_needed_alert is True


def test_action_planner_blocks_protected_sell_or_reduce():
    planner = ActionPlanner()
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=500, holdings=[{"symbol": "FXAIX", "market_value": 34000}])
    packet = create_research_packet_from_idea({"symbol": "FXAIX"})

    plan = planner.plan(
        packet,
        profile=profile,
        portfolio_state=state,
        decision={"recommendation": "SELL", "confidence": 95, "suggested_size_pct": 0},
    )

    assert plan.action == "PROTECTED_HOLD"
    assert plan.order_intent == "NONE"
    assert plan.allowed is False
    assert "protected" in plan.reason.lower()


def test_action_planner_allows_reduce_for_nonprotected_holding():
    planner = ActionPlanner()
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=500, holdings=[{"symbol": "AAPL", "market_value": 4000}])
    packet = create_research_packet_from_idea({"symbol": "AAPL"})

    plan = planner.plan(
        packet,
        profile=profile,
        portfolio_state=state,
        decision={"recommendation": "REDUCE", "confidence": 78, "suggested_size_pct": 5},
    )

    assert plan.action == "REDUCE"
    assert plan.order_intent == "SELL"
    assert plan.target_value == 1700.0
    assert plan.trade_value == 2300.0
    assert plan.allowed is True


def test_thesis_monitor_marks_review_due_by_cadence():
    monitor = ThesisMonitor(today=date(2026, 4, 29))
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "review_cadence": "monthly",
            "invalidation_conditions": ["Cloud growth materially slows"],
        }
    )

    status = monitor.evaluate(
        packet,
        last_review_date=date(2026, 3, 20),
        current_evidence=["Cloud growth remains durable"],
    )

    assert status.review_due is True
    assert status.days_since_review == 40
    assert status.thesis_state == "stale"


def test_thesis_monitor_detects_broken_invalidation_conditions():
    monitor = ThesisMonitor(today=date(2026, 4, 29))
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "review_cadence": "monthly",
            "invalidation_conditions": ["Cloud growth materially slows"],
        }
    )

    status = monitor.evaluate(
        packet,
        last_review_date=date(2026, 4, 24),
        current_evidence=["Cloud growth materially slows and guidance was cut"],
    )

    assert status.review_due is False
    assert status.thesis_state == "broken"
    assert status.matched_invalidation_conditions == ["Cloud growth materially slows"]


def test_thesis_monitor_detects_weakening_evidence_before_break():
    monitor = ThesisMonitor(today=date(2026, 4, 29))
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "review_cadence": "quarterly",
            "invalidation_conditions": ["Cloud growth materially slows"],
        }
    )

    status = monitor.evaluate(
        packet,
        last_review_date=date(2026, 4, 1),
        current_evidence=["Recent report mentions margin pressure and slowing growth."],
    )

    assert status.review_due is False
    assert status.thesis_state == "weakening"
    assert "margin pressure" in status.matched_invalidation_conditions


def test_action_planner_cli_outputs_dry_run_json(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 5000, "holdings": []}),
        encoding="utf-8",
    )
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(
        json.dumps({"recommendation": "BUY", "confidence": 86, "suggested_size_pct": 6}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--symbol",
            "NVDA",
            "--portfolio-state",
            str(portfolio_path),
            "--decision-file",
            str(decision_path),
        ]
    )

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["symbol"] == "NVDA"
    assert payload["order_intent"] == "BUY"
    assert payload["allowed"] is True
