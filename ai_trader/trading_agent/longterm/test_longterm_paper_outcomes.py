import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_outcomes import build_paper_outcome_markdown, summarize_paper_outcomes
from longterm.paper_outcomes_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.decision_journal import LongTermDecisionJournal
from research.intake import create_research_packet_from_idea


def _filled_event(ledger, *, symbol="NVDA", fill_price=100.0, status="filled"):
    ledger.record_execution_event(
        {
            "decision_id": f"decision-{symbol.lower()}",
            "preview_id": f"preview-{symbol.lower()}",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": f"order-{symbol.lower()}",
            "symbol": symbol,
            "side": "buy",
            "notional": 1000,
            "status": status,
            "filled_quantity": 10,
            "filled_price": fill_price,
            "benchmark_symbol": "FXAIX",
            "benchmark_price_at_fill": 50.0,
            "paper_mode": True,
            "live_mode": False,
        }
    )


def _filled_event_without_benchmark(ledger, *, decision_id, symbol="NVDA", fill_price=100.0):
    ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_id": f"preview-{symbol.lower()}",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": f"order-{symbol.lower()}",
            "symbol": symbol,
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_quantity": 10,
            "filled_price": fill_price,
            "paper_mode": True,
            "live_mode": False,
        }
    )


def _record_decision(journal, *, symbol="NVDA", benchmark_price=50.0):
    packet = create_research_packet_from_idea(
        {
            "symbol": symbol,
            "company_name": f"{symbol} Inc.",
            "idea_source": "unit_test",
            "business_summary": "Test company.",
            "thesis_summary": "Test thesis.",
            "benchmark_symbol": "FXAIX",
        }
    )
    return journal.record_decision(
        packet,
        decision={"recommendation": "BUY", "confidence": 80, "suggested_size_pct": 1.0},
        candidate_price=100.0,
        benchmark_price=benchmark_price,
    )


def test_paper_outcomes_compare_filled_positions_against_benchmark(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event(ledger, symbol="NVDA", fill_price=100.0)

    result = summarize_paper_outcomes(
        ledger,
        price_map={"NVDA": {"current_price": 120.0}, "FXAIX": {"current_price": 55.0}},
    )

    assert result["evaluated_fills"] == 1
    assert result["items"][0]["symbol"] == "NVDA"
    assert result["items"][0]["paper_return_pct"] == 20.0
    assert result["items"][0]["benchmark_return_pct"] == 10.0
    assert result["items"][0]["excess_return_pct"] == 10.0
    assert result["average_excess_return_pct"] == 10.0
    assert "Paper Outcome Summary" in build_paper_outcome_markdown(result)


def test_paper_outcomes_can_use_journal_benchmark_price_proxy(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal, symbol="NVDA", benchmark_price=50.0)
    _filled_event_without_benchmark(ledger, decision_id=decision_id, symbol="NVDA", fill_price=100.0)

    result = summarize_paper_outcomes(
        ledger,
        price_map={"NVDA": {"current_price": 120.0}, "FXAIX": {"current_price": 55.0}},
        journal=journal,
    )

    item = result["items"][0]
    assert result["evaluated_fills"] == 1
    assert result["proxy_benchmark_count"] == 1
    assert result["benchmark_source_counts"]["decision_journal_proxy"] == 1
    assert item["benchmark_price_at_fill"] == 50.0
    assert item["benchmark_price_source"] == "decision_journal_proxy"
    assert item["benchmark_symbol"] == "FXAIX"
    assert item["paper_return_pct"] == 20.0
    assert item["benchmark_return_pct"] == 10.0
    assert item["excess_return_pct"] == 10.0
    markdown = build_paper_outcome_markdown(result)
    assert "decision_journal_proxy" in markdown
    assert decision_id[:8] in markdown


def test_paper_outcomes_counts_unlinked_fills_without_benchmark(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event_without_benchmark(ledger, decision_id="missing-decision", symbol="NVDA", fill_price=100.0)

    result = summarize_paper_outcomes(
        ledger,
        price_map={"NVDA": {"current_price": 120.0}, "FXAIX": {"current_price": 55.0}},
    )

    assert result["evaluated_fills"] == 0
    assert result["pending_count"] == 1
    assert result["benchmark_source_counts"]["missing"] == 1
    assert result["unlinked_count"] == 1
    assert result["items"][0]["missing_reasons"] == ["missing_benchmark_price_at_fill"]


def test_paper_outcomes_marks_missing_prices_as_pending(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event(ledger, symbol="NVDA", fill_price=100.0)

    result = summarize_paper_outcomes(ledger, price_map={})

    assert result["evaluated_fills"] == 0
    assert result["pending_count"] == 1
    assert result["items"][0]["status"] == "pending_price"


def test_paper_outcomes_cli_reads_price_map(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event(ledger, symbol="NVDA", fill_price=100.0)
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps({"NVDA": {"current_price": 130.0}, "FXAIX": {"current_price": 52.5}}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(["--ledger-db", str(ledger.db_path), "--price-map", str(prices), "--json"])

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["paper_return_pct"] == 30.0
    assert payload["items"][0]["benchmark_return_pct"] == 5.0


def test_paper_outcomes_cli_can_use_journal_proxy(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal, symbol="NVDA", benchmark_price=50.0)
    _filled_event_without_benchmark(ledger, decision_id=decision_id, symbol="NVDA", fill_price=100.0)
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps({"NVDA": {"current_price": 120.0}, "FXAIX": {"current_price": 55.0}}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--ledger-db",
            str(ledger.db_path),
            "--journal-db",
            str(journal.db_path),
            "--price-map",
            str(prices),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["evaluated_fills"] == 1
    assert payload["proxy_benchmark_count"] == 1
    assert payload["items"][0]["benchmark_price_source"] == "decision_journal_proxy"
