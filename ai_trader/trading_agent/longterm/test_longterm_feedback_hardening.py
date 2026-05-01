import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.feedback_refresh import (
    build_feedback_markdown,
    outcome_freshness,
    run_feedback_refresh,
)
from longterm.feedback_refresh_cli import build_parser, run_cli
from longterm.paper_execution_eligibility import PaperExecutionEligibilityBuilder
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol="NVDA", *, candidate_price=100, benchmark_price=100):
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
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 8,
            "key_thesis": "Strong long-term setup.",
        },
        candidate_price=candidate_price,
        benchmark_price=benchmark_price,
    )


def _action_plan(decision_id, *, symbol="NVDA"):
    return {
        "plan_id": "plan-1",
        "mode": "dry_run",
        "intents": [
            {
                "symbol": symbol,
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 1000,
                "target_value": 1000,
                "allowed": True,
                "decision_id": decision_id,
            }
        ],
    }


def _record_preview(ledger, decision_id, *, symbol="NVDA", allowed=True):
    return ledger.record_preview(
        {
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "previews": [
                {
                    "preview_id": f"preview-{symbol.lower()}",
                    "plan_id": "plan-1",
                    "decision_id": decision_id,
                    "trade_id": "trade-1",
                    "symbol": symbol,
                    "side": "buy",
                    "order_type": "market_notional_preview",
                    "notional": 1000,
                    "allowed": allowed,
                    "blocked_reasons": [] if allowed else ["cash shortfall"],
                }
            ],
        },
        timestamp=datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
    )


def test_record_eligibility_events_persists_full_traceability(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    eligibility = {
        "plan_id": "plan-1",
        "items": [
            {
                "decision_id": "decision-123456789",
                "symbol": "NVDA",
                "status": "preview_stale",
                "action": "REFRESH_PREVIEW",
                "preview_id": "preview-nvda",
                "preview_log_id": "log-1",
                "trade_id": "trade-1",
                "blocked_reasons": ["preview is stale"],
            }
        ],
    }

    result = ledger.record_eligibility_events(eligibility)
    rows = ledger.list_execution_events(decision_id="decision-123456789")

    assert result["events_recorded"] == 1
    assert rows[0]["status"] == "eligibility_blocked"
    assert rows[0]["decision_id"] == "decision-123456789"
    assert rows[0]["preview_id"] == "preview-nvda"
    assert rows[0]["trade_id"] == "trade-1"
    assert rows[0]["event_json"]["journal_short_id"] == "decision"
    assert rows[0]["event_json"]["requires_revalidation"] is True
    assert rows[0]["event_json"]["eligibility_item"]["blocked_reasons"] == ["preview is stale"]


def test_record_eligibility_events_is_idempotent_for_same_preview_status(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    eligibility = {
        "plan_id": "plan-1",
        "items": [
            {
                "decision_id": "decision-1",
                "symbol": "NVDA",
                "status": "eligible",
                "action": "PAPER_SUBMIT_READY",
                "preview_id": "preview-nvda",
                "preview_log_id": "log-1",
                "blocked_reasons": [],
            }
        ],
    }

    first = ledger.record_eligibility_events(eligibility)
    second = ledger.record_eligibility_events(eligibility)
    rows = ledger.list_execution_events(decision_id="decision-1")

    assert first["events_recorded"] == 1
    assert second["events_recorded"] == 0
    assert len(rows) == 1
    assert rows[0]["status"] == "eligibility_ready"


def test_outcome_freshness_marks_never_refreshed_and_stale_without_mutating_rows(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    never_id = _record_decision(journal, "NVDA")
    stale_id = _record_decision(journal, "AAPL")
    journal.update_outcome(stale_id, candidate_price=101, benchmark_price=100)
    old_timestamp = (datetime(2026, 3, 1, tzinfo=UTC)).isoformat()
    conn = __import__("sqlite3").connect(journal.db_path)
    conn.execute(
        "UPDATE longterm_decision_journal SET outcome_updated_at = ? WHERE decision_id = ?",
        (old_timestamp, stale_id),
    )
    conn.commit()
    conn.close()

    result = outcome_freshness(
        journal,
        stale_after_days=30,
        today=datetime(2026, 5, 1, tzinfo=UTC),
    )

    by_id = {row["decision_id"]: row for row in result["items"]}
    assert by_id[never_id]["freshness_state"] == "never_refreshed"
    assert by_id[stale_id]["freshness_state"] == "stale"
    assert result["counts"]["never_refreshed"] == 1
    assert result["counts"]["stale"] == 1
    assert journal.get_decision(never_id)["outcome_updated_at"] is None


def test_feedback_refresh_runs_full_dry_run_loop_and_keeps_tuning_analysis_only(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _record_preview(ledger, decision_id, allowed=False)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    eligibility = PaperExecutionEligibilityBuilder(
        now_func=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        paper_execution_enabled=True,
    ).build(_action_plan(decision_id), ledger=ledger, profile=profile, portfolio_state=state)
    before_rank = journal.list_recommendation_table(limit=5)[0]["ranking_score"]
    lessons_path = tmp_path / "lessons.json"
    lessons_path.write_text(json.dumps([{"symbol": "NVDA", "lesson": "Repeated cash shortfall."}]), encoding="utf-8")

    result = run_feedback_refresh(
        journal=journal,
        paper_ledger=ledger,
        profile=profile,
        portfolio_state=state,
        action_plan=_action_plan(decision_id),
        eligibility_payload=eligibility,
        record_eligibility_events=True,
        reconciliation={"rows": [{"symbol": "NVDA", "status": "mismatch", "mismatch_count": 1}]},
        outcome_price_map={"NVDA": {"candidate_price": 120, "benchmark_price": 110}},
        lessons=[{"symbol": "NVDA", "lesson": "Repeated cash shortfall."}],
        today=datetime(2026, 5, 1, tzinfo=UTC),
    )
    after_rank = journal.list_recommendation_table(limit=5)[0]["ranking_score"]
    events = ledger.list_execution_events(decision_id=decision_id)

    assert result["mode"] == "dry_run_feedback_refresh"
    assert result["order_submission_enabled"] is False
    assert result["profile_rebuild"]["profiles_rebuilt"] == 1
    assert result["paper_preview_feedback"]["profiles_updated"] == 1
    assert result["reconciliation_feedback"]["profiles_updated"] == 1
    assert result["outcome_refresh"]["decisions_updated"] == 1
    assert result["eligibility_events"]["events_recorded"] == 1
    assert result["benchmark_guard"]["reason"]
    assert result["review_status_counts"]
    assert result["feedback_tuning_inputs"]["analysis_only"] is True
    assert result["feedback_tuning_inputs"]["active_rules_reference"]["sha256"]
    assert result["feedback_tuning_inputs"]["lessons_count"] == 1
    assert before_rank == after_rank
    assert events[0]["status"] == "eligibility_blocked"
    assert "cash shortfall" in build_feedback_markdown(result)


def test_feedback_refresh_blocks_fxaix_eligibility_and_reports_guard(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal, "FXAIX")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _record_preview(ledger, decision_id, symbol="FXAIX")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    result = run_feedback_refresh(
        journal=journal,
        paper_ledger=ledger,
        profile=profile,
        portfolio_state=state,
        action_plan=_action_plan(decision_id, symbol="FXAIX"),
        record_eligibility_events=True,
        today=datetime(2026, 5, 1, tzinfo=UTC),
    )

    item = result["eligibility"]["items"][0]
    assert item["status"] == "protected_symbol"
    assert item["eligible"] is False
    assert result["eligibility_events"]["events_recorded"] == 1
    assert "protected" in ledger.list_execution_events(decision_id=decision_id)[0]["event_json"]["blocked_reasons"][0]


def test_feedback_refresh_partial_price_map_marks_uncovered_symbols_never_refreshed(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA")
    _record_decision(journal, "AAPL")

    result = run_feedback_refresh(
        journal=journal,
        outcome_price_map={"NVDA": {"candidate_price": 120, "benchmark_price": 110}},
        today=datetime(2026, 5, 1, tzinfo=UTC),
    )
    freshness_by_symbol = {
        row["symbol"]: row["freshness_state"]
        for row in result["outcome_freshness"]["items"]
    }

    assert freshness_by_symbol["NVDA"] == "fresh"
    assert freshness_by_symbol["AAPL"] == "never_refreshed"


def test_feedback_refresh_treats_threshold_boundary_as_fresh(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    journal.update_outcome(decision_id, candidate_price=101, benchmark_price=100)
    timestamp = datetime(2026, 4, 1, tzinfo=UTC).isoformat()
    conn = __import__("sqlite3").connect(journal.db_path)
    conn.execute(
        "UPDATE longterm_decision_journal SET outcome_updated_at = ? WHERE decision_id = ?",
        (timestamp, decision_id),
    )
    conn.commit()
    conn.close()

    result = outcome_freshness(
        journal,
        stale_after_days=30,
        today=datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert result["items"][0]["days_since_outcome_update"] == 30
    assert result["items"][0]["freshness_state"] == "fresh"


def test_feedback_refresh_malformed_lessons_file_degrades_with_warning(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal)
    lessons_path = tmp_path / "bad-lessons.json"
    lessons_path.write_text("{not json", encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--lessons-file",
            str(lessons_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"]
    assert payload["feedback_tuning_inputs"]["lessons_count"] == 0


def test_feedback_refresh_record_events_requires_paper_ledger(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal)
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--record-eligibility-events",
            "--json",
        ]
    )

    try:
        run_cli(args)
    except ValueError as exc:
        assert "--paper-ledger-db" in str(exc)
    else:
        raise AssertionError("recording eligibility events should require --paper-ledger-db")


def test_feedback_refresh_cli_outputs_json(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    _record_preview(ledger, decision_id)
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    price_map_path = tmp_path / "prices.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"], "tradable_capital": 34000}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    price_map_path.write_text(json.dumps({"NVDA": {"candidate_price": 110, "benchmark_price": 105}}), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--paper-ledger-db",
            str(ledger.db_path),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--outcome-price-map",
            str(price_map_path),
            "--record-eligibility-events",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run_feedback_refresh"
    assert payload["eligibility"]["eligible_count"] == 0
    assert payload["eligibility"]["items"][0]["status"] == "execution_disabled"
