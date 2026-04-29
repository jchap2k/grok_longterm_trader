import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.journal_cli import build_parser, run_cli
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
