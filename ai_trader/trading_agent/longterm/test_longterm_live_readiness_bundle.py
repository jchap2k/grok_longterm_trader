import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.live_readiness_bundle import (
    build_live_readiness_bundle,
    build_live_readiness_bundle_markdown,
)
from longterm.live_readiness_bundle_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger


def _filled_event(ledger):
    ledger.record_execution_event(
        {
            "decision_id": "decision-1",
            "preview_log_id": "preview-log-1",
            "preview_id": "preview-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": "NVDA",
            "side": "buy",
            "notional": 10,
            "status": "filled",
            "paper_mode": True,
            "live_mode": False,
            "filled_quantity": 0.05,
            "filled_price": 200,
        }
    )


def _base_observed():
    return {
        "dry_run_cycles": 30,
        "benchmark_proven": True,
        "protected_symbol_enforced": True,
        "manual_approval": True,
        "kill_switch": True,
        "audit_logs": True,
        "broker_read_reconciliation": True,
        "explicit_live_mode_config": True,
        "secrets_not_committed": True,
    }


def test_live_readiness_bundle_combines_base_broker_and_paper_evidence(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event(ledger)

    bundle = build_live_readiness_bundle(
        base_observed=_base_observed(),
        paper_ledger=ledger,
        required_order_model="whole_share",
    )

    assert bundle["ready"] is True
    assert bundle["observed"]["paper_trading_verified"] is True
    assert bundle["observed"]["broker_capability_match"] is True
    assert bundle["paper_trading_verification"]["paper_trading_verified"] is True
    assert "Ready for live trading: yes" in build_live_readiness_bundle_markdown(bundle)


def test_live_readiness_bundle_keeps_notional_fractional_schwab_mismatch_unmet(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event(ledger)

    bundle = build_live_readiness_bundle(
        base_observed=_base_observed(),
        paper_ledger=ledger,
        required_order_model="notional_fractional",
    )

    assert bundle["ready"] is False
    assert "broker_capability_match" in bundle["unmet_gate_keys"]
    assert bundle["broker_capabilities"]["compatible"] is False


def test_live_readiness_bundle_cli_outputs_json(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _filled_event(ledger)
    base_path = tmp_path / "base.json"
    base_path.write_text(json.dumps(_base_observed()), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--observed-file",
            str(base_path),
            "--paper-ledger-db",
            str(ledger.db_path),
            "--required-order-model",
            "whole_share",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "live_readiness_bundle"
    assert payload["ready"] is True
