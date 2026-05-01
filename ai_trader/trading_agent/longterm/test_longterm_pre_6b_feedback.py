import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.next_actions import build_next_actions_markdown
from longterm.paper_execution_eligibility import (
    PaperExecutionEligibilityBuilder,
    build_paper_execution_eligibility_markdown,
)
from longterm.paper_execution_eligibility_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol="NVDA", recommendation="BUY"):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "company_name": symbol,
                "idea_source": "unit_test",
                "business_summary": "Durable business.",
                "benchmark_symbol": "FXAIX",
            }
        ),
        decision={
            "recommendation": recommendation,
            "confidence": 92,
            "suggested_size_pct": 8,
            "key_thesis": "Strong long-term setup.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def _action_plan(decision_id, *, symbol="NVDA", intent_type="BUY", allowed=True):
    return {
        "schema_version": 1,
        "plan_id": "plan-1",
        "mode": "dry_run",
        "status": "ready",
        "benchmark_gate_reason": "Benchmark gate allows new buys.",
        "intents": [
            {
                "symbol": symbol,
                "intent_type": intent_type,
                "order_intent": "BUY",
                "trade_value": 1000,
                "target_value": 1000,
                "allowed": allowed,
                "reason": "Candidate is ready.",
                "decision_id": decision_id,
            }
        ],
    }


def _record_preview(ledger, decision_id, *, timestamp=None, allowed=True, side="buy", symbol="NVDA"):
    preview_log_id = ledger.record_preview(
        {
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "previews": [
                {
                    "preview_id": f"preview-{symbol.lower()}",
                    "plan_id": "plan-1",
                    "decision_id": decision_id,
                    "symbol": symbol,
                    "side": side,
                    "order_type": "market_notional_preview" if side != "none" else "no_order",
                    "notional": 1000 if side != "none" else 0,
                    "allowed": allowed,
                    "blocked_reasons": [] if allowed else ["cash shortfall"],
                }
            ],
        },
        timestamp=timestamp,
    )
    return preview_log_id


def test_execution_events_require_decision_id_and_preserve_traceability(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")

    try:
        ledger.record_execution_event({"symbol": "NVDA", "status": "submit_blocked"})
    except ValueError as exc:
        assert "decision_id" in str(exc)
    else:
        raise AssertionError("record_execution_event should require decision_id")

    event_id = ledger.record_execution_event(
        {
            "decision_id": "decision-1",
            "preview_log_id": "log-1",
            "preview_id": "preview-nvda",
            "plan_id": "plan-1",
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "submit_blocked",
            "error": "paper execution disabled",
        }
    )
    rows = ledger.list_execution_events(limit=5)

    assert rows[0]["event_id"] == event_id
    assert rows[0]["decision_id"] == "decision-1"
    assert rows[0]["status"] == "submit_blocked"
    assert rows[0]["event_json"]["error"] == "paper execution disabled"


def test_paper_execution_eligibility_requires_fresh_ready_preview_and_explicit_gate(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = "decision-1"
    _record_preview(ledger, decision_id)
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    disabled = PaperExecutionEligibilityBuilder(
        now_func=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        max_preview_age_hours=48,
        paper_execution_enabled=False,
    ).build(_action_plan(decision_id), ledger=ledger, profile=profile, portfolio_state=state)

    assert disabled["eligible_count"] == 0
    assert disabled["items"][0]["status"] == "execution_disabled"
    assert "paper execution disabled" in disabled["items"][0]["blocked_reasons"]

    enabled = PaperExecutionEligibilityBuilder(
        now_func=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        max_preview_age_hours=48,
        paper_execution_enabled=True,
    ).build(_action_plan(decision_id), ledger=ledger, profile=profile, portfolio_state=state)

    assert enabled["eligible_count"] == 1
    assert enabled["items"][0]["eligible"] is True
    assert enabled["items"][0]["status"] == "eligible"
    assert enabled["items"][0]["preview_is_fresh"] is True


def test_paper_execution_eligibility_blocks_stale_blocked_no_order_and_protected(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    stale_time = datetime(2026, 4, 25, tzinfo=UTC).isoformat()
    _record_preview(ledger, "stale-decision", timestamp=stale_time)
    _record_preview(ledger, "blocked-decision", allowed=False, symbol="MSFT")
    _record_preview(ledger, "no-order-decision", side="none", symbol="AAPL")
    _record_preview(ledger, "protected-decision", symbol="FXAIX")
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-1",
        "intents": [
            {"symbol": "NVDA", "intent_type": "BUY", "allowed": True, "decision_id": "stale-decision"},
            {"symbol": "MSFT", "intent_type": "BUY", "allowed": True, "decision_id": "blocked-decision"},
            {"symbol": "AAPL", "intent_type": "BUY", "allowed": True, "decision_id": "no-order-decision"},
            {"symbol": "FXAIX", "intent_type": "BUY", "allowed": True, "decision_id": "protected-decision"},
        ],
    }

    result = PaperExecutionEligibilityBuilder(
        now_func=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        max_preview_age_hours=48,
        paper_execution_enabled=True,
    ).build(plan, ledger=ledger, profile=profile, portfolio_state=state)
    by_symbol = {item["symbol"]: item for item in result["items"]}

    assert by_symbol["NVDA"]["status"] == "preview_stale"
    assert by_symbol["MSFT"]["status"] == "preview_blocked"
    assert by_symbol["AAPL"]["status"] == "preview_no_order"
    assert by_symbol["FXAIX"]["status"] == "protected_symbol"
    assert result["eligible_count"] == 0


def test_paper_execution_eligibility_blocks_missing_preview_without_valid_until_error(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    result = PaperExecutionEligibilityBuilder(
        now_func=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        paper_execution_enabled=True,
    ).build(_action_plan("missing-preview"), ledger=ledger, profile=profile, portfolio_state=state)

    assert result["items"][0]["status"] == "preview_missing"
    assert result["items"][0]["valid_until"] == ""


def test_reconciliation_feedback_and_outcome_refresh_feed_symbol_profile(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)

    updated = journal.apply_paper_reconciliation_feedback(
        {
            "rows": [
                {
                    "symbol": "NVDA",
                    "status": "mismatch",
                    "mismatch_count": 2,
                    "notes": ["position value below target"],
                }
            ]
        }
    )
    refreshed = journal.refresh_outcomes_from_price_map(
        {
            "NVDA": {"candidate_price": 120, "benchmark_price": 110},
        }
    )
    profile = journal.get_symbol_feedback_profile("NVDA")
    enriched = journal.enrich_idea_with_symbol_feedback(
        {"symbol": "NVDA", "company_name": "Nvidia", "idea_source": "manual", "business_summary": "AI platform."}
    )

    assert updated["profiles_updated"] == 1
    assert refreshed["decisions_updated"] == 1
    assert refreshed["updated_decision_ids"] == [decision_id]
    assert profile["latest_reconciliation_status"] == "mismatch"
    assert profile["paper_reconciliation_mismatch_count"] == 2
    assert any("Paper reconciliation feedback: latest=mismatch, mismatches=2." in note for note in enriched["source_notes"])
    assert any("position value below target" in note for note in enriched["source_notes"])
    row = journal.get_decision(decision_id)
    assert row["candidate_return_pct"] == 20.0
    assert row["benchmark_return_pct"] == 10.0


def test_next_actions_can_surface_paper_execution_eligibility_feedback(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    eligibility = {
        "items": [
            {
                "decision_id": decision_id,
                "symbol": "NVDA",
                "status": "preview_stale",
                "action": "REFRESH_PREVIEW",
                "blocked_reasons": ["preview is stale"],
            }
        ]
    }

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        paper_execution_eligibility=eligibility,
    )

    assert "paper_execution_preview_stale" in markdown
    assert "REFRESH_PREVIEW" in markdown
    assert "preview is stale" in markdown
    assert "## Paper Execution Eligibility" in build_paper_execution_eligibility_markdown(eligibility)


def test_paper_execution_eligibility_cli_outputs_json(tmp_path, capsys):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = "decision-1"
    _record_preview(ledger, decision_id)
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--ledger-db",
            str(ledger.db_path),
            "--paper-execution-enabled",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["eligible_count"] == 1
    assert payload["items"][0]["decision_id"] == decision_id
