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
    workflow = _write_json(tmp_path / "workflow.json", {"ready_for_supervised_submit": True, "blocker_count": 0})
    readiness = _write_json(
        tmp_path / "readiness.json",
        {
            "ready_for_supervised_smoke": True,
            "blocker_count": 0,
            "account_cleanliness": {"clean": True, "position_count": 0, "unexpected_symbols": []},
        },
    )
    runbook_check = _write_json(
        tmp_path / "runbook_check.json",
        {"ready_for_supervised_submit": True, "blocker_count": 0, "action_plan_hash": "abc123"},
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
    assert "Monday Paper Operator Check" in build_paper_monday_check_markdown(result)


def test_paper_monday_check_blocks_on_revealed_submit_and_leftover_position(tmp_path):
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
    workflow = _write_json(tmp_path / "workflow.json", {"ready_for_supervised_submit": True})
    readiness = _write_json(
        tmp_path / "readiness.json",
        {
            "ready_for_supervised_smoke": True,
            "account_cleanliness": {"clean": False, "position_count": 1, "unexpected_symbols": ["NVDA"]},
        },
    )
    runbook_check = _write_json(tmp_path / "runbook_check.json", {"ready_for_supervised_submit": True})

    result = build_paper_monday_check(
        runbook=runbook,
        workflow_smoke=workflow,
        paper_smoke_readiness=readiness,
        runbook_check=runbook_check,
    )

    assert "submit_command_revealed" in result["blockers"]
    assert "paper_account_not_clean" in result["blockers"]
    assert result["leftover_symbols"] == ["NVDA"]


def test_paper_monday_check_cli_outputs_json(tmp_path, capsys):
    runbook = _write_json(tmp_path / "runbook.json", {"steps": []})
    workflow = _write_json(tmp_path / "workflow.json", {"ready_for_supervised_submit": False})
    readiness = _write_json(tmp_path / "readiness.json", {"ready_for_supervised_smoke": False})
    runbook_check = _write_json(tmp_path / "runbook_check.json", {"ready_for_supervised_submit": False})

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
