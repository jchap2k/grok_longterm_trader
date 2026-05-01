import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.broker_capabilities import evaluate_broker_capability_match
from longterm.paper_account_cleanliness import evaluate_paper_account_cleanliness
from longterm.paper_smoke_readiness import (
    build_paper_smoke_readiness_markdown,
    build_paper_smoke_readiness_report,
)
from longterm.paper_smoke_readiness_cli import build_parser, run_cli
from longterm.portfolio_state import PortfolioState


def test_paper_smoke_readiness_passes_with_clean_account_and_compatible_paper_model():
    cleanliness = evaluate_paper_account_cleanliness(
        PortfolioState(cash=74000, holdings=[], protected_symbols=["FXAIX"]),
        expected_cash=74000,
    )
    broker_capabilities = evaluate_broker_capability_match(
        paper_broker="alpaca_paper",
        live_broker="schwab_api",
        required_order_model="whole_share",
    )

    report = build_paper_smoke_readiness_report(
        account_cleanliness=cleanliness,
        broker_capabilities=broker_capabilities,
        scheduler_readiness={"blocker_count": 0, "warning_count": 1},
    )

    assert report["ready_for_supervised_smoke"] is True
    assert report["blocker_count"] == 0
    assert report["warning_count"] == 2
    assert "Ready for supervised smoke: yes" in build_paper_smoke_readiness_markdown(report)


def test_paper_smoke_readiness_blocks_dirty_account_and_incompatible_broker_model():
    cleanliness = evaluate_paper_account_cleanliness(
        PortfolioState(
            cash=73990,
            holdings=[{"symbol": "NVDA", "market_value": 10, "quantity": 0.05}],
            protected_symbols=["FXAIX"],
        ),
        expected_cash=74000,
    )
    broker_capabilities = evaluate_broker_capability_match(
        paper_broker="alpaca_paper",
        live_broker="schwab_api",
        required_order_model="notional_fractional",
    )

    report = build_paper_smoke_readiness_report(
        account_cleanliness=cleanliness,
        broker_capabilities=broker_capabilities,
        scheduler_readiness={"blocker_count": 0, "warning_count": 1},
    )

    assert report["ready_for_supervised_smoke"] is False
    assert "paper_account_not_clean" in report["blockers"]
    assert "broker_capability_mismatch" in report["blockers"]


def test_paper_smoke_readiness_cli_outputs_json(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 74000, "holdings": [], "protected_symbols": ["FXAIX"]}),
        encoding="utf-8",
    )
    scheduler_path = tmp_path / "scheduler.json"
    scheduler_path.write_text(json.dumps({"blocker_count": 0, "warning_count": 1}), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--expected-cash",
            "74000",
            "--scheduler-readiness",
            str(scheduler_path),
            "--required-order-model",
            "whole_share",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "paper_smoke_readiness"
    assert payload["ready_for_supervised_smoke"] is True
