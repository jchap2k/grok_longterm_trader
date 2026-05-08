import json
from pathlib import Path

from longterm.scheduler_profile_cli import build_parser, run_cli


def test_scheduler_profile_cli_writes_and_validates_local_profile(tmp_path, capsys):
    template = tmp_path / "template.json"
    output_profile = tmp_path / "ongoing_no_submit_scheduler.local.json"
    output_dir = tmp_path / "scheduler_runs"
    journal_db = tmp_path / "journal.db"
    ledger_db = tmp_path / "paper_ledger.db"
    action_plan = tmp_path / "account_action_plan.json"
    profile_config = tmp_path / "roth_ira_profile.json"
    rules_path = tmp_path / "active_rules.txt"
    validation_summary = tmp_path / "scheduler_profile_validation.json"

    rules_path.write_text("<rules />", encoding="utf-8")
    template.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "description": "test template",
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": "path/to/runs",
                    "journal_db": "path/to/journal.db",
                    "ledger_db": "path/to/ledger.db",
                    "action_plan": "path/to/action_plan.json",
                    "profile_config": "path/to/profile.json",
                    "rules_path": "path/to/rules.txt",
                    "validate_config_only": True,
                    "json": True,
                },
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--template",
                str(template),
                "--output-profile",
                str(output_profile),
                "--set",
                f"output_dir={output_dir}",
                "--set",
                f"journal_db={journal_db}",
                "--set",
                f"ledger_db={ledger_db}",
                "--set",
                f"action_plan={action_plan}",
                "--set",
                f"profile_config={profile_config}",
                "--set",
                f"rules_path={rules_path}",
                "--set",
                f"summary_output={validation_summary}",
                "--set",
                f"scheduler_config_validation={validation_summary}",
                "--enable",
                "allow_existing_paper_positions",
                "--enable",
                "expected_cash_from_portfolio_state",
                "--validate-after-write",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    profile_payload = json.loads(output_profile.read_text(encoding="utf-8"))
    profile_args = profile_payload["args"]
    validation_payload = json.loads(validation_summary.read_text(encoding="utf-8"))

    assert code == 0
    assert printed["status"] == "ready"
    assert printed["profile"] == str(output_profile.resolve())
    assert printed["validation_summary"] == str(validation_summary.resolve())
    assert profile_args["validate_config_only"] is True
    assert profile_args["preset"] == "ongoing-no-submit"
    assert profile_args["output_dir"] == str(output_dir)
    assert profile_args["journal_db"] == str(journal_db)
    assert profile_args["ledger_db"] == str(ledger_db)
    assert profile_args["action_plan"] == str(action_plan)
    assert profile_args["profile_config"] == str(profile_config)
    assert profile_args["rules_path"] == str(rules_path)
    assert profile_args["summary_output"] == str(validation_summary)
    assert profile_args["scheduler_config_validation"] == str(validation_summary)
    assert profile_args["allow_existing_paper_positions"] is True
    assert profile_args["expected_cash_from_portfolio_state"] is True
    assert "submit_paper_orders" not in profile_args
    assert "confirm_paper_submit" not in profile_args
    assert validation_payload["mode"] == "pipeline_scheduler_config_validation"
    assert validation_payload["status"] == "ready"
    assert validation_payload["order_submission_enabled"] is False
    assert validation_payload["config_file"] == str(output_profile.resolve())
    assert validation_payload["resource_controls"]["bounded"] is True
    assert "--scheduler-config-validation" in validation_payload["commands"]["account_refresh"]
    assert str(validation_summary.resolve()) in validation_payload["commands"]["account_refresh"]
    assert not output_dir.exists()


def test_scheduler_profile_cli_rejects_unknown_override(tmp_path):
    template = tmp_path / "template.json"
    output_profile = tmp_path / "local.json"
    template.write_text(json.dumps({"args": {"preset": "ongoing-no-submit"}}), encoding="utf-8")

    try:
        run_cli(
            build_parser().parse_args(
                [
                    "--template",
                    str(template),
                    "--output-profile",
                    str(output_profile),
                    "--set",
                    "not_a_real_scheduler_arg=value",
                ]
            )
        )
    except ValueError as exc:
        assert "Unknown scheduler config arg" in str(exc)
    else:
        raise AssertionError("Expected unknown override to fail closed")
