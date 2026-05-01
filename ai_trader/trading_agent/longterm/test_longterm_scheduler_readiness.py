import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from longterm.scheduler_readiness import (
    build_scheduler_readiness_markdown,
    build_scheduler_readiness_report,
)
from longterm.scheduler_readiness_cli import build_parser, run_cli
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol="NVDA"):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "company_name": symbol,
                "idea_source": "unit_test",
                "business_summary": "Durable business.",
                "benchmark_symbol": "FXAIX",
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 5,
            "key_thesis": "Strong long-term setup.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def _action_plan(decision_id, *, symbol="NVDA", source_symbol="", order_intent="BUY"):
    return {
        "plan_id": "plan-1",
        "mode": "dry_run",
        "intents": [
            {
                "symbol": symbol,
                "source_symbol": source_symbol,
                "intent_type": "BUY",
                "order_intent": order_intent,
                "allowed": True,
                "decision_id": decision_id,
                "trade_value": 1000,
            }
        ],
    }


def test_scheduler_readiness_is_advisory_even_when_inputs_are_clean(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    portfolio = PortfolioState(cash=5000, holdings=[{"symbol": "NVDA", "market_value": 2500}])

    report = build_scheduler_readiness_report(
        journal,
        portfolio_state=portfolio,
        action_plan=_action_plan(decision_id),
        feedback_summary={
            "order_submission_enabled": False,
            "benchmark_guard": {"should_pause_new_buys": False, "reason": "ok"},
        },
        paper_lifecycle_summary={"items": [{"symbol": "NVDA", "lifecycle_state": "outcome_evaluated"}]},
    )

    assert report["mode"] == "scheduler_readiness_report"
    assert report["scheduler_submission_enabled"] is False
    assert report["ready_for_scheduler_paper_submit"] is False
    assert report["blocker_count"] == 0
    assert any(check["check_id"] == "scheduler_advisory_only_v1" for check in report["checks"])
    assert "Scheduler Readiness" in build_scheduler_readiness_markdown(report)


def test_scheduler_readiness_blocks_protected_symbol_buy_intents(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal, symbol="FXAIX")
    portfolio = PortfolioState(cash=5000, holdings=[{"symbol": "FXAIX", "market_value": 30000}])

    report = build_scheduler_readiness_report(
        journal,
        portfolio_state=portfolio,
        action_plan=_action_plan(decision_id, symbol="FXAIX"),
        feedback_summary={"order_submission_enabled": False},
    )

    assert report["blocker_count"] >= 1
    assert any(check["check_id"] == "protected_symbol_intents" and check["status"] == "blocker" for check in report["checks"])


def test_scheduler_readiness_blocks_lifecycle_errors_and_warns_on_rejections(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal)

    report = build_scheduler_readiness_report(
        journal,
        portfolio_state=PortfolioState(cash=5000, holdings=[]),
        action_plan={"intents": []},
        paper_lifecycle_summary={
            "items": [
                {"symbol": "NVDA", "lifecycle_state": "execution_status_error"},
                {"symbol": "MSFT", "lifecycle_state": "execution_rejected"},
            ]
        },
    )

    assert any(check["check_id"] == "paper_lifecycle_errors" and check["status"] == "blocker" for check in report["checks"])
    assert any(check["check_id"] == "paper_execution_rejections" and check["status"] == "warning" for check in report["checks"])


def test_scheduler_readiness_cli_outputs_json(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    portfolio_path = tmp_path / "portfolio.json"
    action_plan_path = tmp_path / "action_plan.json"
    feedback_path = tmp_path / "feedback.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "holdings": []}), encoding="utf-8")
    action_plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    feedback_path.write_text(
        json.dumps({"order_submission_enabled": False, "benchmark_guard": {"should_pause_new_buys": False}}),
        encoding="utf-8",
    )
    lifecycle_path.write_text(json.dumps({"items": []}), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(action_plan_path),
            "--feedback-summary",
            str(feedback_path),
            "--paper-lifecycle-summary",
            str(lifecycle_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "scheduler_readiness_report"
    assert payload["ready_for_scheduler_paper_submit"] is False
