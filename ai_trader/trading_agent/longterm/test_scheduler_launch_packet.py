import json
from pathlib import Path

from longterm.scheduler_launch_packet_cli import build_parser, run_cli
from longterm.scheduler_launch_packet import SchedulerLaunchPacketInputs, build_scheduler_launch_packet


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ready_artifacts(tmp_path: Path) -> dict[str, Path]:
    validation = _write(
        tmp_path / "scheduler_profile_validation.json",
        {
            "status": "ready",
            "recurring_no_submit_ready": True,
            "config_file": str(tmp_path / "ongoing_no_submit_scheduler.run.json"),
            "operating_mode_summary": {
                "ready_for_unattended_no_submit": True,
                "broker_submit_boundary": "blocked_by_no_submit_scheduler",
            },
            "order_submission_enabled": False,
        },
    )
    task_plan = _write(
        tmp_path / "scheduler_task_plan.json",
        {
            "status": "ready",
            "task_name": "LongTermTraderNoSubmit",
            "profile_file": str(tmp_path / "ongoing_no_submit_scheduler.run.json"),
            "profile_run_mode": "no-submit",
            "order_submission_enabled": False,
        },
    )
    manifest = _write(
        tmp_path / "dashboard_manifest.json",
        {
            "schema_version": 1,
            "mode": "operator_dashboard_manifest",
            "scheduler_config_validation": str(validation),
            "scheduler_task_plan": str(task_plan),
            "scheduler_handoff": str(tmp_path / "scheduler_handoff.json"),
            "scheduler_task_registration": str(tmp_path / "scheduler_task_registration_review.json"),
            "order_submission_enabled": False,
        },
    )
    handoff = _write(
        tmp_path / "scheduler_handoff.json",
        {
            "status": "ready",
            "ready": True,
            "scheduler_config_validation": str(validation),
            "scheduler_task_plan": str(task_plan),
            "dashboard_manifest": str(manifest),
            "checks": {
                "scheduler_config_validation": "ready",
                "recurring_no_submit_readiness": "ready",
                "scheduler_task_plan": "ready",
                "dashboard_manifest": "ready",
                "order_submission_boundary": "ready",
            },
            "blockers": [],
            "order_submission_enabled": False,
        },
    )
    registration = _write(
        tmp_path / "scheduler_task_registration_review.json",
        {
            "status": "ready_for_registration_review",
            "task_name": "LongTermTraderNoSubmit",
            "registration_requested": False,
            "registration_executed": False,
            "order_submission_enabled": False,
        },
    )
    return {
        "validation": validation,
        "task_plan": task_plan,
        "manifest": manifest,
        "handoff": handoff,
        "registration": registration,
    }


def test_launch_packet_reports_ready_no_submit_chain_with_review_only_actions(tmp_path):
    artifacts = _ready_artifacts(tmp_path)
    action_plan = _write(
        tmp_path / "account_action_plan.json",
        {
            "schema_version": 1,
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "intents": [
                {
                    "symbol": "MSFT",
                    "intent_type": "BUY",
                    "order_intent": "BUY",
                    "allowed": True,
                    "trade_value": 1200,
                    "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                },
                {
                    "symbol": "AAPL",
                    "intent_type": "REBALANCE",
                    "order_intent": "REVIEW",
                    "allowed": False,
                    "review_type": "rebalance_review",
                },
                {
                    "symbol": "SPY",
                    "intent_type": "PARK_IDLE_CASH",
                    "order_intent": "BUY",
                    "allowed": True,
                    "trade_value": 5000,
                    "risk_review": {"market_regime": "normal"},
                },
            ],
        },
    )
    stage6b_plan = _write(
        tmp_path / "stage6b_candidates.json",
        {
            "filter_mode": "stage6b_actionable_buys_and_approved_parking",
            "order_submission_enabled": False,
            "intents": [
                {"symbol": "MSFT", "intent_type": "BUY", "order_intent": "BUY"},
                {"symbol": "SPY", "intent_type": "PARK_IDLE_CASH", "order_intent": "BUY"},
            ],
        },
    )
    market_regime = _write(
        tmp_path / "market_regime.json",
        {"risk_regime": "normal", "vix_level": 16.2, "ten_year_yield_trend": "stable"},
    )
    portfolio_news = _write(
        tmp_path / "portfolio_news_monitor.json",
        {
            "status": "completed",
            "queue_count": 1,
            "review_trigger_count": 1,
            "high_impact_count": 0,
            "order_submission_enabled": False,
        },
    )

    packet = build_scheduler_launch_packet(
        SchedulerLaunchPacketInputs(
            scheduler_config_validation=artifacts["validation"],
            scheduler_task_plan=artifacts["task_plan"],
            scheduler_handoff=artifacts["handoff"],
            scheduler_task_registration=artifacts["registration"],
            dashboard_manifest=artifacts["manifest"],
            action_plan=action_plan,
            stage6b_candidate_plan=stage6b_plan,
            market_regime=market_regime,
            portfolio_news_monitor=portfolio_news,
        )
    )

    assert packet["status"] == "ready_for_no_submit_launch_review"
    assert packet["order_submission_enabled"] is False
    assert packet["chain"]["ready"] is True
    assert packet["sell_rebalance_review"]["source_review_intent_count"] == 1
    assert packet["sell_rebalance_review"]["stage6b_leak_count"] == 0
    assert packet["parking_review"]["parking_intent_count"] == 1
    assert packet["parking_review"]["symbols"] == ["SPY"]
    assert packet["panic_monitor"]["off_schedule_review_recommended"] is True
    assert packet["next_safe_action"] == "review_no_submit_launch_packet_then_optionally_register_scheduler_task"


def test_launch_packet_blocks_if_stage6b_plan_contains_sell_or_rebalance(tmp_path):
    artifacts = _ready_artifacts(tmp_path)
    stage6b_plan = _write(
        tmp_path / "stage6b_candidates.json",
        {
            "order_submission_enabled": False,
            "intents": [{"symbol": "AAPL", "intent_type": "REBALANCE", "order_intent": "SELL"}],
        },
    )

    packet = build_scheduler_launch_packet(
        SchedulerLaunchPacketInputs(
            scheduler_config_validation=artifacts["validation"],
            scheduler_task_plan=artifacts["task_plan"],
            scheduler_handoff=artifacts["handoff"],
            scheduler_task_registration=artifacts["registration"],
            dashboard_manifest=artifacts["manifest"],
            stage6b_candidate_plan=stage6b_plan,
        )
    )

    assert packet["status"] == "blocked"
    assert "stage6b_plan_contains_sell_or_rebalance_intent" in packet["blockers"]
    assert packet["sell_rebalance_review"]["stage6b_leak_count"] == 1


def test_launch_packet_blocks_if_parking_intent_missing_regime_context(tmp_path):
    artifacts = _ready_artifacts(tmp_path)
    action_plan = _write(
        tmp_path / "account_action_plan.json",
        {
            "order_submission_enabled": False,
            "intents": [
                {
                    "symbol": "TLT",
                    "intent_type": "PARK_DEFENSIVE_CASH",
                    "order_intent": "BUY",
                    "allowed": True,
                    "trade_value": 2500,
                }
            ],
        },
    )

    packet = build_scheduler_launch_packet(
        SchedulerLaunchPacketInputs(
            scheduler_config_validation=artifacts["validation"],
            scheduler_task_plan=artifacts["task_plan"],
            scheduler_handoff=artifacts["handoff"],
            scheduler_task_registration=artifacts["registration"],
            dashboard_manifest=artifacts["manifest"],
            action_plan=action_plan,
        )
    )

    assert packet["status"] == "blocked"
    assert "parking_intent_missing_market_regime_context" in packet["blockers"]


def test_launch_packet_cli_writes_json_and_markdown(tmp_path, capsys):
    artifacts = _ready_artifacts(tmp_path)
    output = tmp_path / "scheduler_launch_packet.json"
    markdown = tmp_path / "scheduler_launch_packet.md"

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-config-validation",
                str(artifacts["validation"]),
                "--scheduler-task-plan",
                str(artifacts["task_plan"]),
                "--scheduler-handoff",
                str(artifacts["handoff"]),
                "--scheduler-task-registration",
                str(artifacts["registration"]),
                "--dashboard-manifest",
                str(artifacts["manifest"]),
                "--output",
                str(output),
                "--markdown-output",
                str(markdown),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    markdown_text = markdown.read_text(encoding="utf-8")
    assert code == 0
    assert printed["status"] == "ready_for_no_submit_launch_review"
    assert saved["chain"]["ready"] is True
    assert "Scheduler No-Submit Launch Packet" in markdown_text
