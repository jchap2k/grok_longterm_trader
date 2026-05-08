import json

from longterm.scheduler_handoff_cli import build_parser, run_cli


def test_scheduler_handoff_cli_writes_ready_packet(tmp_path, capsys):
    validation = tmp_path / "scheduler_profile_validation.json"
    task_plan = tmp_path / "scheduler_task_plan.json"
    manifest = tmp_path / "dashboard_manifest.json"
    output = tmp_path / "scheduler_handoff.json"
    run_profile = tmp_path / "ongoing_no_submit_scheduler.run.json"

    validation.write_text(
        json.dumps(
            {
                "mode": "pipeline_scheduler_config_validation",
                "status": "ready",
                "config_file": str(run_profile),
                "order_submission_enabled": False,
                "resource_controls": {"bounded": True, "provider_mode": "perplexity"},
            }
        ),
        encoding="utf-8",
    )
    task_plan.write_text(
        json.dumps(
            {
                "mode": "windows_task_scheduler_plan",
                "status": "ready",
                "task_name": "LongTermTraderNoSubmit",
                "profile_file": str(run_profile),
                "order_submission_enabled": False,
                "profile_validation": {"status": "ready", "order_submission_enabled": False},
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scheduler_config_validation": str(validation),
                "scheduler_task_plan": str(task_plan),
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-config-validation",
                str(validation),
                "--scheduler-task-plan",
                str(task_plan),
                "--dashboard-manifest",
                str(manifest),
                "--output",
                str(output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert printed == saved
    assert saved["mode"] == "scheduler_handoff_check"
    assert saved["status"] == "ready"
    assert saved["ready"] is True
    assert saved["order_submission_enabled"] is False
    assert saved["task_name"] == "LongTermTraderNoSubmit"
    assert saved["profile_file"] == str(run_profile)
    assert saved["checks"]["scheduler_config_validation"] == "ready"
    assert saved["checks"]["scheduler_task_plan"] == "ready"
    assert saved["checks"]["dashboard_manifest"] == "ready"
    assert saved["blockers"] == []
    assert saved["next_safe_action"] == "review_dashboard_then_register_task_manually_if_approved"


def test_scheduler_handoff_cli_blocks_mismatched_manifest(tmp_path, capsys):
    validation = tmp_path / "scheduler_profile_validation.json"
    task_plan = tmp_path / "scheduler_task_plan.json"
    manifest = tmp_path / "dashboard_manifest.json"
    other_task_plan = tmp_path / "other_task_plan.json"
    run_profile = tmp_path / "ongoing_no_submit_scheduler.run.json"

    validation.write_text(
        json.dumps({"status": "ready", "config_file": str(run_profile), "order_submission_enabled": False}),
        encoding="utf-8",
    )
    task_plan.write_text(
        json.dumps({"status": "ready", "profile_file": str(run_profile), "order_submission_enabled": False}),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "scheduler_config_validation": str(validation),
                "scheduler_task_plan": str(other_task_plan),
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-config-validation",
                str(validation),
                "--scheduler-task-plan",
                str(task_plan),
                "--dashboard-manifest",
                str(manifest),
                "--json",
            ]
        )
    )

    assert code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "blocked"
    assert "dashboard_manifest_task_plan_mismatch" in printed["blockers"]
