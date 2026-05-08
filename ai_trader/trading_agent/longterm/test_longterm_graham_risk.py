import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.graham_risk import (
    classify_defensive_enterprising_mode,
    evaluate_permanent_loss_risk,
    evaluate_staged_entry,
    mr_market_review_trigger,
)
from longterm.portfolio_state import Holding


def test_permanent_loss_risk_flags_leverage_dilution_accounting_and_overpayment():
    report = evaluate_permanent_loss_risk(
        {
            "symbol": "HYPE",
            "valuation_score": 18,
            "evidence_brief": "Extreme P/E, priced for perfection, and optimistic forward estimates.",
            "balance_sheet_assessment": "High leverage with refinancing risk and weak cash conversion.",
            "source_notes": ["dilution risk", "non-GAAP accounting concerns", "product disruption"],
        }
    )

    assert report.severity == "high"
    assert report.score < 50
    assert {
        "overpayment",
        "leverage",
        "refinancing_risk",
        "weak_cash_conversion",
        "dilution",
        "accounting_quality",
        "business_disruption",
    }.issubset(set(report.flags))


def test_defensive_enterprising_mode_and_staged_entry_for_moderate_margin():
    packet = {
        "symbol": "GOOD",
        "quality_score": 88,
        "valuation_score": 58,
        "evidence_brief": "Durable moat, reasonable valuation, normalized free cash flow, and net cash.",
    }
    risk = evaluate_permanent_loss_risk(packet)

    assert classify_defensive_enterprising_mode(packet, margin_of_safety_score=66, risk_report=risk) == "enterprising_candidate"

    staged = evaluate_staged_entry(suggested_size_pct=6.0, margin_of_safety_score=66, risk_report=risk)
    assert staged.recommended_size_pct == 2.0
    assert staged.label == "starter_position"
    assert "staged" in staged.reason.lower()


def test_staged_entry_does_not_resize_missing_margin_without_risk_flags():
    packet = {
        "symbol": "PLAIN",
        "quality_score": 82,
        "evidence_brief": "Durable growth and acceptable leverage.",
    }
    risk = evaluate_permanent_loss_risk(packet)

    staged = evaluate_staged_entry(suggested_size_pct=8.0, margin_of_safety_score=40, risk_report=risk)

    assert staged.label == "target_position"
    assert staged.recommended_size_pct == 8.0


def test_mr_market_review_trigger_distinguishes_drawdown_and_rally():
    drawdown = mr_market_review_trigger(
        Holding(symbol="ADBE", quantity=2, market_value=700, original_purchase_total_cost=1000)
    )
    rally = mr_market_review_trigger(
        Holding(symbol="MSFT", quantity=2, market_value=1500, original_purchase_total_cost=1000)
    )

    assert drawdown.review_due is True
    assert drawdown.category == "mr_market_drawdown_review"
    assert "broken thesis" in drawdown.reason.lower()
    assert rally.review_due is True
    assert rally.category == "mr_market_rally_review"
    assert "valuation" in rally.reason.lower()
