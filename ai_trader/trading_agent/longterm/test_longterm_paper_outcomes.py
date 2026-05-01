import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_outcomes import build_paper_outcome_markdown, summarize_paper_outcomes
from longterm.paper_outcomes_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger


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
