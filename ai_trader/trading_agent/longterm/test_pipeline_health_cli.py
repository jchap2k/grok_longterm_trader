import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.pipeline_health_cli import build_parser, run_cli


def test_pipeline_health_cli_reads_summary_and_writes_report(tmp_path, capsys):
    present = tmp_path / "selected.json"
    present.write_text(json.dumps([{"symbol": "MSFT"}]), encoding="utf-8")
    missing = tmp_path / "missing.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    report = tmp_path / "pipeline_health.json"
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "artifact_paths": {
                    "research_queue_selected": str(present),
                    "paper_preview": str(missing),
                },
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-summary",
                str(pipeline_summary),
                "--report-output",
                str(report),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert code == 1
    assert printed["status"] == "attention_required"
    assert saved["rollup"]["research_selection"]["selected_symbols"] == ["MSFT"]
    assert saved["health"]["missing"] == ["paper_preview"]


def test_pipeline_health_cli_can_require_artifacts(tmp_path, capsys):
    pipeline_summary = tmp_path / "pipeline_summary.json"
    pipeline_summary.write_text(json.dumps({"artifact_paths": {}}), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-summary",
                str(pipeline_summary),
                "--require-artifact",
                "operator_status_bundle",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert printed["missing_required_artifacts"] == ["operator_status_bundle"]


def test_pipeline_health_cli_reports_ready_when_all_artifacts_are_parseable(tmp_path, capsys):
    selected = tmp_path / "selected.json"
    preview = tmp_path / "preview.json"
    selected.write_text(json.dumps([{"symbol": "MSFT"}]), encoding="utf-8")
    preview.write_text(json.dumps({"ready_count": 1}), encoding="utf-8")
    pipeline_summary = tmp_path / "pipeline_summary.json"
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "order_submission_enabled": False,
                "artifact_paths": {
                    "research_queue_selected": str(selected),
                    "paper_preview": str(preview),
                },
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-summary",
                str(pipeline_summary),
                "--require-artifact",
                "paper_preview",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["status"] == "ready"
    assert printed["health"]["status"] == "ready"
    assert printed["rollup"]["paper_preview"]["ready_count"] == 1


def test_pipeline_health_cli_includes_scheduler_resource_controls(tmp_path, capsys):
    selected = tmp_path / "selected.json"
    selected.write_text(json.dumps([{"symbol": "MSFT"}]), encoding="utf-8")
    pipeline_summary = tmp_path / "pipeline_summary.json"
    scheduler_summary = tmp_path / "pipeline_scheduler_summary.json"
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "order_submission_enabled": False,
                "artifact_paths": {"research_queue_selected": str(selected)},
            }
        ),
        encoding="utf-8",
    )
    scheduler_summary.write_text(
        json.dumps(
            {
                "status": "planned",
                "runs": [
                    {
                        "status": "planned",
                        "resource_controls": {
                            "provider_mode": "perplexity",
                            "paid_provider_enabled": True,
                            "research_max_pass_count": 25,
                            "generated_committee_max_batches": 1,
                            "bounded": True,
                            "estimated_cost_usd": "unknown",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--pipeline-summary",
                str(pipeline_summary),
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["resource_controls"]["provider_mode"] == "perplexity"
    assert printed["resource_controls"]["bounded"] is True
    assert printed["resource_controls"]["research_max_pass_count"] == 25
