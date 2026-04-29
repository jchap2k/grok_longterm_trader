import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.position_sizing import PositionSizingPolicy
from longterm.review_cadence import ReviewCadencePolicy
from longterm.reviewers import (
    BalanceSheetReviewer,
    BusinessStoryReviewer,
    QualityDurabilityReviewer,
    QualityAtReasonablePriceReviewer,
)
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def test_business_story_reviewer_rewards_clear_thesis_and_penalizes_vague_story():
    reviewer = BusinessStoryReviewer()
    strong = create_research_packet_from_idea(
        {
            "symbol": "NVDA",
            "business_summary": "AI infrastructure platform with GPU, software, and networking leadership.",
            "thesis_summary": "Data-center AI demand can persist for several years.",
            "primary_growth_driver": "AI accelerator demand",
            "industry_context": "Leader in accelerated computing.",
            "confirming_signals": ["Data-center revenue growth", "Gross margin durability"],
            "invalidation_conditions": ["AI capex slowdown", "Margin compression"],
        }
    )
    vague = create_research_packet_from_idea({"symbol": "ABC", "thesis_summary": "Could go up."})

    strong_result = reviewer.review(strong)
    vague_result = reviewer.review(vague)

    assert strong_result.score >= 80
    assert "clear thesis" in " ".join(strong_result.support).lower()
    assert vague_result.score < 50
    assert vague_result.objections


def test_balance_sheet_reviewer_flags_debt_stress_and_rewards_net_cash():
    reviewer = BalanceSheetReviewer()
    strong = create_research_packet_from_idea(
        {"symbol": "AAPL", "balance_sheet_assessment": "Net cash, strong free cash flow, manageable debt."}
    )
    weak = create_research_packet_from_idea(
        {"symbol": "XYZ", "balance_sheet_assessment": "High leverage, refinancing risk, weak cash flow."}
    )

    assert reviewer.review(strong).score >= 75
    weak_result = reviewer.review(weak)
    assert weak_result.score <= 35
    assert any("leverage" in item.lower() for item in weak_result.objections)


def test_quality_at_reasonable_price_requires_quality_and_valuation():
    reviewer = QualityAtReasonablePriceReviewer()
    attractive = create_research_packet_from_idea(
        {"symbol": "MSFT", "quality_score": 88, "valuation_score": 72}
    )
    expensive = create_research_packet_from_idea(
        {"symbol": "TSLA", "quality_score": 85, "valuation_score": 35}
    )

    attractive_result = reviewer.review(attractive)
    expensive_result = reviewer.review(expensive)

    assert attractive_result.score >= 75
    assert attractive_result.passed is True
    assert expensive_result.score < attractive_result.score
    assert expensive_result.passed is False


def test_quality_durability_reviewer_rewards_patterns_and_flags_quality_traps():
    reviewer = QualityDurabilityReviewer()
    durable = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "business_summary": "Cloud and software platform with recurring revenue and high switching costs.",
            "thesis_summary": "Pricing power, installed base expansion, and market share gains can compound.",
            "industry_context": "Stable oligopoly with durable enterprise demand and rational competition.",
            "primary_growth_driver": "Recurring revenue and cloud share gains",
            "balance_sheet_assessment": "Net cash and strong free cash flow.",
        }
    )
    fragile = create_research_packet_from_idea(
        {
            "symbol": "XYZ",
            "business_summary": "Cyclical hardware vendor with high leverage and dependency on one customer.",
            "thesis_summary": "Management hopes demand recovers before cheaper good-enough substitutes arrive.",
            "industry_context": "Fragmented price-war market exposed to technological disruption.",
            "balance_sheet_assessment": "High leverage and weak cash conversion.",
        }
    )

    durable_result = reviewer.review(durable)
    fragile_result = reviewer.review(fragile)

    assert durable_result.passed is True
    assert any("pricing power" in item.lower() for item in durable_result.support)
    assert fragile_result.passed is False
    assert fragile_result.score < durable_result.score
    assert any("dependency" in item.lower() for item in fragile_result.objections)


def test_review_cadence_policy_varies_by_company_category_and_risk():
    policy = ReviewCadencePolicy()
    fast_grower = create_research_packet_from_idea(
        {"symbol": "NVDA", "company_category": "fast_grower"}
    )
    cyclical_with_risk = create_research_packet_from_idea(
        {
            "symbol": "CAT",
            "company_category": "cyclical",
            "balance_sheet_assessment": "Rising debt and cyclical demand risk.",
        }
    )

    assert policy.assign(fast_grower).review_cadence == "monthly"
    assert policy.assign(cyclical_with_risk).review_cadence == "biweekly"


def test_position_sizing_starts_small_adds_on_confirmation_and_protects_core():
    policy = PositionSizingPolicy()
    profile = PortfolioProfile(
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
    )
    packet = create_research_packet_from_idea({"symbol": "NVDA"})

    starter = policy.recommend(
        packet,
        profile=profile,
        decision={"recommendation": "BUY", "confidence": 82, "suggested_size_pct": 8},
        current_position_pct=0,
        confirmation_count=0,
    )
    add = policy.recommend(
        packet,
        profile=profile,
        decision={"recommendation": "BUY", "confidence": 88, "suggested_size_pct": 8},
        current_position_pct=3,
        confirmation_count=2,
    )
    protected = policy.recommend(
        create_research_packet_from_idea({"symbol": "FXAIX"}),
        profile=profile,
        decision={"recommendation": "REDUCE", "confidence": 90, "suggested_size_pct": 0},
        current_position_pct=50,
    )

    assert starter.action == "START"
    assert starter.target_size_pct == 3.0
    assert add.action == "ADD"
    assert add.target_size_pct == 8.0
    assert protected.action == "PROTECTED_HOLD"
    assert protected.target_size_pct == 50.0
