import json
from pathlib import Path

from longterm.scheduler_no_submit_smoke_cli import build_parser, run_cli
from longterm.test_scheduler_launch_packet import _ready_artifacts


def test_no_submit_smoke_writes_packet_markdown_and_summary(tmp_path, capsys):
    artifacts = _ready_artifacts(tmp_path)
    output_dir = tmp_path / "smoke"

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-config-validation",
                str(artifacts["validation"]),
                "--scheduler-task-plan",
                str(artifacts["task_plan"]),
                "--scheduler-handoff",
                str(artifacts["handoff"]),
                "--scheduler-task-registration",
                str(artifacts["registration"]),
                "--dashboard-manifest",
                str(artifacts["manifest"]),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    summary = json.loads((output_dir / "scheduler_no_submit_smoke.json").read_text(encoding="utf-8"))
    packet = json.loads((output_dir / "scheduler_launch_packet.json").read_text(encoding="utf-8"))
    markdown = (output_dir / "scheduler_launch_packet.md").read_text(encoding="utf-8")
    assert code == 0
    assert printed["status"] == "ready_for_no_submit_launch_review"
    assert summary["launch_packet"] == str(output_dir / "scheduler_launch_packet.json")
    assert summary["order_submission_enabled"] is False
    assert packet["chain"]["ready"] is True
    assert "Scheduler No-Submit Launch Packet" in markdown
