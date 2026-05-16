"""Tests for the Thesis Re-underwriting Engine (MVP)."""

import tempfile
from pathlib import Path

import pytest

from longterm.decision_journal import LongTermDecisionJournal
from longterm.reunderwriting_engine import (
    is_reunderwriting_due,
    recommend_delta_enrichment_tier_for_holding,
    run_reunderwriting,
)
from research.research_packet import ResearchPacket


def test_engine_dry_run_and_record_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "reunderwrite_test.db"
        journal = LongTermDecisionJournal(db_path)

        packet = ResearchPacket(
            symbol="RETEST",
            company_name="Reunderwrite Test Co",
            idea_source="test",
            benchmark_symbol="SPY",
            review_cadence="quarterly",
        )
        parent_id = journal.record_decision(
            packet,
            decision={"recommendation": "BUY", "confidence": 80, "key_thesis": "Original durable moat thesis"},
            candidate_price=100.0,
            benchmark_price=500.0,
        )

        # Dry run should never write
        dry = run_reunderwriting(journal, symbol="RETEST", dry_run=True, force=True, notes="test dry")
        assert dry["success"] is True
        assert dry["dry_run"] is True
        assert dry["action_taken"] == "dry_run"
        assert "would_record" in dry

        # Real record with manual durability
        real = run_reunderwriting(
            journal, symbol="RETEST", dry_run=False, force=True, manual_durability="weakening", notes="margin pressure observed"
        )
        assert real["success"] is True
        assert real["action_taken"] == "recorded"
        assert "child_decision_id" in real

        # Parent should now have denormalized durability
        parent = journal.get_decision(parent_id)
        assert parent.get("thesis_durability") == "weakening"
        assert parent.get("last_reunderwritten_date") is not None

        # Lineage should show one child
        lineage = journal.get_reunderwriting_lineage(parent_id)
        assert lineage["total_reunderwritings"] == 1
        assert lineage["latest_durability"] == "weakening"


def test_is_reunderwriting_due_logic():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "due_test.db"
        journal = LongTermDecisionJournal(db_path)

        packet = ResearchPacket(symbol="DUE", company_name="Due Co", idea_source="test", benchmark_symbol="SPY")
        journal.record_decision(packet, decision={"recommendation": "BUY"}, candidate_price=10.0, benchmark_price=100.0)

        due_info = is_reunderwriting_due(journal, "DUE")
        assert due_info["due"] is False  # healthy new position

        # Force a weakening state via manual re-underwrite
        run_reunderwriting(journal, symbol="DUE", dry_run=False, force=True, manual_durability="broken")

        due_info2 = is_reunderwriting_due(journal, "DUE")
        assert due_info2["due"] is True
        assert due_info2["current_durability"] == "broken"


def test_delta_enrichment_tier_recommendation_for_holdings():
    rec = recommend_delta_enrichment_tier_for_holding(
        symbol="HOLDING",
        current_durability="stable",
    )
    assert rec["is_existing_holding"] is True
    # For stable holdings we expect light or deterministic (never heavy by default)
    assert rec["tier"] in (0, 1, 2)


def test_engine_with_weakening_evidence_moves_durability():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "weak_test.db"
        journal = LongTermDecisionJournal(db_path)

        packet = ResearchPacket(
            symbol="WEAK",
            company_name="Weakening Co",
            idea_source="test",
            benchmark_symbol="SPY",
            invalidation_conditions=["margin collapse", "guidance cut"],
        )
        journal.record_decision(packet, decision={"recommendation": "BUY"}, candidate_price=50.0, benchmark_price=500.0)

        evidence = ["Q2 showed unexpected margin pressure and guidance cut on key product line"]

        res = run_reunderwriting(
            journal, symbol="WEAK", dry_run=True, force=True, fresh_evidence=evidence
        )
        assert res["success"]
        # ThesisMonitor should detect the weakening language / invalidation match
        assert res["durability"] in ("weakening", "broken")
        assert res.get("thesis_state_from_monitor") in ("broken", "weakening") or "invalidation" in res.get("delta_summary", "").lower()
