import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from longterm.paper_submit_mode_plan import (
    PaperSubmitModePlanInputs,
    build_paper_submit_mode_plan,
    build_parser,
    run_cli,
)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ready_artifacts(
    tmp_path: Path,
    *,
    generated_at: str | None = None,
    success_count: int = 3,
) -> tuple[Path, Path, Path]:
    generated_at = generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    handoff = _write_json(
        tmp_path / "scheduler_handoff.json",
        {
            "mode": "scheduler_handoff_check",
            "status": "ready",
            "ready": True,
            "generated_at": generated_at,
            "checks": {"order_submission_boundary": "ready"},
            "order_submission_enabled": False,
            "blockers": [],
        },
    )
    scheduler = _write_json(
        tmp_path / "pipeline_scheduler_summary.json",
        {
            "mode": "pipeline_scheduler",
            "status": "completed",
            "success_count": success_count,
            "error_count": 0,
            "order_submission_enabled": False,
            "runs": [
                {"status": "completed", "position_review_queue_exit_code": 0}
                for _ in range(success_count)
            ],
        },
    )
    position_queue = _write_json(
        tmp_path / "position_review_queue.json",
        {
            "mode": "position_review_queue",
            "status": "completed",
            "order_submission_enabled": False,
            "llm_calls_enabled": False,
            "broker_calls_enabled": False,
            "review_count": 1,
            "review_queue": [{"symbol": "MSFT", "review_type": "thesis_news_review"}],
        },
    )
    return handoff, scheduler, position_queue


def test_submit_mode_plan_reports_ready_without_emitting_submit_command(tmp_path):
    handoff, scheduler, position_queue = _ready_artifacts(tmp_path)

    report = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=handoff,
            pipeline_scheduler_summary=scheduler,
            position_review_queue=position_queue,
        ),
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    assert report["status"] == "ready_for_manual_review"
    assert report["blockers"] == []
    assert report["order_submission_enabled"] is False
    assert report["submit_profile_enabled"] is False
    assert report["broker_calls_enabled"] is False
    assert report["runnable_submit_command_emitted"] is False
    assert "submit_command" not in report
    assert report["checks"]["scheduler_handoff"] == "ready"
    assert report["checks"]["no_submit_scheduler_soak"] == "ready"
    assert report["checks"]["position_review_queue"] == "ready"
    assert report["next_safe_action"] == "manual_review_required_before_submit_profile"


def test_submit_mode_plan_blocks_until_min_clean_scheduler_runs(tmp_path):
    handoff, scheduler, position_queue = _ready_artifacts(tmp_path, success_count=2)

    report = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=handoff,
            pipeline_scheduler_summary=scheduler,
            position_review_queue=position_queue,
        ),
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    assert report["status"] == "blocked"
    assert "no_submit_clean_run_count_below_minimum" in report["blockers"]
    assert report["checks"]["no_submit_scheduler_soak"] == "blocked"
    assert report["scheduler_soak"]["required_clean_runs"] == 3
    assert report["scheduler_soak"]["observed_clean_runs"] == 2


def test_submit_mode_plan_blocks_missing_or_stale_handoff(tmp_path):
    _, scheduler, position_queue = _ready_artifacts(
        tmp_path,
        generated_at=(datetime(2026, 5, 6, 8, tzinfo=UTC)).isoformat().replace("+00:00", "Z"),
    )

    missing = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=tmp_path / "missing_handoff.json",
            pipeline_scheduler_summary=scheduler,
            position_review_queue=position_queue,
        ),
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )
    stale = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=tmp_path / "scheduler_handoff.json",
            pipeline_scheduler_summary=scheduler,
            position_review_queue=position_queue,
            max_handoff_age_hours=24,
        ),
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    assert missing["status"] == "blocked"
    assert "scheduler_handoff_missing_or_unreadable" in missing["blockers"]
    assert stale["status"] == "blocked"
    assert "scheduler_handoff_stale" in stale["blockers"]


@pytest.mark.parametrize(
    ("artifact_name", "field"),
    [
        ("scheduler_handoff.json", "order_submission_enabled"),
        ("pipeline_scheduler_summary.json", "order_submission_enabled"),
        ("position_review_queue.json", "broker_calls_enabled"),
    ],
)
def test_submit_mode_plan_blocks_submit_or_broker_flags(tmp_path, artifact_name, field):
    handoff, scheduler, position_queue = _ready_artifacts(tmp_path)
    path_by_name = {
        "scheduler_handoff.json": handoff,
        "pipeline_scheduler_summary.json": scheduler,
        "position_review_queue.json": position_queue,
    }
    payload = json.loads(path_by_name[artifact_name].read_text(encoding="utf-8"))
    payload[field] = True
    path_by_name[artifact_name].write_text(json.dumps(payload), encoding="utf-8")

    report = build_paper_submit_mode_plan(
        PaperSubmitModePlanInputs(
            scheduler_handoff=handoff,
            pipeline_scheduler_summary=scheduler,
            position_review_queue=position_queue,
        ),
        now_func=lambda: datetime(2026, 5, 8, 16, tzinfo=UTC),
    )

    assert report["status"] == "blocked"
    assert any("submission" in blocker or "broker" in blocker for blocker in report["blockers"])


def test_submit_mode_plan_cli_writes_json(tmp_path, capsys):
    handoff, scheduler, position_queue = _ready_artifacts(tmp_path)
    output = tmp_path / "paper_submit_mode_plan.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--scheduler-handoff",
                str(handoff),
                "--pipeline-scheduler-summary",
                str(scheduler),
                "--position-review-queue",
                str(position_queue),
                "--min-clean-scheduler-runs",
                "3",
                "--output",
                str(output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed == saved
    assert saved["status"] == "ready_for_manual_review"
