import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.feedback_refresh import run_feedback_refresh
from longterm.paper_trade_ledger import PaperTradeLedger
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


def _execution_event(ledger, decision_id, *, status="filled", symbol="NVDA"):
    ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_id": "preview-1",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": symbol,
            "side": "buy",
            "notional": 1000,
            "status": status,
            "client_order_id": "client-1",
            "submission_attempt_id": "attempt-1",
            "filled_quantity": 3,
            "filled_price": 101.5,
            "paper_mode": True,
            "live_mode": False,
        }
    )


def test_symbol_feedback_profiles_capture_paper_execution_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _execution_event(ledger, decision_id, status="filled")

    result = run_feedback_refresh(journal=journal, paper_ledger=ledger)
    profile = journal.get_symbol_feedback_profile("NVDA")
    enriched = journal.enrich_idea_with_symbol_feedback(
        {"symbol": "NVDA", "company_name": "Nvidia", "idea_source": "manual", "business_summary": "AI platform."}
    )

    assert result["paper_execution_feedback"]["profiles_updated"] == 1
    assert profile["latest_paper_execution_status"] == "filled"
    assert profile["paper_execution_filled_count"] == 1
    assert profile["latest_paper_broker_order_id"] == "broker-order-1"
    assert any("Paper execution feedback: latest=filled" in note for note in enriched["source_notes"])


def test_feedback_tuning_inputs_include_paper_execution_counts(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _execution_event(ledger, decision_id, status="rejected")

    result = run_feedback_refresh(journal=journal, paper_ledger=ledger)
    symbol_row = result["feedback_tuning_inputs"]["symbols"][0]

    assert symbol_row["paper_execution_latest_status"] == "rejected"
    assert symbol_row["paper_execution_rejected_count"] == 1
