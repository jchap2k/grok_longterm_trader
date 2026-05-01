import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea
from longterm.research_runner import LongTermResearchRunner
from research.research_packet import CompanyCategory


def test_create_research_packet_from_idea_inherits_portfolio_controls():
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        total_account_value=68000.0,
        tradable_capital=34000.0,
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
    )

    packet = create_research_packet_from_idea(
        {
            "symbol": "msft",
            "company_name": "Microsoft",
            "company_category": "stalwart",
            "business_summary": "Large-cap platform software leader.",
            "thesis_summary": "Cloud and enterprise software durability.",
            "source_notes": ["Manual lead from user watchlist"],
        },
        profile=profile,
        idea_source="manual_watchlist",
    )

    payload = packet.to_dict()

    assert payload["symbol"] == "MSFT"
    assert payload["company_category"] == "stalwart"
    assert payload["account_strategy_mode"] == "roth_ira"
    assert payload["protected_symbols"] == ["FXAIX"]
    assert payload["benchmark_symbol"] == "FXAIX"
    assert payload["defensive_parking_symbol"] == "SPY"
    assert payload["idea_source"] == "manual_watchlist"


def test_create_research_packet_from_idea_accepts_enum_category():
    packet = create_research_packet_from_idea(
        {
            "symbol": "NVDA",
            "company_category": CompanyCategory.FAST_GROWER,
        }
    )

    assert packet.company_category is CompanyCategory.FAST_GROWER


def test_research_packet_completeness_allows_source_notes_as_context():
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "idea_source": "discovery_sp500",
            "source_notes": ["Discovery metrics: revenue growth 16%."],
        }
    )

    assert packet.completeness_warnings() == []
    assert packet.is_minimally_complete_for_research() is True


def test_research_packet_completeness_blocks_thin_ticker_stub():
    packet = create_research_packet_from_idea({"symbol": "TSLA"})

    assert packet.completeness_warnings() == [
        "TSLA: missing company_name",
        "TSLA: missing idea_source",
        "TSLA: missing research context",
    ]
    assert packet.is_minimally_complete_for_research() is False


def test_longterm_research_runner_builds_context_and_calls_grok_helper(monkeypatch):
    captured = {}

    class FakeCheapGrokHeavy:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def call_with_context(self, task_prompt, context_sections=None):
            captured["task_prompt"] = task_prompt
            captured["context_sections"] = context_sections or {}
            return '{"recommendation":"BUY","confidence":81}'

    monkeypatch.setattr(
        "longterm.research_runner.CheapGrokHeavy",
        FakeCheapGrokHeavy,
    )

    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        total_account_value=68000.0,
        tradable_capital=34000.0,
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
    )
    packet = create_research_packet_from_idea(
        {
            "symbol": "AAPL",
            "company_name": "Apple",
            "business_summary": "Consumer technology ecosystem.",
            "thesis_summary": "Services mix and ecosystem lock-in support long-term durability.",
            "primary_growth_driver": "Services mix expansion",
            "industry_context": "Mega-cap technology leader",
        },
        profile=profile,
        idea_source="manual_watchlist",
    )

    runner = LongTermResearchRunner(
        api_key="test-key",
        config_path="dummy-config.json",
        agent_max_tokens=500,
        max_concurrent=3,
        verbose=False,
    )
    result = runner.run(
        packet,
        financial_metrics="Free cash flow remains strong.",
        macro_regime="Rates remain elevated but stable.",
        market_risk_context="No extreme volatility stress.",
        supporting_evidence="Installed base and services revenue remain durable.",
        risk_flags="Regulatory and saturation risk.",
    )

    assert result == '{"recommendation":"BUY","confidence":81}'
    assert captured["init_kwargs"]["agent_specs_path"] == "dummy-config.json"
    assert captured["init_kwargs"]["agent_preset"] == "decision_4"
    assert captured["context_sections"]["portfolio_context"].startswith("Account mode: roth_ira.")
    assert "FXAIX" in captured["context_sections"]["benchmark_context"]
    assert captured["context_sections"]["bull_thesis"].startswith(
        "Services mix and ecosystem lock-in support"
    )
    assert "research_principles" in captured["context_sections"]
    assert "business first" in captured["context_sections"]["research_principles"]
    assert "BusinessStoryReviewer" in captured["context_sections"]["deterministic_reviews"]
    assert "Bull case:" in captured["context_sections"]["thesis_challenge"]
    assert "Bear case:" in captured["context_sections"]["thesis_challenge"]
    assert "review_cadence" in captured["context_sections"]
    assert "Start new positions small" in captured["context_sections"]["sizing_policy_context"]
    assert "AAPL" in captured["task_prompt"]


def test_longterm_research_runner_records_structured_decision(monkeypatch, tmp_path):
    class FakeCheapGrokHeavy:
        def __init__(self, **kwargs):
            pass

        def call_with_context(self, task_prompt, context_sections=None):
            return (
                '{"recommendation":"BUY","confidence":84,'
                '"suggested_size_pct":6.0,"key_thesis":"Durable compounder."}'
            )

    monkeypatch.setattr(
        "longterm.research_runner.CheapGrokHeavy",
        FakeCheapGrokHeavy,
    )

    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
    )
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "thesis_summary": "Cloud and software durability.",
        },
        profile=profile,
    )
    runner = LongTermResearchRunner(
        api_key="test-key",
        config_path="dummy-config.json",
        verbose=False,
    )

    decision_id = runner.run_and_record(
        packet,
        journal_db_path=tmp_path / "journal.db",
        candidate_price=400.0,
        benchmark_price=180.0,
    )

    row = runner.decision_journal.get_decision(decision_id)

    assert row["symbol"] == "MSFT"
    assert row["recommendation"] == "BUY"
    assert row["confidence"] == 84
    assert row["suggested_size_pct"] == 6.0
    assert row["benchmark_symbol"] == "FXAIX"
