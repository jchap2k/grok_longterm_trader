import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.action_plan_filter import build_paper_submit_candidate_plan
from longterm.action_plan_filter_cli import build_parser, run_cli


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
    assert filtered["filter_mode"] == "stage6b_simple_actionable_stock_buys"
    assert [item["symbol"] for item in filtered["intents"]] == ["MSFT"]
    assert filtered["excluded_summary"]["PARK_IDLE_CASH"] == 1
    assert filtered["excluded_summary"]["REVIEW"] == 1
    assert filtered["excluded_summary"]["BUY_NOT_ALLOWED_OR_NOT_ACTIONABLE"] == 1
    assert filtered["order_submission_enabled"] is False


def test_action_plan_filter_cli_writes_candidate_plan(tmp_path, capsys):
    source = tmp_path / "action_plan.json"
    output = tmp_path / "candidate_plan.json"
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
                    {"intent_type": "PARK_IDLE_CASH", "order_intent": "BUY", "symbol": "SPY", "allowed": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(build_parser().parse_args(["--action-plan", str(source), "--output", str(output), "--json"]))

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["kept_count"] == 1
    assert saved["intents"][0]["symbol"] == "MA"
