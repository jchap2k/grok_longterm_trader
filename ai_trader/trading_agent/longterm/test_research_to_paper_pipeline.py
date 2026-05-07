import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_to_paper_pipeline import (
    PipelineStage,
    build_pipeline_artifact_health,
    build_pipeline_artifact_rollup,
    build_committee_batch_stages,
    build_final_planning_action_plan_extract_stage,
    build_final_planning_refresh_stage,
    build_generated_committee_batch_runner_stage,
    build_paper_preflight_stages,
    build_portfolio_news_monitor_ingest_stage,
    build_research_campaign_stages,
    run_pipeline_stages,
    validate_stage_command,
)
from longterm.research_to_paper_pipeline_cli import build_parser, run_cli
from longterm.path_utils import write_json_artifact


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


def test_runner_fails_closed_when_stage_times_out(tmp_path, monkeypatch):
    stage = PipelineStage(
        stage_id="final_planning_refresh",
        title="Final planning",
        command="python slow_final_planning.py",
        timeout_seconds=1.5,
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args") or args[0],
            timeout=kwargs.get("timeout"),
            output="partial out",
            stderr="partial err",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_pipeline_stages(
        [stage],
        output_dir=tmp_path,
        summary_output=tmp_path / "pipeline_summary.json",
    )

    saved = json.loads((tmp_path / "pipeline_summary.json").read_text(encoding="utf-8"))
    final_stage = saved["stages"][0]
    assert result.status == "failed"
    assert result.blocker_count == 1
    assert final_stage["status"] == "failed"
    assert final_stage["blocker"] == "stage_timeout:final_planning_refresh"
    assert final_stage["timeout_seconds"] == 1.5
    assert "timed out after 1.5 seconds" in Path(final_stage["log_path"]).read_text(encoding="utf-8")


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


def test_pipeline_summary_includes_artifact_rollups_for_scheduler_dashboard(tmp_path):
    selected = tmp_path / "research_queue_selected.json"
    selected.write_text(
        json.dumps([{"symbol": "MSFT"}, {"symbol": "NVDA"}]),
        encoding="utf-8",
    )
    committee = tmp_path / "committee_batch_run_summary.json"
    committee.write_text(
        json.dumps({"batch_count": 2, "completed_count": 2, "failed_count": 0, "skipped_count": 0}),
        encoding="utf-8",
    )
    action_plan = tmp_path / "stage6b_submit_candidates.json"
    action_plan.write_text(
        json.dumps(
            {
                "intents": [
                    {"symbol": "MSFT", "intent_type": "BUY", "allowed": True},
                    {"symbol": "SPY", "intent_type": "PARK_IDLE_CASH", "allowed": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    preview = tmp_path / "paper_preview.json"
    preview.write_text(json.dumps({"preview_count": 1, "ready_count": 1}), encoding="utf-8")
    workflow = tmp_path / "paper_workflow_smoke.json"
    workflow.write_text(json.dumps({"ready_count": 1, "blocked_count": 0}), encoding="utf-8")
    stages = [
        PipelineStage(
            stage_id="rollup",
            title="Rollup",
            command="python rollup.py",
            artifact_paths={
                "research_queue_selected": str(selected),
                "generated_committee_batch_run_summary": str(committee),
                "candidate_action_plan": str(action_plan),
                "paper_preview": str(preview),
                "workflow_smoke": str(workflow),
            },
        )
    ]

    result = run_pipeline_stages(
        stages,
        output_dir=tmp_path / "out",
        summary_output=tmp_path / "summary.json",
        command_runner=lambda command: (0, "", ""),
    )

    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    rollup = saved["artifact_rollup"]
    assert result.artifact_rollup["research_selection"]["selected_symbols"] == ["MSFT", "NVDA"]
    assert rollup["research_selection"]["selected_count"] == 2
    assert rollup["committee_batches"]["completed_count"] == 2
    assert rollup["committee_batches"]["remaining_count"] == 0
    assert rollup["action_plan"]["intent_counts"] == {"BUY": 1, "PARK_IDLE_CASH": 1}
    assert rollup["action_plan"]["allowed_count"] == 2
    assert rollup["paper_preview"]["ready_count"] == 1
    assert rollup["workflow_smoke"]["blocked_count"] == 0
    assert rollup["health"]["missing_count"] == 0


def test_portfolio_news_monitor_ingest_stage_rolls_up_queue_for_scheduler_dashboard(tmp_path):
    report_path = tmp_path / "portfolio_news_monitor.json"
    ingest_path = tmp_path / "portfolio_news_monitor_ingest.json"
    write_json_artifact(
        report_path,
        {
            "schema_version": 1,
            "status": "completed",
            "generated_at": "2026-05-06T15:00:00Z",
            "order_submission_enabled": False,
            "llm_calls_enabled": False,
            "monitored_symbols": ["ADBE", "MSFT"],
            "monitored_count": 2,
            "articles_checked": 3,
            "enrichment_needed_count": 2,
            "high_impact_count": 1,
            "enrichment_needed_queue": [
                {
                    "symbol": "ADBE",
                    "title": "Adobe launches new AI product",
                    "relevance_score": 0.91,
                    "impact_category": "Product/Tech - High",
                    "linked_decision_id": "decision-adbe",
                    "thesis_impact_hint": "potential_confirmation",
                    "next_step": "schedule_deeper_enrichment",
                },
                {
                    "symbol": "MSFT",
                    "title": "Microsoft faces regulatory review",
                    "relevance_score": 0.84,
                    "impact_category": "Regulatory - Medium",
                    "linked_decision_id": "decision-msft",
                    "thesis_impact_hint": "review_required",
                    "next_step": "schedule_deeper_enrichment",
                },
            ],
            "warnings": [],
        },
    )

    result = run_pipeline_stages(
        [
            build_portfolio_news_monitor_ingest_stage(
                portfolio_news_monitor=report_path,
                output_dir=tmp_path,
            )
        ],
        output_dir=tmp_path,
        summary_output=tmp_path / "pipeline_summary.json",
    )

    stage = result.stage_results[0]
    rollup = result.artifact_rollup["portfolio_news_monitor"]
    assert result.status == "completed"
    assert result.order_submission_enabled is False
    assert stage.stage_id == "ingest_portfolio_news_monitor"
    assert stage.artifact_paths["portfolio_news_monitor"] == str(report_path)
    assert stage.artifact_paths["portfolio_news_monitor_ingest"] == str(ingest_path)
    assert stage.artifact_paths["portfolio_news_followup_ideas"] == str(tmp_path / "portfolio_news_followup_ideas.json")
    assert ingest_path.exists()
    assert rollup["queue_count"] == 2
    assert rollup["high_impact_count"] == 1
    assert rollup["review_trigger_count"] == 1
    assert rollup["followup_idea_count"] == 2
    assert rollup["followup_symbols"] == ["ADBE", "MSFT"]
    assert rollup["symbols"] == ["ADBE", "MSFT"]
    assert rollup["high_impact_symbols_with_decisions"] == ["ADBE"]
    assert rollup["top_triggers"][0]["symbol"] == "ADBE"
    followups = json.loads((tmp_path / "portfolio_news_followup_ideas.json").read_text(encoding="utf-8"))
    assert followups[0]["symbol"] == "ADBE"
    assert followups[0]["company_name"] == "ADBE"
    assert followups[0]["idea_source"] == "portfolio_news_monitor"
    assert "Adobe launches new AI product" in "\n".join(followups[0]["source_notes"])
    assert followups[0]["portfolio_news_monitor_metadata"]["trigger_count"] == 1


def test_portfolio_news_monitor_ingest_stage_blocks_on_malformed_report(tmp_path):
    report_path = tmp_path / "portfolio_news_monitor.json"
    report_path.write_text("not-json", encoding="utf-8")

    result = run_pipeline_stages(
        [
            build_portfolio_news_monitor_ingest_stage(
                portfolio_news_monitor=report_path,
                output_dir=tmp_path,
            )
        ],
        output_dir=tmp_path,
        summary_output=tmp_path / "pipeline_summary.json",
    )

    stage = result.stage_results[0]
    assert result.status == "failed"
    assert result.blocker_count == 1
    assert stage.blocker == "stage_failed:ingest_portfolio_news_monitor"
    assert "portfolio news monitor report is not valid JSON" in Path(stage.log_path).read_text(encoding="utf-8")


def test_pipeline_artifact_health_reports_missing_and_malformed_artifacts(tmp_path):
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text("{}", encoding="utf-8")
    bad.write_text("{", encoding="utf-8")

    health = build_pipeline_artifact_health(
        {
            "good": str(good),
            "bad": str(bad),
            "missing": str(tmp_path / "missing.json"),
            "empty": "",
        }
    )

    assert health["present_count"] == 1
    assert health["malformed_count"] == 1
    assert health["missing_count"] == 1
    assert health["empty_path_count"] == 1
    assert health["status"] == "attention_required"


def test_pipeline_artifact_health_treats_directories_as_present_artifacts(tmp_path):
    directory = tmp_path / "committee_batches"
    directory.mkdir()
    json_file = tmp_path / "summary.json"
    json_file.write_text("{}", encoding="utf-8")

    health = build_pipeline_artifact_health(
        {
            "committee_batch_dir": str(directory),
            "generated_committee_batch_run_summary": str(json_file),
        }
    )

    assert health["status"] == "ready"
    assert health["present_count"] == 2
    assert health["malformed_count"] == 0
    assert "committee_batch_dir" in health["present"]


def test_pipeline_artifact_health_treats_non_json_files_as_present_artifacts(tmp_path):
    ledger = tmp_path / "paper_ledger.db"
    ledger.write_bytes(b"\x8a\x00sqlite-ish-binary")

    health = build_pipeline_artifact_health({"paper_ledger": str(ledger)})

    assert health["status"] == "ready"
    assert health["present"] == ["paper_ledger"]
    assert health["malformed_count"] == 0


def test_pipeline_artifact_health_handles_long_windows_artifact_paths(tmp_path):
    artifact_dir = tmp_path
    while len(str(artifact_dir)) < 225:
        artifact_dir = artifact_dir / "scheduler_prerun_snapshot_segment"
    artifact = artifact_dir / f"paper_monday_operator_check_{'x' * 48}.json"
    assert len(str(artifact_dir)) < 260
    assert len(str(artifact)) > 260
    write_json_artifact(artifact, {"ready": True})

    health = build_pipeline_artifact_health({"monday_operator_check": str(artifact)})

    assert health["status"] == "ready"
    assert health["present"] == ["monday_operator_check"]
    assert health["missing_count"] == 0
    assert health["malformed_count"] == 0


def test_build_pipeline_artifact_rollup_is_safe_for_missing_files(tmp_path):
    rollup = build_pipeline_artifact_rollup(
        {
            "research_queue_selected": str(tmp_path / "missing_selected.json"),
            "candidate_action_plan": str(tmp_path / "missing_plan.json"),
        }
    )

    assert rollup["research_selection"]["selected_count"] == 0
    assert rollup["action_plan"]["intent_counts"] == {}
    assert rollup["health"]["missing_count"] == 2


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
    preview_stage = next(stage for stage in stages if stage.stage_id == "paper_preview")
    assert preview_stage.artifact_paths["price_map"] == str(price_map)


def test_build_paper_preflight_stages_can_allow_existing_paper_positions(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio):
        path.write_text("{}", encoding="utf-8")

    default_stages = build_paper_preflight_stages(
        output_dir=tmp_path / "default",
        rules_path=rules_path,
        action_plan=action_plan,
        portfolio_state=portfolio,
        journal_db=journal,
        ledger_db=ledger,
        price_map=price_map,
        skip_price_map=True,
    )
    ongoing_stages = build_paper_preflight_stages(
        output_dir=tmp_path / "ongoing",
        rules_path=rules_path,
        action_plan=action_plan,
        portfolio_state=portfolio,
        journal_db=journal,
        ledger_db=ledger,
        price_map=price_map,
        skip_price_map=True,
        allow_existing_paper_positions=True,
    )

    default_readiness = next(stage for stage in default_stages if stage.stage_id == "paper_smoke_readiness")
    ongoing_readiness = next(stage for stage in ongoing_stages if stage.stage_id == "paper_smoke_readiness")
    default_monday_check = next(stage for stage in default_stages if stage.stage_id == "monday_operator_check")
    ongoing_monday_check = next(stage for stage in ongoing_stages if stage.stage_id == "monday_operator_check")
    default_workflow = next(stage for stage in default_stages if stage.stage_id == "workflow_smoke")
    ongoing_workflow = next(stage for stage in ongoing_stages if stage.stage_id == "workflow_smoke")
    assert "--allow-existing-submissions" not in default_workflow.command
    assert "--allow-existing-submissions" in ongoing_workflow.command
    assert "--allow-existing-paper-positions" not in default_readiness.command
    assert "--allow-existing-paper-positions" not in default_monday_check.command
    assert "--allow-existing-paper-positions" in ongoing_readiness.command
    assert "--allow-existing-paper-positions" in ongoing_monday_check.command


def test_build_paper_preflight_stages_creates_empty_price_map_when_fetch_skipped(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    journal = tmp_path / "journal.db"
    ledger = tmp_path / "paper_ledger.db"
    for path in (rules_path, action_plan, portfolio):
        path.write_text("{}", encoding="utf-8")

    stages = build_paper_preflight_stages(
        output_dir=tmp_path / "pipeline",
        rules_path=rules_path,
        action_plan=action_plan,
        portfolio_state=portfolio,
        journal_db=journal,
        ledger_db=ledger,
        skip_price_map=True,
    )

    stage_ids = [stage.stage_id for stage in stages]
    empty_stage = next(stage for stage in stages if stage.stage_id == "empty_price_map")
    preview_stage = next(stage for stage in stages if stage.stage_id == "paper_preview")
    workflow_stage = next(stage for stage in stages if stage.stage_id == "workflow_smoke")
    assert stage_ids.index("empty_price_map") < stage_ids.index("paper_preview")
    assert "paper_price_map.json" in empty_stage.command
    assert empty_stage.artifact_paths["price_map"].endswith("paper_price_map.json")
    assert "--price-map" in preview_stage.command
    assert "--price-map" in workflow_stage.command
    assert "paper_price_map.json" in workflow_stage.command


def test_committee_batch_stages_use_existing_cycle_script_in_order(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    (batch_dir / "research-batch-002.json").write_text("[]", encoding="utf-8")
    (batch_dir / "research-batch-001.json").write_text("[]", encoding="utf-8")

    stages = build_committee_batch_stages(
        committee_batch_dir=batch_dir,
        output_dir=tmp_path / "out",
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        market_regime_file=tmp_path / "market.json",
        motley_fool_config=tmp_path / "missing_fool.json",
        agent_preset="decision_6",
    )

    assert [stage.stage_id for stage in stages] == ["committee_batch_001", "committee_batch_002"]
    assert "scripts/run_longterm_cycle.py" in stages[0].command
    assert "--idea-batch" in stages[0].command
    assert "--agent-preset decision_6" in stages[0].command
    assert "--market-regime-file" in stages[0].command
    assert "--submit-paper-orders" not in "\n".join(stage.command for stage in stages)
    assert stages[0].stdout_artifact_path.endswith("committee_batch_001_cycle.json")


def test_final_planning_refresh_stage_uses_empty_cycle(tmp_path):
    stage = build_final_planning_refresh_stage(
        output_dir=tmp_path,
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        market_regime_file=tmp_path / "market.json",
        motley_fool_config=tmp_path / "missing_fool.json",
        agent_preset="decision_6",
        active_sleeve_value=35000,
        available_cash=74000,
    )

    assert stage.stage_id == "final_planning_refresh"
    assert "scripts/run_longterm_cycle.py" in stage.command
    assert "--idea-batch" in stage.command
    assert "--active-sleeve-value 35000" in stage.command
    assert "--available-cash 74000" in stage.command
    assert stage.artifact_paths["empty_idea_batch"].endswith("empty_idea_batch.json")
    assert stage.stdout_artifact_path.endswith("final_planning_refresh.json")


def test_final_planning_refresh_stage_can_be_timeout_bounded(tmp_path):
    stage = build_final_planning_refresh_stage(
        output_dir=tmp_path,
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        timeout_seconds=45,
    )

    assert stage.stage_id == "final_planning_refresh"
    assert stage.timeout_seconds == 45


def test_research_campaign_stages_prepare_selection_and_committee_batches(tmp_path):
    source = tmp_path / "nasdaq.txt"
    source.write_text("Symbol|Security Name\nMSFT|Microsoft\n", encoding="utf-8")
    campaign_dir = tmp_path / "campaign"

    stages = build_research_campaign_stages(
        output_dir=tmp_path / "pipeline",
        source_file=source,
        source_url="",
        source="nasdaq_trader",
        campaign_dir=campaign_dir,
        resume=True,
        run_until="research_queue_ready",
        watchlist_limit=305,
        top_percent=10,
        min_pass_count=10,
        max_pass_count=50,
        max_fundamental_fetches=100,
        evidence_batch_size=25,
        max_evidence_batches=2,
        rate_limit_pause_seconds=69,
        polygon_news=True,
        xai_grok=False,
        skip_grok=True,
        selection_top_percent=20,
        selection_min_count=10,
        selection_max_count=50,
        portfolio_state=tmp_path / "portfolio.json",
        research_batch_size=5,
    )

    assert [stage.stage_id for stage in stages] == ["research_campaign", "research_batch_split"]
    commands = "\n".join(stage.command for stage in stages)
    assert "scripts/longterm_research_automation_campaign.py" in commands
    assert "scripts/longterm_research_universe.py" in commands
    assert "--source-file" in stages[0].command
    assert "--source-url" not in stages[0].command
    assert "--run-until research_queue_ready" in stages[0].command
    assert "--resume" in stages[0].command
    assert "--polygon-news" in stages[0].command
    assert "--skip-grok" in stages[0].command
    assert "--rate-limit-pause-seconds 69" in stages[0].command
    assert "--research-ideas" in stages[1].command
    assert "research_selection" in stages[1].command
    assert "research_queue_selected.json" in stages[1].command
    assert stages[1].artifact_paths["committee_batch_dir"].endswith("committee_batches")
    assert "--submit-paper-orders" not in commands


def test_research_campaign_stages_can_use_perplexity_research(tmp_path):
    source = tmp_path / "nasdaq.txt"
    source.write_text("Symbol|Security Name\nMSFT|Microsoft\n", encoding="utf-8")

    stages = build_research_campaign_stages(
        output_dir=tmp_path / "pipeline",
        source_file=source,
        source_url="",
        source="nasdaq_trader",
        campaign_dir=tmp_path / "campaign",
        polygon_news=True,
        perplexity_research=True,
        perplexity_search_context_size="low",
        perplexity_credits_purchased_to_date=12.0,
    )

    command = stages[0].command
    assert "--perplexity-research" in command
    assert "--perplexity-search-context-size low" in command
    assert "--perplexity-credits-purchased-to-date 12" in command
    assert "--skip-grok" not in command
    assert "--xai-grok" not in command
    assert "--submit-paper-orders" not in command


def test_research_campaign_stages_require_one_source(tmp_path):
    with pytest.raises(ValueError, match="source-file or source-url"):
        build_research_campaign_stages(
            output_dir=tmp_path,
            source_file="",
            source_url="",
            source="nasdaq_trader",
            campaign_dir=tmp_path / "campaign",
        )

    with pytest.raises(ValueError, match="not both"):
        build_research_campaign_stages(
            output_dir=tmp_path,
            source_file=tmp_path / "source.csv",
            source_url="https://example.com/list.csv",
            source="nasdaq_trader",
            campaign_dir=tmp_path / "campaign",
        )


def test_generated_committee_batch_runner_stage_uses_campaign_batches(tmp_path):
    stage = build_generated_committee_batch_runner_stage(
        output_dir=tmp_path / "pipeline",
        campaign_dir=tmp_path / "campaign",
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        market_regime_file=tmp_path / "market.json",
        motley_fool_config=tmp_path / "missing_fool.json",
        agent_preset="decision_6",
        profile_config=tmp_path / "profile.json",
        resume=True,
    )

    assert stage.stage_id == "generated_committee_batches"
    assert "scripts/longterm_committee_batch_runner.py" in stage.command
    assert "--committee-batch-dir" in stage.command
    assert "committee_batches" in stage.command
    assert "--resume" in stage.command
    assert "--submit-paper-orders" not in stage.command
    assert stage.artifact_paths["generated_committee_batch_run_summary"].endswith(
        "committee_batch_run_summary.json"
    )


def test_generated_committee_batch_runner_stage_can_use_explicit_batch_dir(tmp_path):
    batch_dir = tmp_path / "committee_preflight" / "committee_batches"
    stage = build_generated_committee_batch_runner_stage(
        output_dir=tmp_path / "pipeline",
        committee_batch_dir=batch_dir,
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        resume=True,
    )

    assert stage.stage_id == "generated_committee_batches"
    assert str(batch_dir) in stage.command
    assert "--resume" in stage.command
    assert stage.artifact_paths["committee_batch_dir"] == str(batch_dir)


def test_generated_committee_batch_runner_stage_can_limit_batches_per_run(tmp_path):
    stage = build_generated_committee_batch_runner_stage(
        output_dir=tmp_path / "pipeline",
        committee_batch_dir=tmp_path / "committee_batches",
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        max_batches=2,
    )

    assert "--max-batches 2" in stage.command


def test_generated_committee_batch_runner_stage_requires_one_batch_source(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        build_generated_committee_batch_runner_stage(
            output_dir=tmp_path / "pipeline",
            journal_db=tmp_path / "journal.db",
            portfolio_state=tmp_path / "portfolio.json",
        )

    with pytest.raises(ValueError, match="exactly one"):
        build_generated_committee_batch_runner_stage(
            output_dir=tmp_path / "pipeline",
            campaign_dir=tmp_path / "campaign",
            committee_batch_dir=tmp_path / "batches",
            journal_db=tmp_path / "journal.db",
            portfolio_state=tmp_path / "portfolio.json",
        )


def test_final_planning_action_plan_extract_stage_writes_action_plan(tmp_path):
    output_dir = tmp_path / "pipeline"
    output_dir.mkdir()
    action_plan = tmp_path / "account_action_plan.json"
    final_refresh = output_dir / "final_planning_refresh.json"
    final_refresh.write_text(
        json.dumps({"account_action_plan": {"schema_version": 1, "intents": [{"symbol": "MSFT"}]}}),
        encoding="utf-8",
    )
    stage = build_final_planning_action_plan_extract_stage(
        output_dir=output_dir,
        action_plan=action_plan,
    )

    result = run_pipeline_stages(
        [stage],
        output_dir=output_dir,
        summary_output=tmp_path / "summary.json",
    )

    saved = json.loads(action_plan.read_text(encoding="utf-8"))
    assert result.status == "completed"
    assert saved["intents"][0]["symbol"] == "MSFT"
    assert stage.artifact_paths["action_plan"] == str(action_plan)


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


def test_pipeline_cli_can_resolve_expected_cash_from_portfolio_state(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, price_map):
        path.write_text("{}", encoding="utf-8")
    portfolio.write_text(json.dumps({"cash": 67641.28}), encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
                "--price-map",
                str(price_map),
                "--skip-price-map",
                "--expected-cash-from-portfolio-state",
                "--print-plan-only",
                "--summary-output",
                str(summary),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    commands = "\n".join(stage["command"] for stage in printed["stages"])
    assert code == 0
    assert "--expected-cash 67641.28" in commands


def test_pipeline_cli_rejects_ambiguous_expected_cash_sources(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    for path in (rules_path, action_plan):
        path.write_text("{}", encoding="utf-8")
    portfolio.write_text(json.dumps({"cash": 67641.28}), encoding="utf-8")

    with pytest.raises(ValueError, match="Choose either --expected-cash or --expected-cash-from-portfolio-state"):
        run_cli(
            build_parser().parse_args(
                [
                    "--output-dir",
                    str(tmp_path / "pipeline"),
                    "--rules-path",
                    str(rules_path),
                    "--action-plan",
                    str(action_plan),
                    "--portfolio-state",
                    str(portfolio),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "ledger.db"),
                    "--expected-cash",
                    "74000",
                    "--expected-cash-from-portfolio-state",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_cli_requires_numeric_cash_when_resolving_from_portfolio_state(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    for path in (rules_path, action_plan):
        path.write_text("{}", encoding="utf-8")
    portfolio.write_text(json.dumps({"holdings": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="cash"):
        run_cli(
            build_parser().parse_args(
                [
                    "--output-dir",
                    str(tmp_path / "pipeline"),
                    "--rules-path",
                    str(rules_path),
                    "--action-plan",
                    str(action_plan),
                    "--portfolio-state",
                    str(portfolio),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "ledger.db"),
                    "--expected-cash-from-portfolio-state",
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_cli_can_resolve_planning_capital_from_portfolio_state(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    market = tmp_path / "market.json"
    for path in (rules_path, action_plan, price_map, market):
        path.write_text("{}", encoding="utf-8")
    portfolio.write_text(
        json.dumps(
            {
                "cash": 1250,
                "protected_symbols": ["FXAIX"],
                "holdings": [
                    {"symbol": "ADBE", "market_value": 700},
                    {"symbol": "FXAIX", "market_value": 34000},
                ],
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--final-planning-refresh",
                "--market-regime-file",
                str(market),
                "--planning-capital-from-portfolio-state",
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    planning_command = next(stage["command"] for stage in printed["stages"] if stage["stage_id"] == "final_planning_refresh")
    assert code == 0
    assert "--available-cash 1250" in planning_command
    assert "--active-sleeve-value 1950" in planning_command


def test_pipeline_cli_rejects_ambiguous_planning_capital_sources(tmp_path):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    for path in (rules_path, action_plan):
        path.write_text("{}", encoding="utf-8")
    portfolio.write_text(json.dumps({"cash": 1250, "holdings": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="Choose explicit planning capital or --planning-capital-from-portfolio-state"):
        run_cli(
            build_parser().parse_args(
                [
                    "--output-dir",
                    str(tmp_path / "pipeline"),
                    "--rules-path",
                    str(rules_path),
                    "--final-planning-refresh",
                    "--planning-capital-from-portfolio-state",
                    "--active-sleeve-value",
                    "74000",
                    "--action-plan",
                    str(action_plan),
                    "--portfolio-state",
                    str(portfolio),
                    "--journal-db",
                    str(tmp_path / "journal.db"),
                    "--ledger-db",
                    str(tmp_path / "ledger.db"),
                    "--print-plan-only",
                ]
            )
        )


def test_pipeline_cli_can_include_committee_and_planning_stages(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    market = tmp_path / "market.json"
    fool = tmp_path / "missing_fool.json"
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    (batch_dir / "research-batch-001.json").write_text("[]", encoding="utf-8")
    for path in (rules_path, action_plan, portfolio, price_map, market):
        path.write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--committee-batch-dir",
                str(batch_dir),
                "--final-planning-refresh",
                "--market-regime-file",
                str(market),
                "--motley-fool-config",
                str(fool),
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    assert code == 0
    assert printed["stage_count"] == 16
    assert printed["stages"][0]["stage_id"] == "committee_batch_001"
    assert printed["stages"][1]["stage_id"] == "final_planning_refresh"
    assert printed["stages"][2]["stage_id"] == "extract_final_action_plan"


def test_pipeline_cli_print_plan_shows_final_planning_timeout_bound(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio, price_map):
        path.write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--final-planning-refresh",
                "--final-planning-timeout-seconds",
                "30",
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    planning_stage = next(stage for stage in printed["stages"] if stage["stage_id"] == "final_planning_refresh")
    assert code == 0
    assert planning_stage["timeout_seconds"] == 30


def test_pipeline_cli_can_prepend_research_campaign_stages(tmp_path, capsys):
    source = tmp_path / "nasdaq.txt"
    source.write_text("Symbol|Security Name\nMSFT|Microsoft\n", encoding="utf-8")
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio, price_map):
        path.write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--research-source-file",
                str(source),
                "--research-source",
                "nasdaq_trader",
                "--research-campaign-dir",
                str(tmp_path / "research_campaign"),
                "--research-resume",
                "--research-run-until",
                "research_queue_ready",
                "--research-batch-size",
                "5",
                "--skip-grok",
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    assert code == 0
    assert printed["stage_count"] == 15
    assert printed["stages"][0]["stage_id"] == "research_campaign"
    assert printed["stages"][1]["stage_id"] == "research_batch_split"
    assert printed["stages"][2]["stage_id"] == "preflight_rules"
    assert printed["artifact_paths"]["committee_batch_dir"].endswith("committee_batches")


def test_pipeline_cli_can_run_generated_committee_batches_between_research_and_preflight(tmp_path, capsys):
    source = tmp_path / "nasdaq.txt"
    source.write_text("Symbol|Security Name\nMSFT|Microsoft\n", encoding="utf-8")
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    market = tmp_path / "market.json"
    for path in (rules_path, action_plan, portfolio, price_map, market):
        path.write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--research-source-file",
                str(source),
                "--research-source",
                "nasdaq_trader",
                "--research-campaign-dir",
                str(tmp_path / "research_campaign"),
                "--run-generated-committee-batches",
                "--market-regime-file",
                str(market),
                "--final-planning-refresh",
                "--skip-grok",
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    assert code == 0
    assert printed["stages"][0]["stage_id"] == "research_campaign"
    assert printed["stages"][1]["stage_id"] == "research_batch_split"
    assert printed["stages"][2]["stage_id"] == "generated_committee_batches"
    assert printed["stages"][3]["stage_id"] == "final_planning_refresh"
    assert printed["stages"][4]["stage_id"] == "extract_final_action_plan"
    assert printed["stages"][5]["stage_id"] == "preflight_rules"


def test_pipeline_cli_can_run_resume_generated_runner_from_explicit_batch_dir(tmp_path, capsys):
    batch_dir = tmp_path / "committee_batches"
    batch_dir.mkdir()
    (batch_dir / "research-batch-001.json").write_text("[]", encoding="utf-8")
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio, price_map):
        path.write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--committee-batch-dir",
                str(batch_dir),
                "--run-generated-committee-batches",
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    assert code == 0
    assert printed["stages"][0]["stage_id"] == "generated_committee_batches"
    assert printed["stages"][1]["stage_id"] == "preflight_rules"
    assert "longterm_committee_batch_runner.py" in printed["stages"][0]["command"]
    assert "--resume" in printed["stages"][0]["command"]
    assert printed["stage_count"] == 14


def test_pipeline_cli_passes_generated_committee_max_batches(tmp_path, capsys):
    batch_dir = tmp_path / "committee_batches"
    batch_dir.mkdir()
    (batch_dir / "research-batch-001.json").write_text("[]", encoding="utf-8")
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    for path in (rules_path, action_plan, portfolio, price_map):
        path.write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--committee-batch-dir",
                str(batch_dir),
                "--run-generated-committee-batches",
                "--generated-committee-max-batches",
                "1",
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
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
    assert code == 0
    assert "--max-batches 1" in printed["stages"][0]["command"]


def test_pipeline_cli_ingests_portfolio_news_monitor_before_preflight(tmp_path, capsys):
    rules_path = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "account_action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    price_map = tmp_path / "prices.json"
    monitor = tmp_path / "portfolio_news_monitor.json"
    for path in (rules_path, action_plan, portfolio, price_map):
        path.write_text("{}", encoding="utf-8")
    write_json_artifact(
        monitor,
        {
            "schema_version": 1,
            "status": "completed",
            "enrichment_needed_queue": [],
            "order_submission_enabled": False,
            "llm_calls_enabled": False,
        },
    )
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--output-dir",
                str(tmp_path / "pipeline"),
                "--rules-path",
                str(rules_path),
                "--action-plan",
                str(action_plan),
                "--portfolio-state",
                str(portfolio),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--ledger-db",
                str(tmp_path / "ledger.db"),
                "--price-map",
                str(price_map),
                "--portfolio-news-monitor",
                str(monitor),
                "--skip-price-map",
                "--print-plan-only",
                "--summary-output",
                str(summary),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["stages"][0]["stage_id"] == "ingest_portfolio_news_monitor"
    assert printed["stages"][1]["stage_id"] == "preflight_rules"
    assert printed["artifact_paths"]["portfolio_news_monitor"] == str(monitor)
    assert "--input" in printed["stages"][0]["command"]
