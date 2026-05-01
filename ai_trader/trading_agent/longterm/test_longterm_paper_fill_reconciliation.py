import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_reconciliation import (
    build_paper_reconciliation_markdown,
    reconcile_paper_account,
)
from longterm.paper_reconciliation_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState


def _event(ledger, *, symbol="NVDA", status="filled", notional=1000):
    ledger.record_execution_event(
        {
            "decision_id": f"decision-{symbol.lower()}",
            "preview_id": f"preview-{symbol.lower()}",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": f"order-{symbol.lower()}",
            "symbol": symbol,
            "side": "buy",
            "notional": notional,
            "status": status,
            "filled_quantity": 3 if status in {"filled", "partially_filled"} else 0,
            "filled_price": 101.5 if status in {"filled", "partially_filled"} else None,
            "paper_mode": True,
            "live_mode": False,
        }
    )


def test_reconciliation_uses_filled_execution_events_as_expected_positions(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _event(ledger, symbol="NVDA", status="filled", notional=1000)
    _event(ledger, symbol="MSFT", status="filled", notional=1500)
    actual = PortfolioState(
        cash=500,
        protected_symbols=["FXAIX"],
        holdings=[{"symbol": "NVDA", "market_value": 980, "quantity": 3}],
    )

    report = reconcile_paper_account(actual, paper_ledger=ledger)

    assert report["filled_execution_count"] == 2
    assert report["missing_filled_symbols"] == ["MSFT"]
    assert report["paper_fill_reconciliation"][0]["symbol"] == "NVDA"
    assert report["paper_fill_reconciliation"][0]["status"] == "present"
    assert report["paper_fill_reconciliation"][0]["broker_order_id"] == "order-nvda"


def test_reconciliation_flags_rejected_execution_if_holding_exists(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _event(ledger, symbol="PLTR", status="rejected", notional=1000)
    actual = PortfolioState(
        cash=500,
        protected_symbols=["FXAIX"],
        holdings=[{"symbol": "PLTR", "market_value": 1000, "quantity": 10}],
    )

    report = reconcile_paper_account(actual, paper_ledger=ledger)

    assert report["unexpected_rejected_fill_symbols"] == ["PLTR"]
    assert report["paper_fill_reconciliation"][0]["status"] == "unexpected_holding_after_rejected_order"


def test_reconciliation_cli_accepts_paper_ledger_db(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _event(ledger, symbol="NVDA", status="filled", notional=1000)
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "cash": 500,
                "protected_symbols": ["FXAIX"],
                "holdings": [{"symbol": "NVDA", "market_value": 1000, "quantity": 3}],
            }
        ),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--paper-ledger-db",
            str(ledger.db_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filled_execution_count"] == 1
    assert "Paper Fill Reconciliation" in build_paper_reconciliation_markdown(payload)
