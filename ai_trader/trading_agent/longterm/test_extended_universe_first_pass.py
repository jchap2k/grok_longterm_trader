import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.extended_universe_first_pass_cli import build_parser, run_cli


def _metrics(symbol: str, *, strong: bool = True) -> dict:
    if strong:
        growth = ("22.00%", "24.00%", "18.00%", "17.00%")
        valuation = ("24.0x", "17.0x", "22.0x", "1.3x")
        profitability = ("62.00%", "28.00%", "20.00%", "31.00%", "0.2x")
    else:
        growth = ("1.00%", "-8.00%", "-12.00%", "-10.00%")
        valuation = ("95.0x", "70.0x", "85.0x", "7.0x")
        profitability = ("18.00%", "3.00%", "-2.00%", "2.00%", "1.7x")
    return {
        "symbol": symbol,
        "source_type": "python_fundamental_metrics",
        "revenue_growth_cagr": {
            "3_yr_revenue_growth": growth[0],
            "3_yr_ebitda_growth": growth[1],
            "3_yr_eps_growth": growth[2],
            "3_yr_fcf_per_share_growth": growth[3],
        },
        "valuation_ttm": {
            "price_earnings": valuation[0],
            "ev_ebitda": valuation[1],
            "price_free_cash_flow": valuation[2],
            "price_book_value": "8.0x",
            "price_earnings_growth_5yr": valuation[3],
        },
        "profitability_ttm": {
            "gross_margin": profitability[0],
            "operating_margin": profitability[1],
            "free_cash_flow_margin": profitability[2],
            "return_on_equity": profitability[3],
            "debt_equity": profitability[4],
        },
        "financials_ttm": {"total_cash": "$20.00B", "total_debt": "$10.00B"},
        "warnings": [],
    }


def test_extended_universe_first_pass_cli_prepares_scans_and_writes_artifacts(tmp_path, monkeypatch, capsys):
    import longterm.extended_universe_first_pass_cli as cli

    output_dir = tmp_path / "first_pass"

    def fake_loader(url, *, source):
        assert url == "https://example.test/nasdaqlisted.txt"
        assert source == "nasdaq_listed"
        return [
            {"symbol": "TOP1", "company_name": "Top One", "source": source},
            {"symbol": "WEAK1", "company_name": "Weak One", "source": source},
            {"symbol": "MID1", "company_name": "Middle One", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)

    def fake_fetch(symbol: str) -> dict:
        return _metrics(symbol, strong=symbol != "WEAK1")

    code = run_cli(
        build_parser().parse_args(
            [
                "--source-url",
                "https://example.test/nasdaqlisted.txt",
                "--source",
                "nasdaq_listed",
                "--watchlist-limit",
                "3",
                "--batch-size",
                "2",
                "--provider",
                "yfinance",
                "--fetch-limit",
                "3",
                "--top-percent",
                "67",
                "--output-dir",
                str(output_dir),
            ]
        ),
        fetch_metrics=fake_fetch,
    )

    printed = json.loads(capsys.readouterr().out)
    ideas = json.loads((output_dir / "extended_watchlist_ideas.json").read_text(encoding="utf-8"))
    passed = json.loads((output_dir / "python_scan_passed.json").read_text(encoding="utf-8"))
    report = (output_dir / "python_scan_report.md").read_text(encoding="utf-8")
    assert code == 0
    assert printed["mode"] == "extended_universe_first_pass"
    assert printed["prepare"]["watchlist_ideas_count"] == 3
    assert printed["scan"]["passed_count"] == 3
    assert [idea["symbol"] for idea in ideas] == ["TOP1", "WEAK1", "MID1"]
    assert [idea["symbol"] for idea in passed] == ["TOP1", "MID1", "WEAK1"]
    assert "# Extended Universe Python First Pass" in report
    assert printed["artifacts"]["markdown_report"].endswith("python_scan_report.md")
