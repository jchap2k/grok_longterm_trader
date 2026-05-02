import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.research_evidence_brief import (
    EVIDENCE_BRIEF_VERSION,
    build_research_evidence_brief,
)


def _enriched_idea() -> dict:
    return {
        "symbol": "AMZN",
        "company_name": "Amazon",
        "fundamental_metrics": {
            "revenue_growth_cagr": {
                "3_yr_revenue_growth": "11.73%",
                "3_yr_ebitda_growth": "62.75%",
            },
            "valuation_ttm": {
                "price_earnings": "37.1x",
                "ev_ebitda": "17.9x",
            },
            "profitability_ttm": {
                "gross_margin": "50.29%",
                "operating_margin": "11.16%",
                "debt_equity": "0.4x",
            },
            "financials_ttm": {
                "revenue": "$716.92B (+12.38%)",
                "free_cash_flow": "$7.70B (-76.60%)",
            },
            "warnings": ["High capex phase may pressure near-term FCF."],
        },
        "quality_growth_scorecard": {
            "superscore": 54.1,
            "quality_score": 53.0,
            "growth_score": 42.0,
            "valuation_score": 53.0,
            "safety_score": 46.0,
            "investing_type": "Aggressive Growth",
            "estimated_drawdown_band": "-40% to -60%",
            "score_reasons": [
                "strong gross margin",
                "strong EBITDA growth",
                "expensive P/FCF",
            ],
        },
        "latest_earnings_enrichment": {
            "quarter": "Q1",
            "confidence": 0.9,
            "summary": "AWS growth and advertising margin expansion offset ecommerce pressure.",
            "key_financial_takeaways": [
                "Revenue: $716.92B (+12.38%)",
                "Free Cash Flow: $7.70B (-76.60%)",
            ],
            "thesis_positive_developments": ["AWS AI demand remains strong."],
            "thesis_negative_developments": ["AI capex is pressuring free cash flow."],
        },
        "relevant_news": [
            {
                "date": "2026-05-02",
                "source": "The Motley Fool",
                "title": "Amazon Just Proved It's No Longer an AI Underdog",
                "impact_category": "Product/Tech - High",
                "relevance_score": 0.435,
                "primary_subject_score": 0.95,
            },
            {
                "date": "2026-05-01",
                "source": "Benzinga",
                "title": "Microsoft, Amazon On Watch - Goldman Sounds Alarm On Cloud Cash Burn",
                "impact_category": "Product/Tech - High",
                "relevance_score": 0.47,
                "primary_subject_score": 0.5,
            },
        ],
        "grok_research_enrichment": {
            "confidence": 0.85,
            "thesis_relevant_catalysts": [
                "AWS achieved 28% Q1 revenue growth and remains an AI infrastructure leader.",
                "Advertising growth diversifies the thesis beyond ecommerce.",
            ],
            "bull_cases": ["AWS and ads can compound at attractive margins."],
            "bear_cases": ["AI capex could absorb most operating cash flow."],
            "risk_flags": ["FCF pressure from infrastructure spend."],
            "warnings": ["Evidence thin on long-term AI ROI."],
        },
    }


def test_build_research_evidence_brief_summarizes_enriched_context():
    brief = build_research_evidence_brief(_enriched_idea())

    assert brief.startswith(f"{EVIDENCE_BRIEF_VERSION} | AMZN")
    assert "Fundamentals:" in brief
    assert "3yr revenue growth 11.73%" in brief
    assert "P/E 37.1x" in brief
    assert "Scorecard:" in brief
    assert "super 54.1" in brief
    assert "Latest earnings:" in brief
    assert "AWS growth and advertising margin expansion" in brief
    assert "Primary news:" in brief
    assert "Amazon Just Proved It's No Longer an AI Underdog" in brief
    assert "Grok catalyst synthesis:" in brief
    assert "AWS achieved 28% Q1 revenue growth" in brief
    assert "Warnings:" in brief
    assert "High capex phase" in brief


def test_build_research_evidence_brief_returns_empty_for_plain_idea():
    assert build_research_evidence_brief({"symbol": "AMZN", "company_name": "Amazon"}) == ""


def test_build_research_evidence_brief_caps_output_and_limits_news_items():
    idea = _enriched_idea()
    idea["relevant_news"] = [
        {
            "date": "2026-05-02",
            "source": "Source",
            "title": f"Amazon primary article {idx}",
            "impact_category": "Earnings - High",
            "relevance_score": 0.8,
            "primary_subject_score": 0.9,
        }
        for idx in range(10)
    ]

    brief = build_research_evidence_brief(idea, max_news_items=3, max_chars=1200)

    assert "Amazon primary article 0" in brief
    assert "Amazon primary article 2" in brief
    assert "Amazon primary article 3" not in brief
    assert len(brief) <= 1200
