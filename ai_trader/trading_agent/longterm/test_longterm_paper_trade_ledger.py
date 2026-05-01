import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_order_preview_cli import build_parser as build_preview_parser
from longterm.paper_order_preview_cli import run_cli as run_preview_cli
from longterm.paper_preview_ledger_cli import build_parser as build_ledger_parser
from longterm.paper_preview_ledger_cli import run_cli as run_ledger_cli
from longterm.paper_trade_ledger import PaperTradeLedger


def _preview_payload():
    return {
        "plan_id": "plan-1",
        "order_submission_enabled": False,
        "previews": [
            {
                "preview_id": "plan-1-001-buy",
                "plan_id": "plan-1",
                "decision_id": "decision-1",
                "transaction_id": "",
                "trade_id": None,
                "symbol": "NVDA",
                "side": "buy",
                "order_type": "market_notional_preview",
                "notional": 1000,
                "allowed": True,
                "reason": "Preview passed.",
                "blocked_reasons": [],
            },
            {
                "preview_id": "plan-1-002-none",
                "plan_id": "plan-1",
                "decision_id": "decision-2",
                "transaction_id": "",
                "trade_id": None,
                "symbol": "AAPL",
                "side": "none",
                "order_type": "no_order",
                "notional": 0,
                "allowed": True,
                "reason": "Review due.",
                "blocked_reasons": [],
            },
        ],
    }


def test_paper_trade_ledger_records_preview_rows_with_decision_traceability(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")

    preview_log_id = ledger.record_preview(_preview_payload())
    rows = ledger.list_previews(limit=10)
    by_decision = ledger.preview_status_by_decision()

    assert preview_log_id
    assert len(rows) == 2
    assert rows[0]["preview_log_id"] == preview_log_id
    assert rows[0]["decision_id"] == "decision-1"
    assert rows[0]["status"] == "ready"
    assert by_decision["decision-1"]["ready_count"] == 1
    assert by_decision["decision-2"]["no_order_count"] == 1


def test_paper_trade_ledger_summary_counts_ready_blocked_and_no_order(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    payload = _preview_payload()
    payload["previews"][0]["allowed"] = False
    payload["previews"][0]["blocked_reasons"] = ["cash shortfall"]
    ledger.record_preview(payload)

    summary = ledger.summarize_previews()

    assert summary["total_rows"] == 2
    assert summary["status_counts"]["blocked"] == 1
    assert summary["status_counts"]["no_order"] == 1
    assert summary["order_submission_enabled"] is False


def test_paper_order_preview_cli_can_record_preview_to_ledger(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "paper.db"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"], "holdings": []}), encoding="utf-8")
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "plan-cli",
                "intents": [
                    {
                        "symbol": "NVDA",
                        "intent_type": "BUY",
                        "order_intent": "BUY",
                        "trade_value": 1000,
                        "allowed": True,
                        "decision_id": "decision-nvda",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    parser = build_preview_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--record-preview",
            "--ledger-db",
            str(ledger_path),
            "--json",
        ]
    )

    assert run_preview_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = PaperTradeLedger(ledger_path).list_previews()

    assert payload["preview_log_id"]
    assert rows[0]["decision_id"] == "decision-nvda"


def test_paper_preview_ledger_cli_lists_and_summarizes(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(_preview_payload())
    parser = build_ledger_parser()

    list_args = parser.parse_args(["list", "--ledger-db", str(ledger.db_path)])
    assert run_ledger_cli(list_args) == 0
    assert json.loads(capsys.readouterr().out)[0]["decision_id"] == "decision-1"

    summary_args = parser.parse_args(["summary", "--ledger-db", str(ledger.db_path)])
    assert run_ledger_cli(summary_args) == 0
    assert "# Paper Preview Ledger Summary" in capsys.readouterr().out
