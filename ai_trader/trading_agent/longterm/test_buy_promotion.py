import json
import sys
from pathlib import Path
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.buy_promotion import (
    BuyPromotionReviewer,
    build_buy_promotion_markdown,
    build_buy_promotion_reviews,
)
from longterm.buy_promotion_cli import run_cli
from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.research_packet import ResearchPacket


def _profile() -> PortfolioProfile:
    return PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
    )


def _row(**overrides) -> dict:
    row = {
        "symbol": "NVDA",
        "company_name": "Nvidia",
        "decision_id": "decision-nvda",
        "recommendation": "BUY",
        "confidence": 75,
        "suggested_size_pct": 4.0,
        "key_thesis": "CUDA moat and AI accelerator demand support durable growth.",
    }
    row.update(overrides)
    return row


def _packet(**overrides) -> dict:
    packet = {
        "symbol": "NVDA",
        "company_name": "Nvidia",
        "protected_symbols": ["FXAIX"],
        "quality_score": 90,
        "valuation_score": 70,
        "evidence_brief": (
            "research_evidence_brief_v1 | NVDA\n"
            "Fundamentals: 3yr revenue growth 80%; P/E 40x.\n"
            "Article evidence: Nvidia article (source Reuters, confidence 0.8, basis snippet_grounded).\n"
            "Grok catalyst synthesis: AI accelerator demand remains durable."
        ),
    }
    packet.update(overrides)
    return packet


def test_buy_promotion_marks_strong_buy_actionable():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(),
        packet=_packet(),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.symbol == "NVDA"
    assert review.promotion_decision == "ACTIONABLE_BUY"
    assert review.confidence == 75
    assert review.blockers == []
    assert review.evidence_score >= 80
    assert any("First-pass BUY cleared" in reason for reason in review.reasons)


def test_buy_promotion_sends_low_confidence_buy_to_watchlist():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(confidence=62),
        packet=_packet(),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "WATCHLIST_PENDING_CONFIRMATION"
    assert "confidence_below_actionable_threshold" in review.followups


def test_buy_promotion_requires_article_evidence_for_actionable_buy():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(),
        packet=_packet(evidence_brief="research_evidence_brief_v1 | NVDA\nFundamentals: strong."),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "WATCHLIST_PENDING_EVIDENCE"
    assert "missing_article_evidence" in review.followups


def test_buy_promotion_surfaces_margin_of_safety_followup_without_hard_block():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(symbol="HYPE", confidence=82),
        packet=_packet(
            symbol="HYPE",
            valuation_score=22,
            evidence_brief=(
                "research_evidence_brief_v1 | HYPE\n"
                "Fundamentals: extreme P/E and valuation score 22.\n"
                "Article evidence: Hype article (source Reuters, confidence 0.8, basis snippet_grounded).\n"
                "Grok catalyst synthesis: Fast growth, but shares are priced for perfection."
            ),
            balance_sheet_assessment="High leverage and weak cash conversion.",
            source_notes=["Optimistic forward estimates and dilution risk."],
        ),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "WATCHLIST_PENDING_CONFIRMATION"
    assert "margin_of_safety_review" in review.followups
    assert "permanent_loss_review" in review.followups
    assert "overpayment" in review.permanent_loss_flags
    assert "leverage" in review.permanent_loss_flags
    assert review.defensive_enterprising_mode == "speculative_watchlist"
    assert review.margin_of_safety_score < 60
    assert any("margin of safety" in reason.lower() for reason in review.reasons)


def test_buy_promotion_records_missing_margin_detail_without_starving_clean_buy():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(symbol="AMZN", confidence=78, suggested_size_pct=2.5),
        packet=_packet(
            symbol="AMZN",
            valuation_score=None,
            evidence_brief=(
                "research_evidence_brief_v1 | AMZN\n"
                "Fundamentals: durable growth and acceptable leverage.\n"
                "Article evidence: Amazon article (source Reuters, confidence 0.8, basis snippet_grounded).\n"
                "Grok catalyst synthesis: AWS and advertising durability."
            ),
        ),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "ACTIONABLE_BUY"
    assert "margin_of_safety_review" not in review.followups
    assert review.margin_of_safety_score < 60
    assert review.defensive_enterprising_mode == "enterprising_candidate"


def test_buy_promotion_keeps_warning_marked_buy_on_watchlist():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(),
        packet=_packet(latest_earnings_enrichment={"warnings": ["missing_earnings_article"]}),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "WATCHLIST_PENDING_EVIDENCE"
    assert "missing_earnings_article" in review.followups


def test_buy_promotion_blocks_protected_symbol():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(symbol="FXAIX", decision_id="decision-fxaix"),
        packet=_packet(symbol="FXAIX", protected_symbols=["FXAIX"]),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "BLOCKED"
    assert "protected_symbol" in review.blockers


def test_buy_promotion_reviews_existing_position_instead_of_new_buy():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(),
        packet=_packet(),
        profile=_profile(),
        portfolio_state=PortfolioState(
            cash=10000,
            holdings=[{"symbol": "NVDA", "market_value": 2000}],
            protected_symbols=["FXAIX"],
        ),
    )

    assert review.promotion_decision == "REVIEW_EXISTING_POSITION"
    assert review.portfolio_fit_score < 100


def test_buy_promotion_does_not_promote_pass_rows():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(recommendation="PASS", confidence=30, suggested_size_pct=0),
        packet=_packet(),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert review.promotion_decision == "NOT_PROMOTED"
    assert "first_pass_not_buy_or_add" in review.blockers


def test_buy_promotion_markdown_renders_operator_table():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(),
        packet=_packet(),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    markdown = build_buy_promotion_markdown([review])

    assert "# Buy Promotion Review" in markdown
    assert "| NVDA | ACTIONABLE_BUY | BUY | 75 |" in markdown
    assert "Margin Safety" in markdown
    assert "Perm Loss" in markdown
    assert "Entry Plan" in markdown
    assert "First-pass BUY cleared" in markdown


def test_buy_promotion_review_serializes_to_json_dict():
    review = BuyPromotionReviewer().evaluate_decision_row(
        _row(),
        packet=_packet(),
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    payload = review.to_dict()

    assert json.loads(json.dumps(payload))["promotion_decision"] == "ACTIONABLE_BUY"
    assert "margin_of_safety_score" in payload
    assert "permanent_loss_flags" in payload
    assert "staged_entry_size_pct" in payload


def test_build_buy_promotion_reviews_from_journal(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = ResearchPacket(**_packet(idea_source="unit_test"))
    journal.record_decision(
        packet,
        decision={
            "recommendation": "BUY",
            "confidence": 75,
            "suggested_size_pct": 4.0,
            "key_thesis": "AI accelerator demand remains durable.",
        },
    )

    reviews = build_buy_promotion_reviews(
        journal,
        profile=_profile(),
        portfolio_state=PortfolioState(cash=10000, protected_symbols=["FXAIX"]),
    )

    assert [review.symbol for review in reviews] == ["NVDA"]
    assert reviews[0].promotion_decision == "ACTIONABLE_BUY"


def test_buy_promotion_cli_writes_markdown_report(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = ResearchPacket(**_packet(idea_source="unit_test"))
    journal.record_decision(
        packet,
        decision={
            "recommendation": "BUY",
            "confidence": 75,
            "suggested_size_pct": 4.0,
            "key_thesis": "AI accelerator demand remains durable.",
        },
    )
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    output_path = tmp_path / "buy_promotion.md"
    profile_path.write_text(
        json.dumps(
            {
                "account_strategy_mode": "roth_ira",
                "tradable_capital": 34000,
                "protected_symbols": ["FXAIX"],
                "benchmark_symbol": "FXAIX",
                "defensive_parking_symbol": "SPY",
            }
        ),
        encoding="utf-8",
    )
    portfolio_path.write_text(
        json.dumps({"cash": 10000, "holdings": [], "protected_symbols": ["FXAIX"]}),
        encoding="utf-8",
    )

    exit_code = run_cli(
        Namespace(
            journal_db=str(tmp_path / "journal.db"),
            profile_config=str(profile_path),
            portfolio_state=str(portfolio_path),
            limit=20,
            json=False,
            output=str(output_path),
        )
    )

    assert exit_code == 0
    markdown = output_path.read_text(encoding="utf-8")
    assert "| NVDA | ACTIONABLE_BUY | BUY | 75 |" in markdown
