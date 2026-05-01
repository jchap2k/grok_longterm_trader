import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.benchmark_guard import BenchmarkGuard
from longterm.risk_review import RiskReviewBuilder
from longterm.thesis_challenge import ThesisChallengeReviewer
from portfolio.portfolio_profile import PortfolioProfile
from longterm.portfolio_state import PortfolioState
from research.intake import create_research_packet_from_idea


def test_thesis_challenge_reviewer_builds_bull_and_bear_cases_from_packet():
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "business_summary": "Enterprise software platform with recurring revenue.",
            "thesis_summary": "Cloud and AI tooling can compound durable enterprise demand.",
            "confirming_signals": ["Azure share gains", "Margin expansion"],
            "invalidation_conditions": ["Cloud growth materially slows", "AI spend fails to monetize"],
            "reviewer_objections": ["Valuation leaves little room for disappointment."],
        }
    )

    challenge = ThesisChallengeReviewer().review(packet)

    assert "Cloud and AI tooling" in challenge.bull_case
    assert "Cloud growth materially slows" in challenge.bear_case
    assert any("Valuation leaves little room" in risk for risk in challenge.key_risks)
    assert "AI spend fails to monetize" in challenge.kill_criteria
    assert challenge.to_context_text().startswith("Bull case:")


def test_risk_review_blocks_protected_symbol_and_benchmark_paused_buy():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    guard_result = BenchmarkGuard(min_decisions=2).evaluate(
        {
            "evaluated_decisions": 3,
            "average_excess_return_pct": -4.0,
            "decisions_beating_benchmark": 0,
        }
    )

    protected = RiskReviewBuilder().build(
        {"symbol": "FXAIX", "recommendation": "BUY", "suggested_size_pct": 5},
        profile=profile,
        portfolio_state=state,
        benchmark_guard_result=guard_result,
    )
    paused = RiskReviewBuilder().build(
        {"symbol": "NVDA", "recommendation": "BUY", "suggested_size_pct": 5},
        profile=profile,
        portfolio_state=state,
        benchmark_guard_result=guard_result,
    )

    assert protected.allowed is False
    assert any("protected" in reason.lower() for reason in protected.veto_reasons)
    assert paused.allowed is False
    assert any("benchmark" in reason.lower() or "pause" in reason.lower() for reason in paused.veto_reasons)


def test_risk_review_warns_on_stale_thesis_and_oversized_position():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=10000, protected_symbols=["FXAIX"])
    guard_result = BenchmarkGuard().evaluate({"evaluated_decisions": 0})

    review = RiskReviewBuilder(max_new_position_pct=8).build(
        {"symbol": "NVDA", "recommendation": "BUY", "suggested_size_pct": 12},
        profile=profile,
        portfolio_state=state,
        benchmark_guard_result=guard_result,
        review_status={"review_due": True, "thesis_state": "weakening"},
    )

    assert review.allowed is True
    assert review.risk_level == "high"
    assert any("suggested size" in warning.lower() for warning in review.warnings)
    assert any("weakening" in warning.lower() for warning in review.warnings)
