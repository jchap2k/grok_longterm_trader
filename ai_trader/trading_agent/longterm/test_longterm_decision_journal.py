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
