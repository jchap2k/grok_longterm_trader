import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_runbook_check import build_paper_runbook_check, build_paper_runbook_check_markdown
from longterm.paper_runbook_check_cli import build_parser, run_cli


def test_paper_runbook_check_passes_when_artifacts_are_ready(tmp_path):
    workflow = tmp_path / "paper_workflow_smoke.json"
    readiness = tmp_path / "paper_smoke_readiness.json"
    workflow.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "ready_for_supervised_submit": True,
                "promotion_summary": {"blocked_count": 0},
                "execution_audit": {"plan_id": "plan-1"},
            }
        ),
        encoding="utf-8",
    )
    readiness.write_text(
        json.dumps({"schema_version": 2, "ready_for_supervised_smoke": True, "workflow_promotion_summary": {"blocked_count": 0}}),
        encoding="utf-8",
    )

    action_plan = {"plan_id": "plan-1", "intents": [{"symbol": "NVDA", "decision_id": "decision-1"}]}
    report = build_paper_runbook_check(
        workflow_smoke=workflow,
        paper_smoke_readiness=readiness,
        action_plan=action_plan,
    )

    assert report["mode"] == "paper_runbook_check"
    assert report["ready_for_supervised_submit"] is True
    assert report["plan_id"] == "plan-1"
    assert report["action_plan_hash"]
    assert report["generated_at"]
    assert report["schema_version"] == 2
    assert report["promotion_summary"]["workflow_blocked_count"] == 0
    assert report["blockers"] == []
    assert "Ready for supervised submit: yes" in build_paper_runbook_check_markdown(report)


def test_paper_runbook_check_blocks_missing_or_not_ready_artifacts(tmp_path):
    workflow = tmp_path / "paper_workflow_smoke.json"
    readiness = tmp_path / "paper_smoke_readiness.json"
    workflow.write_text(json.dumps({"ready_for_supervised_submit": False}), encoding="utf-8")

    report = build_paper_runbook_check(workflow_smoke=workflow, paper_smoke_readiness=readiness)

    assert report["ready_for_supervised_submit"] is False
    assert "workflow_smoke_not_ready" in report["blockers"]
    assert "paper_smoke_readiness_missing" in report["blockers"]


def test_paper_runbook_check_blocks_old_or_promotion_blocked_artifacts(tmp_path):
    old_workflow = tmp_path / "old_workflow.json"
    old_readiness = tmp_path / "old_readiness.json"
    old_workflow.write_text(json.dumps({"schema_version": 1, "ready_for_supervised_submit": True}), encoding="utf-8")
    old_readiness.write_text(json.dumps({"schema_version": 1, "ready_for_supervised_smoke": True}), encoding="utf-8")

    old_report = build_paper_runbook_check(workflow_smoke=old_workflow, paper_smoke_readiness=old_readiness)

    assert old_report["ready_for_supervised_submit"] is False
    assert "workflow_smoke_schema_too_old" in old_report["blockers"]
    assert "paper_smoke_readiness_schema_too_old" in old_report["blockers"]

    workflow = tmp_path / "promotion_workflow.json"
    readiness = tmp_path / "promotion_readiness.json"
    workflow.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "ready_for_supervised_submit": True,
                "promotion_summary": {"blocked_count": 1, "non_actionable_count": 1},
            }
        ),
        encoding="utf-8",
    )
    readiness.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "ready_for_supervised_smoke": True,
                "workflow_promotion_summary": {"blocked_count": 1, "non_actionable_count": 1},
            }
        ),
        encoding="utf-8",
    )

    promotion_report = build_paper_runbook_check(workflow_smoke=workflow, paper_smoke_readiness=readiness)

    assert promotion_report["ready_for_supervised_submit"] is False
    assert "workflow_buy_promotion_blockers" in promotion_report["blockers"]
    assert "paper_smoke_readiness_buy_promotion_blockers" in promotion_report["blockers"]
    assert promotion_report["promotion_summary"]["workflow_blocked_count"] == 1


def test_paper_runbook_check_cli_outputs_json(tmp_path, capsys):
    workflow = tmp_path / "paper_workflow_smoke.json"
    readiness = tmp_path / "paper_smoke_readiness.json"
    report_path = tmp_path / "paper_runbook_check.json"
    action_plan_path = tmp_path / "action_plan.json"
    action_plan_path.write_text(
        json.dumps({"plan_id": "plan-1", "intents": [{"symbol": "NVDA", "decision_id": "decision-1"}]}),
        encoding="utf-8",
    )
    workflow.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "ready_for_supervised_submit": True,
                "promotion_summary": {"blocked_count": 0},
                "execution_audit": {"plan_id": "plan-1"},
            }
        ),
        encoding="utf-8",
    )
    readiness.write_text(json.dumps({"schema_version": 2, "ready_for_supervised_smoke": True}), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--workflow-smoke",
            str(workflow),
            "--paper-smoke-readiness",
            str(readiness),
            "--action-plan",
            str(action_plan_path),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["ready_for_supervised_submit"] is True
    assert payload["action_plan_hash"]
    assert file_payload["ready_for_supervised_submit"] is True
