import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.latest_earnings_enrichment import (
    build_latest_earnings_enrichment,
    enrich_idea_with_latest_earnings,
    enrich_ideas_with_latest_earnings,
)
from longterm.latest_earnings_enrichment_cli import build_parser, run_cli


def _idea() -> dict:
    return {
        "symbol": "TSLA",
        "company_name": "Tesla",
        "business_summary": "Electric vehicles batteries software autonomy robotaxi energy",
        "fundamental_metrics": {
            "financials_ttm": {
                "revenue": "$120.00B (+2.25%)",
                "net_income": "$3.90B (-38.10%)",
                "free_cash_flow": "$7.00B (+3.24%)",
                "capital_expenditure": "-$9.50B (+5.26%)",
            },
            "profitability_ttm": {
                "operating_margin": "5.00%",
                "free_cash_flow_margin": "5.83%",
            },
        },
        "relevant_news": [
            {
                "title": "Tesla Q1 earnings show robotaxi software growth but margin pressure",
                "url": "https://example.com/tsla-q1",
                "date": "2026-04-22",
                "summary": "Revenue improved while automotive margins stayed pressured. Management highlighted robotaxi, FSD subscriptions, and higher capex.",
                "impact_category": "Earnings - High",
                "source": "Reuters",
                "relevance_score": 0.92,
            },
            {
                "title": "Tesla Cybercab launch plans raise capex questions",
                "url": "https://example.com/tsla-cybercab",
                "date": "2026-04-28",
                "summary": "Investors questioned whether Cybercab spending can generate attractive returns.",
                "impact_category": "Product/Tech - High",
                "source": "Yahoo Finance",
                "relevance_score": 0.81,
            },
        ],
    }


def test_build_latest_earnings_enrichment_from_relevant_news_and_fundamentals():
    enrichment = build_latest_earnings_enrichment(_idea(), as_of_date="2026-05-02")

    assert enrichment["source_type"] == "python_latest_earnings_enrichment"
    assert enrichment["basis"] == "source_filtered_articles_and_provider_metrics"
    assert enrichment["quarter"] == "Q1"
    assert enrichment["source_urls"] == ["https://example.com/tsla-q1"]
    assert "Revenue: $120.00B (+2.25%)" in enrichment["key_financial_takeaways"]
    assert "Free Cash Flow: $7.00B (+3.24%)" in enrichment["key_financial_takeaways"]
    assert any("robotaxi" in item.lower() for item in enrichment["thesis_positive_developments"])
    assert any("margin" in item.lower() for item in enrichment["thesis_negative_developments"])
    assert enrichment["confidence"] >= 0.65
    assert enrichment["warnings"] == []


def test_build_latest_earnings_enrichment_warns_when_no_earnings_sources():
    idea = {"symbol": "XYZ", "relevant_news": [{"title": "XYZ signs partnership", "url": "https://example.com"}]}

    enrichment = build_latest_earnings_enrichment(idea, as_of_date="2026-05-02")

    assert enrichment["confidence"] <= 0.4
    assert "missing_earnings_article" in enrichment["warnings"]
    assert enrichment["source_urls"] == []


def test_enrich_idea_with_latest_earnings_adds_source_note_and_payload():
    enriched = enrich_idea_with_latest_earnings(_idea(), as_of_date="2026-05-02")

    assert enriched["latest_earnings_enrichment"]["source_urls"] == ["https://example.com/tsla-q1"]
    assert any("Latest earnings enrichment" in note for note in enriched["source_notes"])


def test_enrich_ideas_with_latest_earnings_handles_batch():
    enriched = enrich_ideas_with_latest_earnings([_idea()], as_of_date="2026-05-02")

    assert enriched[0]["symbol"] == "TSLA"
    assert enriched[0]["latest_earnings_enrichment"]["confidence"] >= 0.65


def test_latest_earnings_enrichment_cli_enriches_batch(tmp_path, capsys):
    ideas = tmp_path / "ideas.json"
    output = tmp_path / "earnings_enriched.json"
    ideas.write_text(json.dumps([_idea()]), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--output",
                str(output),
                "--as-of-date",
                "2026-05-02",
            ]
        )
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["latest_earnings_enrichment"]["quarter"] == "Q1"
    assert summary["enriched_count"] == 1
