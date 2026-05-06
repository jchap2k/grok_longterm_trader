import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.research_packet import ResearchPacket
from longterm.company_classifier import CompanyCategory, classify_company
from longterm.defensive_policy import DefensiveAction, evaluate_defensive_posture
from longterm.prompt_builder import build_research_prompt
from portfolio.portfolio_profile import PortfolioProfile


def test_research_packet_serializes_combined_score_and_lists():
    packet = ResearchPacket(
        symbol="NVDA",
        company_name="NVIDIA",
        company_category=CompanyCategory.FAST_GROWER,
        account_strategy_mode="roth_ira",
        protected_symbols=["FXAIX"],
        business_summary="Leading accelerated computing platform.",
        thesis_summary="AI infrastructure demand remains structurally strong.",
        primary_growth_driver="Data center GPU adoption",
        industry_context="Semiconductor leader with strong AI exposure.",
        quality_score=9.0,
        valuation_score=6.0,
        invalidation_conditions=["Data center growth stalls"],
        confirming_signals=["Revenue growth remains above 25%"],
        review_cadence="quarterly",
    )

    payload = packet.to_dict()

    assert payload["company_category"] == "fast_grower"
    assert payload["account_strategy_mode"] == "roth_ira"
    assert payload["protected_symbols"] == ["FXAIX"]
    assert payload["combined_attractiveness_score"] == pytest.approx(7.5)
    assert payload["invalidation_conditions"] == ["Data center growth stalls"]
    assert payload["confirming_signals"] == ["Revenue growth remains above 25%"]


def test_classifier_prefers_turnaround_and_cyclical_flags_before_growth_buckets():
    turnaround = classify_company(
        revenue_growth_pct=8.0,
        earnings_growth_pct=12.0,
        turnaround_signals=True,
    )
    cyclical = classify_company(
        revenue_growth_pct=18.0,
        earnings_growth_pct=20.0,
        is_cyclical=True,
    )

    assert turnaround is CompanyCategory.TURNAROUND
    assert cyclical is CompanyCategory.CYCLICAL


def test_classifier_assigns_growth_buckets():
    assert classify_company(4.0, 5.0) is CompanyCategory.SLOW_GROWER
    assert classify_company(9.0, 8.0) is CompanyCategory.STALWART
    assert classify_company(24.0, 28.0) is CompanyCategory.FAST_GROWER


def test_build_research_prompt_includes_packet_details_and_review_questions():
    packet = ResearchPacket(
        symbol="DUOL",
        company_name="Duolingo",
        company_category=CompanyCategory.FAST_GROWER,
        account_strategy_mode="roth_ira",
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
        business_summary="Consumer learning platform with subscription growth.",
        thesis_summary="Execution strength and category leadership can support a multi-quarter winner.",
        primary_growth_driver="Paid subscriber growth",
        industry_context="Education software with strong consumer brand.",
        quality_score=8.0,
        valuation_score=5.5,
        review_cadence="quarterly",
        confirming_signals=["Paid subscribers keep compounding"],
        invalidation_conditions=["Engagement weakens for two quarters"],
    )

    prompt = build_research_prompt(packet, source_notes=["Idea source: Motley Fool candidate list"])

    assert "DUOL" in prompt
    assert "fast_grower" in prompt
    assert "roth_ira" in prompt
    assert "FXAIX" in prompt
    assert "SPY" in prompt
    assert "Idea source: Motley Fool candidate list" in prompt
    assert "rebalance into more pullback-resilient holdings" in prompt
    assert "Never recommend selling, trimming, rotating, or rebalancing protected symbols" in prompt
    assert "Use defensive_parking_symbol for temporary index-fund parking" in prompt
    assert "What breaks the thesis?" in prompt
    assert "Respond with valid JSON" in prompt


def test_portfolio_profile_normalizes_benchmark_protection_and_tradable_capital():
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        total_account_value=68000.0,
        tradable_capital=34000.0,
        protected_symbols=["fxaix"],
        benchmark_symbol="fxaix",
        defensive_parking_symbol="spy",
    )

    payload = profile.to_dict()

    assert payload["account_strategy_mode"] == "roth_ira"
    assert payload["protected_symbols"] == ["FXAIX"]
    assert payload["benchmark_symbol"] == "FXAIX"
    assert payload["defensive_parking_symbol"] == "SPY"
    assert payload["protected_capital"] == pytest.approx(34000.0)


def test_portfolio_profile_exposes_tax_mode_and_approved_parking_symbols():
    roth = PortfolioProfile(
        account_strategy_mode="roth_ira",
        protected_symbols=["fxaix"],
        benchmark_symbol="fxaix",
        defensive_parking_symbol="spy",
        low_risk_parking_symbol="sgov",
        duration_hedge_symbol="tlt",
        cash_symbol="cash",
    )
    taxable = PortfolioProfile(
        account_strategy_mode="taxable",
        defensive_parking_symbol="spy",
        low_risk_parking_symbol="sgov",
        duration_hedge_symbol="tlt",
    )

    assert roth.is_non_taxable is True
    assert taxable.is_non_taxable is False
    assert roth.approved_parking_symbols == ["SPY", "SGOV", "TLT"]
    assert roth.is_approved_parking_symbol("spy") is True
    assert roth.is_approved_parking_symbol("fxaix") is False
    assert roth.is_approved_parking_symbol("cash") is False


def test_defensive_policy_prefers_cash_in_extreme_vix_roth_mode():
    action = evaluate_defensive_posture(
        account_strategy_mode="roth_ira",
        vix_level=61.0,
        broad_trend_broken=True,
        leadership_failing=True,
    )

    assert action.action is DefensiveAction.MOVE_TO_CASH
    assert "Extreme volatility" in action.rationale


def test_defensive_policy_waits_for_confirmation_before_redeploy():
    action = evaluate_defensive_posture(
        account_strategy_mode="roth_ira",
        vix_level=24.0,
        broad_trend_broken=False,
        leadership_failing=False,
        recovery_confirmed=False,
        currently_defensive=True,
    )

    assert action.action is DefensiveAction.HOLD_DEFENSIVE
    assert "not fully confirmed" in action.rationale


def test_defensive_policy_redeploys_after_volatility_cools_and_recovery_confirms():
    action = evaluate_defensive_posture(
        account_strategy_mode="roth_ira",
        vix_level=22.0,
        broad_trend_broken=False,
        leadership_failing=False,
        recovery_confirmed=True,
        currently_defensive=True,
    )

    assert action.action is DefensiveAction.REDEPLOY
