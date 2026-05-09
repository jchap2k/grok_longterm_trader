import json
from pathlib import Path

from longterm.scheduler_soak_plan_cli import build_parser, run_cli


def _profile(path: Path, **overrides) -> Path:
    args = {
        "preset": "ongoing-no-submit",
        "validate_config_only": False,
        "output_dir": str(path.parent / "scheduler_runs"),
        "journal_db": str(path.parent / "journal.db"),
        "ledger_db": str(path.parent / "paper_ledger.db"),
        "action_plan": str(path.parent / "account_action_plan.json"),
        "max_cycles": 1,
        "run_interval_seconds": 0,
        "order_submission_enabled": False,
    }
    args.update(overrides)
    path.write_text(json.dumps({"args": args}), encoding="utf-8")
    return path


def test_scheduler_soak_plan_cli_writes_preview_without_running_scheduler(tmp_path, capsys):
    profile = _profile(tmp_path / "ongoing_no_submit_scheduler.run.json")
    output = tmp_path / "scheduler_soak_plan.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--profile-file",
                str(profile),
                "--output",
                str(output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["status"] == "ready_for_no_submit_soak_review"
    assert saved["scheduler_executed"] is False
    assert saved["order_submission_enabled"] is False
    assert "longterm_pipeline_scheduler.py" in saved["preview_command"]
    assert "pipeline_scheduler_summary" in saved["expected_artifacts"]


def test_scheduler_soak_plan_cli_accepts_real_scheduler_profile_control_names(tmp_path, capsys):
    profile = _profile(
        tmp_path / "ongoing_no_submit_scheduler.run.json",
        max_cycles=None,
        run_interval_seconds=None,
        max_runs=1,
        interval_seconds=3600,
    )
    output = tmp_path / "scheduler_soak_plan.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--profile-file",
                str(profile),
                "--output",
                str(output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["status"] == "ready_for_no_submit_soak_review"
    assert saved["resource_controls"]["max_runs"] == 1
    assert saved["resource_controls"]["interval_seconds"] == 3600
    assert saved["order_submission_enabled"] is False


def test_scheduler_soak_plan_blocks_submit_capable_profile(tmp_path):
    profile = _profile(tmp_path / "ongoing_no_submit_scheduler.run.json", submit_paper_orders=True)

    try:
        run_cli(build_parser().parse_args(["--profile-file", str(profile)]))
    except ValueError as exc:
        assert "Submit-capable" in str(exc)
    else:
        raise AssertionError("Expected submit-capable soak profile to fail closed")
