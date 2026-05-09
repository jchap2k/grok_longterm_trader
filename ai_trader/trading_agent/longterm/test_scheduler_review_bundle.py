import json
from datetime import UTC, datetime
from pathlib import Path

from longterm.operator_dashboard_server import (
    build_dashboard_manifest,
    build_paper_submit_mode_plan_from_manifest,
    load_dashboard_manifest,
    resolve_dashboard_request,
)
from longterm.scheduler_review_bundle import SchedulerReviewBundleInputs, build_scheduler_review_bundle
from longterm.scheduler_review_bundle_cli import build_parser, run_cli


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ready_inputs(tmp_path: Path) -> SchedulerReviewBundleInputs:
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    action_plan = _write_json(tmp_path / "action_plan.json", {"intents": []})
    portfolio = _write_json(tmp_path / "portfolio_state.json", {"cash": 1000, "holdings": []})
    scheduler_policy = _write_json(
        tmp_path / "scheduler_policy.json",
        {
            "status": "ready",
            "blockers": [],
            "benchmark_guard": {"should_pause_new_buys": False, "reason": "ok"},
            "resource_controls": {"bounded": True},
        },
    )
    manifest = _write_json(
        tmp_path / "dashboard_manifest.json",
        build_dashboard_manifest(
            action_plan=action_plan,
            portfolio_state=portfolio,
            scheduler_policy=scheduler_policy,
        ),
    )
    handoff = _write_json(
        tmp_path / "scheduler_handoff.json",
        {
            "mode": "scheduler_handoff_check",
            "status": "ready",
            "ready": True,
            "generated_at": generated_at,
            "checks": {"order_submission_boundary": "ready"},
            "order_submission_enabled": False,
            "blockers": [],
        },
    )
    scheduler = _write_json(
        tmp_path / "pipeline_scheduler_summary.json",
        {
            "mode": "pipeline_scheduler",
            "status": "completed",
            "success_count": 1,
            "error_count": 0,
            "order_submission_enabled": False,
            "runs": [
                {
                    "status": "completed",
                    "blocker": "",
                    "position_review_queue_exit_code": 0,
                    "account_refresh_exit_code": 0,
                    "post_run_verification_exit_code": 0,
                    "resource_controls": {
                        "bounded": True,
                        "final_planning_refresh": True,
                        "final_planning_timeout_seconds": 900,
                    },
                }
            ],
        },
    )
    position_queue = _write_json(
        tmp_path / "position_review_queue.json",
        {
            "mode": "position_review_queue",
            "status": "completed",
            "order_submission_enabled": False,
            "broker_calls_enabled": False,
            "llm_calls_enabled": False,
            "review_count": 1,
            "high_priority_count": 1,
        },
    )
    verification = _write_json(
        tmp_path / "post_run_verification.json",
        {
            "mode": "pipeline_scheduler_cadence_verification",
            "status": "ready",
            "blockers": [],
            "warnings": [],
            "order_submission_enabled": False,
            "resource_controls": {
                "bounded": True,
                "final_planning_refresh": True,
                "final_planning_timeout_seconds": 900,
                "portfolio_news_followup_batches": True,
                "portfolio_news_followup_committee_batches": True,
            },
            "policy_state_timestamps": {
                "last_final_planning_at": "2026-05-08T15:30:00Z",
                "last_followup_batch_split_at": "2026-05-08T15:10:00Z",
                "last_followup_committee_at": "2026-05-08T15:20:00Z",
            },
        },
    )
    return SchedulerReviewBundleInputs(
        dashboard_manifest=manifest,
        scheduler_handoff=handoff,
        pipeline_scheduler_summary=scheduler,
        position_review_queue=position_queue,
        post_run_verification=verification,
        output_dir=tmp_path / "bundle",
    )


def test_scheduler_review_bundle_writes_ready_plan_and_dashboard_manifest(tmp_path):
    summary = build_scheduler_review_bundle(
        _ready_inputs(tmp_path),
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    review_manifest = Path(summary["dashboard_review_gates_manifest"])
    plan_path = Path(summary["paper_submit_mode_plan"])
    loaded_plan = build_paper_submit_mode_plan_from_manifest(load_dashboard_manifest(review_manifest))
    status, _, body = resolve_dashboard_request(review_manifest, "/api/paper-submit-mode-plan.json")

    assert summary["status"] == "ready_for_manual_review"
    assert summary["order_submission_enabled"] is False
    assert summary["broker_calls_enabled"] is False
    assert summary["llm_calls_enabled"] is False
    assert summary["runnable_submit_command_emitted"] is False
    assert plan_path.exists()
    assert review_manifest.exists()
    assert loaded_plan["status"] == "ready_for_manual_review"
    assert status == 200
    assert json.loads(body.decode("utf-8"))["status"] == "ready_for_manual_review"


def test_scheduler_review_bundle_blocks_verification_or_scheduler_submit_boundary(tmp_path):
    inputs = _ready_inputs(tmp_path)
    verification = json.loads(Path(inputs.post_run_verification).read_text(encoding="utf-8"))
    verification["status"] = "attention_required"
    verification["blockers"] = ["resource_controls_not_bounded"]
    Path(inputs.post_run_verification).write_text(json.dumps(verification), encoding="utf-8")
    scheduler = json.loads(Path(inputs.pipeline_scheduler_summary).read_text(encoding="utf-8"))
    scheduler["order_submission_enabled"] = True
    Path(inputs.pipeline_scheduler_summary).write_text(json.dumps(scheduler), encoding="utf-8")

    summary = build_scheduler_review_bundle(
        inputs,
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    assert summary["status"] == "blocked"
    assert "post_run_verification_not_ready" in summary["blockers"]
    assert "post_run_verification:resource_controls_not_bounded" in summary["blockers"]
    assert "pipeline_scheduler_summary_order_submission_enabled" in summary["blockers"]
    assert Path(summary["dashboard_review_gates_manifest"]).exists()
    assert Path(summary["paper_submit_mode_plan"]).exists()


def test_scheduler_review_bundle_blocks_benchmark_pause_and_buy_promotion_blockers(tmp_path):
    inputs = _ready_inputs(tmp_path)
    manifest = json.loads(Path(inputs.dashboard_manifest).read_text(encoding="utf-8"))
    scheduler_policy = Path(manifest["scheduler_policy"])
    policy = json.loads(scheduler_policy.read_text(encoding="utf-8"))
    policy["benchmark_guard"]["should_pause_new_buys"] = True
    scheduler_policy.write_text(json.dumps(policy), encoding="utf-8")
    buy_promotion = _write_json(
        tmp_path / "buy_promotion.json",
        {"status": "attention_required", "blocked_count": 1},
    )
    inputs = SchedulerReviewBundleInputs(**{**inputs.__dict__, "buy_promotion_artifact": buy_promotion})

    summary = build_scheduler_review_bundle(
        inputs,
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    assert summary["status"] == "blocked"
    assert "scheduler_policy_benchmark_guard_paused" in summary["blockers"]
    assert "buy_promotion_blockers_present" in summary["blockers"]


def test_scheduler_review_bundle_is_idempotent_and_cli_returns_status(tmp_path, capsys):
    inputs = _ready_inputs(tmp_path)
    args = [
        "--dashboard-manifest",
        str(inputs.dashboard_manifest),
        "--scheduler-handoff",
        str(inputs.scheduler_handoff),
        "--pipeline-scheduler-summary",
        str(inputs.pipeline_scheduler_summary),
        "--position-review-queue",
        str(inputs.position_review_queue),
        "--post-run-verification",
        str(inputs.post_run_verification),
        "--output-dir",
        str(inputs.output_dir),
        "--json",
    ]

    first = run_cli(build_parser().parse_args(args))
    first_printed = json.loads(capsys.readouterr().out)
    second = run_cli(build_parser().parse_args(args))
    second_printed = json.loads(capsys.readouterr().out)

    assert first == 0
    assert second == 0
    assert first_printed["paper_submit_mode_plan"] == second_printed["paper_submit_mode_plan"]
    assert first_printed["dashboard_review_gates_manifest"] == second_printed["dashboard_review_gates_manifest"]
    text = json.dumps(second_printed).lower()
    assert "--submit-paper-orders" not in text
    assert "longterm_paper_execution.py" not in text
