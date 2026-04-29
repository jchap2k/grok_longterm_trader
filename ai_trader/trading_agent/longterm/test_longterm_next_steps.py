import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_parser import parse_decision_response
from longterm.decision_journal import LongTermDecisionJournal
from longterm.cli import build_parser, create_packet_from_args, run_cli
from longterm.research_runner import LongTermResearchRunner
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def test_portfolio_profile_loads_from_json_file(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "account_strategy_mode": "roth_ira",
                "tradable_capital": 34000,
                "protected_symbols": ["fxaix"],
                "benchmark_symbol": "fxaix",
                "defensive_parking_symbol": "spy",
            }
        ),
        encoding="utf-8",
    )

    profile = PortfolioProfile.from_file(path)

    assert profile.account_strategy_mode == "roth_ira"
    assert profile.protected_symbols == ["FXAIX"]
    assert profile.benchmark_symbol == "FXAIX"
    assert profile.defensive_parking_symbol == "SPY"
    assert profile.tradable_capital == 34000.0


def test_default_roth_profile_config_loads():
    config_path = (
        Path(__file__).resolve().parent
        / "configs"
        / "roth_ira_profile.json"
    )

    profile = PortfolioProfile.from_file(config_path)

    assert profile.account_strategy_mode == "roth_ira"
    assert profile.protected_symbols == ["FXAIX"]
    assert profile.benchmark_symbol == "FXAIX"
    assert profile.defensive_parking_symbol == "SPY"


def test_parse_decision_response_accepts_fenced_json():
    raw = """Here is the decision:

```json
{"recommendation":"BUY","confidence":82,"suggested_size_pct":"6.5"}
```
"""

    parsed = parse_decision_response(raw)

    assert parsed["recommendation"] == "BUY"
    assert parsed["confidence"] == 82
    assert parsed["suggested_size_pct"] == 6.5


def test_decision_journal_summarizes_benchmark_outcomes(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    first = create_research_packet_from_idea({"symbol": "AAPL", "benchmark_symbol": "FXAIX"})
    second = create_research_packet_from_idea({"symbol": "MSFT", "benchmark_symbol": "FXAIX"})
    first_id = journal.record_decision(
        first,
        decision={"recommendation": "BUY", "confidence": 80},
        candidate_price=100,
        benchmark_price=100,
    )
    second_id = journal.record_decision(
        second,
        decision={"recommendation": "BUY", "confidence": 78},
        candidate_price=50,
        benchmark_price=100,
    )
    journal.update_outcome(first_id, candidate_price=120, benchmark_price=110)
    journal.update_outcome(second_id, candidate_price=45, benchmark_price=105)

    summary = journal.summarize_benchmark_performance()

    assert summary["evaluated_decisions"] == 2
    assert summary["average_candidate_return_pct"] == 5.0
    assert summary["average_benchmark_return_pct"] == 7.5
    assert summary["average_excess_return_pct"] == -2.5
    assert summary["decisions_beating_benchmark"] == 1


def test_runner_run_and_record_uses_robust_response_parser(monkeypatch, tmp_path):
    class FakeCheapGrokHeavy:
        def __init__(self, **kwargs):
            pass

        def call_with_context(self, task_prompt, context_sections=None):
            return '```json\n{"recommendation":"PASS","confidence":"61"}\n```'

    monkeypatch.setattr("longterm.research_runner.CheapGrokHeavy", FakeCheapGrokHeavy)

    packet = create_research_packet_from_idea(
        {"symbol": "TSLA", "benchmark_symbol": "FXAIX"}
    )
    runner = LongTermResearchRunner(
        api_key="test-key",
        config_path="dummy-config.json",
        verbose=False,
    )
    decision_id = runner.run_and_record(
        packet,
        journal_db_path=tmp_path / "journal.db",
        candidate_price=250,
        benchmark_price=180,
    )

    row = runner.decision_journal.get_decision(decision_id)

    assert row["recommendation"] == "PASS"
    assert row["confidence"] == 61


def test_cli_dry_run_builds_packet_without_calling_grok(capsys):
    parser = build_parser()
    args = parser.parse_args(
        [
            "--symbol",
            "aapl",
            "--company-name",
            "Apple",
            "--thesis",
            "Services and ecosystem durability.",
            "--business-summary",
            "Consumer technology platform.",
            "--dry-run",
        ]
    )

    exit_code = run_cli(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"symbol": "AAPL"' in captured.out
    assert '"benchmark_symbol": "FXAIX"' in captured.out


def test_create_packet_from_args_uses_profile_config_defaults():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--symbol",
            "nvda",
            "--thesis",
            "AI infrastructure demand.",
        ]
    )

    packet = create_packet_from_args(args)

    assert packet.symbol == "NVDA"
    assert packet.account_strategy_mode == "roth_ira"
    assert packet.protected_symbols == ["FXAIX"]
