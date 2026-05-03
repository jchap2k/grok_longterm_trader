import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_monday_check import build_paper_monday_check, build_paper_monday_check_markdown
from longterm.paper_monday_check_cli import build_parser, run_cli


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_paper_monday_check_summarizes_ready_artifacts_and_redacted_submit(tmp_path):
    runbook = _write_json(
        tmp_path / "runbook.json",
        {
            "mode": "paper_runbook",
            "steps": [
                {"step_id": "supervised_submit", "command": "Submit command redacted.", "requires_explicit_reveal": True}
            ],
        },
    )
    workflow = _write_json(
        tmp_path / "workflow.json",
        {
            "schema_version": 2,
            "ready_for_supervised_submit": True,
            "blocker_count": 0,
            "promotion_summary": {"blocked_count": 0},
            "preview": {"allowed_count": 1, "blocked_count": 0, "no_order_count": 1},
            "execution_audit": {"ready_count": 1, "blocked_count": 0, "excluded_count": 1},
        },
    )
    readiness = _write_json(
        tmp_path / "readiness.json",
        {
            "schema_version": 2,
            "ready_for_supervised_smoke": True,
            "blocker_count": 0,
            "workflow_promotion_summary": {"blocked_count": 0},
            "account_cleanliness": {"clean": True, "position_count": 0, "unexpected_symbols": []},
        },
    )
    runbook_check = _write_json(
        tmp_path / "runbook_check.json",
        {"schema_version": 2, "ready_for_supervised_submit": True, "blocker_count": 0, "action_plan_hash": "abc123"},
    )
    status_refresh = _write_json(
        tmp_path / "status_refresh.json",
        {"submitted_order_count": 0, "refreshed_count": 0, "error_count": 0},
    )

    result = build_paper_monday_check(
        runbook=runbook,
        workflow_smoke=workflow,
        paper_smoke_readiness=readiness,
        runbook_check=runbook_check,
        status_refresh=status_refresh,
    )

    assert result["ready_for_review"] is True
    assert result["blocker_count"] == 0
    assert result["submit_command_revealed"] is False
    assert result["action_plan_hash_present"] is True
    assert result["account_clean"] is True
    assert result["leftover_position_count"] == 0
    assert result["workflow_preview_allowed_count"] == 1
    assert result["workflow_preview_no_order_count"] == 1
    assert result["workflow_execution_ready_count"] == 1
    assert result["workflow_execution_excluded_count"] == 1
    assert result["workflow_promotion_blocked_count"] == 0
    markdown = build_paper_monday_check_markdown(result)
    assert "Monday Paper Operator Check" in markdown
    assert "Workflow execution excluded: 1" in markdown


def test_paper_monday_check_blocks_old_or_promotion_blocked_artifacts(tmp_path):
    runbook = _write_json(tmp_path / "runbook.json", {"steps": []})
    workflow = _write_json(
        tmp_path / "workflow.json",
        {
            "schema_version": 2,
            "ready_for_supervised_submit": False,
            "promotion_summary": {"blocked_count": 1, "non_actionable_count": 1},
        },
    )
    readiness = _write_json(
        tmp_path / "readiness.json",
        {
            "schema_version": 2,
            "ready_for_supervised_smoke": False,
            "workflow_promotion_summary": {"blocked_count": 1, "non_actionable_count": 1},
        },
    )
    runbook_check = _write_json(
        tmp_path / "runbook_check.json",
        {
            "schema_version": 1,
            "ready_for_supervised_submit": False,
            "action_plan_hash": "abc123",
        },
    )

    result = build_paper_monday_check(
        runbook=runbook,
        workflow_smoke=workflow,
        paper_smoke_readiness=readiness,
        runbook_check=runbook_check,
    )

    assert "workflow_buy_promotion_blockers" in result["blockers"]
    assert "paper_smoke_buy_promotion_blockers" in result["blockers"]
    assert "runbook_check_schema_too_old" in result["blockers"]
    assert result["workflow_promotion_blocked_count"] == 1


def test_paper_monday_check_marks_revealed_submit_for_manual_review_without_blocking(tmp_path):
    runbook = _write_json(
        tmp_path / "runbook.json",
        {
            "steps": [
                {
                    "step_id": "supervised_submit",
                    "command": "python scripts/longterm_paper_execution.py --submit-paper-orders",
                    "requires_explicit_reveal": False,
                }
            ]
        },
    )
    workflow = _write_json(tmp_path / "workflow.json", {"schema_version": 2, "ready_for_supervised_submit": True})
    readiness = _write_json(
        tmp_path / "readiness.json",
        {
            "schema_version": 2,
            "ready_for_supervised_smoke": True,
            "account_cleanliness": {"clean": True, "position_count": 0, "unexpected_symbols": []},
        },
    )
    runbook_check = _write_json(
        tmp_path / "runbook_check.json",
        {"schema_version": 2, "ready_for_supervised_submit": True, "action_plan_hash": "abc123"},
    )

    result = build_paper_monday_check(
        runbook=runbook,
        workflow_smoke=workflow,
        paper_smoke_readiness=readiness,
        runbook_check=runbook_check,
    )

    assert result["ready_for_review"] is True
    assert result["submit_command_revealed"] is True
    assert result["manual_submit_review_required"] is True
    assert "submit_command_revealed" not in result["blockers"]


def test_paper_monday_check_blocks_on_leftover_position_even_if_submit_revealed(tmp_path):
    runbook = _write_json(
        tmp_path / "runbook.json",
        {
            "steps": [
                {
                    "step_id": "supervised_submit",
                    "command": "python scripts/longterm_paper_execution.py --submit-paper-orders",
                    "requires_explicit_reveal": False,
                }
            ]
        },
    )
    workflow = _write_json(tmp_path / "workflow.json", {"schema_version": 2, "ready_for_supervised_submit": True})
    readiness = _write_json(
        tmp_path / "readiness.json",
        {
            "schema_version": 2,
            "ready_for_supervised_smoke": True,
            "account_cleanliness": {"clean": False, "position_count": 1, "unexpected_symbols": ["NVDA"]},
        },
    )
    runbook_check = _write_json(
        tmp_path / "runbook_check.json",
        {"schema_version": 2, "ready_for_supervised_submit": True, "action_plan_hash": "abc123"},
    )

    result = build_paper_monday_check(
        runbook=runbook,
        workflow_smoke=workflow,
        paper_smoke_readiness=readiness,
        runbook_check=runbook_check,
    )

    assert "paper_account_not_clean" in result["blockers"]
    assert result["leftover_symbols"] == ["NVDA"]


def test_paper_monday_check_cli_outputs_json(tmp_path, capsys):
    runbook = _write_json(tmp_path / "runbook.json", {"steps": []})
    workflow = _write_json(tmp_path / "workflow.json", {"schema_version": 2, "ready_for_supervised_submit": False})
    readiness = _write_json(tmp_path / "readiness.json", {"schema_version": 2, "ready_for_supervised_smoke": False})
    runbook_check = _write_json(tmp_path / "runbook_check.json", {"schema_version": 2, "ready_for_supervised_submit": False})

    args = build_parser().parse_args(
        [
            "--runbook",
            str(runbook),
            "--workflow-smoke",
            str(workflow),
            "--paper-smoke-readiness",
            str(readiness),
            "--runbook-check",
            str(runbook_check),
            "--json",
        ]
    )

    assert run_cli(args) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "paper_monday_operator_check"
    assert payload["ready_for_review"] is False
