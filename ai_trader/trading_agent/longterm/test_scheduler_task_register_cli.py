import json

from longterm.scheduler_task_register_cli import build_parser, run_cli


def _write_ready_artifacts(tmp_path):
    handoff = tmp_path / "scheduler_handoff.json"
    task_plan = tmp_path / "scheduler_task_plan.json"
    task_plan.write_text(
        json.dumps(
            {
                "mode": "windows_task_scheduler_plan",
                "status": "ready",
                "task_name": "LongTermTraderNoSubmit",
                "order_submission_enabled": False,
                "schtasks_command": "schtasks /Create /F /SC DAILY /TN LongTermTraderNoSubmit /ST 09:35 /TR python",
                "powershell_command": "Register-ScheduledTask -TaskName LongTermTraderNoSubmit",
            }
        ),
        encoding="utf-8",
    )
    handoff.write_text(
        json.dumps(
            {
                "mode": "scheduler_handoff_check",
                "status": "ready",
                "ready": True,
                "scheduler_task_plan": str(task_plan),
                "checks": {
                    "scheduler_config_validation": "ready",
                    "recurring_no_submit_readiness": "ready",
                    "scheduler_task_plan": "ready",
                    "dashboard_manifest": "ready",
                    "order_submission_boundary": "ready",
                },
                "blockers": [],
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    return handoff, task_plan


def test_scheduler_task_register_cli_dry_run_does_not_register(tmp_path, capsys):
    handoff, task_plan = _write_ready_artifacts(tmp_path)
    output = tmp_path / "scheduler_task_registration_review.json"
    calls = []

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-handoff",
                str(handoff),
                "--output",
                str(output),
                "--json",
            ]
        ),
        command_runner=lambda command: calls.append(command) or 0,
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert printed == saved
    assert calls == []
    assert saved["mode"] == "windows_task_scheduler_registration_review"
    assert saved["status"] == "ready_for_registration_review"
    assert saved["registration_executed"] is False
    assert saved["order_submission_enabled"] is False
    assert saved["scheduler_handoff"] == str(handoff.resolve())
    assert saved["scheduler_task_plan"] == str(task_plan.resolve())
    assert saved["task_name"] == "LongTermTraderNoSubmit"
    assert "schtasks /Create" in saved["registration_command"]
    assert saved["next_safe_action"] == "rerun_with_confirm_register_only_if_operator_approves_windows_task"


def test_scheduler_task_register_cli_requires_confirm_token_to_register(tmp_path):
    handoff, _ = _write_ready_artifacts(tmp_path)

    try:
        run_cli(
            build_parser().parse_args(
                [
                    "--scheduler-handoff",
                    str(handoff),
                    "--register",
                    "--json",
                ]
            ),
            command_runner=lambda command: 0,
        )
    except ValueError as exc:
        assert "NO_SUBMIT_SCHEDULER_REGISTER" in str(exc)
    else:
        raise AssertionError("Expected registration without confirmation token to fail")


def test_scheduler_task_register_cli_registers_only_after_confirm_token(tmp_path, capsys):
    handoff, _ = _write_ready_artifacts(tmp_path)
    calls = []

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-handoff",
                str(handoff),
                "--register",
                "--confirm-register",
                "NO_SUBMIT_SCHEDULER_REGISTER",
                "--json",
            ]
        ),
        command_runner=lambda command: calls.append(command) or 0,
    )

    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert len(calls) == 1
    assert calls[0] == printed["registration_command"]
    assert printed["status"] == "registered"
    assert printed["registration_executed"] is True
    assert printed["registration_exit_code"] == 0


def test_scheduler_task_register_cli_blocks_unready_handoff(tmp_path):
    handoff, _ = _write_ready_artifacts(tmp_path)
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    payload["ready"] = False
    payload["blockers"] = ["recurring_no_submit_readiness_not_confirmed"]
    handoff.write_text(json.dumps(payload), encoding="utf-8")

    try:
        run_cli(
            build_parser().parse_args(["--scheduler-handoff", str(handoff), "--json"]),
            command_runner=lambda command: 0,
        )
    except ValueError as exc:
        assert "handoff is not ready" in str(exc)
    else:
        raise AssertionError("Expected blocked handoff to fail")
