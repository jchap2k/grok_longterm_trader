import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.operator_dashboard import build_operator_dashboard, build_operator_dashboard_html
from longterm.operator_dashboard_cli import build_parser, run_cli


def test_operator_dashboard_summarizes_capital_deployment_and_next_step():
    dashboard = build_operator_dashboard(
        action_plan={
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

    code = run_cli(
        build_parser().parse_args(
            [
                "--action-plan",
                str(action_plan),
                "--market-regime",
                str(market_regime),
                "--operator-status",
                str(operator_status),
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
    assert saved["agent_state"] == "ready_to_reveal_submit_command"
    assert "Long-Term Trader Dashboard" in html_output.read_text(encoding="utf-8")
