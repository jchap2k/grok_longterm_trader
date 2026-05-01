import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.capital_alert_cli import build_parser as build_capital_alert_parser
from longterm.capital_alert_cli import run_cli as run_capital_alert_cli
from longterm.email_sender import EmailSendResult
from longterm.journal_cli import build_parser, run_cli
from longterm.motley_fool_capture_cli import build_parser as build_motley_parser
from longterm.motley_fool_capture_cli import run_cli as run_motley_cli
from longterm.motley_fool_settings import load_motley_fool_capture_settings
from research.intake import create_research_packet_from_idea


def _record_sample_decision(db_path: Path) -> str:
    journal = LongTermDecisionJournal(db_path)
    packet = create_research_packet_from_idea(
        {
            "symbol": "AAPL",
            "company_name": "Apple",
            "benchmark_symbol": "FXAIX",
        }
    )
    return journal.record_decision(
        packet,
        decision={
            "recommendation": "BUY",
            "confidence": 82,
            "suggested_size_pct": 6.5,
            "key_thesis": "Durable compounder.",
        },
        candidate_price=100.0,
        benchmark_price=200.0,
    )


def test_decision_journal_lists_recent_decisions(tmp_path):
    db_path = tmp_path / "journal.db"
    decision_id = _record_sample_decision(db_path)
    journal = LongTermDecisionJournal(db_path)

    rows = journal.list_recent_decisions(limit=5)

    assert rows[0]["decision_id"] == decision_id
    assert rows[0]["symbol"] == "AAPL"


def test_journal_cli_summary_outputs_json(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    decision_id = _record_sample_decision(db_path)
    LongTermDecisionJournal(db_path).update_outcome(
        decision_id,
        candidate_price=112.0,
        benchmark_price=210.0,
    )
    parser = build_parser()
    args = parser.parse_args(["summary", "--journal-db", str(db_path)])

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["evaluated_decisions"] == 1
    assert payload["average_excess_return_pct"] == 7.0


def test_journal_cli_update_outcome_updates_returns(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    decision_id = _record_sample_decision(db_path)
    parser = build_parser()
    args = parser.parse_args(
        [
            "update-outcome",
            "--journal-db",
            str(db_path),
            "--decision-id",
            decision_id,
            "--candidate-price",
            "90",
            "--benchmark-price",
            "220",
            "--notes",
            "first review",
        ]
    )

    exit_code = run_cli(args)
    row = LongTermDecisionJournal(db_path).get_decision(decision_id)

    assert exit_code == 0
    assert "updated" in capsys.readouterr().out
    assert row["candidate_return_pct"] == -10.0
    assert row["benchmark_return_pct"] == 10.0
    assert row["excess_return_pct"] == -20.0
    assert row["outcome_notes"] == "first review"


def test_journal_cli_list_outputs_recent_rows(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    _record_sample_decision(db_path)
    parser = build_parser()
    args = parser.parse_args(["list", "--journal-db", str(db_path), "--limit", "3"])

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["symbol"] == "AAPL"


def test_journal_cli_lists_and_resolves_deferred_research_items(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    journal = LongTermDecisionJournal(db_path)
    deferred_id = journal.record_deferred_research_item(
        {
            "symbol": "TSLA",
            "reason": "incomplete_research_packet",
            "missing_fields": ["company_name"],
            "provenance_bucket": "manual",
            "suggested_next_step": "enrich_candidate_before_research",
        }
    )
    parser = build_parser()

    list_args = parser.parse_args(["deferred-list", "--journal-db", str(db_path)])
    list_exit_code = run_cli(list_args)
    listed_payload = json.loads(capsys.readouterr().out)

    assert list_exit_code == 0
    assert listed_payload[0]["deferred_id"] == deferred_id
    assert listed_payload[0]["symbol"] == "TSLA"

    resolve_args = parser.parse_args(
        [
            "deferred-resolve",
            "--journal-db",
            str(db_path),
            "--deferred-id",
            deferred_id,
            "--notes",
            "Added fundamentals.",
        ]
    )
    resolve_exit_code = run_cli(resolve_args)

    assert resolve_exit_code == 0
    assert "resolved" in capsys.readouterr().out
    assert LongTermDecisionJournal(db_path).list_deferred_research_items(limit=5) == []


def test_journal_cli_report_can_record_rank_snapshot(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    _record_sample_decision(db_path)
    parser = build_parser()
    args = parser.parse_args(
        ["report", "--journal-db", str(db_path), "--limit", "3", "--record-rank-snapshot"]
    )

    exit_code = run_cli(args)
    snapshot = LongTermDecisionJournal(db_path).latest_recommendation_rank_by_symbol()

    assert exit_code == 0
    assert "# Long-Term Trader Decision Report" in capsys.readouterr().out
    assert snapshot["AAPL"]["rank"] == 1


def test_journal_cli_records_and_lists_thesis_reviews(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    decision_id = _record_sample_decision(db_path)
    parser = build_parser()

    record_args = parser.parse_args(
        [
            "thesis-review-record",
            "--journal-db",
            str(db_path),
            "--symbol",
            "AAPL",
            "--thesis-state",
            "healthy",
            "--status",
            "reviewed",
            "--notes",
            "Services thesis remains intact.",
            "--evidence",
            "Services revenue still growing.",
            "--decision-id",
            decision_id,
            "--review-trigger",
            "manual",
            "--current-market-value",
            "4200",
        ]
    )

    record_exit_code = run_cli(record_args)
    record_output = capsys.readouterr().out

    assert record_exit_code == 0
    assert "recorded thesis review" in record_output

    list_args = parser.parse_args(["thesis-review-list", "--journal-db", str(db_path)])
    list_exit_code = run_cli(list_args)
    payload = json.loads(capsys.readouterr().out)

    assert list_exit_code == 0
    assert payload[0]["symbol"] == "AAPL"
    assert payload[0]["thesis_state"] == "healthy"
    assert payload[0]["evidence"] == ["Services revenue still growing."]


def test_capital_alert_cli_outputs_markdown_without_sending(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    journal = LongTermDecisionJournal(db_path)
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 91,
            "suggested_size_pct": 8,
            "key_thesis": "AI infrastructure leader.",
        },
    )
    parser = build_capital_alert_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(db_path),
            "--active-sleeve-value",
            "34000",
            "--available-cash",
            "500",
        ]
    )

    exit_code = run_capital_alert_cli(args)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "# Capital Needed Alert" in output
    assert "NVDA" in output


def test_capital_alert_cli_suppresses_when_existing_holding_should_be_sold_first(tmp_path, capsys):
    db_path = tmp_path / "journal.db"
    journal = LongTermDecisionJournal(db_path)
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "AAPL", "benchmark_symbol": "FXAIX"}),
        decision={"recommendation": "SELL", "confidence": 88, "suggested_size_pct": 0},
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={"recommendation": "BUY", "confidence": 94, "suggested_size_pct": 8},
    )
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 500, "holdings": [{"symbol": "AAPL", "market_value": 3000}]}),
        encoding="utf-8",
    )
    parser = build_capital_alert_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(db_path),
            "--active-sleeve-value",
            "34000",
            "--available-cash",
            "500",
            "--portfolio-state",
            str(portfolio_path),
        ]
    )

    exit_code = run_capital_alert_cli(args)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "No capital-needed alert" in output
    assert "sell/reduce" in output


def test_capital_alert_cli_can_send_with_injected_sender(tmp_path, capsys):
    class FakeSender:
        def __init__(self):
            self.email = None
            self.settings = None

        def send(self, email, settings):
            self.email = email
            self.settings = settings
            return EmailSendResult(True, "fake sent")

    db_path = tmp_path / "journal.db"
    journal = LongTermDecisionJournal(db_path)
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={"recommendation": "BUY", "confidence": 91, "suggested_size_pct": 8},
    )
    config_path = tmp_path / "email_notifications.json"
    config_path.write_text(
        json.dumps(
            {
                "email_notifications": True,
                "email_to": "user@example.com",
                "email_from": "bot@example.com",
                "email_username": "abc123@smtp-brevo.com",
                "email_password": "fake-smtp-password",
                "email_smtp_host": "smtp-relay.brevo.com",
                "email_smtp_port": 587,
            }
        ),
        encoding="utf-8",
    )
    sender = FakeSender()
    parser = build_capital_alert_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(db_path),
            "--active-sleeve-value",
            "34000",
            "--available-cash",
            "500",
            "--email-config",
            str(config_path),
            "--send",
        ]
    )

    exit_code = run_capital_alert_cli(args, sender=sender)

    assert exit_code == 0
    assert "fake sent" in capsys.readouterr().out
    assert sender.email.should_send is True
    assert sender.email.recipient_email == "user@example.com"
    assert sender.settings.enabled is True


def test_motley_fool_capture_cli_outputs_investigation_ideas(capsys):
    def fake_capture(source_key, *, profile_dir=None, url=None):
        return [
            {
                "symbol": "MOGA",
                "company_name": "Moog",
                "idea_source": "motley_fool_dashboard",
                "source_notes": ["Motley Fool candidate."],
            }
        ]

    parser = build_motley_parser()
    args = parser.parse_args(["--source", "dashboard"])

    exit_code = run_motley_cli(args, capture_func=fake_capture)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["symbol"] == "MOGA"
    assert payload[0]["idea_source"] == "motley_fool_dashboard"


def test_motley_fool_settings_missing_config_is_disabled(tmp_path):
    settings = load_motley_fool_capture_settings(tmp_path / "missing.json")

    assert settings.enabled is False
    assert settings.cookie_ready is False
    assert settings.can_capture is False
    assert settings.should_open_login is False


def test_motley_fool_settings_enabled_cookie_ready_can_capture(tmp_path):
    config_path = tmp_path / "motley_fool_capture.json"
    config_path.write_text(
        json.dumps(
            {
                "motley_fool": {
                    "enabled": True,
                    "cookie_ready": True,
                    "profile_dir": "~/.grok3api_chrome_profile",
                    "sources": ["new_recommendations", "quant_rankings"],
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_motley_fool_capture_settings(config_path)

    assert settings.enabled is True
    assert settings.cookie_ready is True
    assert settings.can_capture is True
    assert settings.should_open_login is False
    assert settings.sources == ["new_recommendations", "quant_rankings"]


def test_motley_fool_settings_enabled_without_cookie_requests_login(tmp_path):
    config_path = tmp_path / "motley_fool_capture.json"
    config_path.write_text(
        json.dumps(
            {
                "motley_fool": {
                    "enabled": True,
                    "cookie_ready": False,
                    "open_login_when_cookie_missing": True,
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_motley_fool_capture_settings(config_path)

    assert settings.can_capture is False
    assert settings.should_open_login is True
