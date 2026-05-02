import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.operator_status_bundle import build_operator_status_bundle, build_operator_status_markdown
from longterm.operator_status_bundle_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from research.intake import create_research_packet_from_idea


def _record_decision(journal):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "unit_test",
                "business_summary": "AI platform.",
                "benchmark_symbol": "FXAIX",
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 5,
            "key_thesis": "AI platform earnings compounder.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def test_operator_status_bundle_combines_lifecycle_readiness_and_position_report(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_id": "preview-nvda",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_price": 100,
            "benchmark_price_at_fill": 100,
            "paper_mode": True,
            "live_mode": False,
        }
    )
    portfolio = PortfolioState(cash=5000, holdings=[{"symbol": "NVDA", "market_value": 4200}])
    action_plan = {
        "plan_id": "plan-1",
        "intents": [
            {
                "symbol": "NVDA",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "decision_id": decision_id,
                "trade_value": 1000,
                "allowed": True,
                "promotion_review": {
                    "symbol": "NVDA",
                    "promotion_decision": "ACTIONABLE_BUY",
                    "followups": [],
                    "blockers": [],
                },
            },
            {
                "symbol": "VEEV",
                "intent_type": "REVIEW",
                "order_intent": "NONE",
                "decision_id": "decision-veev",
                "trade_value": 0,
                "allowed": True,
                "promotion_review": {
                    "symbol": "VEEV",
                    "promotion_decision": "WATCHLIST_PENDING_EVIDENCE",
                    "followups": ["missing_earnings_article"],
                    "blockers": [],
                },
            }
        ],
    }

    bundle = build_operator_status_bundle(
        journal,
        portfolio_state=portfolio,
        paper_ledger=ledger,
        action_plan=action_plan,
        price_map={"NVDA": 120, "FXAIX": 110},
        feedback_summary={"order_submission_enabled": False, "benchmark_guard": {"should_pause_new_buys": False}},
    )

    assert bundle["mode"] == "operator_status_bundle"
    assert bundle["order_submission_enabled"] is False
    assert bundle["paper_lifecycle"]["state_counts"]["outcome_evaluated"] == 1
    assert bundle["buy_promotion_summary"]["counts"]["ACTIONABLE_BUY"] == 1
    assert bundle["buy_promotion_summary"]["counts"]["WATCHLIST_PENDING_EVIDENCE"] == 1
    assert bundle["buy_promotion_summary"]["items"][1]["symbol"] == "VEEV"
    assert bundle["scheduler_readiness"]["ready_for_scheduler_paper_submit"] is False
    assert "Paper outcome vs FXAIX: 10.0%" in bundle["position_report_markdown"]
    markdown = build_operator_status_markdown(bundle)
    assert "# Long-Term Operator Status Bundle" in markdown
    assert "## Buy Promotion" in markdown
    assert "| VEEV | WATCHLIST_PENDING_EVIDENCE | missing_earnings_article |  |" in markdown
    assert "## Scheduler Readiness" in markdown


def test_operator_status_bundle_cli_outputs_json(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    journal_path = journal.db_path
    ledger_path = tmp_path / "paper.db"
    portfolio_path = tmp_path / "portfolio.json"
    action_plan_path = tmp_path / "action_plan.json"
    price_map_path = tmp_path / "prices.json"
    feedback_path = tmp_path / "feedback.json"
    PaperTradeLedger(ledger_path).record_execution_event(
        {
            "decision_id": decision_id,
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_price": 100,
            "benchmark_price_at_fill": 100,
            "paper_mode": True,
            "live_mode": False,
        }
    )
    portfolio_path.write_text(json.dumps({"cash": 5000, "holdings": [{"symbol": "NVDA", "market_value": 4200}]}), encoding="utf-8")
    action_plan_path.write_text(
        json.dumps(
            {
                "intents": [
                    {
                        "symbol": "NVDA",
                        "order_intent": "BUY",
                        "decision_id": decision_id,
                        "promotion_review": {
                            "symbol": "NVDA",
                            "promotion_decision": "ACTIONABLE_BUY",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    price_map_path.write_text(json.dumps({"NVDA": 120, "FXAIX": 110}), encoding="utf-8")
    feedback_path.write_text(json.dumps({"order_submission_enabled": False}), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal_path),
            "--portfolio-state",
            str(portfolio_path),
            "--paper-ledger-db",
            str(ledger_path),
            "--action-plan",
            str(action_plan_path),
            "--price-map",
            str(price_map_path),
            "--feedback-summary",
            str(feedback_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "operator_status_bundle"
    assert payload["buy_promotion_summary"]["counts"]["ACTIONABLE_BUY"] == 1
    assert payload["paper_lifecycle"]["state_counts"]["outcome_evaluated"] == 1
