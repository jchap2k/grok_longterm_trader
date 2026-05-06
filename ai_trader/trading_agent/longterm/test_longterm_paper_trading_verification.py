import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.paper_trading_verification import (
    build_paper_trading_verification_markdown,
    build_paper_trading_verification_report,
)
from longterm.paper_trading_verification_cli import build_parser, run_cli


def _event(ledger, *, status="filled", symbol="NVDA", decision_id="decision-1"):
    ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_log_id": "preview-log-1",
            "preview_id": "preview-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": symbol,
            "side": "buy",
            "notional": 10,
            "status": status,
            "paper_mode": True,
            "live_mode": False,
            "filled_quantity": 0.05 if status == "filled" else 0,
            "filled_price": 200 if status == "filled" else None,
        }
    )


def test_paper_trading_verification_passes_after_filled_paper_execution(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _event(ledger, status="filled")

    report = build_paper_trading_verification_report(ledger)

    assert report["paper_trading_verified"] is True
    assert report["live_readiness_observed"] == {"paper_trading_verified": True}
    assert report["filled_symbol_count"] == 1
    assert "Paper trading verified: yes" in build_paper_trading_verification_markdown(report)


def test_paper_trading_verification_requires_filled_execution_without_current_errors(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _event(ledger, status="rejected")
    _event(ledger, status="status_refresh_error", symbol="MSFT", decision_id="decision-2")

    report = build_paper_trading_verification_report(ledger)

    assert report["paper_trading_verified"] is False
    assert report["live_readiness_observed"] == {"paper_trading_verified": False}
    assert "no_filled_paper_execution" in report["blockers"]
    assert "current_status_error_present" in report["blockers"]


def test_paper_trading_verification_cli_writes_observed_fragment(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    observed_path = tmp_path / "observed.json"
    _event(ledger, status="filled")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--ledger-db",
            str(ledger.db_path),
            "--observed-output",
            str(observed_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    observed = json.loads(observed_path.read_text(encoding="utf-8"))

    assert payload["paper_trading_verified"] is True
    assert observed == {"paper_trading_verified": True}


def test_paper_trading_verification_cli_writes_long_observed_output_path(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _event(ledger, status="filled")
    observed_dir = tmp_path
    while len(str(observed_dir)) < 225:
        observed_dir = observed_dir / "scheduler_prerun_snapshot_segment"
    observed_path = observed_dir / f"paper_trading_observed_{'x' * 48}.json"
    assert len(str(observed_dir)) < 260
    assert len(str(observed_path)) > 260
    args = build_parser().parse_args(
        [
            "--ledger-db",
            str(ledger.db_path),
            "--observed-output",
            str(observed_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    observed = json.loads(_read_text(observed_path))

    assert payload["paper_trading_verified"] is True
    assert observed == {"paper_trading_verified": True}


def _read_text(path):
    path = Path(path)
    if sys.platform == "win32":
        return Path("\\\\?\\" + str(path.resolve())).read_text(encoding="utf-8")
    return path.read_text(encoding="utf-8")
