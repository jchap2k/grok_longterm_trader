import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brokers.base_broker import Order, OrderSide, OrderStatus, OrderType
from brokers import alpaca_broker as alpaca_broker_module
from longterm.paper_order_status_refresh import (
    PaperOrderStatusRefresh,
    build_paper_order_status_refresh_markdown,
)
from longterm.paper_order_status_refresh_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger


class FakeStatusBroker:
    def __init__(self, orders=None, fail_ids=None):
        self.orders = orders or {}
        self.fail_ids = set(fail_ids or [])
        self.calls = []

    def get_order_status(self, order_id):
        self.calls.append(order_id)
        if order_id in self.fail_ids:
            raise TimeoutError("status timeout")
        return self.orders[order_id]


class FakeAlpacaStatus:
    value = "pending_new"


class FakeAlpacaSide:
    value = "buy"


class FakeAlpacaType:
    value = "market"


class FakeAlpacaNotionalOrder:
    id = "order-1"
    symbol = "NVDA"
    side = FakeAlpacaSide()
    type = FakeAlpacaType()
    status = FakeAlpacaStatus()
    qty = None
    limit_price = None
    stop_price = None
    filled_avg_price = None
    filled_qty = None
    created_at = datetime(2026, 5, 1, 15, 30)
    filled_at = None


class FakeAlpacaOrderClient:
    def get_order_by_id(self, order_id):
        return FakeAlpacaNotionalOrder()


def _submitted_event(ledger, *, decision_id="decision-1", broker_order_id="order-1", symbol="NVDA"):
    return ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_log_id": "preview-log-1",
            "preview_id": "preview-1",
            "plan_id": "plan-1",
            "broker_order_id": broker_order_id,
            "symbol": symbol,
            "side": "buy",
            "notional": 1000,
            "status": "submitted",
            "client_order_id": f"client-{broker_order_id}",
            "submission_attempt_id": "attempt-1",
            "paper_mode": True,
            "live_mode": False,
        }
    )


def _order(order_id="order-1", *, status=OrderStatus.FILLED, filled_quantity=3, filled_price=101.5):
    return Order(
        order_id=order_id,
        symbol="NVDA",
        side=OrderSide.BUY,
        quantity=3,
        order_type=OrderType.MARKET,
        status=status,
        filled_price=filled_price,
        filled_quantity=filled_quantity,
        filled_at=datetime(2026, 5, 1, 15, 30),
    )


def test_status_refresh_records_filled_event_for_submitted_order(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _submitted_event(ledger)
    broker = FakeStatusBroker({"order-1": _order()})

    result = PaperOrderStatusRefresh().run(ledger=ledger, broker=broker)
    latest = ledger.list_execution_events(limit=1)[0]

    assert broker.calls == ["order-1"]
    assert result["refreshed_count"] == 1
    assert result["status_counts"]["filled"] == 1
    assert latest["status"] == "filled"
    assert latest["decision_id"] == "decision-1"
    assert latest["event_json"]["paper_mode"] is True
    assert latest["event_json"]["live_mode"] is False
    assert latest["event_json"]["filled_quantity"] == 3
    assert latest["event_json"]["filled_price"] == 101.5


def test_status_refresh_handles_pending_alpaca_notional_order_without_qty(monkeypatch, tmp_path):
    monkeypatch.setattr(alpaca_broker_module, "ALPACA_AVAILABLE", True)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _submitted_event(ledger)
    broker = alpaca_broker_module.AlpacaBroker(
        api_key="paper-key",
        secret_key="paper-secret",
        paper_trading=True,
    )
    broker.connected = True
    broker.trading_client = FakeAlpacaOrderClient()

    result = PaperOrderStatusRefresh().run(ledger=ledger, broker=broker)
    latest = ledger.list_execution_events(limit=1)[0]

    assert result["error_count"] == 0
    assert result["status_counts"]["pending"] == 1
    assert latest["status"] == "pending"
    assert latest["event_json"]["filled_quantity"] == 0.0
    assert latest["event_json"]["filled_price"] is None


def test_status_refresh_is_idempotent_for_unchanged_terminal_status(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _submitted_event(ledger)
    broker = FakeStatusBroker({"order-1": _order()})
    refresh = PaperOrderStatusRefresh()

    first = refresh.run(ledger=ledger, broker=broker)
    second = refresh.run(ledger=ledger, broker=broker)

    assert first["events_recorded"] == 1
    assert second["events_recorded"] == 0
    assert second["skipped_count"] == 1
    assert len([row for row in ledger.list_execution_events(limit=10) if row["status"] == "filled"]) == 1


def test_status_refresh_records_error_event_when_broker_status_lookup_fails(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _submitted_event(ledger)
    broker = FakeStatusBroker(fail_ids={"order-1"})

    result = PaperOrderStatusRefresh().run(ledger=ledger, broker=broker)
    latest = ledger.list_execution_events(limit=1)[0]

    assert result["error_count"] == 1
    assert latest["status"] == "status_refresh_error"
    assert "status timeout" in latest["error"]
    assert latest["event_json"]["broker_order_id"] == "order-1"


def test_status_refresh_cli_outputs_json_and_markdown_with_injected_broker(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _submitted_event(ledger)
    broker = FakeStatusBroker({"order-1": _order(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=1)})
    parser = build_parser()

    json_args = parser.parse_args(["--ledger-db", str(ledger.db_path), "--json"])
    assert run_cli(json_args, broker_factory=lambda: broker) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status_counts"]["partially_filled"] == 1

    markdown = build_paper_order_status_refresh_markdown(payload)
    assert "# Paper Order Status Refresh" in markdown
    assert "partially_filled" in markdown


def test_status_refresh_cli_writes_report_output_with_injected_broker(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _submitted_event(ledger)
    broker = FakeStatusBroker({"order-1": _order()})
    report_path = tmp_path / "paper_order_status_refresh.json"

    args = build_parser().parse_args(
        [
            "--ledger-db",
            str(ledger.db_path),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args, broker_factory=lambda: broker) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert printed["status_counts"]["filled"] == 1
    assert saved["status_counts"]["filled"] == 1


def test_status_refresh_cli_skips_broker_connection_when_no_submitted_orders(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    report_path = tmp_path / "empty_status_refresh.json"

    def raise_if_called():
        raise AssertionError("broker should not be constructed when there are no submitted orders")

    args = build_parser().parse_args(
        [
            "--ledger-db",
            str(ledger.db_path),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args, broker_factory=raise_if_called) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert printed["submitted_order_count"] == 0
    assert printed["refreshed_count"] == 0
    assert saved["submitted_order_count"] == 0
