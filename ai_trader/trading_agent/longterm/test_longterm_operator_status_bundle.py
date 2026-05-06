import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.operator_status_bundle import build_operator_status_bundle, build_operator_status_markdown
from longterm.operator_status_bundle_cli import build_parser, run_cli
from longterm.path_utils import write_json_artifact
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from research.intake import create_research_packet_from_idea


def _record_decision(journal):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "unit_test",
                "business_summary": "AI platform.",
                "benchmark_symbol": "FXAIX",
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 5,
            "key_thesis": "AI platform earnings compounder.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def test_operator_status_bundle_combines_lifecycle_readiness_and_position_report(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_execution_event(
        {
            "decision_id": decision_id,
            "preview_id": "preview-nvda",
            "preview_log_id": "preview-log-1",
            "plan_id": "plan-1",
            "broker_order_id": "broker-order-1",
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_price": 100,
            "benchmark_price_at_fill": 100,
            "paper_mode": True,
            "live_mode": False,
        }
    )
    portfolio = PortfolioState(cash=5000, holdings=[{"symbol": "NVDA", "market_value": 4200}])
    action_plan = {
        "plan_id": "plan-1",
        "suppressed_reasons": ["taxable_broad_parking_suppressed"],
        "intents": [
            {
                "symbol": "NVDA",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "decision_id": decision_id,
                "trade_value": 1000,
                "allowed": True,
                "promotion_review": {
                    "symbol": "NVDA",
                    "promotion_decision": "ACTIONABLE_BUY",
                    "followups": [],
                    "blockers": [],
                },
            },
            {
                "symbol": "VEEV",
                "intent_type": "REVIEW",
                "order_intent": "NONE",
                "decision_id": "decision-veev",
                "trade_value": 0,
                "allowed": True,
                "promotion_review": {
                    "symbol": "VEEV",
                    "promotion_decision": "WATCHLIST_PENDING_EVIDENCE",
                    "followups": ["missing_earnings_article"],
                    "blockers": [],
                },
            }
        ],
    }

    bundle = build_operator_status_bundle(
        journal,
        portfolio_state=portfolio,
        paper_ledger=ledger,
        action_plan=action_plan,
        price_map={"NVDA": 120, "FXAIX": 110},
        feedback_summary={"order_submission_enabled": False, "benchmark_guard": {"should_pause_new_buys": False}},
    )

    assert bundle["mode"] == "operator_status_bundle"
    assert bundle["order_submission_enabled"] is False
    assert bundle["paper_lifecycle"]["state_counts"]["outcome_evaluated"] == 1
    assert bundle["buy_promotion_summary"]["counts"]["ACTIONABLE_BUY"] == 1
    assert bundle["buy_promotion_summary"]["counts"]["WATCHLIST_PENDING_EVIDENCE"] == 1
    assert bundle["buy_promotion_summary"]["items"][1]["symbol"] == "VEEV"
    assert bundle["account_action_plan_summary"]["suppressed_reasons"] == ["taxable_broad_parking_suppressed"]
    assert bundle["account_action_plan_summary"]["suppressed_count"] == 1
    assert bundle["scheduler_readiness"]["ready_for_scheduler_paper_submit"] is False
    assert "Paper outcome vs FXAIX: 10.0%" in bundle["position_report_markdown"]
    markdown = build_operator_status_markdown(bundle)
    assert "# Long-Term Operator Status Bundle" in markdown
    assert "## Buy Promotion" in markdown
    assert "| VEEV | WATCHLIST_PENDING_EVIDENCE | missing_earnings_article |  |" in markdown
    assert "## Account Plan Suppressions" in markdown
    assert "| Taxable Broad Parking Suppressed | taxable_broad_parking_suppressed |" in markdown
    assert "## Scheduler Readiness" in markdown


def test_operator_status_bundle_surfaces_monday_artifact_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    monday_check = {
        "mode": "paper_monday_operator_check",
        "ready_for_review": False,
        "blocker_count": 2,
        "blockers": ["runbook_check_not_ready", "submit_command_revealed"],
        "workflow_smoke_ready": True,
        "paper_smoke_ready": True,
        "runbook_check_ready": False,
        "workflow_preview_allowed_count": 1,
        "workflow_execution_ready_count": 1,
        "workflow_execution_excluded_count": 1,
        "workflow_promotion_blocked_count": 0,
        "paper_smoke_promotion_blocked_count": 0,
        "submit_command_revealed": True,
        "account_clean": True,
        "status_refresh_error_count": 0,
    }

    bundle = build_operator_status_bundle(journal, monday_operator_check=monday_check)

    assert bundle["monday_operator_check_summary"]["ready_for_review"] is False
    assert bundle["monday_operator_check_summary"]["blocker_count"] == 2
    assert bundle["monday_operator_check_summary"]["blockers"] == [
        "runbook_check_not_ready",
        "submit_command_revealed",
    ]
    markdown = build_operator_status_markdown(bundle)
    assert "## Monday Paper Artifacts" in markdown
    assert "- Ready for review: no" in markdown
    assert "- Blockers: 2" in markdown
    assert "- runbook_check_not_ready" in markdown
    assert "- submit_command_revealed" in markdown


def test_operator_status_bundle_surfaces_live_readiness_summary(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    live_readiness = {
        "mode": "live_readiness_bundle",
        "ready": False,
        "unmet_gate_keys": ["paper_trading_verified", "manual_approval_recorded"],
        "observed": {
            "paper_trading_verified": False,
            "broker_capability_match": True,
            "paper_smoke_ready": True,
        },
        "paper_smoke_safety": {
            "schema_ok": True,
            "promotion_blocked_count": 0,
        },
    }

    bundle = build_operator_status_bundle(journal, live_readiness_bundle=live_readiness)

    assert bundle["live_readiness_summary"]["ready"] is False
    assert bundle["live_readiness_summary"]["unmet_gate_count"] == 2
    assert bundle["live_readiness_summary"]["unmet_gate_keys"] == [
        "paper_trading_verified",
        "manual_approval_recorded",
    ]
    assert bundle["live_readiness_summary"]["paper_smoke_schema_ok"] is True
    markdown = build_operator_status_markdown(bundle)
    assert "## Live Readiness Evidence" in markdown
    assert "- Ready: no" in markdown
    assert "- paper_trading_verified" in markdown
    assert "- manual_approval_recorded" in markdown


def test_operator_status_bundle_surfaces_status_refresh_summary(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    status_refresh = {
        "mode": "paper_order_status_refresh",
        "submitted_order_count": 2,
        "refreshed_count": 2,
        "events_recorded": 1,
        "skipped_count": 1,
        "error_count": 1,
        "status_counts": {"filled": 1, "status_refresh_error": 1},
    }

    bundle = build_operator_status_bundle(journal, status_refresh=status_refresh)

    assert bundle["status_refresh_summary"]["submitted_order_count"] == 2
    assert bundle["status_refresh_summary"]["error_count"] == 1
    assert bundle["status_refresh_summary"]["status_counts"]["filled"] == 1
    markdown = build_operator_status_markdown(bundle)
    assert "## Paper Status Refresh" in markdown
    assert "- Submitted orders checked: 2" in markdown
    assert "- Errors: 1" in markdown
    assert "| filled | 1 |" in markdown


def test_operator_status_bundle_adds_agent_next_step_rollup(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ready_bundle = build_operator_status_bundle(
        journal,
        monday_operator_check={
            "ready_for_review": True,
            "blocker_count": 0,
            "submit_command_revealed": False,
        },
        status_refresh={
            "submitted_order_count": 0,
            "error_count": 0,
            "status_counts": {},
        },
    )
    blocked_bundle = build_operator_status_bundle(
        journal,
        monday_operator_check={
            "ready_for_review": False,
            "blocker_count": 1,
            "blockers": ["paper_account_not_clean"],
            "submit_command_revealed": False,
        },
        status_refresh={
            "submitted_order_count": 0,
            "error_count": 0,
            "status_counts": {},
        },
    )
    status_error_bundle = build_operator_status_bundle(
        journal,
        monday_operator_check={
            "ready_for_review": True,
            "blocker_count": 0,
            "submit_command_revealed": False,
        },
        status_refresh={
            "submitted_order_count": 1,
            "error_count": 1,
            "status_counts": {"status_refresh_error": 1},
        },
    )
    revealed_bundle = build_operator_status_bundle(
        journal,
        monday_operator_check={
            "ready_for_review": True,
            "blocker_count": 0,
            "blockers": [],
            "submit_command_revealed": True,
            "manual_submit_review_required": True,
        },
        status_refresh={
            "submitted_order_count": 0,
            "error_count": 0,
            "status_counts": {},
        },
    )

    assert ready_bundle["agent_next_step"]["state"] == "ready_to_reveal_submit_command"
    assert ready_bundle["agent_next_step"]["order_submission_enabled"] is False
    assert blocked_bundle["agent_next_step"]["state"] == "blocked_preflight"
    assert "paper_account_not_clean" in blocked_bundle["agent_next_step"]["blockers"]
    assert status_error_bundle["agent_next_step"]["state"] == "review_status_errors"
    assert revealed_bundle["agent_next_step"]["state"] == "submit_command_revealed_review_required"
    assert revealed_bundle["agent_next_step"]["order_submission_enabled"] is False
    markdown = build_operator_status_markdown(ready_bundle)
    assert "## Agent Next Step" in markdown
    assert "- State: `ready_to_reveal_submit_command`" in markdown


def test_operator_status_bundle_surfaces_scheduler_policy_next_safe_action(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    scheduler_policy = {
        "mode": "pipeline_scheduler_policy",
        "recommended_mode": "panic_regime_reassessment",
        "urgency": "high",
        "reasons": ["vix_panic_threshold"],
        "warnings": ["active_rules_changed"],
        "affected_symbols": ["ADBE", "MSFT"],
        "next_safe_action": "rerun_market_regime_and_next_actions_no_submit",
        "order_submission_enabled": False,
    }

    bundle = build_operator_status_bundle(journal, scheduler_policy=scheduler_policy)
    markdown = build_operator_status_markdown(bundle)

    assert bundle["scheduler_policy_summary"]["recommended_mode"] == "panic_regime_reassessment"
    assert bundle["scheduler_policy_summary"]["affected_symbols"] == ["ADBE", "MSFT"]
    assert bundle["agent_next_step"]["state"] == "scheduler_policy_panic_regime_reassessment"
    assert "rerun market regime and next actions no submit" in bundle["agent_next_step"]["message"]
    assert bundle["agent_next_step"]["order_submission_enabled"] is False
    assert "## Scheduler Policy" in markdown
    assert "- Recommended mode: `panic_regime_reassessment`" in markdown
    assert "- Next safe action: rerun_market_regime_and_next_actions_no_submit" in markdown
    assert "- active_rules_changed" in markdown


def test_operator_status_bundle_surfaces_scheduler_resource_controls(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    scheduler_policy = {
        "mode": "pipeline_scheduler_policy",
        "recommended_mode": "resource_control_review",
        "urgency": "high",
        "reasons": ["missing_research_max_pass_count"],
        "warnings": ["paid_research_provider_planned"],
        "blockers": ["scheduler_resource_controls_unbounded"],
        "resource_controls": {
            "provider_mode": "perplexity",
            "paid_provider_enabled": True,
            "research_max_pass_count": 25,
            "generated_committee_max_batches": 1,
            "bounded": True,
        },
        "next_safe_action": "review_scheduler_resource_controls_before_running_paid_work",
        "order_submission_enabled": False,
    }

    bundle = build_operator_status_bundle(journal, scheduler_policy=scheduler_policy)
    markdown = build_operator_status_markdown(bundle)

    assert bundle["scheduler_policy_summary"]["resource_controls"]["provider_mode"] == "perplexity"
    assert bundle["scheduler_policy_summary"]["resource_controls"]["research_max_pass_count"] == 25
    assert bundle["agent_next_step"]["scheduler_resource_provider_mode"] == "perplexity"
    assert bundle["agent_next_step"]["scheduler_resource_bounded"] is True
    assert "### Scheduler Resource Controls" in markdown
    assert "- Provider: `perplexity`" in markdown
    assert "- Research max pass count: `25`" in markdown
    assert "- Bounded: `true`" in markdown


def test_operator_status_bundle_surfaces_committee_preset_policy(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    committee_policy = {
        "mode": "committee_preset_policy",
        "recommended_preset": "decision_6",
        "default_preset": "decision_4",
        "escalation_required": True,
        "escalation_reasons": ["large_position_change:NVDA", "borderline_valuation:AMZN"],
        "affected_symbols": ["NVDA", "AMZN"],
        "order_submission_enabled": False,
    }

    bundle = build_operator_status_bundle(journal, committee_preset_policy=committee_policy)
    markdown = build_operator_status_markdown(bundle)

    assert bundle["committee_preset_policy_summary"]["recommended_preset"] == "decision_6"
    assert bundle["committee_preset_policy_summary"]["affected_symbols"] == ["NVDA", "AMZN"]
    assert bundle["committee_preset_policy_summary"]["order_submission_enabled"] is False
    assert bundle["agent_next_step"]["committee_recommended_preset"] == "decision_6"
    assert "## Committee Preset Policy" in markdown
    assert "- Recommended preset: `decision_6`" in markdown
    assert "- large_position_change:NVDA" in markdown


def test_operator_status_bundle_cli_outputs_json(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    journal_path = journal.db_path
    ledger_path = tmp_path / "paper.db"
    portfolio_path = tmp_path / "portfolio.json"
    action_plan_path = tmp_path / "action_plan.json"
    price_map_path = tmp_path / "prices.json"
    feedback_path = tmp_path / "feedback.json"
    monday_check_path = tmp_path / "monday_check.json"
    live_readiness_path = tmp_path / "live_readiness_bundle.json"
    status_refresh_path = tmp_path / "paper_order_status_refresh.json"
    scheduler_policy_path = tmp_path / "scheduler_policy.json"
    committee_policy_path = tmp_path / "committee_preset_policy.json"
    report_path = tmp_path / "operator_status_bundle.json"
    PaperTradeLedger(ledger_path).record_execution_event(
        {
            "decision_id": decision_id,
            "symbol": "NVDA",
            "side": "buy",
            "notional": 1000,
            "status": "filled",
            "filled_price": 100,
            "benchmark_price_at_fill": 100,
            "paper_mode": True,
            "live_mode": False,
        }
    )
    portfolio_path.write_text(json.dumps({"cash": 5000, "holdings": [{"symbol": "NVDA", "market_value": 4200}]}), encoding="utf-8")
    action_plan_path.write_text(
        json.dumps(
            {
                "intents": [
                    {
                        "symbol": "NVDA",
                        "order_intent": "BUY",
                        "decision_id": decision_id,
                        "promotion_review": {
                            "symbol": "NVDA",
                            "promotion_decision": "ACTIONABLE_BUY",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    price_map_path.write_text(json.dumps({"NVDA": 120, "FXAIX": 110}), encoding="utf-8")
    feedback_path.write_text(json.dumps({"order_submission_enabled": False}), encoding="utf-8")
    monday_check_path.write_text(
        json.dumps(
            {
                "mode": "paper_monday_operator_check",
                "ready_for_review": True,
                "blocker_count": 0,
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    live_readiness_path.write_text(
        json.dumps(
            {
                "mode": "live_readiness_bundle",
                "ready": False,
                "unmet_gate_keys": ["paper_trading_verified"],
                "observed": {"paper_smoke_ready": True},
            }
        ),
        encoding="utf-8",
    )
    status_refresh_path.write_text(
        json.dumps(
            {
                "mode": "paper_order_status_refresh",
                "submitted_order_count": 0,
                "refreshed_count": 0,
                "error_count": 0,
                "status_counts": {},
            }
        ),
        encoding="utf-8",
    )
    scheduler_policy_path.write_text(
        json.dumps(
            {
                "recommended_mode": "account_refresh_only",
                "urgency": "low",
                "reasons": ["account_refresh_stale"],
                "next_safe_action": "refresh_account_and_dashboard_artifacts",
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    committee_policy_path.write_text(
        json.dumps(
            {
                "recommended_preset": "decision_4",
                "default_preset": "decision_4",
                "escalation_required": False,
                "escalation_reasons": [],
                "affected_symbols": [],
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal_path),
            "--portfolio-state",
            str(portfolio_path),
            "--paper-ledger-db",
            str(ledger_path),
            "--action-plan",
            str(action_plan_path),
            "--price-map",
            str(price_map_path),
            "--feedback-summary",
            str(feedback_path),
            "--monday-operator-check",
            str(monday_check_path),
            "--live-readiness-bundle",
            str(live_readiness_path),
            "--status-refresh",
            str(status_refresh_path),
            "--scheduler-policy",
            str(scheduler_policy_path),
            "--committee-preset-policy",
            str(committee_policy_path),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "operator_status_bundle"
    assert payload["buy_promotion_summary"]["counts"]["ACTIONABLE_BUY"] == 1
    assert payload["paper_lifecycle"]["state_counts"]["outcome_evaluated"] == 1
    assert payload["monday_operator_check_summary"]["ready_for_review"] is True
    assert payload["live_readiness_summary"]["unmet_gate_keys"] == ["paper_trading_verified"]
    assert payload["status_refresh_summary"]["submitted_order_count"] == 0
    assert payload["scheduler_policy_summary"]["recommended_mode"] == "account_refresh_only"
    assert payload["committee_preset_policy_summary"]["recommended_preset"] == "decision_4"
    assert json.loads(report_path.read_text(encoding="utf-8"))["mode"] == "operator_status_bundle"


def test_operator_status_bundle_cli_reads_and_writes_long_artifact_paths(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal_path = journal.db_path
    ledger_path = tmp_path / "paper.db"
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "holdings": []}), encoding="utf-8")
    long_dir = tmp_path
    while len(str(long_dir)) < 225:
        long_dir = long_dir / "scheduler_prerun_snapshot_segment"
    monday_check_path = long_dir / f"paper_monday_operator_check_{'x' * 32}.json"
    status_refresh_path = long_dir / f"paper_order_status_refresh_{'x' * 32}.json"
    report_path = long_dir / f"operator_status_bundle_{'x' * 32}.json"
    assert len(str(long_dir)) < 260
    assert len(str(monday_check_path)) > 260
    assert len(str(report_path)) > 260
    write_json_artifact(
        monday_check_path,
        {
            "mode": "paper_monday_operator_check",
            "ready_for_review": True,
            "blocker_count": 0,
            "blockers": [],
        },
    )
    write_json_artifact(
        status_refresh_path,
        {
            "mode": "paper_order_status_refresh",
            "submitted_order_count": 0,
            "refreshed_count": 0,
            "error_count": 0,
            "status_counts": {},
        },
    )
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal_path),
            "--portfolio-state",
            str(portfolio_path),
            "--paper-ledger-db",
            str(ledger_path),
            "--monday-operator-check",
            str(monday_check_path),
            "--status-refresh",
            str(status_refresh_path),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(_read_text(report_path))

    assert payload["mode"] == "operator_status_bundle"
    assert payload["monday_operator_check_summary"]["ready_for_review"] is True
    assert saved["mode"] == "operator_status_bundle"


def _read_text(path):
    path = Path(path)
    if sys.platform == "win32":
        return Path("\\\\?\\" + str(path.resolve())).read_text(encoding="utf-8")
    return path.read_text(encoding="utf-8")
