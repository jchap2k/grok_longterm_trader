import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.broker_capabilities import (
    build_broker_capability_markdown,
    evaluate_broker_capability_match,
)
from longterm.broker_capabilities_cli import build_parser, run_cli


def test_capability_report_blocks_alpaca_fractional_paper_to_schwab_api_live():
    report = evaluate_broker_capability_match(
        paper_broker="alpaca_paper",
        live_broker="schwab_api",
        required_order_model="notional_fractional",
    )

    assert report["compatible"] is False
    assert report["live_readiness_observed"] == {"broker_capability_match": False}
    assert "live_broker_lacks_fractional_shares" in report["blockers"]
    assert "live_broker_lacks_notional_orders" in report["blockers"]
    assert report["live_broker"]["supports_fractional_shares"] is False
    assert "Schwab" in build_broker_capability_markdown(report)


def test_capability_report_allows_whole_share_model_on_schwab_api():
    report = evaluate_broker_capability_match(
        paper_broker="alpaca_paper",
        live_broker="schwab_api",
        required_order_model="whole_share",
    )

    assert report["compatible"] is True
    assert report["blockers"] == []
    assert report["warnings"]


def test_broker_capability_cli_outputs_json(tmp_path, capsys):
    observed_path = tmp_path / "observed.json"
    parser = build_parser()
    args = parser.parse_args(
        [
            "--paper-broker",
            "alpaca_paper",
            "--live-broker",
            "schwab_api",
            "--required-order-model",
            "notional_fractional",
            "--observed-output",
            str(observed_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    observed = json.loads(observed_path.read_text(encoding="utf-8"))

    assert payload["compatible"] is False
    assert observed == {"broker_capability_match": False}
