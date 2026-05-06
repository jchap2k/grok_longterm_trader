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


def test_paper_smoke_readiness_uses_workflow_smoke_when_provided():
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
        scheduler_readiness={"blocker_count": 0, "warning_count": 0},
        workflow_smoke={"ready_for_supervised_submit": False, "blockers": ["preview_blocked_rows"]},
    )

    assert report["ready_for_supervised_smoke"] is False
    assert "workflow_smoke_not_ready" in report["blockers"]
    assert report["workflow_smoke"]["blockers"] == ["preview_blocked_rows"]


def test_paper_smoke_readiness_surfaces_promotion_blockers_from_workflow():
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
        scheduler_readiness={"blocker_count": 0, "warning_count": 0},
        workflow_smoke={
            "ready_for_supervised_submit": False,
            "blockers": ["buy_promotion_blocked_rows"],
            "promotion_summary": {"blocked_count": 1, "missing_count": 0, "non_actionable_count": 1},
        },
    )

    assert report["ready_for_supervised_smoke"] is False
    assert "workflow_buy_promotion_blockers" in report["blockers"]
    assert report["workflow_promotion_summary"]["non_actionable_count"] == 1


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


def test_paper_smoke_readiness_can_allow_existing_paper_positions_for_ongoing_portfolio():
    cleanliness = evaluate_paper_account_cleanliness(
        PortfolioState(
            cash=67641.28,
            holdings=[{"symbol": "ADBE", "market_value": 755, "quantity": 3}],
            protected_symbols=["FXAIX"],
        ),
        expected_cash=67641.28,
    )
    broker_capabilities = evaluate_broker_capability_match(
        paper_broker="alpaca_paper",
        live_broker="schwab_api",
        required_order_model="whole_share",
    )

    report = build_paper_smoke_readiness_report(
        account_cleanliness=cleanliness,
        broker_capabilities=broker_capabilities,
        scheduler_readiness={"blocker_count": 0, "warning_count": 0},
        workflow_smoke={"ready_for_supervised_submit": True, "promotion_summary": {}},
        allow_existing_positions=True,
    )

    assert report["ready_for_supervised_smoke"] is True
    assert "paper_account_not_clean" not in report["blockers"]
    assert report["allow_existing_positions"] is True


def test_paper_smoke_readiness_still_blocks_cash_mismatch_when_existing_positions_allowed():
    cleanliness = evaluate_paper_account_cleanliness(
        PortfolioState(
            cash=67000,
            holdings=[{"symbol": "ADBE", "market_value": 755, "quantity": 3}],
            protected_symbols=["FXAIX"],
        ),
        expected_cash=67641.28,
    )
    broker_capabilities = evaluate_broker_capability_match(
        paper_broker="alpaca_paper",
        live_broker="schwab_api",
        required_order_model="whole_share",
    )

    report = build_paper_smoke_readiness_report(
        account_cleanliness=cleanliness,
        broker_capabilities=broker_capabilities,
        scheduler_readiness={"blocker_count": 0, "warning_count": 0},
        workflow_smoke={"ready_for_supervised_submit": True, "promotion_summary": {}},
        allow_existing_positions=True,
    )

    assert report["ready_for_supervised_smoke"] is False
    assert "paper_account_cash_mismatch" in report["blockers"]


def test_paper_smoke_readiness_cli_outputs_json(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 74000, "holdings": [], "protected_symbols": ["FXAIX"]}),
        encoding="utf-8",
    )
    scheduler_path = tmp_path / "scheduler.json"
    scheduler_path.write_text(json.dumps({"blocker_count": 0, "warning_count": 1}), encoding="utf-8")
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps({"ready_for_supervised_submit": True, "blockers": []}), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--expected-cash",
            "74000",
            "--scheduler-readiness",
            str(scheduler_path),
            "--workflow-smoke",
            str(workflow_path),
            "--required-order-model",
            "whole_share",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "paper_smoke_readiness"
    assert payload["ready_for_supervised_smoke"] is True
    assert payload["workflow_smoke"]["ready_for_supervised_submit"] is True


def test_paper_smoke_readiness_cli_allows_existing_positions_when_explicit(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "cash": 67641.28,
                "holdings": [{"symbol": "ADBE", "market_value": 755, "quantity": 3}],
                "protected_symbols": ["FXAIX"],
            }
        ),
        encoding="utf-8",
    )
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps({"ready_for_supervised_submit": True, "blockers": []}), encoding="utf-8")

    args = build_parser().parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--expected-cash",
            "67641.28",
            "--workflow-smoke",
            str(workflow_path),
            "--required-order-model",
            "whole_share",
            "--allow-existing-paper-positions",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allow_existing_positions"] is True
    assert "paper_account_not_clean" not in payload["blockers"]


def test_paper_smoke_readiness_cli_writes_report_output(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    report_path = tmp_path / "paper_smoke_readiness.json"
    portfolio_path.write_text(
        json.dumps({"cash": 74000, "holdings": [], "protected_symbols": ["FXAIX"]}),
        encoding="utf-8",
    )

    args = build_parser().parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--expected-cash",
            "74000",
            "--required-order-model",
            "whole_share",
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert stdout_payload["ready_for_supervised_smoke"] is True
    assert file_payload["ready_for_supervised_smoke"] is True
