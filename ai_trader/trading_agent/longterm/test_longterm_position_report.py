import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.position_report import (
    build_position_intelligence_email,
    build_position_intelligence_report,
)
from longterm.position_report_cli import build_parser, run_cli
from longterm.email_sender import EmailSendResult
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol="NVDA"):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "company_name": "Nvidia" if symbol == "NVDA" else symbol,
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": ["New information: Blackwell supply commentary improved."],
                "invalidation_conditions": ["Data-center margin compression."],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 8,
            "key_thesis": "Blackwell ramp improves long-term earnings power.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def test_position_report_includes_portfolio_summary_and_collected_position_knowledge(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    journal.apply_paper_reconciliation_feedback(
        {"rows": [{"symbol": "NVDA", "status": "mismatch", "mismatch_count": 1, "notes": ["below target"]}]}
    )
    journal.refresh_outcomes_from_price_map({"NVDA": {"candidate_price": 120, "benchmark_price": 110}})
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(
        {
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "previews": [
                {
                    "preview_id": "preview-nvda",
                    "decision_id": decision_id,
                    "symbol": "NVDA",
                    "side": "buy",
                    "order_type": "market_notional_preview",
                    "notional": 1000,
                    "allowed": False,
                    "blocked_reasons": ["cash shortfall"],
                }
            ],
        },
        timestamp=datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
    )
    portfolio = PortfolioState(
        cash=1500,
        holdings=[
            {"symbol": "FXAIX", "market_value": 30000},
            {"symbol": "NVDA", "market_value": 4200},
        ],
        protected_symbols=["FXAIX"],
    )

    report = build_position_intelligence_report(
        journal,
        portfolio_state=portfolio,
        paper_ledger=ledger,
        feedback_summary={
            "outcome_freshness": {
                "items": [
                    {"symbol": "NVDA", "freshness_state": "fresh", "days_since_outcome_update": 0}
                ]
            },
            "eligibility": {
                "items": [
                    {"symbol": "NVDA", "status": "preview_blocked", "blocked_reasons": ["cash shortfall"]}
                ]
            },
        },
    )

    assert "# Long-Term Position Intelligence Report" in report
    assert "| Cash | $1,500.00 |" in report
    assert "| Active sleeve value | $4,200.00 |" in report
    assert "| Protected/core value | $30,000.00 |" in report
    assert "## NVDA - Nvidia" in report
    assert "Current value: $4,200.00" in report
    assert "Latest recommendation: BUY" in report
    assert "Times recommended: 1" in report
    assert "Latest thesis: Blackwell ramp improves long-term earnings power." in report
    assert "New information: Blackwell supply commentary improved." in report
    assert "Paper preview: blocked" in report
    assert "Paper preview blocked reasons: cash shortfall" in report
    assert "Reconciliation: mismatch" in report
    assert "Outcome vs FXAIX: 10.0%" in report
    assert "Outcome freshness: fresh" in report
    assert "Knowledge gaps: none" in report


def test_position_report_surfaces_positions_without_collected_research_as_knowledge_gap(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    portfolio = PortfolioState(
        cash=500,
        holdings=[{"symbol": "TSLA", "market_value": 2500}],
        protected_symbols=["FXAIX"],
    )

    report = build_position_intelligence_report(journal, portfolio_state=portfolio)

    assert "## TSLA - TSLA" in report
    assert "No recommendation profile found." in report
    assert "Knowledge gaps: no recommendation profile; outcome never refreshed; thesis review missing" in report


def test_position_report_cli_outputs_markdown(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal)
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 1500, "holdings": [{"symbol": "NVDA", "market_value": 4200}]}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        ["--journal-db", str(journal.db_path), "--portfolio-state", str(portfolio_path)]
    )

    assert run_cli(args) == 0
    output = capsys.readouterr().out
    assert "# Long-Term Position Intelligence Report" in output
    assert "## NVDA - Nvidia" in output


def test_position_report_email_payload_is_periodic_and_send_gated(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal)
    portfolio = PortfolioState(cash=1500, holdings=[{"symbol": "NVDA", "market_value": 4200}])

    email = build_position_intelligence_email(
        journal,
        portfolio_state=portfolio,
        recipient_email="operator@example.com",
        period="quarterly",
    )

    assert email.should_send is True
    assert email.recipient_email == "operator@example.com"
    assert email.subject == "Quarterly Long-Term Position Intelligence Report"
    assert "informational only" in email.text_body
    assert "# Long-Term Position Intelligence Report" in email.text_body
    assert "<pre" in email.html_body
    assert email.metadata["period"] == "quarterly"


def test_position_report_cli_can_send_with_injected_sender(tmp_path, capsys):
    class FakeSender:
        def __init__(self):
            self.email = None
            self.settings = None

        def send(self, email, settings):
            self.email = email
            self.settings = settings
            return EmailSendResult(True, "fake sent")

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal)
    portfolio_path = tmp_path / "portfolio.json"
    config_path = tmp_path / "email_notifications.json"
    portfolio_path.write_text(
        json.dumps({"cash": 1500, "holdings": [{"symbol": "NVDA", "market_value": 4200}]}),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "email_notifications": True,
                "email_to": "operator@example.com",
                "email_from": "bot@example.com",
                "email_username": "abc123@smtp-brevo.com",
                "email_password": "fake-password",
            }
        ),
        encoding="utf-8",
    )
    sender = FakeSender()
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--email-config",
            str(config_path),
            "--period",
            "monthly",
            "--send",
        ]
    )

    assert run_cli(args, sender=sender) == 0
    assert "fake sent" in capsys.readouterr().out
    assert sender.email.subject == "Monthly Long-Term Position Intelligence Report"
    assert sender.email.should_send is True
