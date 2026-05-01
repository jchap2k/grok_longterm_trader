import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.live_readiness import LiveReadinessChecklist
from longterm.next_actions import (
    NextActionsPlanner,
    build_next_actions_markdown,
    load_evidence_by_symbol,
)
from longterm.portfolio_state import PortfolioState
from longterm.review_templates import ReviewTemplateBuilder
from longterm.scheduler_operating_model import SchedulerOperatingModel
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol, *, confidence=85, size=5, recommendation="BUY"):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "company_name": symbol,
                "idea_source": "manual",
                "thesis_summary": "Durable quality-growth thesis.",
                "business_summary": "Understandable long-term business.",
            }
        ),
        decision={
            "recommendation": recommendation,
            "confidence": confidence,
            "suggested_size_pct": size,
            "key_thesis": "Durable quality-growth thesis.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def test_scheduler_operating_model_defines_safe_default_cadences():
    model = SchedulerOperatingModel.default()

    routines = {routine.key: routine for routine in model.routines}
    markdown = model.to_markdown()
    payload = model.to_dict()

    assert routines["discovery_refresh"].cadence == "weekly"
    assert routines["motley_fool_intake"].cadence == "weekly"
    assert routines["grok_plan_review"].cadence == "as_needed"
    assert routines["next_actions_rebalance"].cadence == "daily"
    assert all(routine.mode == "dry_run" for routine in model.routines)
    assert "No broker execution" in markdown
    assert payload["schema_version"] == 1


def test_review_template_includes_rules_rubric_and_packet_context():
    packet = create_research_packet_from_idea(
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "idea_source": "manual",
            "business_summary": "Cloud and productivity platform.",
            "thesis_summary": "AI and cloud durability.",
            "balance_sheet_assessment": "Net cash balance sheet.",
            "invalidation_conditions": ["Cloud growth materially slows"],
        }
    )

    checklist = ReviewTemplateBuilder().build(
        packet,
        review_status={
            "thesis_state": "stale",
            "review_reason": "Review cadence elapsed.",
        },
        decision_id="decision-123",
        evidence=["Azure growth remains durable."],
    )

    assert checklist.symbol == "MSFT"
    assert "Quality durability" in checklist.to_markdown()
    assert "Valuation discipline" in checklist.to_markdown()
    assert "Cloud growth materially slows" in checklist.to_markdown()
    assert "decision-123" in checklist.to_markdown()
    assert checklist.rules_excerpt


def test_evidence_loader_accepts_json_and_rejects_protected_action_hints(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "AAPL": {
                    "evidence": ["Services revenue still growing."],
                    "decision_id": "decision-aapl",
                },
                "FXAIX": {
                    "evidence": ["Market volatility increased."],
                    "action_hint": "sell",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="protected"):
        load_evidence_by_symbol(evidence_path, protected_symbols=["FXAIX"])

    evidence_path.write_text(
        json.dumps({"AAPL": ["Services revenue still growing."]}),
        encoding="utf-8",
    )

    assert load_evidence_by_symbol(evidence_path)["AAPL"] == [
        "Services revenue still growing."
    ]


def test_next_actions_summary_prioritizes_urgent_review_holding(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA", confidence=95, size=8)
    _record_decision(journal, "AAPL", confidence=70, size=4)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=5000,
        holdings=[{"symbol": "AAPL", "market_value": 4500}],
        protected_symbols=["FXAIX"],
    )

    actions = NextActionsPlanner(
        review_status_by_symbol={
            "AAPL": {
                "thesis_state": "broken",
                "review_due": True,
                "review_reason": "Latest recorded thesis review marked the thesis broken.",
            }
        }
    ).plan(journal, profile=profile, portfolio_state=state)
    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        review_status_by_symbol={
            "AAPL": {
                "thesis_state": "broken",
                "review_due": True,
                "review_reason": "Latest recorded thesis review marked the thesis broken.",
            }
        },
    )

    assert actions[0].category == "urgent_review_holding"
    assert actions[0].symbol == "AAPL"
    assert "## Category Summary" in markdown
    assert "| urgent_review_holding | 1 |" in markdown


def test_live_readiness_checklist_blocks_live_mode_until_all_gates_pass():
    checklist = LiveReadinessChecklist.default()

    result = checklist.evaluate(
        {
            "dry_run_cycles": 10,
            "benchmark_proven": False,
            "paper_trading_verified": False,
            "manual_approval": False,
        }
    )

    assert result.ready is False
    assert "benchmark_proven" in result.unmet_gate_keys
    assert "broker_capability_match" in result.unmet_gate_keys
    assert "explicit_live_mode_config" in result.unmet_gate_keys
    assert "No live broker execution is enabled" in result.to_markdown()


def test_live_readiness_requires_live_broker_capabilities_to_match_paper_sizing():
    checklist = LiveReadinessChecklist.default()

    result = checklist.evaluate(
        {
            "dry_run_cycles": 30,
            "benchmark_proven": True,
            "paper_trading_verified": True,
            "protected_symbol_enforced": True,
            "manual_approval": True,
            "kill_switch": True,
            "audit_logs": True,
            "broker_read_reconciliation": True,
            "explicit_live_mode_config": True,
            "secrets_not_committed": True,
            "broker_capability_match": False,
        }
    )

    assert result.ready is False
    assert "broker_capability_match" in result.unmet_gate_keys
