import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.next_actions import build_next_actions_markdown
from longterm.paper_execution_status import PaperExecutionStatusBuilder
from longterm.paper_preview_ledger_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.position_report import build_position_intelligence_report
from longterm.report_builder import RecommendationTableBuilder, build_markdown_report
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record_decision(journal, *, symbol="NVDA"):
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
    return ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_log_id": "preview-log-1",
            "preview_id": "preview-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": symbol,
            "side": "buy",
            "notional": 1000,
            "status": status,
            "client_order_id": "client-1",
            "submission_attempt_id": "attempt-1",
            "paper_mode": True,
            "live_mode": False,
            "filled_quantity": 3,
            "filled_price": 101.5,
        }
    )


def test_execution_status_builder_maps_latest_events_by_decision_and_symbol(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = "decision-1"
    _execution_event(ledger, decision_id, status="submitted")
    _execution_event(ledger, decision_id, status="filled")

    status = PaperExecutionStatusBuilder(ledger).build()

    by_decision = status.by_decision_id[decision_id]
    by_symbol = status.by_symbol["NVDA"]
    assert by_decision["paper_execution_status"] == "filled"
    assert by_decision["paper_execution_broker_order_id"] == "broker-order-1"
    assert by_decision["paper_execution_filled_quantity"] == 3
    assert by_symbol["paper_execution_filled_count"] == 1
    assert by_symbol["paper_execution_latest_status"] == "filled"


def test_recommendation_report_surfaces_latest_paper_execution_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _execution_event(ledger, decision_id, status="filled")
    status = PaperExecutionStatusBuilder(ledger).build()

    rows = RecommendationTableBuilder(
        journal,
        paper_execution_status_by_decision=status.by_decision_id,
        paper_execution_status_by_symbol=status.by_symbol,
    ).build()
    markdown = build_markdown_report(
        journal,
        paper_execution_status_by_decision=status.by_decision_id,
        paper_execution_status_by_symbol=status.by_symbol,
    )

    assert rows[0]["paper_execution_status"] == "filled"
    assert rows[0]["paper_execution_broker_order_id"] == "broker-order-1"
    assert "Paper Execution" in markdown
    assert "filled" in markdown
    assert "broker-order-1" in markdown


def test_next_actions_and_position_report_surface_execution_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _execution_event(ledger, decision_id, status="filled")
    status = PaperExecutionStatusBuilder(ledger).build()
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=1000, protected_symbols=["FXAIX"], holdings=[{"symbol": "NVDA", "market_value": 1000}])

    actions = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        paper_execution_status_by_decision=status.by_decision_id,
        paper_execution_status_by_symbol=status.by_symbol,
    )
    position = build_position_intelligence_report(journal, portfolio_state=state, paper_ledger=ledger)

    assert "paper_execution_filled" in actions
    assert "broker-order-1" in actions
    assert "Paper execution: filled" in position
    assert "Paper broker order: broker-order-1" in position


def test_paper_preview_ledger_cli_lists_execution_events(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _execution_event(ledger, "decision-1", status="filled")
    parser = build_parser()

    args = parser.parse_args(["executions", "--ledger-db", str(ledger.db_path), "--limit", "5"])
    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["status"] == "filled"
    assert payload[0]["broker_order_id"] == "broker-order-1"
