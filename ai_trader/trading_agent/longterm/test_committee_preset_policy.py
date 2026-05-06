import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.committee_preset_policy import build_committee_preset_recommendation
from longterm.committee_preset_policy_cli import build_parser, run_cli


def test_committee_preset_policy_defaults_to_decision_4_for_routine_context():
    recommendation = build_committee_preset_recommendation(
        action_plan={
            "intents": [
                {
                    "symbol": "MSFT",
                    "intent_type": "BUY",
                    "order_intent": "BUY",
                    "trade_value": 1000,
                    "allowed": True,
                }
            ]
        },
        active_sleeve_value=100000,
        market_regime={"risk_regime": "normal", "vix_level": 18},
    )

    assert recommendation["recommended_preset"] == "decision_4"
    assert recommendation["escalation_required"] is False
    assert recommendation["order_submission_enabled"] is False


def test_committee_preset_policy_escalates_large_position_change():
    recommendation = build_committee_preset_recommendation(
        action_plan={
            "intents": [
                {
                    "symbol": "NVDA",
                    "intent_type": "BUY",
                    "order_intent": "BUY",
                    "trade_value": 8500,
                    "allowed": True,
                }
            ]
        },
        active_sleeve_value=100000,
    )

    assert recommendation["recommended_preset"] == "decision_6"
    assert recommendation["escalation_required"] is True
    assert "large_position_change:NVDA" in recommendation["escalation_reasons"]
    assert recommendation["affected_symbols"] == ["NVDA"]


def test_committee_preset_policy_escalates_rebalance_decisions():
    recommendation = build_committee_preset_recommendation(
        action_plan={
            "intents": [
                {
                    "symbol": "META",
                    "source_symbol": "ADBE",
                    "intent_type": "REBALANCE",
                    "order_intent": "SELL_TO_FUND_BUY",
                    "trade_value": 5000,
                    "allowed": True,
                }
            ]
        },
        active_sleeve_value=200000,
    )

    assert recommendation["recommended_preset"] == "decision_6"
    assert "complex_rebalance_decision:META" in recommendation["escalation_reasons"]


def test_committee_preset_policy_escalates_choppy_macro_regime():
    recommendation = build_committee_preset_recommendation(
        market_regime={"risk_regime": "inflation_rate_shock", "vix_level": 31},
        active_sleeve_value=100000,
    )

    assert recommendation["recommended_preset"] == "decision_6"
    assert "choppy_macro_regime:inflation_rate_shock" in recommendation["escalation_reasons"]
    assert "vix_elevated:31.0" in recommendation["escalation_reasons"]


def test_committee_preset_policy_escalates_borderline_valuation_research_item():
    recommendation = build_committee_preset_recommendation(
        research_items=[
            {
                "symbol": "AMZN",
                "quality_growth_scorecard": {
                    "analysis": {"valuation": 48},
                    "superscore": 82,
                },
            }
        ]
    )

    assert recommendation["recommended_preset"] == "decision_6"
    assert "borderline_valuation:AMZN" in recommendation["escalation_reasons"]


def test_committee_preset_policy_escalates_new_unproven_thesis_when_marked():
    recommendation = build_committee_preset_recommendation(
        research_items=[
            {
                "symbol": "IDCC",
                "prior_decision_count": 0,
                "source_recommendation_count": 1,
                "new_or_unproven_thesis": True,
            }
        ]
    )

    assert recommendation["recommended_preset"] == "decision_6"
    assert "new_unproven_thesis:IDCC" in recommendation["escalation_reasons"]


def test_committee_preset_policy_cli_writes_report(tmp_path, capsys):
    action_plan = tmp_path / "action_plan.json"
    market_regime = tmp_path / "market_regime.json"
    report = tmp_path / "committee_policy.json"
    action_plan.write_text(
        json.dumps(
            {
                "intents": [
                    {
                        "symbol": "NVDA",
                        "intent_type": "BUY",
                        "order_intent": "BUY",
                        "trade_value": 7500,
                        "allowed": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    market_regime.write_text(json.dumps({"risk_regime": "normal", "vix_level": 18}), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--action-plan",
                str(action_plan),
                "--market-regime",
                str(market_regime),
                "--active-sleeve-value",
                "100000",
                "--report-output",
                str(report),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["recommended_preset"] == "decision_6"
    assert saved["order_submission_enabled"] is False
    assert "--submit-paper-orders" not in json.dumps(saved)
