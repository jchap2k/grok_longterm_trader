import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.action_plan_filter import build_paper_submit_candidate_plan
from longterm.action_plan_filter_cli import build_parser, run_cli
from portfolio.portfolio_profile import PortfolioProfile


def test_paper_submit_candidate_filter_keeps_only_actionable_stock_buys():
    plan = {
        "plan_id": "plan-1",
        "mode": "dry_run",
        "intents": [
            {
                "intent_type": "BUY",
                "order_intent": "BUY",
                "symbol": "MSFT",
                "allowed": True,
                "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
            },
            {
                "intent_type": "REVIEW",
                "order_intent": "NONE",
                "symbol": "NVDA",
                "allowed": True,
                "promotion_review": {"promotion_decision": "WATCHLIST_PENDING_EVIDENCE"},
            },
            {"intent_type": "PARK_IDLE_CASH", "order_intent": "BUY", "symbol": "SPY", "allowed": True},
            {
                "intent_type": "BUY",
                "order_intent": "BUY",
                "symbol": "MA",
                "allowed": False,
                "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
            },
        ],
    }

    filtered = build_paper_submit_candidate_plan(plan)

    assert filtered["source_plan_id"] == "plan-1"
    assert filtered["filter_mode"] == "stage6b_actionable_buys_and_approved_parking"
    assert [item["symbol"] for item in filtered["intents"]] == ["MSFT"]
    assert filtered["excluded_summary"]["PARKING_TAXABLE_ACCOUNT"] == 1
    assert filtered["excluded_summary"]["REVIEW"] == 1
    assert filtered["excluded_summary"]["BUY_NOT_ALLOWED_OR_NOT_ACTIONABLE"] == 1
    assert filtered["order_submission_enabled"] is False


def test_paper_submit_candidate_filter_keeps_roth_parking_buy_with_synthetic_decision_id():
    plan = {
        "plan_id": "plan-parking",
        "mode": "dry_run",
        "intents": [
            {
                "intent_type": "PARK_IDLE_CASH",
                "order_intent": "BUY",
                "symbol": "SPY",
                "trade_value": 26554,
                "allowed": True,
                "reason": "Normal regime parking.",
            }
        ],
    }
    profile = PortfolioProfile(account_strategy_mode="roth_ira", defensive_parking_symbol="SPY")

    filtered = build_paper_submit_candidate_plan(plan, profile=profile)

    assert filtered["filter_mode"] == "stage6b_actionable_buys_and_approved_parking"
    assert filtered["kept_count"] == 1
    assert filtered["excluded_count"] == 0
    kept = filtered["intents"][0]
    assert kept["symbol"] == "SPY"
    assert kept["intent_type"] == "PARK_IDLE_CASH"
    assert kept["decision_id"] == "parking-plan-parking-SPY"
    assert kept["parking_execution"] is True


def test_paper_submit_candidate_filter_keeps_roth_defensive_parking_symbols():
    plan = {
        "plan_id": "plan-defensive-parking",
        "mode": "dry_run",
        "intents": [
            {
                "intent_type": "PARK_DEFENSIVE_CASH",
                "order_intent": "BUY",
                "symbol": "SGOV",
                "trade_value": 7000,
                "allowed": True,
            },
            {
                "intent_type": "PARK_DEFENSIVE_CASH",
                "order_intent": "BUY",
                "symbol": "TLT",
                "trade_value": 3000,
                "allowed": True,
            },
        ],
    }
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        defensive_parking_symbol="SPY",
        low_risk_parking_symbol="SGOV",
        duration_hedge_symbol="TLT",
    )

    filtered = build_paper_submit_candidate_plan(plan, profile=profile)

    assert filtered["kept_count"] == 2
    assert [item["symbol"] for item in filtered["intents"]] == ["SGOV", "TLT"]
    assert [item["decision_id"] for item in filtered["intents"]] == [
        "parking-plan-defensive-parking-SGOV",
        "parking-plan-defensive-parking-TLT",
    ]
    assert all(item["parking_execution"] is True for item in filtered["intents"])


def test_paper_submit_candidate_filter_blocks_taxable_or_unapproved_parking():
    plan = {
        "plan_id": "plan-parking",
        "intents": [
            {"intent_type": "PARK_IDLE_CASH", "order_intent": "BUY", "symbol": "SPY", "trade_value": 1000, "allowed": True},
            {"intent_type": "PARK_IDLE_CASH", "order_intent": "BUY", "symbol": "QQQ", "trade_value": 1000, "allowed": True},
        ],
    }
    taxable = PortfolioProfile(account_strategy_mode="taxable", defensive_parking_symbol="SPY")
    roth = PortfolioProfile(account_strategy_mode="roth_ira", defensive_parking_symbol="SPY")

    taxable_filtered = build_paper_submit_candidate_plan(plan, profile=taxable)
    roth_filtered = build_paper_submit_candidate_plan(plan, profile=roth)

    assert taxable_filtered["kept_count"] == 0
    assert taxable_filtered["excluded_summary"]["PARKING_TAXABLE_ACCOUNT"] == 2
    assert [item["symbol"] for item in roth_filtered["intents"]] == ["SPY"]
    assert roth_filtered["excluded_summary"]["PARKING_SYMBOL_NOT_APPROVED"] == 1


def test_action_plan_filter_cli_writes_candidate_plan(tmp_path, capsys):
    source = tmp_path / "action_plan.json"
    output = tmp_path / "candidate_plan.json"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps({"account_strategy_mode": "roth_ira", "defensive_parking_symbol": "SPY"}),
        encoding="utf-8",
    )
    source.write_text(
        json.dumps(
            {
                "intents": [
                    {
                        "intent_type": "BUY",
                        "order_intent": "BUY",
                        "symbol": "MA",
                        "allowed": True,
                        "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                    },
                        {
                            "intent_type": "PARK_IDLE_CASH",
                            "order_intent": "BUY",
                            "symbol": "SPY",
                            "trade_value": 1000,
                            "allowed": True,
                        },
                ]
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            ["--action-plan", str(source), "--output", str(output), "--profile-config", str(profile_path), "--json"]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["kept_count"] == 2
    assert saved["intents"][0]["symbol"] == "MA"
    assert saved["intents"][1]["symbol"] == "SPY"
