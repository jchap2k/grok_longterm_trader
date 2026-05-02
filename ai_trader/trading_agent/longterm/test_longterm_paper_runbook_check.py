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
        json.dumps({"ready_for_supervised_submit": True, "execution_audit": {"plan_id": "plan-1"}}),
        encoding="utf-8",
    )
    readiness.write_text(json.dumps({"ready_for_supervised_smoke": True}), encoding="utf-8")

    report = build_paper_runbook_check(workflow_smoke=workflow, paper_smoke_readiness=readiness)

    assert report["mode"] == "paper_runbook_check"
    assert report["ready_for_supervised_submit"] is True
    assert report["plan_id"] == "plan-1"
    assert report["generated_at"]
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


def test_paper_runbook_check_cli_outputs_json(tmp_path, capsys):
    workflow = tmp_path / "paper_workflow_smoke.json"
    readiness = tmp_path / "paper_smoke_readiness.json"
    report_path = tmp_path / "paper_runbook_check.json"
    workflow.write_text(
        json.dumps({"ready_for_supervised_submit": True, "execution_audit": {"plan_id": "plan-1"}}),
        encoding="utf-8",
    )
    readiness.write_text(json.dumps({"ready_for_supervised_smoke": True}), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--workflow-smoke",
            str(workflow),
            "--paper-smoke-readiness",
            str(readiness),
            "--report-output",
            str(report_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["ready_for_supervised_submit"] is True
    assert file_payload["ready_for_supervised_submit"] is True
