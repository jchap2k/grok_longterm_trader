import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea
from longterm.portfolio_state import PortfolioState
from longterm.research_runner import LongTermResearchRunner
from research.research_packet import CompanyCategory


AGENT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "agent" / "configs" / "longterm_trading_agent_specs.json"
LEGACY_AGENT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "longterm_agent_specs_v1.json"


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


def test_create_research_packet_from_enriched_idea_adds_evidence_brief_without_bloating_notes():
    packet = create_research_packet_from_idea(
        {
            "symbol": "AMZN",
            "company_name": "Amazon",
            "idea_source": "comparison_run",
            "business_summary": "Cloud ecommerce advertising AI logistics platform.",
            "source_notes": ["Original source note."],
            "fundamental_metrics": {
                "revenue_growth_cagr": {"3_yr_revenue_growth": "11.73%"},
                "valuation_ttm": {"price_earnings": "37.1x"},
                "profitability_ttm": {"gross_margin": "50.29%"},
            },
            "relevant_news": [
                {
                    "date": "2026-05-02",
                    "source": "The Motley Fool",
                    "title": "Amazon Just Proved It's No Longer an AI Underdog",
                    "impact_category": "Product/Tech - High",
                    "relevance_score": 0.435,
                    "primary_subject_score": 0.95,
                }
            ],
        }
    )

    assert packet.source_notes == ["Original source note."]
    assert packet.evidence_brief.startswith("research_evidence_brief_v1 | AMZN")
    assert "3yr revenue growth 11.73%" in packet.evidence_brief
    assert "Amazon Just Proved It's No Longer an AI Underdog" in packet.evidence_brief
    assert "fundamental_metrics" not in packet.evidence_brief


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


def test_longterm_research_runner_passes_evidence_brief_as_dedicated_context(monkeypatch):
    captured = {}

    class FakeCheapGrokHeavy:
        def __init__(self, **kwargs):
            pass

        def call_with_context(self, task_prompt, context_sections=None):
            captured["context_sections"] = context_sections or {}
            return '{"recommendation":"PASS","confidence":75}'

    monkeypatch.setattr(
        "longterm.research_runner.CheapGrokHeavy",
        FakeCheapGrokHeavy,
    )

    packet = create_research_packet_from_idea(
        {
            "symbol": "AMZN",
            "company_name": "Amazon",
            "idea_source": "comparison_run",
            "business_summary": "Cloud ecommerce advertising AI logistics platform.",
            "fundamental_metrics": {
                "revenue_growth_cagr": {"3_yr_revenue_growth": "11.73%"},
                "valuation_ttm": {"price_earnings": "37.1x"},
                "profitability_ttm": {"gross_margin": "50.29%"},
            },
        }
    )
    runner = LongTermResearchRunner(
        api_key="test-key",
        config_path="dummy-config.json",
        verbose=False,
    )

    runner.run(packet)

    assert "research_evidence_brief" in captured["context_sections"]
    assert "research_evidence_brief_v1 | AMZN" in captured["context_sections"]["research_evidence_brief"]
    assert "3yr revenue growth 11.73%" in captured["context_sections"]["research_evidence_brief"]


def test_longterm_research_runner_includes_active_rules_context(monkeypatch, tmp_path):
    captured = {}
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text(
        "<trading_rules><identity>Long-term quality-growth active sleeve.</identity>"
        "<protected_holdings>FXAIX is protected.</protected_holdings></trading_rules>",
        encoding="utf-8",
    )

    class FakeCheapGrokHeavy:
        def __init__(self, **kwargs):
            pass

        def call_with_context(self, task_prompt, context_sections=None):
            captured["context_sections"] = context_sections or {}
            return '{"recommendation":"PASS","confidence":75}'

    monkeypatch.setattr(
        "longterm.research_runner.CheapGrokHeavy",
        FakeCheapGrokHeavy,
    )

    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "business_summary": "Large software platform.",
            "thesis_summary": "Cloud durability.",
        }
    )
    runner = LongTermResearchRunner(
        api_key="test-key",
        config_path="dummy-config.json",
        rules_path=rules_path,
        verbose=False,
    )

    runner.run(packet)

    assert "active_rules_context" in captured["context_sections"]
    assert "Long-term quality-growth active sleeve" in captured["context_sections"]["active_rules_context"]
    assert "FXAIX is protected" in captured["context_sections"]["active_rules_context"]


def test_longterm_research_runner_includes_current_portfolio_context(monkeypatch):
    captured = {}

    class FakeCheapGrokHeavy:
        def __init__(self, **kwargs):
            pass

        def call_with_context(self, task_prompt, context_sections=None):
            captured["context_sections"] = context_sections or {}
            return '{"recommendation":"PASS","confidence":75}'

    monkeypatch.setattr(
        "longterm.research_runner.CheapGrokHeavy",
        FakeCheapGrokHeavy,
    )

    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
        low_risk_parking_symbol="SGOV",
        duration_hedge_symbol="TLT",
    )
    packet = create_research_packet_from_idea(
        {
            "symbol": "AMZN",
            "company_name": "Amazon",
            "business_summary": "Large-cap platform company.",
            "thesis_summary": "AWS and advertising durability.",
        },
        profile=profile,
    )
    portfolio_state = PortfolioState(
        cash=5000,
        holdings=[
            {"symbol": "FXAIX", "market_value": 40000},
            {"symbol": "SPY", "market_value": 33150},
            {"symbol": "AMZN", "market_value": 813.03},
        ],
        protected_symbols=["FXAIX"],
    )
    runner = LongTermResearchRunner(
        api_key="test-key",
        config_path="dummy-config.json",
        verbose=False,
    )

    runner.run(packet, portfolio_state=portfolio_state)

    context = captured["context_sections"]["portfolio_context"]
    assert "Cash: $5,000.00" in context
    assert "Active market value: $33,963.03" in context
    assert "Protected/core value: $40,000.00" in context
    assert "Holding FXAIX: $40,000.00, role=protected" in context
    assert "Holding SPY: $33,150.00, role=parking" in context
    assert "Holding AMZN: $813.03, role=existing_candidate_position" in context
    assert "Parking symbols: SPY, SGOV, TLT" in context


def test_longterm_agent_specs_include_active_rules_for_every_committee_role():
    missing = []
    for path in (AGENT_CONFIG_PATH, LEGACY_AGENT_CONFIG_PATH):
        payload = json.loads(path.read_text(encoding="utf-8"))
        missing.extend(
            f"{path.name}:{spec['name']}"
            for spec in payload["agent_specs"]
            if "active_rules_context" not in spec.get("input_sections", [])
        )

    assert missing == []


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
