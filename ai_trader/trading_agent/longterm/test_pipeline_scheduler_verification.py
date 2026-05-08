import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.pipeline_scheduler_verification import build_parser, run_cli


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ready_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_dir = tmp_path / "run_001"
    pipeline_summary = _write_json(
        run_dir / "pipeline_summary.json",
        {
            "status": "completed",
            "order_submission_enabled": False,
            "blocker_count": 0,
            "stages": [
                {"stage_id": "preflight_rules", "status": "passed", "exit_code": 0},
                {"stage_id": "paper_preview", "status": "passed", "exit_code": 0},
            ],
            "artifact_rollup": {"workflow_smoke": {"submitted_count": 0}},
        },
    )
    (run_dir / "dashboard_manifest.json").write_text("{}", encoding="utf-8")
    scheduler_summary = _write_json(
        tmp_path / "pipeline_scheduler_summary.json",
        {
            "status": "completed",
            "success_count": 1,
            "error_count": 0,
            "order_submission_enabled": False,
            "runs": [
                {
                    "run_number": 1,
                    "run_dir": str(run_dir),
                    "status": "completed",
                    "blocker": "",
                    "pre_pipeline_refresh_command": "python scripts/longterm_alpaca_paper_snapshot.py",
                    "pre_pipeline_refresh_exit_code": 0,
                    "pipeline_command": (
                        "python scripts/longterm_research_to_paper_pipeline.py "
                        "--final-planning-refresh --final-planning-timeout-seconds 45"
                    ),
                    "pipeline_exit_code": 0,
                    "position_review_queue_command": "python scripts/longterm_position_review_queue.py",
                    "position_review_queue_exit_code": 0,
                    "pipeline_summary_path": str(pipeline_summary),
                    "scheduler_policy_command": "python scripts/longterm_pipeline_scheduler_policy.py",
                    "scheduler_policy_exit_code": 0,
                    "account_refresh_command": "python scripts/longterm_paper_account_refresh.py",
                    "account_refresh_exit_code": 0,
                    "resource_controls": {
                        "bounded": True,
                        "final_planning_refresh": True,
                        "final_planning_timeout_seconds": 45,
                        "provider_mode": "free_or_skip_grok",
                        "paid_provider_enabled": False,
                    },
                }
            ],
        },
    )
    policy_state = _write_json(
        tmp_path / "scheduler_policy_state.json",
        {
            "last_no_submit_preflight_at": "2026-05-06T12:00:00Z",
            "last_account_refresh_at": "2026-05-06T12:00:00Z",
            "last_final_planning_at": "2026-05-06T12:00:00Z",
            "last_position_review_at": "2026-05-06T12:00:00Z",
            "last_followup_batch_split_at": "2026-05-06T12:00:00Z",
        },
    )
    return scheduler_summary, pipeline_summary, policy_state


def test_scheduler_verification_reports_ready_for_no_submit_cadence(tmp_path, capsys):
    scheduler_summary, _, policy_state = _ready_fixture(tmp_path)
    report = tmp_path / "scheduler_verification.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--policy-state",
                str(policy_state),
                "--require-resource-bounded",
                "--require-final-planning-bound",
                "--require-policy-timestamp",
                "last_no_submit_preflight_at",
                "--require-policy-timestamp",
                "last_account_refresh_at",
                "--require-policy-timestamp",
                "last_followup_batch_split_at",
                "--report-output",
                str(report),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["status"] == "ready"
    assert printed["blockers"] == []
    assert printed["resource_controls"]["bounded"] is True
    assert printed["latest_run"]["pipeline_exit_code"] == 0
    assert printed["latest_run"]["position_review_queue_exit_code"] == 0
    assert printed["policy_state_timestamps"]["last_position_review_at"] == "2026-05-06T12:00:00Z"
    assert printed["policy_state_timestamps"]["last_followup_batch_split_at"] == "2026-05-06T12:00:00Z"
    assert saved["next_safe_action"] == "scheduler_run_verified_for_no_submit_cadence"


def test_scheduler_verification_blocks_submit_capable_commands(tmp_path, capsys):
    scheduler_summary, _, policy_state = _ready_fixture(tmp_path)
    payload = json.loads(scheduler_summary.read_text(encoding="utf-8"))
    payload["runs"][0]["pipeline_command"] += " --submit-paper-orders"
    scheduler_summary.write_text(json.dumps(payload), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--policy-state",
                str(policy_state),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "submit_capable_command_fragment_present" in printed["blockers"]


def test_scheduler_verification_checks_position_review_queue_exit_code(tmp_path, capsys):
    scheduler_summary, _, policy_state = _ready_fixture(tmp_path)
    payload = json.loads(scheduler_summary.read_text(encoding="utf-8"))
    payload["runs"][0]["position_review_queue_exit_code"] = 9
    scheduler_summary.write_text(json.dumps(payload), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--policy-state",
                str(policy_state),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "position_review_queue_exit_code_nonzero" in printed["blockers"]


def test_scheduler_verification_blocks_unbounded_final_planning(tmp_path, capsys):
    scheduler_summary, _, policy_state = _ready_fixture(tmp_path)
    payload = json.loads(scheduler_summary.read_text(encoding="utf-8"))
    payload["runs"][0]["resource_controls"]["final_planning_timeout_seconds"] = None
    scheduler_summary.write_text(json.dumps(payload), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--policy-state",
                str(policy_state),
                "--require-final-planning-bound",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "final_planning_refresh_without_timeout" in printed["blockers"]


def test_scheduler_verification_blocks_pipeline_submission_flag(tmp_path, capsys):
    scheduler_summary, pipeline_summary, policy_state = _ready_fixture(tmp_path)
    payload = json.loads(pipeline_summary.read_text(encoding="utf-8"))
    payload["order_submission_enabled"] = True
    pipeline_summary.write_text(json.dumps(payload), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--policy-state",
                str(policy_state),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "pipeline_order_submission_enabled" in printed["blockers"]


def test_scheduler_verification_requires_policy_timestamps(tmp_path, capsys):
    scheduler_summary, _, policy_state = _ready_fixture(tmp_path)
    policy_state.write_text(json.dumps({}), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--policy-state",
                str(policy_state),
                "--require-policy-timestamp",
                "last_full_research_at",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "policy_timestamp_missing:last_full_research_at" in printed["blockers"]
