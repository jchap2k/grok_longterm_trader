import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def test_longterm_decision_journal_records_decision_with_benchmark_context(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
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
            "thesis_summary": "Services and ecosystem durability.",
        },
        profile=profile,
        idea_source="manual_watchlist",
    )

    decision_id = journal.record_decision(
        packet,
        decision={
            "recommendation": "BUY",
            "confidence": 82,
            "suggested_size_pct": 6.5,
            "key_thesis": "Durable ecosystem compounder.",
        },
        candidate_price=180.0,
        benchmark_price=165.0,
        raw_response='{"recommendation":"BUY"}',
    )

    row = journal.get_decision(decision_id)

    assert row["symbol"] == "AAPL"
    assert row["recommendation"] == "BUY"
    assert row["confidence"] == 82
    assert row["suggested_size_pct"] == 6.5
    assert row["benchmark_symbol"] == "FXAIX"
    assert row["benchmark_price_at_decision"] == 165.0
    assert row["candidate_price_at_decision"] == 180.0
    assert row["idea_source"] == "manual_watchlist"


def test_longterm_decision_journal_persists_macro_regime_context_in_packet_json(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "longterm_decisions.db")
    packet = create_research_packet_from_idea(
        {
            "symbol": "AAPL",
            "company_name": "Apple",
            "idea_source": "manual_watchlist",
            "thesis_summary": "Services and ecosystem durability.",
            "macro_regime_context": {
                "risk_regime": "normal",
                "provider_status": "ok",
                "provider_mode": "fredapi",
                "macro_regime_label": "normal",
            },
        }
    )

    decision_id = journal.record_decision(
        packet,
        decision={"recommendation": "BUY", "confidence": 82},
    )

    row = journal.get_decision(decision_id)
    packet_payload = json.loads(row["packet_json"])

    assert packet_payload["macro_regime_context"]["provider_status"] == "ok"
    assert packet_payload["macro_regime_context"]["provider_mode"] == "fredapi"


def test_longterm_research_runner_includes_macro_regime_reviewer_context():
    from longterm.research_runner import LongTermResearchRunner

    class FakeClient:
        captured_context = {}

        def call_with_context(self, _task_prompt, context_sections):
            self.captured_context = context_sections
            return '{"recommendation":"PASS","confidence":55}'

    runner = LongTermResearchRunner.__new__(LongTermResearchRunner)
    runner._client = FakeClient()
    runner.book_principles_provider = type("P", (), {"recall": lambda self, _query: ""})()
    runner.active_rules_provider = type("R", (), {"load": lambda self: ""})()
    runner.review_cadence_policy = type(
        "C",
        (),
        {
            "assign": lambda self, _packet: type(
                "Cadence",
                (),
                {
                    "review_cadence": "monthly",
                    "expected_hold_horizon": "multi_year",
                    "reason": "default",
                },
            )()
        },
    )()

    packet = create_research_packet_from_idea(
        {
            "symbol": "AAPL",
            "company_name": "Apple",
            "idea_source": "manual_watchlist",
            "business_summary": "Apple has a durable ecosystem with recurring services revenue.",
            "thesis_summary": "Services, installed base, and pricing power can compound over years.",
            "primary_growth_driver": "Services growth",
            "balance_sheet_assessment": "Cash rich with strong free cash flow.",
            "quality_score": 88,
            "valuation_score": 65,
            "macro_regime_context": {
                "provider_status": "ok",
                "provider_mode": "fredapi",
                "risk_regime": "normal",
                "yield_curve_spread": -0.2,
                "credit_spread": 5.3,
            },
        }
    )

    context = runner._build_context_sections(packet)

    assert "MacroRegimeReviewer" in context["deterministic_reviews"]
    assert "credit_spread_elevated" in context["deterministic_reviews"]


def test_longterm_decision_journal_updates_outcome_vs_benchmark(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    packet = create_research_packet_from_idea(
        {
            "symbol": "NVDA",
            "benchmark_symbol": "FXAIX",
        }
    )
    decision_id = journal.record_decision(
        packet,
        decision={"recommendation": "BUY", "confidence": 75},
        candidate_price=100.0,
        benchmark_price=200.0,
    )

    journal.update_outcome(
        decision_id,
        candidate_price=115.0,
        benchmark_price=210.0,
        notes="One-quarter review",
    )

    row = journal.get_decision(decision_id)

    assert row["candidate_return_pct"] == 15.0
    assert row["benchmark_return_pct"] == 5.0
    assert row["excess_return_pct"] == 10.0
    assert row["outcome_notes"] == "One-quarter review"


def test_longterm_decision_journal_schema_is_created(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    LongTermDecisionJournal(db_path)

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert "longterm_decision_journal" in tables


def test_decision_journal_records_dry_run_action_plan(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    plan = {
        "schema_version": 1,
        "plan_id": "plan-123",
        "mode": "dry_run",
        "status": "ready",
        "intents": [{"symbol": "NVDA", "intent_type": "BUY"}],
    }

    record_id = journal.record_action_plan(plan)
    rows = journal.list_action_plans(limit=5)

    assert record_id == "plan-123"
    assert rows[0]["plan_id"] == "plan-123"
    assert rows[0]["mode"] == "dry_run"
    assert rows[0]["status"] == "ready"
    assert rows[0]["plan_json"]["intents"][0]["symbol"] == "NVDA"


def test_decision_journal_records_and_resolves_deferred_research_items(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    item = {
        "symbol": "TSLA",
        "reason": "incomplete_research_packet",
        "missing_fields": ["company_name", "idea_source", "research_context"],
        "provenance_bucket": "manual",
        "suggested_next_step": "enrich_candidate_before_research",
        "suggested_enrichment_command": "python scripts/run_longterm_discovery.py --candidates path\\to\\candidates.json",
    }

    deferred_id = journal.record_deferred_research_item(item, parent_decision_id="decision-123")
    rows = journal.list_deferred_research_items(limit=5)

    assert rows[0]["deferred_id"] == deferred_id
    assert rows[0]["symbol"] == "TSLA"
    assert rows[0]["status"] == "open"
    assert rows[0]["parent_decision_id"] == "decision-123"
    assert rows[0]["missing_fields"] == ["company_name", "idea_source", "research_context"]
    assert rows[0]["deferred_json"]["provenance_bucket"] == "manual"
    assert rows[0]["priority_score"] > 0

    journal.resolve_deferred_research_item(deferred_id, notes="Enriched from fundamentals cache.")

    assert journal.list_deferred_research_items(limit=5) == []
    resolved = journal.list_deferred_research_items(limit=5, include_resolved=True)[0]
    assert resolved["status"] == "resolved"
    assert resolved["resolution_notes"] == "Enriched from fundamentals cache."


def test_decision_journal_tracks_recommendation_rank_movement(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    profile = PortfolioProfile(benchmark_symbol="FXAIX")

    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "company_name": "Nvidia"}, profile=profile),
        decision={"recommendation": "BUY", "confidence": 92, "suggested_size_pct": 8},
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "AAPL", "company_name": "Apple"}, profile=profile),
        decision={"recommendation": "BUY", "confidence": 80, "suggested_size_pct": 5},
    )
    initial_rows = journal.list_recommendation_table(limit=5)
    snapshot_id = journal.record_recommendation_rank_snapshot(initial_rows)

    journal.record_decision(
        create_research_packet_from_idea({"symbol": "AAPL", "company_name": "Apple"}, profile=profile),
        decision={"recommendation": "BUY", "confidence": 96, "suggested_size_pct": 9},
    )
    moved_rows = journal.list_recommendation_table(limit=5)
    aapl = next(row for row in moved_rows if row["symbol"] == "AAPL")
    nvda = next(row for row in moved_rows if row["symbol"] == "NVDA")

    assert snapshot_id
    assert aapl["rank"] == 1
    assert aapl["previous_rank"] == 2
    assert aapl["rank_movement"] == "up"
    assert nvda["previous_rank"] == 1
    assert nvda["rank_movement"] == "down"


def test_recommendation_table_removes_symbol_when_latest_decision_is_pass(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    profile = PortfolioProfile(benchmark_symbol="FXAIX")

    journal.record_decision(
        create_research_packet_from_idea({"symbol": "MSFT", "company_name": "Microsoft"}, profile=profile),
        decision={"recommendation": "BUY", "confidence": 86, "suggested_size_pct": 6},
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "MSFT", "company_name": "Microsoft"}, profile=profile),
        decision={"recommendation": "PASS", "confidence": 72, "suggested_size_pct": 0},
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "company_name": "Nvidia"}, profile=profile),
        decision={"recommendation": "BUY", "confidence": 90, "suggested_size_pct": 5},
    )

    rows = journal.list_recommendation_table(limit=5)

    assert [row["symbol"] for row in rows] == ["NVDA"]


def test_decision_journal_records_thesis_review_events_with_traceability(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    decision_id = journal.record_decision(
        create_research_packet_from_idea({"symbol": "MSFT", "company_name": "Microsoft"}),
        decision={"recommendation": "BUY", "confidence": 86, "suggested_size_pct": 6},
    )

    review_id = journal.record_thesis_review(
        symbol="MSFT",
        thesis_state="healthy",
        status="reviewed",
        review_notes="Cloud and AI thesis remains intact.",
        evidence=["Azure growth remains durable."],
        decision_id=decision_id,
        review_trigger="manual",
        current_market_value=5200.0,
    )
    reviews = journal.list_thesis_reviews(limit=5)
    latest = journal.latest_thesis_review_by_symbol()

    assert review_id
    assert reviews[0]["review_id"] == review_id
    assert reviews[0]["symbol"] == "MSFT"
    assert reviews[0]["thesis_state"] == "healthy"
    assert reviews[0]["decision_id"] == decision_id
    assert reviews[0]["review_trigger"] == "manual"
    assert reviews[0]["current_market_value"] == 5200.0
    assert reviews[0]["evidence"] == ["Azure growth remains durable."]
    assert latest["MSFT"]["review_id"] == review_id


def test_decision_journal_builds_symbol_feedback_profile_from_repeat_recommendations(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)

    first_id = journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": ["Initial Stock Advisor recommendation."],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 88,
            "suggested_size_pct": 6,
            "key_thesis": "AI data center demand remains durable.",
        },
    )
    second_id = journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": [
                    "New information: Blackwell supply commentary improved.",
                    "New information: management raised data-center margin outlook.",
                ],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 8,
            "key_thesis": "Blackwell ramp improves long-term earnings power.",
        },
    )

    profile = journal.get_symbol_feedback_profile("nvda")

    assert first_id
    assert profile["symbol"] == "NVDA"
    assert profile["company_name"] == "Nvidia"
    assert profile["recommendation_count"] == 2
    assert profile["new_information_count"] == 2
    assert profile["latest_decision_id"] == second_id
    assert profile["latest_recommendation"] == "BUY"
    assert profile["latest_thesis"] == "Blackwell ramp improves long-term earnings power."
    assert profile["latest_confidence"] == 92
    assert profile["latest_suggested_size_pct"] == 8.0
    assert "Blackwell supply commentary improved" in profile["new_information"][0]
    assert profile["thesis_history"][0]["thesis"] == "AI data center demand remains durable."
    assert profile["thesis_history"][1]["thesis"] == "Blackwell ramp improves long-term earnings power."
    assert profile["profile_json"]["schema_version"] == 1

    rebuilt = journal.rebuild_symbol_feedback_profiles()
    rebuilt_again = journal.rebuild_symbol_feedback_profiles()

    assert rebuilt["profiles_rebuilt"] == 1
    assert rebuilt_again["profiles_rebuilt"] == 1
    assert journal.get_symbol_feedback_profile("NVDA")["recommendation_count"] == 2


def test_decision_journal_symbol_feedback_ignores_non_recommendation_rows(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)

    journal.record_decision(
        create_research_packet_from_idea({"symbol": "AAPL", "company_name": "Apple"}),
        decision={"recommendation": "SELL", "confidence": 85, "key_thesis": "Valuation stretched."},
    )

    assert journal.get_symbol_feedback_profile("AAPL") is None
    assert journal.rebuild_symbol_feedback_profiles()["profiles_rebuilt"] == 0


def test_symbol_feedback_profile_applies_paper_preview_feedback_without_schema_change(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "manual",
                "business_summary": "AI accelerator platform.",
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 90,
            "suggested_size_pct": 8,
            "key_thesis": "AI demand durable.",
        },
    )

    result = journal.apply_paper_preview_feedback(
        {
            "NVDA": {
                "paper_preview_status": "blocked",
                "paper_preview_log_id": "log-1",
                "paper_preview_id": "preview-nvda",
                "paper_preview_ready_count": 1,
                "paper_preview_blocked_count": 2,
                "paper_preview_no_order_count": 0,
                "paper_preview_blocked_reasons": ["cash shortfall", "cash shortfall", "benchmark gate"],
            }
        }
    )
    profile = journal.get_symbol_feedback_profile("NVDA")

    assert result == {"profiles_updated": 1, "symbols": ["NVDA"]}
    assert profile["paper_preview_ready_count"] == 1
    assert profile["paper_preview_blocked_count"] == 2
    assert profile["paper_preview_no_order_count"] == 0
    assert profile["latest_paper_preview_status"] == "blocked"
    assert profile["latest_paper_preview_log_id"] == "log-1"
    assert profile["latest_paper_preview_id"] == "preview-nvda"
    assert profile["paper_preview_blocked_reasons"] == ["cash shortfall", "benchmark gate"]

    journal.rebuild_symbol_feedback_profiles()

    rebuilt_profile = journal.get_symbol_feedback_profile("NVDA")
    assert rebuilt_profile["paper_preview_blocked_count"] == 2
    assert rebuilt_profile["paper_preview_blocked_reasons"] == ["cash shortfall", "benchmark gate"]


def test_symbol_feedback_enrichment_includes_paper_preview_context(tmp_path):
    db_path = tmp_path / "longterm_decisions.db"
    journal = LongTermDecisionJournal(db_path)
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "manual",
                "business_summary": "AI accelerator platform.",
            }
        ),
        decision={"recommendation": "BUY", "confidence": 90, "key_thesis": "AI demand durable."},
    )
    journal.apply_paper_preview_feedback(
        {
            "NVDA": {
                "paper_preview_status": "blocked",
                "paper_preview_ready_count": 0,
                "paper_preview_blocked_count": 1,
                "paper_preview_no_order_count": 0,
                "paper_preview_blocked_reasons": ["cash shortfall"],
            }
        }
    )

    enriched = journal.enrich_idea_with_symbol_feedback(
        {
            "symbol": "NVDA",
            "company_name": "Nvidia",
            "idea_source": "manual_followup",
            "business_summary": "AI accelerator platform.",
        }
    )

    assert any(
        "Paper preview feedback: ready=0, blocked=1, no_order=0; latest=blocked." in note
        for note in enriched["source_notes"]
    )
    assert "Paper preview blocked reasons: cash shortfall." in enriched["source_notes"]
