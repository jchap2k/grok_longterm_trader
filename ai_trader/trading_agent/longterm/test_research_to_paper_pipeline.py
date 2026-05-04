import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_to_paper_pipeline import (
    PipelineStage,
    build_paper_preflight_stages,
    run_pipeline_stages,
    validate_stage_command,
)
from longterm.research_to_paper_pipeline_cli import build_parser, run_cli


def test_validate_stage_command_rejects_submit_commands():
    stage = PipelineStage(
        stage_id="bad_submit",
        title="Bad submit",
        command="python scripts/longterm_paper_execution.py --submit-paper-orders",
    )

    with pytest.raises(ValueError, match="submit command"):
        validate_stage_command(stage)


def test_print_plan_only_writes_summary_without_executing(tmp_path):
    executed: list[str] = []
    stages = [
        PipelineStage(
            stage_id="one",
            title="One",
            command="python -c \"print('one')\"",
            artifact_paths={"one": str(tmp_path / "one.json")},
        )
    ]
    summary_path = tmp_path / "pipeline_summary.json"

    result = run_pipeline_stages(
        stages,
        output_dir=tmp_path,
        summary_output=summary_path,
        print_plan_only=True,
        command_runner=lambda command: executed.append(command) or (0, "ran", ""),
    )

    saved = json.loads(summary_path.read_text(encoding="utf-8"))
    assert executed == []
    assert result.status == "planned"
    assert saved["status"] == "planned"
    assert saved["order_submission_enabled"] is False
    assert saved["stages"][0]["status"] == "planned"


def test_runner_stops_on_failed_stage_and_logs_output(tmp_path):
    commands: list[str] = []
    stages = [
        PipelineStage(stage_id="ok", title="OK", command="python ok.py"),
        PipelineStage(stage_id="fail", title="Fail", command="python fail.py"),
        PipelineStage(stage_id="never", title="Never", command="python never.py"),
    ]

    def fake_runner(command: str) -> tuple[int, str, str]:
        commands.append(command)
        if "fail.py" in command:
            return 7, "bad out", "bad err"
        return 0, "good out", ""

    result = run_pipeline_stages(
        stages,
        output_dir=tmp_path,
        summary_output=tmp_path / "pipeline_summary.json",
        command_runner=fake_runner,
    )

    assert commands == ["python ok.py", "python fail.py"]
    assert result.status == "failed"
    assert result.blocker_count == 1
    assert result.stage_results[-1].status == "failed"
    assert "bad err" in Path(result.stage_results[-1].log_path).read_text(encoding="utf-8")


def test_runner_writes_stdout_artifact_when_requested(tmp_path):
    preview_path = tmp_path / "paper_preview.json"
    stages = [
        PipelineStage(
            stage_id="paper_preview",
            title="Preview",
            command="python preview.py",
            artifact_paths={"paper_preview": str(preview_path)},
            stdout_artifact_path=str(preview_path),
        )
    ]

    result = run_pipeline_stages(
        stages,
        output_dir=tmp_path,
        summary_output=tmp_path / "pipeline_summary.json",
        command_runner=lambda command: (0, '{"preview_count": 1}', ""),
    )

    assert result.status == "completed"
    assert json.loads(preview_path.read_text(encoding="utf-8"))["preview_count"] == 1


def test_build_paper_preflight_stages_uses_existing_safe_scripts(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio):
        path.write_text("{}", encoding="utf-8")

    stages = build_paper_preflight_stages(
        output_dir=tmp_path,
        rules_path=rules_path,
        action_plan=action_plan,
        portfolio_state=portfolio,
        journal_db=journal,
        ledger_db=ledger,
        price_map=price_map,
        expected_cash=74000,
        profile_config="profile.json",
        skip_price_map=True,
    )

    stage_ids = [stage.stage_id for stage in stages]
    commands = "\n".join(stage.command for stage in stages)
    assert stage_ids == [
        "preflight_rules",
        "filter_action_plan",
        "paper_preview",
        "workflow_smoke",
        "paper_smoke_readiness",
        "paper_runbook",
        "runbook_check",
        "monday_operator_check",
        "status_refresh",
        "paper_lifecycle",
        "paper_trading_verification",
        "live_readiness_bundle",
        "operator_status_bundle",
    ]
    assert "scripts/longterm_action_plan_filter.py" in commands
    assert "scripts/longterm_paper_order_preview.py" in commands
    assert "scripts/longterm_paper_workflow_smoke.py" in commands
    assert "scripts/longterm_paper_runbook.py" in commands
    assert "--submit-paper-orders" not in commands
    assert all(stage.artifact_paths for stage in stages if stage.stage_id != "preflight_rules")


def test_pipeline_cli_print_plan_only_writes_json_summary(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio, price_map):
        path.write_text("{}", encoding="utf-8")
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "ledger.db"
    output_dir = tmp_path / "pipeline"
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(output_dir),
                "--rules-path",
                str(rules_path),
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(journal),
                "--ledger-db",
                str(ledger),
                "--price-map",
                str(price_map),
                "--skip-price-map",
                "--print-plan-only",
                "--summary-output",
                str(summary),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["status"] == "planned"
    assert saved["order_submission_enabled"] is False
    assert saved["stage_count"] == 13
