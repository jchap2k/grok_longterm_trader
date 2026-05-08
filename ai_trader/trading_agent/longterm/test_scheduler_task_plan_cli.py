import json
from pathlib import Path

from longterm.scheduler_task_plan_cli import build_parser, run_cli


def test_scheduler_task_plan_cli_writes_reviewable_windows_task_plan(tmp_path, capsys):
    profile = tmp_path / "ongoing_no_submit_scheduler.run.json"
    output = tmp_path / "scheduler_task_plan.json"
    working_dir = tmp_path / "repo" / "ai_trader" / "trading_agent"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(tmp_path / "scheduler_runs"),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "validate_config_only": False,
                    "json": True,
                },
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--profile-file",
                str(profile),
                "--task-name",
                "LongTermTraderNoSubmit",
                "--start-time",
                "09:35",
                "--working-dir",
                str(working_dir),
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
    assert saved["mode"] == "windows_task_scheduler_plan"
    assert saved["status"] == "ready"
    assert saved["task_name"] == "LongTermTraderNoSubmit"
    assert saved["schedule"]["type"] == "DAILY"
    assert saved["schedule"]["start_time"] == "09:35"
    assert saved["profile_file"] == str(profile.resolve())
    assert saved["working_dir"] == str(working_dir.resolve())
    assert saved["order_submission_enabled"] is False
    assert saved["profile_run_mode"] == "no-submit"
    assert saved["scheduler_command"].endswith(f"--config-file {profile.resolve()}")
    assert "longterm_pipeline_scheduler.py" in saved["scheduler_command"]
    assert "schtasks /Create" in saved["schtasks_command"]
    assert "/TN LongTermTraderNoSubmit" in saved["schtasks_command"]
    assert "/SC DAILY" in saved["schtasks_command"]
    assert "/ST 09:35" in saved["schtasks_command"]
    assert "Register-ScheduledTask" in saved["powershell_command"]
    assert saved["next_safe_action"] == "review_task_plan_then_register_manually_if_approved"


def test_scheduler_task_plan_cli_rejects_validation_only_profile(tmp_path):
    profile = tmp_path / "ongoing_no_submit_scheduler.local.json"
    profile.write_text(
        json.dumps(
            {
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(tmp_path / "scheduler_runs"),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "validate_config_only": True,
                }
            }
        ),
        encoding="utf-8",
    )

    try:
        run_cli(
            build_parser().parse_args(
                [
                    "--profile-file",
                    str(profile),
                    "--task-name",
                    "LongTermTraderNoSubmit",
                    "--start-time",
                    "09:35",
                ]
            )
        )
    except ValueError as exc:
        assert "validation-only" in str(exc)
    else:
        raise AssertionError("Expected validation-only profile to fail")


def test_scheduler_task_plan_cli_rejects_submit_capable_profile(tmp_path):
    profile = tmp_path / "unsafe.json"
    profile.write_text(
        json.dumps(
            {
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(tmp_path / "scheduler_runs"),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "validate_config_only": False,
                    "submit_paper_orders": True,
                }
            }
        ),
        encoding="utf-8",
    )

    try:
        run_cli(
            build_parser().parse_args(
                [
                    "--profile-file",
                    str(profile),
                    "--task-name",
                    "LongTermTraderNoSubmit",
                    "--start-time",
                    "09:35",
                ]
            )
        )
    except ValueError as exc:
        assert "Submit-capable scheduler profile keys are not allowed" in str(exc)
    else:
        raise AssertionError("Expected submit-capable profile to fail")
