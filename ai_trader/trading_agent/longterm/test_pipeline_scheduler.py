import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm import pipeline_scheduler as pipeline_scheduler_module
from longterm.pipeline_scheduler import (
    PipelineSchedulerConfig,
    PipelineSchedulerInputs,
    run_pipeline_scheduler,
    validate_scheduler_command_template,
)
from longterm.pipeline_scheduler_cli import build_parser, parse_args, run_cli


class FakeClock:
    def __init__(self) -> None:
        self.index = 0

    def now(self):
        from datetime import datetime, timezone

        value = datetime(2026, 5, 4, 15, self.index, tzinfo=timezone.utc)
        self.index += 1
        return value


def _safe_pipeline_template() -> str:
    return (
        "python scripts/longterm_research_to_paper_pipeline.py "
        "--output-dir {pipeline_output_dir} "
        "--action-plan action_plan.json "
        "--portfolio-state {portfolio_state} "
        "--journal-db journal.db "
        "--ledger-db ledger.db "
        "--print-plan-only"
    )


def _safe_pre_pipeline_refresh_template() -> str:
    return (
        "python scripts/longterm_alpaca_paper_snapshot.py "
        "--portfolio-state-output {portfolio_state}"
    )


def _safe_account_refresh_template() -> str:
    return (
        "python scripts/longterm_paper_account_refresh.py "
        "--journal-db journal.db "
        "--action-plan action_plan.json "
        "--paper-ledger-db ledger.db "
        "--pipeline-summary {pipeline_summary} "
        "--output-dir {account_refresh_output_dir}"
    )


def _safe_scheduler_policy_template() -> str:
    return (
        "python scripts/longterm_pipeline_scheduler_policy.py "
        "--rules-path {rules_path} "
        "--market-regime market_regime.json "
        "--journal-db journal.db "
        "--policy-state {scheduler_policy_state} "
        "--state-output {scheduler_policy_state} "
        "--pipeline-scheduler-summary {scheduler_summary} "
        "--pipeline-summary {pipeline_summary} "
        "--report-output {scheduler_policy} "
        "--json"
    )


def _safe_post_run_verification_template() -> str:
    return (
        "python scripts/longterm_pipeline_scheduler_verify.py "
        "--pipeline-scheduler-summary {scheduler_summary} "
        "--policy-state {scheduler_policy_state} "
        "--report-output {post_run_verification} "
        "--json"
    )


def _safe_portfolio_news_monitor_template() -> str:
    return (
        "python scripts/longterm_portfolio_news_monitor.py "
        "--portfolio-state {portfolio_state} "
        "--snapshot-file news_snapshot.json "
        "--journal-db journal.db "
        "--output {portfolio_news_monitor} "
        "--json"
    )


def _safe_committee_preset_policy_template() -> str:
    return (
        "python scripts/longterm_committee_preset_policy.py "
        "--action-plan action_plan.json "
        "--market-regime market_regime.json "
        "--active-sleeve-value 100000 "
        "--report-output {committee_preset_policy} "
        "--json"
    )


def test_default_scheduler_command_runner_uses_trading_agent_cwd(monkeypatch):
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr(pipeline_scheduler_module.subprocess, "run", fake_run)

    code, stdout, stderr = pipeline_scheduler_module._run_command("python scripts/example.py")

    assert code == 0
    assert stdout == "ok"
    assert stderr == ""
    assert captured["command"] == "python scripts/example.py"
    assert captured["cwd"] == Path(__file__).resolve().parents[1]
    assert captured["shell"] is True
    assert captured["text"] is True
    assert captured["capture_output"] is True


def test_validate_scheduler_command_template_rejects_submit_and_chaining_fragments(tmp_path):
    bad_commands = [
        _safe_pipeline_template() + " --submit-paper-orders",
        _safe_pipeline_template() + " SUPERVISED_PAPER",
        _safe_pipeline_template().replace(
            "longterm_research_to_paper_pipeline.py",
            "longterm_paper_execution.py",
        ),
        _safe_pipeline_template() + " && python other.py",
        _safe_pipeline_template() + " | python other.py",
        _safe_pipeline_template() + "\npython other.py",
    ]

    for command in bad_commands:
        with pytest.raises(ValueError):
            validate_scheduler_command_template(
                command,
                command_kind="pipeline",
                rules_path=tmp_path / "active_rules.txt",
            )


def test_validate_scheduler_command_template_requires_portfolio_journal_and_rules_context(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    with pytest.raises(ValueError, match="--portfolio-state"):
        validate_scheduler_command_template(
            _safe_pipeline_template().replace("--portfolio-state {portfolio_state} ", ""),
            command_kind="pipeline",
            rules_path=rules_path,
        )

    with pytest.raises(ValueError, match="--journal-db"):
        validate_scheduler_command_template(
            _safe_pipeline_template().replace("--journal-db journal.db ", ""),
            command_kind="pipeline",
            rules_path=rules_path,
        )

    with pytest.raises(ValueError, match="rules_path"):
        validate_scheduler_command_template(
            _safe_pipeline_template(),
            command_kind="pipeline",
            rules_path=tmp_path / "missing_active_rules.txt",
        )


def test_validate_scheduler_policy_command_template_requires_policy_script_rules_and_output(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    validate_scheduler_command_template(
        _safe_scheduler_policy_template(),
        command_kind="scheduler_policy",
        rules_path=rules_path,
    )
    with pytest.raises(ValueError, match="longterm_pipeline_scheduler_policy.py"):
        validate_scheduler_command_template(
            _safe_scheduler_policy_template().replace(
                "longterm_pipeline_scheduler_policy.py",
                "longterm_research_to_paper_pipeline.py",
            ),
            command_kind="scheduler_policy",
            rules_path=rules_path,
        )
    with pytest.raises(ValueError, match="--rules-path"):
        validate_scheduler_command_template(
            _safe_scheduler_policy_template().replace("--rules-path {rules_path} ", ""),
            command_kind="scheduler_policy",
            rules_path=rules_path,
        )
    with pytest.raises(ValueError, match="--report-output"):
        validate_scheduler_command_template(
            _safe_scheduler_policy_template().replace("--report-output {scheduler_policy} ", ""),
            command_kind="scheduler_policy",
            rules_path=rules_path,
        )


def test_validate_committee_preset_policy_template_requires_policy_script_and_output(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    validate_scheduler_command_template(
        _safe_committee_preset_policy_template(),
        command_kind="committee_preset_policy",
        rules_path=rules_path,
    )
    with pytest.raises(ValueError, match="longterm_committee_preset_policy.py"):
        validate_scheduler_command_template(
            _safe_committee_preset_policy_template().replace(
                "longterm_committee_preset_policy.py",
                "longterm_pipeline_scheduler_policy.py",
            ),
            command_kind="committee_preset_policy",
            rules_path=rules_path,
        )
    with pytest.raises(ValueError, match="--report-output"):
        validate_scheduler_command_template(
            _safe_committee_preset_policy_template().replace("--report-output {committee_preset_policy} ", ""),
            command_kind="committee_preset_policy",
            rules_path=rules_path,
        )


def test_validate_post_run_verification_template_requires_verifier_summary_and_output(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    validate_scheduler_command_template(
        _safe_post_run_verification_template(),
        command_kind="post_run_verification",
        rules_path=rules_path,
    )
    with pytest.raises(ValueError, match="longterm_pipeline_scheduler_verify.py"):
        validate_scheduler_command_template(
            _safe_post_run_verification_template().replace(
                "longterm_pipeline_scheduler_verify.py",
                "longterm_pipeline_scheduler_policy.py",
            ),
            command_kind="post_run_verification",
            rules_path=rules_path,
        )
    with pytest.raises(ValueError, match="--pipeline-scheduler-summary"):
        validate_scheduler_command_template(
            _safe_post_run_verification_template().replace(
                "--pipeline-scheduler-summary {scheduler_summary} ",
                "",
            ),
            command_kind="post_run_verification",
            rules_path=rules_path,
        )
    with pytest.raises(ValueError, match="--report-output"):
        validate_scheduler_command_template(
            _safe_post_run_verification_template().replace("--report-output {post_run_verification} ", ""),
            command_kind="post_run_verification",
            rules_path=rules_path,
        )


def test_validate_portfolio_news_monitor_template_requires_script_and_output(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    validate_scheduler_command_template(
        _safe_portfolio_news_monitor_template(),
        command_kind="portfolio_news_monitor",
        rules_path=rules_path,
    )

    with pytest.raises(ValueError, match="longterm_portfolio_news_monitor.py"):
        validate_scheduler_command_template(
            _safe_portfolio_news_monitor_template().replace(
                "longterm_portfolio_news_monitor.py",
                "longterm_other.py",
            ),
            command_kind="portfolio_news_monitor",
            rules_path=rules_path,
        )
    with pytest.raises(ValueError, match="--output"):
        validate_scheduler_command_template(
            _safe_portfolio_news_monitor_template().replace("--output {portfolio_news_monitor} ", ""),
            command_kind="portfolio_news_monitor",
            rules_path=rules_path,
        )


def test_print_plan_only_writes_rendered_summary_without_running_commands(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    executed: list[str] = []

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1, print_plan_only=True),
        command_runner=lambda command: executed.append(command) or (0, "{}", ""),
        now_func=FakeClock().now,
    )

    saved = json.loads((tmp_path / "scheduler" / "pipeline_scheduler_summary.json").read_text(encoding="utf-8"))
    command = saved["runs"][0]["pipeline_command"]
    assert executed == []
    assert summary.status == "planned"
    assert saved["order_submission_enabled"] is False
    assert saved["runs"][0]["status"] == "planned"
    assert "--summary-output" in command
    assert "--rules-path" in command
    assert "{pipeline_summary}" not in command


def test_print_plan_only_renders_portfolio_news_monitor_command(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            portfolio_news_monitor_command_template=_safe_portfolio_news_monitor_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1, print_plan_only=True),
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert summary.status == "planned"
    assert "longterm_portfolio_news_monitor.py" in run.portfolio_news_monitor_command
    assert "portfolio_news_monitor.json" in run.portfolio_news_monitor_command
    assert "{portfolio_news_monitor}" not in run.portfolio_news_monitor_command
    assert run.portfolio_news_monitor_path.endswith("portfolio_news_monitor.json")


def test_successful_scheduler_run_writes_command_logs_health_and_summary(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        summary_marker = "--summary-output "
        summary_path = Path(command.split(summary_marker, 1)[1].split(" --", 1)[0].strip('"'))
        selected = summary_path.parent / "selected.json"
        selected.write_text(json.dumps([{"symbol": "MSFT"}]), encoding="utf-8")
        summary_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "order_submission_enabled": False,
                    "artifact_paths": {"research_queue_selected": str(selected)},
                }
            ),
            encoding="utf-8",
        )
        return 0, "pipeline out", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert summary.status == "completed"
    assert calls == [run.pipeline_command]
    assert Path(run.pipeline_stdout_path).read_text(encoding="utf-8") == "pipeline out"
    health = json.loads(Path(run.pipeline_health_path).read_text(encoding="utf-8"))
    assert health["status"] == "ready"
    assert health["rollup"]["research_selection"]["selected_symbols"] == ["MSFT"]


def test_portfolio_news_monitor_runs_before_pipeline_and_path_is_forwarded(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_portfolio_news_monitor.py" in command:
            monitor_path = Path(command.split("--output ", 1)[1].split(" --", 1)[0].strip('"'))
            monitor_path.write_text(json.dumps({"status": "completed", "enrichment_needed_queue": []}), encoding="utf-8")
            return 0, "monitor", ""
        summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
        summary_path.write_text(json.dumps({"status": "completed", "artifact_paths": {}}), encoding="utf-8")
        return 0, "pipeline", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template() + " --portfolio-news-monitor {portfolio_news_monitor}",
            portfolio_news_monitor_command_template=_safe_portfolio_news_monitor_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert summary.status == "completed"
    assert "longterm_portfolio_news_monitor.py" in calls[0]
    assert "longterm_research_to_paper_pipeline.py" in calls[1]
    assert run.portfolio_news_monitor_exit_code == 0
    assert Path(run.portfolio_news_monitor_stdout_path).read_text(encoding="utf-8") == "monitor"
    assert run.portfolio_news_monitor_path in run.pipeline_command


def test_portfolio_news_monitor_failure_blocks_pipeline(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        return 8, "", "news failed"

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template() + " --portfolio-news-monitor {portfolio_news_monitor}",
            portfolio_news_monitor_command_template=_safe_portfolio_news_monitor_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert len(calls) == 1
    assert summary.status == "failed"
    assert run.blocker == "portfolio_news_monitor_command_failed"
    assert run.portfolio_news_monitor_exit_code == 8
    assert "news failed" in Path(run.portfolio_news_monitor_stderr_path).read_text(encoding="utf-8")


def test_successful_portfolio_news_monitor_updates_state_even_if_pipeline_fails(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    def fake_runner(command: str) -> tuple[int, str, str]:
        if "longterm_portfolio_news_monitor.py" in command:
            monitor_path = Path(command.split("--output ", 1)[1].split(" --", 1)[0].strip('"'))
            monitor_path.write_text(json.dumps({"status": "completed", "enrichment_needed_queue": []}), encoding="utf-8")
            return 0, "monitor", ""
        return 9, "", "pipeline failed"

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template() + " --portfolio-news-monitor {portfolio_news_monitor}",
            portfolio_news_monitor_command_template=_safe_portfolio_news_monitor_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    state = json.loads((tmp_path / "scheduler" / "scheduler_policy_state.json").read_text(encoding="utf-8"))
    assert summary.status == "failed"
    assert summary.runs[0].blocker == "pipeline_command_failed"
    assert state["last_news_monitor_at"] == summary.runs[0].finished_at
    assert "last_no_submit_preflight_at" not in state


def test_failed_pipeline_stops_repeated_scheduler_when_stop_on_error(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        return 9, "", "failed badly"

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=3, stop_on_error=True),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    assert len(calls) == 1
    assert summary.status == "failed"
    assert summary.runs[0].status == "failed"
    assert "failed badly" in Path(summary.runs[0].pipeline_stderr_path).read_text(encoding="utf-8")


def test_account_refresh_runs_after_successful_pipeline_with_pipeline_summary(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(
                json.dumps({"status": "completed", "artifact_paths": {}}),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    assert len(calls) == 2
    assert "longterm_paper_account_refresh.py" in calls[1]
    assert summary.runs[0].account_refresh_exit_code == 0
    assert summary.runs[0].pipeline_summary_path in calls[1]


def test_successful_scheduler_run_updates_cadence_state_after_account_refresh(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    def fake_runner(command: str) -> tuple[int, str, str]:
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "blocker_count": 0,
                        "artifact_paths": {},
                        "stages": [
                            {"stage_id": "final_planning_refresh", "status": "passed"},
                            {"stage_id": "extract_final_action_plan", "status": "passed"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    state = json.loads((tmp_path / "scheduler" / "scheduler_policy_state.json").read_text(encoding="utf-8"))
    assert summary.runs[0].status == "completed"
    assert state["last_no_submit_preflight_at"] == summary.runs[0].finished_at
    assert state["last_account_refresh_at"] == summary.runs[0].finished_at
    assert state["last_final_planning_at"] == summary.runs[0].finished_at
    assert state["active_rules_sha256"]


def test_successful_followup_batch_split_updates_cadence_state(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    def fake_runner(command: str) -> tuple[int, str, str]:
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "blocker_count": 0,
                        "artifact_paths": {},
                        "stages": [
                            {"stage_id": "ingest_portfolio_news_monitor", "status": "passed"},
                            {"stage_id": "portfolio_news_followup_batch_split", "status": "passed"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    state = json.loads((tmp_path / "scheduler" / "scheduler_policy_state.json").read_text(encoding="utf-8"))
    assert summary.runs[0].status == "completed"
    assert state["last_followup_batch_split_at"] == summary.runs[0].finished_at


def test_successful_followup_committee_updates_cadence_state(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    def fake_runner(command: str) -> tuple[int, str, str]:
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            committee_summary = summary_path.parent / "followup_committee_summary.json"
            committee_summary.write_text(
                json.dumps(
                    {
                        "status": "partial",
                        "batch_count": 4,
                        "completed_count": 1,
                        "failed_count": 0,
                        "skipped_count": 0,
                        "remaining_count": 3,
                        "order_submission_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "blocker_count": 0,
                        "artifact_paths": {},
                        "stages": [
                            {
                                "stage_id": "portfolio_news_followup_committee_batches",
                                "status": "passed",
                                "artifact_paths": {
                                    "portfolio_news_followup_committee_batch_run_summary": str(committee_summary)
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    state = json.loads((tmp_path / "scheduler" / "scheduler_policy_state.json").read_text(encoding="utf-8"))
    assert summary.runs[0].status == "completed"
    assert state["last_followup_committee_at"] == summary.runs[0].finished_at


def test_post_run_verification_runs_after_summary_and_updates_record(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(
                json.dumps({"status": "completed", "blocker_count": 0, "artifact_paths": {}}),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        if "longterm_pipeline_scheduler_verify.py" in command:
            scheduler_path = Path(command.split("--pipeline-scheduler-summary ", 1)[1].split(" ", 1)[0].strip('"'))
            report_path = Path(command.split("--report-output ", 1)[1].split(" --", 1)[0].strip('"'))
            assert scheduler_path.exists()
            report_path.write_text(json.dumps({"status": "ready", "blockers": []}), encoding="utf-8")
            return 0, "verified", ""
        raise AssertionError(f"unexpected command: {command}")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            post_run_verification_command_template=_safe_post_run_verification_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    saved = json.loads((tmp_path / "scheduler" / "pipeline_scheduler_summary.json").read_text(encoding="utf-8"))
    assert summary.status == "completed"
    assert "longterm_pipeline_scheduler_verify.py" in calls[-1]
    assert run.post_run_verification_exit_code == 0
    assert Path(run.post_run_verification_path).exists()
    assert Path(run.post_run_verification_stdout_path).read_text(encoding="utf-8") == "verified"
    assert saved["runs"][0]["post_run_verification_exit_code"] == 0


def test_post_run_verification_failure_marks_scheduler_run_failed(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    def fake_runner(command: str) -> tuple[int, str, str]:
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(
                json.dumps({"status": "completed", "blocker_count": 0, "artifact_paths": {}}),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        if "longterm_pipeline_scheduler_verify.py" in command:
            return 3, "", "not ready"
        raise AssertionError(f"unexpected command: {command}")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            post_run_verification_command_template=_safe_post_run_verification_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert summary.status == "failed"
    assert run.status == "failed"
    assert run.blocker == "post_run_verification_command_failed"
    assert run.post_run_verification_exit_code == 3
    assert "not ready" in Path(run.post_run_verification_stderr_path).read_text(encoding="utf-8")


def test_scheduler_policy_runs_between_pipeline_and_account_refresh_and_is_passed_to_refresh(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(
                json.dumps({"status": "completed", "artifact_paths": {}}),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        if "longterm_pipeline_scheduler_policy.py" in command:
            policy_path = Path(command.split("--report-output ", 1)[1].split(" --", 1)[0].strip('"'))
            policy_path.write_text(
                json.dumps(
                    {
                        "recommended_mode": "account_refresh_only",
                        "next_safe_action": "refresh_account_and_dashboard_artifacts",
                        "order_submission_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            return 0, "policy", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            scheduler_policy_command_template=_safe_scheduler_policy_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert len(calls) == 3
    assert "longterm_research_to_paper_pipeline.py" in calls[0]
    assert "longterm_pipeline_scheduler_policy.py" in calls[1]
    assert "longterm_paper_account_refresh.py" in calls[2]
    assert run.scheduler_policy_exit_code == 0
    assert Path(run.scheduler_policy_path).exists()
    assert Path(run.scheduler_policy_stdout_path).read_text(encoding="utf-8") == "policy"
    assert "--scheduler-policy" in run.account_refresh_command
    assert run.scheduler_policy_path in run.account_refresh_command


def test_committee_preset_policy_runs_after_pipeline_and_is_passed_to_refresh(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_research_to_paper_pipeline.py" in command:
            summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            summary_path.write_text(json.dumps({"status": "completed", "artifact_paths": {}}), encoding="utf-8")
            return 0, "pipeline", ""
        if "longterm_committee_preset_policy.py" in command:
            policy_path = Path(command.split("--report-output ", 1)[1].split(" --", 1)[0].strip('"'))
            policy_path.write_text(
                json.dumps(
                    {
                        "recommended_preset": "decision_4",
                        "order_submission_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            return 0, "committee policy", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            committee_preset_policy_command_template=_safe_committee_preset_policy_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert len(calls) == 3
    assert "longterm_research_to_paper_pipeline.py" in calls[0]
    assert "longterm_committee_preset_policy.py" in calls[1]
    assert "longterm_paper_account_refresh.py" in calls[2]
    assert run.committee_preset_policy_exit_code == 0
    assert Path(run.committee_preset_policy_path).exists()
    assert Path(run.committee_preset_policy_stdout_path).read_text(encoding="utf-8") == "committee policy"
    assert "--committee-preset-policy" in run.account_refresh_command
    assert run.committee_preset_policy_path in run.account_refresh_command


def test_pre_pipeline_refresh_runs_before_pipeline_with_run_portfolio_state(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_alpaca_paper_snapshot.py" in command:
            portfolio_path = Path(command.split("--portfolio-state-output ", 1)[1].strip('"'))
            portfolio_path.parent.mkdir(parents=True, exist_ok=True)
            portfolio_path.write_text(json.dumps({"cash": 5000, "holdings": []}), encoding="utf-8")
            return 0, "snapshot", ""
        summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
        summary_path.write_text(json.dumps({"status": "completed", "artifact_paths": {}}), encoding="utf-8")
        return 0, "pipeline", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pre_pipeline_refresh_command_template=_safe_pre_pipeline_refresh_template(),
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert len(calls) == 2
    assert "longterm_alpaca_paper_snapshot.py" in calls[0]
    assert "longterm_research_to_paper_pipeline.py" in calls[1]
    assert run.pre_pipeline_refresh_exit_code == 0
    assert Path(run.pre_pipeline_refresh_stdout_path).read_text(encoding="utf-8") == "snapshot"
    assert run.pre_pipeline_refresh_command in calls[0]
    assert "paper_portfolio_state.json" in run.pre_pipeline_refresh_command
    assert run.pre_pipeline_refresh_command.split("--portfolio-state-output ", 1)[1] in run.pipeline_command


def test_pre_pipeline_refresh_failure_blocks_pipeline(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        return 8, "", "snapshot failed"

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pre_pipeline_refresh_command_template=_safe_pre_pipeline_refresh_template(),
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert len(calls) == 1
    assert run.status == "failed"
    assert run.blocker == "pre_pipeline_refresh_command_failed"
    assert run.pipeline_exit_code is None
    assert "snapshot failed" in Path(run.pre_pipeline_refresh_stderr_path).read_text(encoding="utf-8")


def test_print_plan_only_renders_scheduler_policy_and_refresh_commands(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            scheduler_policy_command_template=_safe_scheduler_policy_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1, print_plan_only=True),
        command_runner=lambda command: (0, "", ""),
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert run.status == "planned"
    assert "paper_portfolio_state.json" in run.pipeline_command
    assert "longterm_pipeline_scheduler_policy.py" in run.scheduler_policy_command
    assert "--report-output" in run.scheduler_policy_command
    assert "scheduler_policy.json" in run.scheduler_policy_command
    assert "--pipeline-scheduler-summary" in run.scheduler_policy_command
    assert "pipeline_scheduler_summary.json" in run.scheduler_policy_command
    assert "--policy-state" in run.scheduler_policy_command
    assert "scheduler_policy_state.json" in run.scheduler_policy_command
    assert "{scheduler_summary}" not in run.scheduler_policy_command
    assert "{scheduler_policy_state}" not in run.scheduler_policy_command
    assert "--scheduler-policy" in run.account_refresh_command
    assert "scheduler_policy.json" in run.account_refresh_command


def test_print_plan_only_renders_committee_preset_policy_command(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            committee_preset_policy_command_template=_safe_committee_preset_policy_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1, print_plan_only=True),
        command_runner=lambda command: (0, "", ""),
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert run.status == "planned"
    assert "longterm_committee_preset_policy.py" in run.committee_preset_policy_command
    assert "--report-output" in run.committee_preset_policy_command
    assert "committee_preset_policy.json" in run.committee_preset_policy_command
    assert "--committee-preset-policy" in run.account_refresh_command
    assert "committee_preset_policy.json" in run.account_refresh_command


def test_print_plan_only_renders_pre_pipeline_refresh_command(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pre_pipeline_refresh_command_template=_safe_pre_pipeline_refresh_template(),
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1, print_plan_only=True),
        command_runner=lambda command: (0, "", ""),
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert "longterm_alpaca_paper_snapshot.py" in run.pre_pipeline_refresh_command
    assert "--portfolio-state-output" in run.pre_pipeline_refresh_command
    assert "paper_portfolio_state.json" in run.pre_pipeline_refresh_command


def test_print_plan_only_renders_post_run_verification_command(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            post_run_verification_command_template=_safe_post_run_verification_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=1, print_plan_only=True),
        command_runner=lambda command: (0, "", ""),
        now_func=FakeClock().now,
    )

    run = summary.runs[0]
    assert "longterm_pipeline_scheduler_verify.py" in run.post_run_verification_command
    assert "--pipeline-scheduler-summary" in run.post_run_verification_command
    assert "pipeline_scheduler_summary.json" in run.post_run_verification_command
    assert "--report-output" in run.post_run_verification_command
    assert "scheduler_cadence_verification.json" in run.post_run_verification_command
    assert "{post_run_verification}" not in run.post_run_verification_command


def test_scheduler_policy_template_can_use_stable_summary_and_state_placeholders(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "custom_scheduler_summary.json"
    calls: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if "longterm_research_to_paper_pipeline.py" in command:
            pipeline_summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
            pipeline_summary_path.write_text(
                json.dumps({"status": "completed", "artifact_paths": {}}),
                encoding="utf-8",
            )
            return 0, "pipeline", ""
        if "longterm_pipeline_scheduler_policy.py" in command:
            state_path = Path(command.split("--state-output ", 1)[1].split(" --", 1)[0].strip('"'))
            policy_path = Path(command.split("--report-output ", 1)[1].split(" --", 1)[0].strip('"'))
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"updated_at": "2026-05-04T15:02:00Z"}), encoding="utf-8")
            policy_path.write_text(
                json.dumps(
                    {
                        "recommended_mode": "account_refresh_only",
                        "order_submission_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            return 0, "policy", ""
        return 0, "refresh", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            scheduler_policy_command_template=_safe_scheduler_policy_template(),
            account_refresh_command_template=_safe_account_refresh_template(),
            rules_path=rules_path,
            summary_output=summary_output,
        ),
        PipelineSchedulerConfig(max_runs=1),
        command_runner=fake_runner,
        now_func=FakeClock().now,
    )

    policy_command = summary.runs[0].scheduler_policy_command
    assert summary.status == "completed"
    assert str(summary_output) in policy_command
    assert str(tmp_path / "scheduler" / "scheduler_policy_state.json") in policy_command
    assert (tmp_path / "scheduler" / "scheduler_policy_state.json").exists()


def test_repeated_scheduler_runs_create_isolated_run_dirs_and_sleep_between_runs(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    sleeps: list[float] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        summary_path = Path(command.split("--summary-output ", 1)[1].split(" --", 1)[0].strip('"'))
        summary_path.write_text(
            json.dumps({"status": "completed", "artifact_paths": {}}),
            encoding="utf-8",
        )
        return 0, "", ""

    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=tmp_path / "scheduler",
            pipeline_command_template=_safe_pipeline_template(),
            rules_path=rules_path,
        ),
        PipelineSchedulerConfig(max_runs=2, interval_seconds=17),
        command_runner=fake_runner,
        sleep_func=lambda seconds: sleeps.append(seconds),
        now_func=FakeClock().now,
    )

    assert [Path(run.run_dir).name for run in summary.runs] == ["run_001", "run_002"]
    assert sleeps == [17]
    assert summary.run_count == 2
    assert summary.success_count == 2


def test_pipeline_scheduler_cli_print_plan_only_outputs_json(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--pipeline-command-template",
                _safe_pipeline_template(),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(summary_output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["status"] == "planned"
    assert saved["runs"][0]["status"] == "planned"


def test_pipeline_scheduler_cli_accepts_scheduler_policy_template(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--pipeline-command-template",
                _safe_pipeline_template(),
                "--scheduler-policy-command-template",
                _safe_scheduler_policy_template(),
                "--account-refresh-command-template",
                _safe_account_refresh_template(),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert run["status"] == "planned"
    assert "longterm_pipeline_scheduler_policy.py" in run["scheduler_policy_command"]
    assert "--scheduler-policy" in run["account_refresh_command"]


def test_pipeline_scheduler_cli_accepts_committee_preset_policy_template(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--pipeline-command-template",
                _safe_pipeline_template(),
                "--committee-preset-policy-command-template",
                _safe_committee_preset_policy_template(),
                "--account-refresh-command-template",
                _safe_account_refresh_template(),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert run["status"] == "planned"
    assert "longterm_committee_preset_policy.py" in run["committee_preset_policy_command"]
    assert "--committee-preset-policy" in run["account_refresh_command"]


def test_pipeline_scheduler_cli_accepts_pre_pipeline_refresh_template(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--pre-pipeline-refresh-command-template",
                _safe_pre_pipeline_refresh_template(),
                "--pipeline-command-template",
                _safe_pipeline_template(),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert run["status"] == "planned"
    assert "longterm_alpaca_paper_snapshot.py" in run["pre_pipeline_refresh_command"]
    assert "paper_portfolio_state.json" in run["pipeline_command"]


def test_pipeline_scheduler_cli_accepts_portfolio_news_monitor_template(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--pipeline-command-template",
                _safe_pipeline_template() + " --portfolio-news-monitor {portfolio_news_monitor}",
                "--portfolio-news-monitor-command-template",
                _safe_portfolio_news_monitor_template(),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert run["status"] == "planned"
    assert "longterm_portfolio_news_monitor.py" in run["portfolio_news_monitor_command"]
    assert "--portfolio-news-monitor" in run["pipeline_command"]
    assert "portfolio_news_monitor.json" in run["pipeline_command"]


def test_pipeline_scheduler_cli_accepts_post_run_verification_template(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--pipeline-command-template",
                _safe_pipeline_template(),
                "--post-run-verification-command-template",
                _safe_post_run_verification_template(),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert run["status"] == "planned"
    assert "longterm_pipeline_scheduler_verify.py" in run["post_run_verification_command"]
    assert "scheduler_cadence_verification.json" in run["post_run_verification_command"]


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_renders_safe_commands(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    summary_output = tmp_path / "summary.json"
    scheduler_config_validation = tmp_path / "scheduler_profile_validation.json"
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--preset",
                "ongoing-no-submit",
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "paper_ledger.db"),
                "--action-plan",
                str(tmp_path / "account_action_plan.json"),
                "--profile-config",
                str(profile),
                "--market-regime-file",
                str(tmp_path / "market_regime.json"),
                "--final-planning-refresh",
                "--final-planning-timeout-seconds",
                "45",
                "--planning-capital-from-portfolio-state",
                "--expected-cash-from-portfolio-state",
                "--skip-price-map",
                "--allow-existing-paper-positions",
                "--scheduler-config-validation",
                str(scheduler_config_validation),
                "--print-plan-only",
                "--summary-output",
                str(summary_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    all_commands = "\n".join(
        [
            run["pre_pipeline_refresh_command"],
            run["pipeline_command"],
            run["scheduler_policy_command"],
            run["account_refresh_command"],
            run["post_run_verification_command"],
        ]
    ).lower()
    assert code == 0
    assert printed["status"] == "planned"
    assert "longterm_alpaca_paper_snapshot.py" in run["pre_pipeline_refresh_command"]
    assert "paper_portfolio_state.json" in run["pre_pipeline_refresh_command"]
    assert "longterm_research_to_paper_pipeline.py" in run["pipeline_command"]
    assert "--portfolio-state" in run["pipeline_command"]
    assert "paper_portfolio_state.json" in run["pipeline_command"]
    assert "--final-planning-refresh" in run["pipeline_command"]
    assert "--final-planning-timeout-seconds 45" in run["pipeline_command"]
    assert "--planning-capital-from-portfolio-state" in run["pipeline_command"]
    assert "--expected-cash-from-portfolio-state" in run["pipeline_command"]
    assert "--skip-price-map" in run["pipeline_command"]
    assert "--allow-existing-paper-positions" in run["pipeline_command"]
    assert "longterm_pipeline_scheduler_policy.py" in run["scheduler_policy_command"]
    assert "--policy-state" in run["scheduler_policy_command"]
    assert "--state-output" in run["scheduler_policy_command"]
    assert "longterm_paper_account_refresh.py" in run["account_refresh_command"]
    assert "--scheduler-policy" in run["account_refresh_command"]
    assert "--scheduler-config-validation" in run["account_refresh_command"]
    assert str(scheduler_config_validation.resolve()) in run["account_refresh_command"]
    assert "dashboard_manifest.json" in run["account_refresh_command"]
    assert "longterm_pipeline_scheduler_verify.py" in run["post_run_verification_command"]
    assert "--require-resource-bounded" in run["post_run_verification_command"]
    assert "--require-final-planning-bound" in run["post_run_verification_command"]
    assert "--require-policy-timestamp last_no_submit_preflight_at" in run["post_run_verification_command"]
    assert "--require-policy-timestamp last_account_refresh_at" in run["post_run_verification_command"]
    assert "--require-policy-timestamp last_final_planning_at" in run["post_run_verification_command"]
    assert "scheduler_cadence_verification.json" in run["post_run_verification_command"]
    assert "--submit-paper-orders" not in all_commands
    assert "--confirm-paper-submit" not in all_commands
    assert "longterm_paper_execution.py" not in all_commands
    controls = run["resource_controls"]
    assert controls["provider_mode"] == "free_or_skip_grok"
    assert controls["paid_provider_enabled"] is False
    assert controls["final_planning_refresh"] is True
    assert controls["final_planning_timeout_seconds"] == 45
    assert controls["generated_committee_batches"] is False
    assert controls["bounded"] is True


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_uses_absolute_script_paths(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--preset",
                "ongoing-no-submit",
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "paper_ledger.db"),
                "--action-plan",
                str(tmp_path / "account_action_plan.json"),
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    assert code == 0
    for field, script_name in [
        ("pre_pipeline_refresh_command", "longterm_alpaca_paper_snapshot.py"),
        ("pipeline_command", "longterm_research_to_paper_pipeline.py"),
        ("scheduler_policy_command", "longterm_pipeline_scheduler_policy.py"),
        ("account_refresh_command", "longterm_paper_account_refresh.py"),
        ("post_run_verification_command", "longterm_pipeline_scheduler_verify.py"),
    ]:
        command = run[field]
        assert str(scripts_dir / script_name) in command
        assert f"python scripts/{script_name}" not in command.replace("\\", "/")


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_normalizes_relative_user_paths(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    rules_path = Path("active_rules.txt")
    rules_path.write_text("<rules />", encoding="utf-8")
    journal = Path("journal.db")
    ledger = Path("paper_ledger.db")
    action_plan = Path("account_action_plan.json")
    profile = Path("profile.json")
    market_regime = Path("market_regime.json")
    profile.write_text("{}", encoding="utf-8")
    market_regime.write_text("{}", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--preset",
                "ongoing-no-submit",
                "--output-dir",
                "scheduler",
                "--rules-path",
                str(rules_path),
                "--journal-db",
                str(journal),
                "--ledger-db",
                str(ledger),
                "--action-plan",
                str(action_plan),
                "--profile-config",
                str(profile),
                "--market-regime-file",
                str(market_regime),
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    all_commands = "\n".join(
        [
            run["pre_pipeline_refresh_command"],
            run["pipeline_command"],
            run["scheduler_policy_command"],
            run["account_refresh_command"],
        ]
    )
    assert code == 0
    for path in [journal, ledger, action_plan, profile, market_regime]:
        assert str(path.resolve()) in all_commands
    assert "--profile-config profile.json" not in all_commands
    assert "--market-regime-file market_regime.json" not in all_commands


def test_pipeline_scheduler_cli_loads_safe_json_profile_args(tmp_path):
    config_path = tmp_path / "scheduler_profile.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "description": "repo local no-submit profile",
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(tmp_path / "scheduler"),
                    "rules_path": str(tmp_path / "active_rules.txt"),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "profile_config": str(tmp_path / "profile.json"),
                    "allow_existing_paper_positions": True,
                    "expected_cash_from_portfolio_state": True,
                    "max_runs": 2,
                    "interval_seconds": 7,
                    "print_plan_only": True,
                    "json": True,
                },
            }
        ),
        encoding="utf-8",
    )

    args = parse_args(["--config-file", str(config_path)])

    assert args.config_file == str(config_path)
    assert args.preset == "ongoing-no-submit"
    assert args.output_dir == str(tmp_path / "scheduler")
    assert args.rules_path == str(tmp_path / "active_rules.txt")
    assert args.journal_db == str(tmp_path / "journal.db")
    assert args.ledger_db == str(tmp_path / "paper_ledger.db")
    assert args.action_plan == str(tmp_path / "account_action_plan.json")
    assert args.profile_config == str(tmp_path / "profile.json")
    assert args.allow_existing_paper_positions is True
    assert args.expected_cash_from_portfolio_state is True
    assert args.max_runs == 2
    assert args.interval_seconds == 7
    assert args.print_plan_only is True
    assert args.json is True


def test_pipeline_scheduler_cli_rejects_unknown_json_profile_args(tmp_path):
    config_path = tmp_path / "scheduler_profile.json"
    config_path.write_text(
        json.dumps({"args": {"preset": "ongoing-no-submit", "submit_paper_orders": True}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown scheduler config arg"):
        parse_args(["--config-file", str(config_path)])


def test_pipeline_scheduler_cli_json_profile_scalar_cli_args_override_config(tmp_path):
    config_path = tmp_path / "scheduler_profile.json"
    config_path.write_text(
        json.dumps(
            {
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": "from_config",
                    "journal_db": "journal.db",
                    "ledger_db": "ledger.db",
                    "action_plan": "action_plan.json",
                    "max_runs": 2,
                    "print_plan_only": True,
                }
            }
        ),
        encoding="utf-8",
    )

    args = parse_args(["--config-file", str(config_path), "--output-dir", "from_cli", "--max-runs", "1"])

    assert args.output_dir == "from_cli"
    assert args.max_runs == 1
    assert args.print_plan_only is True


def test_pipeline_scheduler_cli_validate_config_only_does_not_create_run_dirs(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    output_dir = tmp_path / "scheduler"
    config_path = tmp_path / "scheduler_profile.json"
    config_path.write_text(
        json.dumps(
            {
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(output_dir),
                    "rules_path": str(rules_path),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "allow_existing_paper_positions": True,
                    "expected_cash_from_portfolio_state": True,
                    "json": True,
                    "validate_config_only": True,
                }
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(parse_args(["--config-file", str(config_path)]))

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["status"] == "ready"
    assert printed["mode"] == "pipeline_scheduler_config_validation"
    assert printed["order_submission_enabled"] is False
    assert printed["config_file"] == str(config_path.resolve())
    assert printed["output_dir"] == str(output_dir.resolve())
    assert printed["resource_controls"]["bounded"] is True
    assert "longterm_research_to_paper_pipeline.py" in printed["commands"]["pipeline"]
    assert "--submit-paper-orders" not in "\n".join(printed["commands"].values())
    assert not output_dir.exists()


def test_pipeline_scheduler_cli_validate_config_only_rejects_unbounded_paid_provider(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    config_path = tmp_path / "scheduler_profile.json"
    config_path.write_text(
        json.dumps(
            {
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(tmp_path / "scheduler"),
                    "rules_path": str(rules_path),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "perplexity_research": True,
                    "validate_config_only": True,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires --research-max-pass-count"):
        run_cli(parse_args(["--config-file", str(config_path)]))


def test_pipeline_scheduler_cli_validate_config_only_writes_summary_output(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    output_dir = tmp_path / "scheduler"
    summary_output = tmp_path / "scheduler_profile_validation.json"
    config_path = tmp_path / "scheduler_profile.json"
    config_path.write_text(
        json.dumps(
            {
                "args": {
                    "preset": "ongoing-no-submit",
                    "output_dir": str(output_dir),
                    "rules_path": str(rules_path),
                    "journal_db": str(tmp_path / "journal.db"),
                    "ledger_db": str(tmp_path / "paper_ledger.db"),
                    "action_plan": str(tmp_path / "account_action_plan.json"),
                    "summary_output": str(summary_output),
                    "json": True,
                    "validate_config_only": True,
                }
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(parse_args(["--config-file", str(config_path)]))

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(summary_output.read_text(encoding="utf-8"))
    assert code == 0
    assert saved == printed
    assert saved["mode"] == "pipeline_scheduler_config_validation"
    assert not output_dir.exists()


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_core_paths(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    with pytest.raises(ValueError, match="--journal-db"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_renders_portfolio_news_monitor(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    snapshot = tmp_path / "news_snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text("[]", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--preset",
                "ongoing-no-submit",
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "paper_ledger.db"),
                "--action-plan",
                str(tmp_path / "account_action_plan.json"),
                "--portfolio-news-monitor",
                "--portfolio-news-snapshot-file",
                str(snapshot),
                "--portfolio-news-watchlist-ideas",
                str(watchlist),
                "--portfolio-news-published-after",
                "2026-05-01",
                "--portfolio-news-relevance-threshold",
                "0.7",
                "--portfolio-news-max-articles-per-symbol",
                "3",
                "--portfolio-news-followup-batches",
                "--portfolio-news-followup-batch-size",
                "2",
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert "longterm_portfolio_news_monitor.py" in run["portfolio_news_monitor_command"]
    assert "--snapshot-file" in run["portfolio_news_monitor_command"]
    assert str(snapshot) in run["portfolio_news_monitor_command"]
    assert "--watchlist-ideas" in run["portfolio_news_monitor_command"]
    assert "--published-after 2026-05-01" in run["portfolio_news_monitor_command"]
    assert "--relevance-threshold 0.7" in run["portfolio_news_monitor_command"]
    assert "--max-articles-per-symbol 3" in run["portfolio_news_monitor_command"]
    assert "--portfolio-news-monitor" in run["pipeline_command"]
    assert "portfolio_news_monitor.json" in run["pipeline_command"]
    assert "--portfolio-news-followup-batches" in run["pipeline_command"]
    assert "--portfolio-news-followup-batch-size 2" in run["pipeline_command"]
    assert "--require-policy-timestamp last_news_monitor_at" in run["post_run_verification_command"]
    assert "--require-policy-timestamp last_followup_batch_split_at" in run["post_run_verification_command"]


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_runs_capped_followup_committee(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    snapshot = tmp_path / "news_snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--preset",
                "ongoing-no-submit",
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "paper_ledger.db"),
                "--action-plan",
                str(tmp_path / "account_action_plan.json"),
                "--portfolio-news-monitor",
                "--portfolio-news-snapshot-file",
                str(snapshot),
                "--portfolio-news-followup-batches",
                "--run-portfolio-news-followup-committee-batches",
                "--portfolio-news-followup-max-batches",
                "2",
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    assert code == 0
    assert "--run-portfolio-news-followup-committee-batches" in run["pipeline_command"]
    assert "--portfolio-news-followup-max-batches 2" in run["pipeline_command"]
    assert "--require-policy-timestamp last_followup_committee_at" in run["post_run_verification_command"]
    assert "--submit-paper-orders" not in run["pipeline_command"].lower()
    assert run["resource_controls"]["portfolio_news_followup_committee_batches"] is True
    assert run["resource_controls"]["portfolio_news_followup_max_batches"] == 2
    assert run["resource_controls"]["bounded"] is True


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_news_snapshot_when_enabled(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    with pytest.raises(ValueError, match="--portfolio-news-snapshot-file"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--portfolio-news-monitor",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_monitor_for_followup_batches(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    with pytest.raises(ValueError, match="--portfolio-news-monitor"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--portfolio-news-followup-batches",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_cap_for_followup_committee(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    snapshot = tmp_path / "news_snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="portfolio-news-followup-max-batches"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--portfolio-news-monitor",
                    "--portfolio-news-snapshot-file",
                    str(snapshot),
                    "--portfolio-news-followup-batches",
                    "--run-portfolio-news-followup-committee-batches",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_passes_bounded_research_options(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    source_file = tmp_path / "universe.csv"
    source_file.write_text("symbol\nAAPL\n", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--preset",
                "ongoing-no-submit",
                "--output-dir",
                str(tmp_path / "scheduler"),
                "--rules-path",
                str(rules_path),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "paper_ledger.db"),
                "--action-plan",
                str(tmp_path / "account_action_plan.json"),
                "--research-source-file",
                str(source_file),
                "--research-source",
                "manual_watchlist",
                "--research-campaign-dir",
                str(tmp_path / "campaign"),
                "--research-resume",
                "--research-run-until",
                "research_queue_ready",
                "--research-max-pass-count",
                "25",
                "--research-evidence-batch-size",
                "10",
                "--research-max-evidence-batches",
                "2",
                "--perplexity-research",
                "--perplexity-model",
                "sonar",
                "--perplexity-max-tokens",
                "3500",
                "--perplexity-search-context-size",
                "low",
                "--perplexity-credits-purchased-to-date",
                "12.5",
                "--run-generated-committee-batches",
                "--generated-committee-max-batches",
                "1",
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    run = printed["runs"][0]
    pipeline_command = run["pipeline_command"]
    controls = run["resource_controls"]
    assert code == 0
    assert "--research-source-file" in pipeline_command
    assert str(source_file) in pipeline_command
    assert "--research-source manual_watchlist" in pipeline_command
    assert "--research-campaign-dir" in pipeline_command
    assert "--research-resume" in pipeline_command
    assert "--research-max-pass-count 25" in pipeline_command
    assert "--research-evidence-batch-size 10" in pipeline_command
    assert "--research-max-evidence-batches 2" in pipeline_command
    assert "--perplexity-research" in pipeline_command
    assert "--perplexity-model sonar" in pipeline_command
    assert "--perplexity-max-tokens 3500" in pipeline_command
    assert "--perplexity-search-context-size low" in pipeline_command
    assert "--perplexity-credits-purchased-to-date 12.5" in pipeline_command
    assert "--run-generated-committee-batches" in pipeline_command
    assert "--generated-committee-max-batches 1" in pipeline_command
    assert "--require-policy-timestamp last_full_research_at" in run["post_run_verification_command"]
    assert "--submit-paper-orders" not in pipeline_command.lower()
    assert "--confirm-paper-submit" not in pipeline_command.lower()
    assert controls["provider_mode"] == "perplexity"
    assert controls["paid_provider_enabled"] is True
    assert controls["research_max_pass_count"] == 25
    assert controls["research_evidence_batch_size"] == 10
    assert controls["research_max_evidence_batches"] == 2
    assert controls["generated_committee_batches"] is True
    assert controls["generated_committee_max_batches"] == 1
    assert controls["bounded"] is True
    assert controls["estimated_cost_usd"] == "unknown"
    assert "PERPLEXITY_API_KEY" not in json.dumps(controls)


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_cost_bounds_for_perplexity(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    source_file = tmp_path / "universe.csv"
    source_file.write_text("symbol\nAAPL\n", encoding="utf-8")

    with pytest.raises(ValueError, match="--research-max-pass-count"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--research-source-file",
                    str(source_file),
                    "--research-source",
                    "manual_watchlist",
                    "--research-campaign-dir",
                    str(tmp_path / "campaign"),
                    "--perplexity-research",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_cost_bounds_for_xai_grok(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")
    source_file = tmp_path / "universe.csv"
    source_file.write_text("symbol\nAAPL\n", encoding="utf-8")

    with pytest.raises(ValueError, match="--research-max-pass-count"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--research-source-file",
                    str(source_file),
                    "--research-source",
                    "manual_watchlist",
                    "--research-campaign-dir",
                    str(tmp_path / "campaign"),
                    "--xai-grok",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_scheduler_cli_ongoing_no_submit_preset_requires_committee_batch_cap(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    rules_path.write_text("<rules />", encoding="utf-8")

    with pytest.raises(ValueError, match="--generated-committee-max-batches"):
        run_cli(
            build_parser().parse_args(
                [
                    "--preset",
                    "ongoing-no-submit",
                    "--output-dir",
                    str(tmp_path / "scheduler"),
                    "--rules-path",
                    str(rules_path),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "paper_ledger.db"),
                    "--action-plan",
                    str(tmp_path / "account_action_plan.json"),
                    "--run-generated-committee-batches",
                    "--print-plan-only",
                ]
            )
        )
