import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_lifecycle import build_paper_lifecycle_markdown, build_paper_lifecycle_summary
from longterm.paper_lifecycle_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger


def test_paper_lifecycle_summary_combines_preview_execution_and_outcomes(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(
        {
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "previews": [
                {
                    "preview_id": "preview-nvda",
                    "decision_id": "decision-nvda",
                    "symbol": "NVDA",
                    "side": "buy",
                    "order_type": "market_notional_preview",
                    "notional": 1000,
                    "allowed": True,
                }
            ],
        }
    )
    ledger.record_execution_event(
        {
            "decision_id": "decision-nvda",
            "preview_id": "preview-nvda",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_price": 100,
            "benchmark_price_at_fill": 100,
            "paper_mode": True,
            "live_mode": False,
        }
    )

    summary = build_paper_lifecycle_summary(
        ledger,
        price_map={"NVDA": 120, "FXAIX": 110},
    )
    item = summary["items"][0]

    assert item["symbol"] == "NVDA"
    assert item["lifecycle_state"] == "outcome_evaluated"
    assert item["paper_preview_status"] == "ready"
    assert item["paper_execution_latest_status"] == "filled"
    assert item["paper_outcome_status"] == "evaluated"
    assert item["paper_return_pct"] == 20.0
    assert summary["state_counts"]["outcome_evaluated"] == 1
    assert "outcome_evaluated" in build_paper_lifecycle_markdown(summary)


def test_paper_lifecycle_summary_surfaces_blocked_and_rejected_states(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(
        {
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "previews": [
                {
                    "preview_id": "preview-aapl",
                    "decision_id": "decision-aapl",
                    "symbol": "AAPL",
                    "side": "buy",
                    "order_type": "market_notional_preview",
                    "notional": 1000,
                    "allowed": False,
                    "blocked_reasons": ["benchmark guard paused buys"],
                }
            ],
        }
    )
    ledger.record_execution_event(
        {
            "decision_id": "decision-msft",
            "preview_id": "preview-msft",
            "preview_log_id": "preview-log-2",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-2",
            "symbol": "MSFT",
            "side": "buy",
            "notional": 1000,
            "status": "rejected",
            "error": "broker rejected",
            "paper_mode": True,
            "live_mode": False,
        }
    )

    summary = build_paper_lifecycle_summary(ledger)
    states = {item["symbol"]: item["lifecycle_state"] for item in summary["items"]}

    assert states["AAPL"] == "preview_blocked"
    assert states["MSFT"] == "execution_rejected"
    assert summary["state_counts"]["preview_blocked"] == 1
    assert summary["state_counts"]["execution_rejected"] == 1


def test_paper_lifecycle_cli_outputs_json(tmp_path, capsys):
    ledger_path = tmp_path / "paper.db"
    price_map_path = tmp_path / "prices.json"
    ledger = PaperTradeLedger(ledger_path)
    ledger.record_execution_event(
        {
            "decision_id": "decision-nvda",
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_price": 100,
            "benchmark_price_at_fill": 100,
            "paper_mode": True,
            "live_mode": False,
        }
    )
    price_map_path.write_text(json.dumps({"NVDA": 120, "FXAIX": 110}), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(["--ledger-db", str(ledger_path), "--price-map", str(price_map_path), "--json"])

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state_counts"]["outcome_evaluated"] == 1
