import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.quality_growth_scorecard import (
    build_quality_growth_scorecard,
    enrich_idea_with_quality_growth_scorecard,
    enrich_ideas_with_quality_growth_scorecard,
)
from longterm.quality_growth_scorecard_cli import build_parser, run_cli


def _metrics(
    *,
    revenue_growth: str = "18.00%",
    ebitda_growth: str = "20.00%",
    eps_growth: str = "16.00%",
    fcf_growth: str = "15.00%",
    pe: str = "28.0x",
    ev_ebitda: str = "18.0x",
    p_fcf: str = "24.0x",
    peg: str = "1.4x",
    gross_margin: str = "58.00%",
    operating_margin: str = "24.00%",
    fcf_margin: str = "18.00%",
    roe: str = "26.00%",
    debt_equity: str = "0.3x",
    roic: str = "24.00%",
    total_cash: str = "$80.00B",
    total_debt: str = "$40.00B",
) -> dict:
    return {
        "symbol": "MSFT",
        "source_type": "python_fundamental_metrics",
        "revenue_growth_cagr": {
            "3_yr_revenue_growth": revenue_growth,
            "3_yr_ebitda_growth": ebitda_growth,
            "3_yr_eps_growth": eps_growth,
            "3_yr_fcf_per_share_growth": fcf_growth,
        },
        "valuation_ttm": {
            "price_earnings": pe,
            "ev_ebitda": ev_ebitda,
            "price_free_cash_flow": p_fcf,
            "price_book_value": "8.0x",
            "price_earnings_growth_5yr": peg,
        },
        "profitability_ttm": {
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "free_cash_flow_margin": fcf_margin,
            "return_on_equity": roe,
            "return_on_invested_capital": roic,
            "return_on_capital": roic,
            "debt_equity": debt_equity,
        },
        "financials_ttm": {
            "total_cash": total_cash,
            "total_debt": total_debt,
        },
        "warnings": [],
    }


def _news() -> list[dict]:
    return [
        {
            "title": "Microsoft signs AI cloud contract",
            "impact_category": "Major Contract - High",
            "relevance_score": 0.91,
            "source": "Reuters",
        },
        {
            "title": "Microsoft reports Azure AI growth",
            "impact_category": "Earnings - High",
            "relevance_score": 0.84,
            "source": "Yahoo Finance",
        },
    ]


def test_quality_growth_scorecard_scores_strong_quality_growth_name():
    scorecard = build_quality_growth_scorecard(
        {"symbol": "MSFT", "fundamental_metrics": _metrics(), "relevant_news": _news()}
    )

    assert scorecard["source_type"] == "python_quality_growth_scorecard"
    assert scorecard["basis"] == "deterministic_model"
    assert scorecard["quality_score"] >= 80
    assert scorecard["growth_score"] >= 80
    assert scorecard["valuation_score"] >= 55
    assert scorecard["market_attention_score"] >= 70
    assert scorecard["superscore"] >= 75
    assert scorecard["investing_type"] == "Moderate Compounder"
    assert "quality" in " ".join(scorecard["score_reasons"]).lower()


def test_quality_growth_scorecard_penalizes_weak_expensive_name():
    scorecard = build_quality_growth_scorecard(
        {
            "symbol": "WEAK",
            "fundamental_metrics": _metrics(
                revenue_growth="2.00%",
                ebitda_growth="-20.00%",
                eps_growth="-30.00%",
                fcf_growth="-15.00%",
                pe="250.0x",
                ev_ebitda="120.0x",
                p_fcf="150.0x",
                peg="8.0x",
                gross_margin="18.00%",
                operating_margin="3.00%",
                fcf_margin="2.00%",
                roe="4.00%",
                debt_equity="1.6x",
                roic="3.00%",
                total_cash="$1.00B",
                total_debt="$9.00B",
            ),
            "relevant_news": [],
        }
    )

    assert scorecard["quality_score"] <= 40
    assert scorecard["growth_score"] <= 35
    assert scorecard["valuation_score"] <= 20
    assert scorecard["safety_score"] <= 35
    assert scorecard["superscore"] <= 35
    assert scorecard["investing_type"] == "Speculative / Watchlist"
    assert "debt" in " ".join(scorecard["score_reasons"]).lower()


def test_quality_growth_scorecard_adds_valuation_sanity_without_replacing_valuation_score():
    strong = build_quality_growth_scorecard(
        {
            "symbol": "MSFT",
            "fundamental_metrics": _metrics(pe="20.0x", p_fcf="18.0x", peg="1.2x", roic="32.00%"),
            "relevant_news": _news(),
        }
    )
    stretched = build_quality_growth_scorecard(
        {
            "symbol": "HYPE",
            "fundamental_metrics": _metrics(
                pe="180.0x",
                p_fcf="140.0x",
                peg="7.0x",
                roic="4.00%",
                total_cash="$1.00B",
                total_debt="$12.00B",
            ),
            "relevant_news": _news(),
        }
    )

    assert "valuation_sanity_score" in strong
    assert "valuation_sanity_reasons" in strong
    assert strong["valuation_sanity_score"] > stretched["valuation_sanity_score"]
    assert any("fcf yield" in reason.lower() for reason in strong["valuation_sanity_reasons"])
    assert any("debt" in reason.lower() for reason in stretched["valuation_sanity_reasons"])
    assert strong["valuation_score"] >= 55


def test_enrich_idea_with_quality_growth_scorecard_adds_packet_fields_and_note():
    idea = {"symbol": "MSFT", "fundamental_metrics": _metrics(), "relevant_news": _news()}

    enriched = enrich_idea_with_quality_growth_scorecard(idea)

    assert enriched["quality_growth_scorecard"]["superscore"] >= 75
    assert enriched["quality_score"] == enriched["quality_growth_scorecard"]["quality_score"]
    assert enriched["valuation_score"] == enriched["quality_growth_scorecard"]["valuation_score"]
    assert any("Python quality-growth scorecard" in note for note in enriched["source_notes"])


def test_enrich_ideas_with_quality_growth_scorecard_handles_batch():
    enriched = enrich_ideas_with_quality_growth_scorecard(
        [{"symbol": "msft", "fundamental_metrics": _metrics(), "relevant_news": _news()}]
    )

    assert enriched[0]["symbol"] == "MSFT"
    assert enriched[0]["quality_growth_scorecard"]["basis"] == "deterministic_model"


def test_quality_growth_scorecard_cli_enriches_batch(tmp_path, capsys):
    ideas = tmp_path / "ideas.json"
    output = tmp_path / "enriched.json"
    ideas.write_text(
        json.dumps([{"symbol": "MSFT", "fundamental_metrics": _metrics(), "relevant_news": _news()}]),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--output",
                str(output),
            ]
        )
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["quality_growth_scorecard"]["source_type"] == "python_quality_growth_scorecard"
    assert summary["enriched_count"] == 1
