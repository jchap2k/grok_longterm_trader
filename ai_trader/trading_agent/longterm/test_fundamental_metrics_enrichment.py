import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.fundamental_metrics_enrichment import (
    enrich_idea_with_fundamental_metrics,
    enrich_ideas_with_fundamental_metrics,
    fetch_yfinance_fundamental_metrics,
    format_compact_value,
    normalize_fundamental_metrics,
)
from longterm.fundamental_metrics_enrichment_cli import build_parser, run_cli


def _raw_metrics(symbol: str = "TSLA") -> dict:
    return {
        "symbol": symbol,
        "as_of_date": "2026-05-02",
        "currency": "USD",
        "price": 300.0,
        "market_cap": 1_000_000_000_000,
        "enterprise_value": 1_050_000_000_000,
        "shares_outstanding": 3_200_000_000,
        "earnings_growth_5y_pct": 20.0,
        "annual": [
            {
                "fiscal_year": 2022,
                "revenue": 100_000_000_000,
                "operating_income": 12_000_000_000,
                "eps": 3.0,
                "ebitda": 16_000_000_000,
                "free_cash_flow": 6_000_000_000,
                "shares_outstanding": 3_000_000_000,
            },
            {
                "fiscal_year": 2023,
                "revenue": 105_000_000_000,
                "operating_income": 11_000_000_000,
                "eps": 2.7,
                "ebitda": 15_000_000_000,
                "free_cash_flow": 5_500_000_000,
                "shares_outstanding": 3_050_000_000,
            },
            {
                "fiscal_year": 2024,
                "revenue": 108_000_000_000,
                "operating_income": 10_500_000_000,
                "eps": 2.3,
                "ebitda": 14_500_000_000,
                "free_cash_flow": 5_000_000_000,
                "shares_outstanding": 3_100_000_000,
            },
            {
                "fiscal_year": 2025,
                "revenue": 115_762_500_000,
                "operating_income": 9_000_000_000,
                "eps": 1.5,
                "ebitda": 12_000_000_000,
                "free_cash_flow": 4_500_000_000,
                "shares_outstanding": 3_200_000_000,
            },
        ],
        "ttm": {
            "revenue": 120_000_000_000,
            "gross_profit": 24_000_000_000,
            "operating_income": 6_000_000_000,
            "ebitda": 10_500_000_000,
            "net_income": 3_900_000_000,
            "capital_expenditure": -9_500_000_000,
            "free_cash_flow": 7_000_000_000,
            "total_debt": 9_200_000_000,
            "total_equity": 84_700_000_000,
            "total_cash": 44_700_000_000,
        },
        "previous_ttm": {
            "revenue": 117_360_000_000,
            "ebitda": 14_000_000_000,
            "net_income": 6_300_000_000,
            "capital_expenditure": -9_025_000_000,
            "free_cash_flow": 6_780_000_000,
            "total_debt": 8_765_000_000,
            "total_equity": 72_800_000_000,
            "total_cash": 37_340_000_000,
        },
    }


def test_format_compact_value_uses_billions_and_millions():
    assert format_compact_value(97_880_000_000) == "$97.88B"
    assert format_compact_value(941_000_000) == "$941.00M"
    assert format_compact_value(-9_530_000_000) == "-$9.53B"


def test_normalize_fundamental_metrics_builds_fool_like_sections():
    metrics = normalize_fundamental_metrics(_raw_metrics())

    assert metrics["symbol"] == "TSLA"
    assert metrics["source_type"] == "python_fundamental_metrics"
    assert metrics["revenue_growth_cagr"]["3_yr_revenue_growth"] == "5.00%"
    assert metrics["revenue_growth_cagr"]["3_yr_eps_growth"] == "-20.63%"
    assert metrics["valuation_ttm"]["price_earnings"] == "256.4x"
    assert metrics["valuation_ttm"]["ev_ebitda"] == "100.0x"
    assert metrics["valuation_ttm"]["price_free_cash_flow"] == "142.9x"
    assert metrics["valuation_ttm"]["price_book_value"] == "11.8x"
    assert metrics["valuation_ttm"]["price_earnings_growth_5yr"] == "12.8x"
    assert metrics["profitability_ttm"]["gross_margin"] == "20.00%"
    assert metrics["profitability_ttm"]["operating_margin"] == "5.00%"
    assert metrics["profitability_ttm"]["free_cash_flow_margin"] == "5.83%"
    assert metrics["profitability_ttm"]["return_on_equity"] == "4.60%"
    assert metrics["profitability_ttm"]["debt_equity"] == "0.1x"
    assert metrics["financials_ttm"]["revenue"] == "$120.00B (+2.25%)"
    assert metrics["financials_ttm"]["ebitda"] == "$10.50B (-25.00%)"
    assert metrics["warnings"] == []


def test_enrich_idea_with_fundamental_metrics_updates_packet_fields_and_notes():
    idea = {"symbol": "TSLA", "company_name": "Tesla", "source_notes": ["Existing note."]}

    enriched = enrich_idea_with_fundamental_metrics(idea, _raw_metrics())

    assert enriched["fundamental_metrics"]["valuation_ttm"]["price_earnings"] == "256.4x"
    assert enriched["valuation_score"] == 20.0
    assert enriched["quality_score"] == 40.0
    assert "Total Debt: $9.20B" in enriched["balance_sheet_assessment"]
    assert any("Python fundamental metrics" in note for note in enriched["source_notes"])


def test_enrich_ideas_with_fundamental_metrics_uses_symbol_keyed_cache():
    enriched = enrich_ideas_with_fundamental_metrics(
        [{"symbol": "tsla", "company_name": "Tesla"}],
        {"TSLA": _raw_metrics()},
    )

    assert enriched[0]["symbol"] == "TSLA"
    assert enriched[0]["fundamental_metrics"]["symbol"] == "TSLA"


def test_fundamental_metrics_cli_enriches_from_snapshot_file(tmp_path, capsys):
    ideas = tmp_path / "ideas.json"
    snapshots = tmp_path / "fundamentals.json"
    output = tmp_path / "enriched.json"
    ideas.write_text(json.dumps([{"symbol": "TSLA", "company_name": "Tesla"}]), encoding="utf-8")
    snapshots.write_text(json.dumps({"TSLA": _raw_metrics()}), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--snapshot-file",
                str(snapshots),
                "--output",
                str(output),
            ]
        )
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["fundamental_metrics"]["profitability_ttm"]["gross_margin"] == "20.00%"
    assert summary["mode"] == "snapshot_file"
    assert summary["enriched_count"] == 1


def test_fundamental_metrics_cli_can_fetch_with_injected_provider(tmp_path, capsys):
    ideas = tmp_path / "ideas.json"
    output = tmp_path / "enriched.json"
    ideas.write_text(json.dumps([{"symbol": "TSLA", "company_name": "Tesla"}]), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--provider",
                "yfinance",
                "--output",
                str(output),
            ]
        ),
        fetch_metrics=lambda symbol: _raw_metrics(symbol),
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["fundamental_metrics"]["valuation_ttm"]["ev_ebitda"] == "100.0x"
    assert summary["mode"] == "yfinance"
    assert summary["snapshot_count"] == 1


def test_fetch_yfinance_fundamental_metrics_uses_injected_ticker_factory():
    class BoolHostileColumns(list):
        def __bool__(self):
            raise ValueError("ambiguous truth value")

    class FakeFrame:
        def __init__(self, rows):
            self.rows = rows
            self.columns = BoolHostileColumns(["2025", "2024", "2023", "2022"])

        @property
        def empty(self):
            return False

        def loc(self, row_name):
            return self.rows[row_name]

    class FakeTicker:
        info = {
            "currentPrice": 300,
            "marketCap": 1_000_000_000_000,
            "enterpriseValue": 1_050_000_000_000,
            "sharesOutstanding": 3_200_000_000,
            "earningsQuarterlyGrowth": 0.20,
            "currency": "USD",
        }
        financials = FakeFrame(
            {
                "Total Revenue": [115_762_500_000, 108_000_000_000, 105_000_000_000, 100_000_000_000],
                "Operating Income": [9_000_000_000, 10_500_000_000, 11_000_000_000, 12_000_000_000],
                "EBITDA": [12_000_000_000, 14_500_000_000, 15_000_000_000, 16_000_000_000],
                "Net Income": [4_800_000_000, 7_100_000_000, 8_000_000_000, 9_000_000_000],
                "Diluted EPS": [1.5, 2.3, 2.7, 3.0],
            }
        )
        cashflow = FakeFrame(
            {
                "Free Cash Flow": [4_500_000_000, 5_000_000_000, 5_500_000_000, 6_000_000_000],
                "Capital Expenditure": [-9_500_000_000, -9_000_000_000, -8_500_000_000, -8_000_000_000],
            }
        )
        balance_sheet = FakeFrame(
            {
                "Total Debt": [9_200_000_000, 8_765_000_000, 8_000_000_000, 7_500_000_000],
                "Stockholders Equity": [84_700_000_000, 72_800_000_000, 65_000_000_000, 60_000_000_000],
                "Cash And Cash Equivalents": [44_700_000_000, 37_340_000_000, 31_000_000_000, 25_000_000_000],
            }
        )

    raw = fetch_yfinance_fundamental_metrics("TSLA", ticker_factory=lambda symbol: FakeTicker())

    assert raw["symbol"] == "TSLA"
    assert raw["annual"][0]["fiscal_year"] == "2022"
    assert raw["annual"][-1]["revenue"] == 115_762_500_000
    assert raw["ttm"]["revenue"] == 115_762_500_000
    assert raw["previous_ttm"]["total_cash"] == 37_340_000_000
    assert raw["earnings_growth_5y_pct"] == 20.0
