import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_account_cleanliness import (
    build_paper_account_cleanliness_markdown,
    evaluate_paper_account_cleanliness,
)
from longterm.paper_account_cleanliness_cli import build_parser, run_cli
from longterm.portfolio_state import PortfolioState


def test_cleanliness_report_passes_empty_paper_account_with_cash_tolerance():
    state = PortfolioState(cash=73999.89, holdings=[], protected_symbols=["FXAIX"])

    report = evaluate_paper_account_cleanliness(
        state,
        expected_cash=74000.0,
        cash_tolerance=1.0,
    )

    assert report["clean"] is True
    assert report["unexpected_symbols"] == []
    assert report["cash_within_tolerance"] is True
    assert report["cash_delta"] == -0.11
    assert "Clean: yes" in build_paper_account_cleanliness_markdown(report)


def test_cleanliness_report_flags_unexpected_non_protected_holdings():
    state = PortfolioState(
        cash=73990.0,
        holdings=[{"symbol": "NVDA", "market_value": 9.93, "quantity": 0.050106727}],
        protected_symbols=["FXAIX"],
    )

    report = evaluate_paper_account_cleanliness(state, expected_cash=74000.0)

    assert report["clean"] is False
    assert report["unexpected_symbols"] == ["NVDA"]
    assert report["unexpected_holdings"][0]["quantity"] == 0.050106727
    assert report["cash_within_tolerance"] is False


def test_paper_account_cleanliness_cli_outputs_json(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 74000, "protected_symbols": ["FXAIX"], "holdings": []}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--expected-cash",
            "74000",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["clean"] is True
    assert payload["mode"] == "paper_account_cleanliness"
