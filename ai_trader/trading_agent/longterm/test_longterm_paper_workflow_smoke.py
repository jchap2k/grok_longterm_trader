import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.paper_workflow_smoke import build_paper_workflow_smoke_report
from longterm.paper_workflow_smoke_cli import build_parser, run_cli
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _actionable_promotion(symbol="NVDA"):
    return {
        "symbol": symbol,
        "promotion_decision": "ACTIONABLE_BUY",
        "is_orderable": True,
        "evidence_brief": "Versioned evidence packet includes thesis and article support.",
        "evidence_version": "2026-05-02T12:00:00Z",
        "blockers": [],
        "followups": [],
        "warnings": [],
    }


@dataclass
class FakeQuote:
    price: float


class FakeQuoteProvider:
    def __init__(self, prices):
        self.prices = prices

    def get_quote(self, symbol):
        return FakeQuote(self.prices[symbol])


class ChattyQuoteProvider(FakeQuoteProvider):
    def get_quote(self, symbol):
        print(f"quote noise for {symbol}")
        return super().get_quote(symbol)

    def close(self):
        print("workflow provider close noise")


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


def _action_plan(decision_id):
    return {
        "schema_version": 1,
        "plan_id": "plan-smoke",
        "mode": "dry_run",
        "status": "ready",
        "benchmark_gate_reason": "Benchmark gate allows new buys.",
        "intents": [
            {
                "symbol": "NVDA",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 1000,
                "target_value": 1000,
                "allowed": True,
                "reason": "Candidate is ready.",
                "decision_id": decision_id,
                "promotion_review": _actionable_promotion(),
            }
        ],
    }


def test_paper_workflow_smoke_builds_price_preview_and_execution_audit(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)

    report = build_paper_workflow_smoke_report(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        quote_provider=FakeQuoteProvider({"NVDA": 193.5}),
    )

    assert report["mode"] == "paper_workflow_smoke"
    assert report["ready_for_supervised_submit"] is True
    assert report["price_map"]["price_map"] == {"NVDA": 193.5}
    assert report["preview"]["previews"][0]["quantity"] == 5
    assert report["execution_audit"]["ready_count"] == 1
    assert report["execution_audit"]["submitted_count"] == 0
    assert len(ledger.list_previews(limit=10)) == 1
    assert ledger.list_execution_events(limit=10) == []


def test_paper_workflow_smoke_blocks_non_actionable_buy_promotion(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal, symbol="VEEV")
    action_plan = _action_plan(decision_id)
    action_plan["intents"][0]["symbol"] = "VEEV"
    action_plan["intents"][0]["promotion_review"] = {
        "promotion_decision": "WATCHLIST_PENDING_EVIDENCE",
        "is_orderable": False,
        "blockers": ["missing_article_evidence"],
    }

    report = build_paper_workflow_smoke_report(
        action_plan,
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        quote_provider=FakeQuoteProvider({"VEEV": 200.0}),
    )

    assert report["ready_for_supervised_submit"] is False
    assert "buy_promotion_blocked_rows" in report["blockers"]
    assert "preview_blocked_rows" in report["blockers"]
    assert "execution_audit_blocked_items" in report["blockers"]
    assert report["promotion_summary"]["blocked_count"] == 1


def test_paper_workflow_smoke_stays_ready_when_action_plan_contains_parking_intent(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal, symbol="AMZN")
    action_plan = _action_plan(decision_id)
    action_plan["intents"][0]["symbol"] = "AMZN"
    action_plan["intents"].append(
        {
            "symbol": "SPY",
            "intent_type": "PARK_IDLE_CASH",
            "order_intent": "BUY",
            "trade_value": 4150,
            "allowed": True,
            "reason": "Normal regime parking.",
        }
    )

    report = build_paper_workflow_smoke_report(
        action_plan,
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        quote_provider=FakeQuoteProvider({"AMZN": 200.0}),
    )

    assert report["ready_for_supervised_submit"] is True
    assert report["preview"]["allowed_count"] == 1
    assert report["preview"]["no_order_count"] == 1
    assert report["execution_audit"]["ready_count"] == 1
    assert report["execution_audit"]["excluded_count"] == 1


def test_paper_workflow_smoke_blocks_when_price_map_missing_symbol(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)

    report = build_paper_workflow_smoke_report(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        quote_provider=FakeQuoteProvider({"NVDA": 0.0}),
    )

    assert report["ready_for_supervised_submit"] is False
    assert "price_map_missing_symbols" in report["blockers"]
    assert "preview_blocked_rows" in report["blockers"]
    assert "execution_audit_blocked_items" in report["blockers"]


def test_paper_workflow_smoke_cli_outputs_json(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    action_plan_path = tmp_path / "action_plan.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    action_plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(tmp_path / "paper.db"),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(action_plan_path),
            "--json",
        ]
    )

    assert run_cli(args, quote_provider_factory=lambda: FakeQuoteProvider({"NVDA": 193.5})) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready_for_supervised_submit"] is True
    assert payload["order_submission_enabled"] is False


def test_paper_workflow_smoke_cli_keeps_json_stdout_clean_when_provider_is_chatty(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    action_plan_path = tmp_path / "action_plan.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    action_plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(tmp_path / "paper.db"),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(action_plan_path),
            "--json",
        ]
    )

    assert run_cli(args, quote_provider_factory=lambda: ChattyQuoteProvider({"NVDA": 193.5})) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["ready_for_supervised_submit"] is True
    assert "quote noise" not in captured.out
    assert "workflow provider close noise" not in captured.out
    assert "quote noise" in captured.err
    assert "workflow provider close noise" in captured.err


def test_paper_workflow_smoke_cli_writes_report_output(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    action_plan_path = tmp_path / "action_plan.json"
    report_path = tmp_path / "paper_workflow_smoke.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    action_plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(tmp_path / "paper.db"),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(action_plan_path),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args, quote_provider_factory=lambda: FakeQuoteProvider({"NVDA": 193.5})) == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert stdout_payload["ready_for_supervised_submit"] is True
    assert file_payload["ready_for_supervised_submit"] is True
    assert file_payload["preview"]["preview_count"] == 1
