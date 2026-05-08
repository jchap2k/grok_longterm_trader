import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.alpaca_paper_account import PaperAccountPosition, PaperAccountSnapshot
from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_account_refresh import refresh_paper_account_artifacts
from longterm.paper_account_refresh_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from research.intake import create_research_packet_from_idea


class FakePaperAccountReader:
    def read_snapshot(self, *, profile=None):
        return PaperAccountSnapshot(
            mode="paper",
            cash=1000.0,
            portfolio_value=6500.0,
            buying_power=1000.0,
            protected_symbols=["FXAIX"],
            positions=[
                PaperAccountPosition(
                    symbol="ADBE",
                    quantity=3,
                    current_price=252.0,
                    market_value=756.0,
                    avg_entry_price=250.0,
                    unrealized_pnl=6.0,
                    unrealized_pnl_percent=0.8,
                ),
                PaperAccountPosition(
                    symbol="FXAIX",
                    quantity=10,
                    current_price=574.4,
                    market_value=5744.0,
                    avg_entry_price=500.0,
                    unrealized_pnl=744.0,
                    unrealized_pnl_percent=14.88,
                ),
            ],
        )


class ChattyFakePaperAccountReader(FakePaperAccountReader):
    def read_snapshot(self, *, profile=None):
        print("broker connection noise")
        return super().read_snapshot(profile=profile)


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _record_adbe_decision(journal_path):
    journal = LongTermDecisionJournal(journal_path)
    packet = create_research_packet_from_idea(
        {
            "symbol": "ADBE",
            "company_name": "Adobe",
            "idea_source": "test",
            "business_summary": "Creative software platform.",
        }
    )
    return journal.record_decision(
        packet,
        decision={"recommendation": "BUY", "confidence": 80, "suggested_size_pct": 4, "key_thesis": "Durable subscriptions."},
        candidate_price=250.0,
        benchmark_price=500.0,
    )


def test_refresh_paper_account_artifacts_writes_current_portfolio_dashboard_and_summary(tmp_path):
    profile = tmp_path / "profile.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    action_plan = tmp_path / "action_plan.json"
    evidence = tmp_path / "evidence.json"
    market = tmp_path / "market.json"
    prices = tmp_path / "prices.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    pipeline_scheduler_summary = tmp_path / "pipeline_scheduler_summary.json"
    scheduler_config_validation = tmp_path / "scheduler_profile_validation.json"
    scheduler_task_plan = tmp_path / "scheduler_task_plan.json"
    scheduler_handoff = tmp_path / "scheduler_handoff.json"
    position_review_queue = tmp_path / "position_review_queue.json"
    paper_submit_mode_plan = tmp_path / "paper_submit_mode_plan.json"
    scheduler_policy = tmp_path / "scheduler_policy.json"
    committee_preset_policy = tmp_path / "committee_preset_policy.json"
    output_dir = tmp_path / "refresh"
    site_dir = output_dir / "site"
    manifest = output_dir / "dashboard_manifest.json"
    _write_json(profile, {"protected_symbols": []})
    _write_json(
        action_plan,
        {"intents": [{"intent_type": "BUY", "symbol": "ADBE", "allowed": True, "trade_value": 750}]},
    )
    _write_json(
        evidence,
        [
            {
                "symbol": "ADBE",
                "business_summary": "Adobe is a creative platform.",
                "quality_growth_scorecard": {"superscore": 78.5, "quality_score": 100.0},
                "python_first_pass_scan": {"moneyball_score": 74.1, "quant_score": 82.3},
                "latest_earnings_enrichment": {"summary": "AI growth supported the thesis."},
            }
        ],
    )
    _write_json(market, {"risk_regime": "normal"})
    _write_json(
        prices,
        {
            "ADBE": [{"date": "2026-05-01", "close": 252.0}],
            "FXAIX": [{"date": "2026-05-01", "close": 574.4}],
        },
    )
    _write_json(
        pipeline_summary,
        {
            "status": "completed",
            "artifact_paths": {"paper_preview": str(tmp_path / "paper_preview.json")},
        },
    )
    _write_json(
        pipeline_scheduler_summary,
        {
            "status": "planned",
            "runs": [
                {
                    "resource_controls": {
                        "provider_mode": "perplexity",
                        "research_max_pass_count": 25,
                        "bounded": True,
                    }
                }
            ],
        },
    )
    _write_json(
        scheduler_config_validation,
        {
            "mode": "pipeline_scheduler_config_validation",
            "status": "ready",
            "config_file": str(tmp_path / "ongoing_no_submit_scheduler.local.json"),
            "resource_controls": {"provider_mode": "perplexity", "bounded": True},
            "order_submission_enabled": False,
        },
    )
    _write_json(
        scheduler_task_plan,
        {
            "mode": "windows_task_scheduler_plan",
            "status": "ready",
            "task_name": "LongTermTraderNoSubmit",
            "profile_file": str(tmp_path / "ongoing_no_submit_scheduler.run.json"),
            "profile_run_mode": "no-submit",
            "schedule": {"type": "DAILY", "start_time": "09:35"},
            "order_submission_enabled": False,
        },
    )
    _write_json(
        scheduler_handoff,
        {
            "mode": "scheduler_handoff_check",
            "status": "ready",
            "checks": {
                "scheduler_config_validation": "ready",
                "scheduler_task_plan": "ready",
                "dashboard_manifest": "ready",
                "order_submission_boundary": "ready",
            },
            "next_safe_action": "review_task_plan_then_register_manually_if_approved",
            "order_submission_enabled": False,
        },
    )
    _write_json(
        position_review_queue,
        {
            "mode": "position_review_queue",
            "status": "completed",
            "review_count": 1,
            "review_queue": [{"symbol": "ADBE", "review_type": "sell_review", "severity": "high"}],
            "order_submission_enabled": False,
            "broker_calls_enabled": False,
            "llm_calls_enabled": False,
        },
    )
    _write_json(
        paper_submit_mode_plan,
        {
            "mode": "paper_submit_mode_plan",
            "status": "ready_for_manual_review",
            "checks": {"position_review_queue": "ready", "order_submission_boundary": "ready"},
            "order_submission_enabled": False,
            "submit_profile_enabled": False,
            "broker_calls_enabled": False,
            "runnable_submit_command_emitted": False,
            "next_safe_action": "manual_review_required_before_submit_profile",
        },
    )
    _write_json(
        scheduler_policy,
        {
            "recommended_mode": "account_refresh_only",
            "urgency": "low",
            "reasons": ["account_refresh_stale"],
            "next_safe_action": "refresh_account_and_dashboard_artifacts",
            "order_submission_enabled": False,
        },
    )
    _write_json(
        committee_preset_policy,
        {
            "recommended_preset": "decision_4",
            "escalate_to_decision_6": False,
            "reasons": ["routine_position_review"],
            "order_submission_enabled": False,
        },
    )
    decision_id = _record_adbe_decision(journal)
    PaperTradeLedger(ledger).record_execution_event(
        {
            "decision_id": decision_id,
            "symbol": "ADBE",
            "side": "buy",
            "status": "filled",
            "filled_price": 250.0,
            "filled_quantity": 3,
            "notional": 750.0,
        }
    )

    summary = refresh_paper_account_artifacts(
        profile_config=profile,
        journal_db=journal,
        action_plan_path=action_plan,
        paper_ledger_db=ledger,
        output_dir=output_dir,
        market_regime_path=market,
        evidence_file=evidence,
        price_history_file=prices,
        pipeline_summary_path=pipeline_summary,
        pipeline_scheduler_summary_path=pipeline_scheduler_summary,
        scheduler_config_validation_path=scheduler_config_validation,
        scheduler_task_plan_path=scheduler_task_plan,
        scheduler_handoff_path=scheduler_handoff,
        position_review_queue_path=position_review_queue,
        paper_submit_mode_plan_path=paper_submit_mode_plan,
        scheduler_policy_path=scheduler_policy,
        committee_preset_policy_path=committee_preset_policy,
        dashboard_manifest_output=manifest,
        dashboard_site_output_dir=site_dir,
        reader_factory=lambda: FakePaperAccountReader(),
    )

    portfolio = json.loads(Path(summary["portfolio_state_path"]).read_text(encoding="utf-8"))
    assert summary["read_only"] is True
    assert summary["order_submission_enabled"] is False
    assert summary["live_mode"] is False
    assert summary["paper_mode"] is True
    assert summary["protected_symbols_applied"] == ["FXAIX"]
    assert portfolio["protected_symbols"] == ["FXAIX"]
    adbe = next(item for item in portfolio["holdings"] if item["symbol"] == "ADBE")
    assert adbe["original_purchase_total_cost"] == 750.0
    assert adbe["avg_entry_price"] == 250.0
    assert adbe["unrealized_pnl"] == 6.0
    fxaix = next(item for item in portfolio["holdings"] if item["symbol"] == "FXAIX")
    assert fxaix["status"] == "Protected / core"
    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    adbe_html = (site_dir / "tickers" / "ADBE.html").read_text(encoding="utf-8")
    assert "No current portfolio holdings were supplied" not in index_html
    assert "$750.00" in index_html
    assert "$756.00" in index_html
    assert "First-Pass Scan" in adbe_html
    assert "Scheduler Policy" in index_html
    assert "Scheduler Profile" in index_html
    assert "ongoing_no_submit_scheduler.local.json" in index_html
    assert "Windows Task Scheduler" in index_html
    assert "LongTermTraderNoSubmit" in index_html
    assert "Scheduler Handoff" in index_html
    assert "Review Task Plan Then Register Manually If Approved" in index_html
    assert "Position Review Queue" in index_html
    assert "Paper Submit Mode Plan" in index_html
    assert "Manual Review Required Before Submit Profile" in index_html
    assert "Account Refresh Only" in index_html
    assert "Moneyball" in adbe_html
    assert "74.1" in adbe_html
    assert "Quant" in adbe_html
    assert "82.3" in adbe_html
    assert Path(summary["operator_status_path"]).exists()
    assert Path(summary["dashboard_manifest_path"]).exists()
    assert Path(summary["refresh_summary_path"]).exists()
    assert Path(summary["paper_outcome_summary_path"]).exists()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["pipeline_summary"] == str(pipeline_summary)
    assert manifest_payload["pipeline_scheduler_summary"] == str(pipeline_scheduler_summary)
    assert manifest_payload["scheduler_config_validation"] == str(scheduler_config_validation)
    assert manifest_payload["scheduler_task_plan"] == str(scheduler_task_plan)
    assert manifest_payload["scheduler_handoff"] == str(scheduler_handoff)
    assert manifest_payload["position_review_queue"] == str(position_review_queue)
    assert manifest_payload["paper_submit_mode_plan"] == str(paper_submit_mode_plan)
    assert manifest_payload["scheduler_policy"] == str(scheduler_policy)
    assert manifest_payload["committee_preset_policy"] == str(committee_preset_policy)
    assert summary["components"]["pipeline_summary"]["exists"] is True
    assert summary["components"]["pipeline_scheduler_summary"]["exists"] is True
    assert summary["components"]["scheduler_config_validation"]["exists"] is True
    assert summary["components"]["scheduler_task_plan"]["exists"] is True
    assert summary["components"]["scheduler_handoff"]["exists"] is True
    assert summary["components"]["position_review_queue"]["exists"] is True
    assert summary["components"]["paper_submit_mode_plan"]["exists"] is True
    assert summary["components"]["scheduler_policy"]["exists"] is True
    assert summary["components"]["committee_preset_policy"]["exists"] is True
    status_bundle = json.loads(Path(summary["operator_status_path"]).read_text(encoding="utf-8"))
    assert status_bundle["committee_preset_policy_summary"]["recommended_preset"] == "decision_4"
    assert status_bundle["agent_next_step"]["committee_recommended_preset"] == "decision_4"
    outcome = json.loads(Path(summary["paper_outcome_summary_path"]).read_text(encoding="utf-8"))
    assert outcome["evaluated_fills"] == 1
    assert outcome["proxy_benchmark_count"] == 1
    assert summary["paper_outcome_evaluated_fills"] == 1


def test_paper_account_refresh_cli_writes_summary(tmp_path, capsys):
    profile = tmp_path / "profile.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    action_plan = tmp_path / "action_plan.json"
    output_dir = tmp_path / "refresh"
    _write_json(profile, {"protected_symbols": ["FXAIX"]})
    _write_json(action_plan, {"intents": []})

    code = run_cli(
        build_parser().parse_args(
            [
                "--profile-config",
                str(profile),
                "--journal-db",
                str(journal),
                "--action-plan",
                str(action_plan),
                "--paper-ledger-db",
                str(ledger),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        ),
        reader_factory=lambda: FakePaperAccountReader(),
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["read_only"] is True
    assert printed["order_submission_enabled"] is False
    assert Path(printed["refresh_summary_path"]).exists()


def test_paper_account_refresh_cli_keeps_json_stdout_when_reader_prints(tmp_path, capsys):
    profile = tmp_path / "profile.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    action_plan = tmp_path / "action_plan.json"
    output_dir = tmp_path / "refresh"
    _write_json(profile, {"protected_symbols": ["FXAIX"]})
    _write_json(action_plan, {"intents": []})

    code = run_cli(
        build_parser().parse_args(
            [
                "--profile-config",
                str(profile),
                "--journal-db",
                str(journal),
                "--action-plan",
                str(action_plan),
                "--paper-ledger-db",
                str(ledger),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        ),
        reader_factory=lambda: ChattyFakePaperAccountReader(),
    )

    captured = capsys.readouterr()
    printed = json.loads(captured.out)
    assert code == 0
    assert printed["read_only"] is True
    assert "broker connection noise" not in captured.out
    assert "broker connection noise" in captured.err


def test_paper_account_refresh_cli_accepts_committee_preset_policy(tmp_path, capsys):
    profile = tmp_path / "profile.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    action_plan = tmp_path / "action_plan.json"
    committee_preset_policy = tmp_path / "committee_preset_policy.json"
    output_dir = tmp_path / "refresh"
    _write_json(profile, {"protected_symbols": ["FXAIX"]})
    _write_json(action_plan, {"intents": []})
    _write_json(
        committee_preset_policy,
        {
            "recommended_preset": "decision_6",
            "escalate_to_decision_6": True,
            "reasons": ["market_regime_choppy"],
            "order_submission_enabled": False,
        },
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--profile-config",
                str(profile),
                "--journal-db",
                str(journal),
                "--action-plan",
                str(action_plan),
                "--paper-ledger-db",
                str(ledger),
                "--output-dir",
                str(output_dir),
                "--committee-preset-policy",
                str(committee_preset_policy),
                "--json",
            ]
        ),
        reader_factory=lambda: FakePaperAccountReader(),
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["components"]["committee_preset_policy"]["exists"] is True
    status_bundle = json.loads(Path(printed["operator_status_path"]).read_text(encoding="utf-8"))
    assert status_bundle["committee_preset_policy_summary"]["recommended_preset"] == "decision_6"
