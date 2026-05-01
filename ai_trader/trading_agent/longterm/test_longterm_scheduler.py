import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.scheduler import (
    LongTermSchedulerConfig,
    LongTermSchedulerInputs,
    build_cycle_kwargs,
    run_longterm_scheduler,
)
from longterm.scheduler_cli import build_parser, run_cli


def _write_profile(path: Path) -> None:
    path.write_text(
        '{"account_strategy_mode":"roth_ira","tradable_capital":35000,"protected_symbols":["FXAIX"],"benchmark_symbol":"FXAIX","defensive_parking_symbol":"SPY"}',
        encoding="utf-8",
    )


def test_build_cycle_kwargs_loads_fresh_portfolio_state_each_time(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text('{"cash":1000,"holdings":[]}', encoding="utf-8")
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text('[{"symbol":"MSFT","source":"sp500"}]', encoding="utf-8")

    inputs = LongTermSchedulerInputs(
        profile_config=profile_path,
        portfolio_state=portfolio_path,
        discovery_candidates=discovery_path,
        journal_db=tmp_path / "journal.db",
        launch_login_if_needed=True,
        quiet=True,
    )

    first = build_cycle_kwargs(inputs)
    portfolio_path.write_text('{"cash":2500,"holdings":[]}', encoding="utf-8")
    second = build_cycle_kwargs(inputs)

    assert first["portfolio_state"].cash == 1000.0
    assert second["portfolio_state"].cash == 2500.0
    assert second["discovery_candidates"] == [{"symbol": "MSFT", "source": "sp500"}]
    assert second["journal_db_path"] == tmp_path / "journal.db"
    assert second["launch_login_if_needed"] is True
    assert second["verbose"] is False


def test_build_cycle_kwargs_loads_discovery_source_file(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    source_path = tmp_path / "sp500.csv"
    source_path.write_text(
        "Symbol,Security,GICS Sector\nMSFT,Microsoft,Information Technology\n",
        encoding="utf-8",
    )

    inputs = LongTermSchedulerInputs(
        profile_config=profile_path,
        discovery_source_file=source_path,
        discovery_source="sp500",
    )

    kwargs = build_cycle_kwargs(inputs)

    assert kwargs["discovery_candidates"][0]["symbol"] == "MSFT"
    assert kwargs["discovery_candidates"][0]["source"] == "sp500"


def test_build_cycle_kwargs_loads_discovery_enrichment_file(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    source_path = tmp_path / "sp500.csv"
    enrichment_path = tmp_path / "fundamentals.json"
    source_path.write_text("Symbol,Security\nMSFT,Microsoft\n", encoding="utf-8")
    enrichment_path.write_text(
        '{"MSFT":{"market_cap":3000000000000,"revenue_growth_1y_pct":16}}',
        encoding="utf-8",
    )

    inputs = LongTermSchedulerInputs(
        profile_config=profile_path,
        discovery_source_file=source_path,
        discovery_source="sp500",
        discovery_enrichment_file=enrichment_path,
        discovery_enrichment_source="fundamentals_cache",
    )

    kwargs = build_cycle_kwargs(inputs)

    assert kwargs["discovery_candidates"][0]["market_cap"] == 3_000_000_000_000.0
    assert "Enriched from fundamentals_cache." in kwargs["discovery_candidates"][0]["notes"]


def test_scheduler_run_once_records_explicit_outputs(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    calls = []

    def fake_cycle(**kwargs):
        calls.append(kwargs)
        return {
            "status": "completed",
            "capture_status": "disabled",
            "setup_status": "not_requested",
            "decision_ids": ["decision-AAPL"],
            "total_idea_count": 1,
            "skipped_idea_count": 1,
            "skipped_ideas": [{"symbol": "TSLA", "reason": "incomplete_research_packet"}],
            "deferred_research_queue": [
                {
                    "symbol": "TSLA",
                    "reason": "incomplete_research_packet",
                    "missing_fields": ["company_name", "idea_source", "research_context"],
                    "provenance_bucket": "manual",
                    "suggested_next_step": "enrich_candidate_before_research",
                    "suggested_enrichment_command": "python scripts/run_longterm_discovery.py ...",
                }
            ],
            "recommendation_report_markdown": "# report\n",
            "next_actions_markdown": "# actions\n",
            "capital_alert_markdown": "# capital\n",
            "rebalance_markdown": "# rebalance\n",
            "account_action_plan": {"mode": "dry_run", "intents": [{"symbol": "NVDA"}]},
            "idea_provenance_summary": {"manual": 1},
            "packet_completeness_warnings": [],
        }

    summary = run_longterm_scheduler(
        inputs=LongTermSchedulerInputs(profile_config=profile_path),
        config=LongTermSchedulerConfig(max_runs=1, interval_seconds=60),
        cycle_func=fake_cycle,
        sleep_func=lambda seconds: None,
    )

    assert summary.status == "completed"
    assert summary.run_count == 1
    assert summary.success_count == 1
    assert summary.error_count == 0
    assert summary.runs[0].decision_ids == ["decision-AAPL"]
    assert summary.runs[0].skipped_idea_count == 1
    assert summary.runs[0].skipped_ideas == [{"symbol": "TSLA", "reason": "incomplete_research_packet"}]
    assert summary.runs[0].deferred_research_queue[0]["symbol"] == "TSLA"
    assert summary.runs[0].deferred_research_queue[0]["missing_fields"] == [
        "company_name",
        "idea_source",
        "research_context",
    ]
    assert summary.runs[0].recommendation_report_markdown == "# report\n"
    assert summary.runs[0].next_actions_markdown == "# actions\n"
    assert summary.runs[0].capital_alert_markdown == "# capital\n"
    assert summary.runs[0].rebalance_markdown == "# rebalance\n"
    assert summary.runs[0].account_action_plan["mode"] == "dry_run"
    assert summary.runs[0].idea_provenance_summary == {"manual": 1}
    assert calls[0]["profile"].protected_symbols == ["FXAIX"]


def test_scheduler_repeats_until_max_runs_and_sleeps_between_runs(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    sleeps = []

    summary = run_longterm_scheduler(
        inputs=LongTermSchedulerInputs(profile_config=profile_path),
        config=LongTermSchedulerConfig(max_runs=3, interval_seconds=15),
        cycle_func=lambda **kwargs: {"status": "completed", "decision_ids": []},
        sleep_func=lambda seconds: sleeps.append(seconds),
    )

    assert summary.status == "completed"
    assert summary.run_count == 3
    assert summary.success_count == 3
    assert sleeps == [15, 15]


def test_scheduler_can_continue_after_cycle_error(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    attempts = []

    def flaky_cycle(**kwargs):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        return {"status": "completed", "decision_ids": []}

    summary = run_longterm_scheduler(
        inputs=LongTermSchedulerInputs(profile_config=profile_path),
        config=LongTermSchedulerConfig(
            max_runs=2,
            interval_seconds=5,
            stop_on_error=False,
        ),
        cycle_func=flaky_cycle,
        sleep_func=lambda seconds: None,
    )

    assert summary.status == "completed_with_errors"
    assert summary.run_count == 2
    assert summary.success_count == 1
    assert summary.error_count == 1
    assert summary.runs[0].status == "error"
    assert "temporary failure" in summary.runs[0].error


def test_scheduler_can_stop_after_cycle_error(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)

    summary = run_longterm_scheduler(
        inputs=LongTermSchedulerInputs(profile_config=profile_path),
        config=LongTermSchedulerConfig(
            max_runs=3,
            interval_seconds=5,
            stop_on_error=True,
        ),
        cycle_func=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("hard failure")),
        sleep_func=lambda seconds: None,
    )

    assert summary.status == "stopped_on_error"
    assert summary.run_count == 1
    assert summary.success_count == 0
    assert summary.error_count == 1


def test_scheduler_cli_forwards_inputs_and_prints_summary(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text('{"cash":5000,"holdings":[]}', encoding="utf-8")

    scheduler_calls = []

    def fake_scheduler(*, inputs, config):
        scheduler_calls.append((inputs, config))
        return {
            "status": "completed",
            "run_count": 1,
            "success_count": 1,
            "error_count": 0,
            "runs": [],
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--journal-db",
            str(tmp_path / "journal.db"),
            "--run-once",
            "--launch-login-if-needed",
            "--quiet",
        ]
    )

    exit_code = run_cli(args, scheduler_func=fake_scheduler)

    output = capsys.readouterr().out
    inputs, config = scheduler_calls[0]
    assert exit_code == 0
    assert '"status": "completed"' in output
    assert inputs.profile_config == profile_path
    assert inputs.portfolio_state == portfolio_path
    assert inputs.launch_login_if_needed is True
    assert inputs.quiet is True
    assert config.max_runs == 1


def test_scheduler_cli_forwards_discovery_source_file(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    source_path = tmp_path / "sp500.csv"
    source_path.write_text("Symbol,Security\nMSFT,Microsoft\n", encoding="utf-8")

    scheduler_calls = []

    def fake_scheduler(*, inputs, config):
        scheduler_calls.append((inputs, config))
        return {
            "status": "completed",
            "run_count": 1,
            "success_count": 1,
            "error_count": 0,
            "runs": [],
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--discovery-source-file",
            str(source_path),
            "--discovery-source",
            "sp500",
            "--run-once",
        ]
    )

    exit_code = run_cli(args, scheduler_func=fake_scheduler)

    inputs, config = scheduler_calls[0]
    assert exit_code == 0
    assert inputs.discovery_source_file == source_path
    assert inputs.discovery_source == "sp500"
    assert config.max_runs == 1


def test_scheduler_cli_forwards_discovery_enrichment_file(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    enrichment_path = tmp_path / "fundamentals.json"
    enrichment_path.write_text('{"MSFT":{"market_cap":3000000000000}}', encoding="utf-8")

    scheduler_calls = []

    def fake_scheduler(*, inputs, config):
        scheduler_calls.append((inputs, config))
        return {
            "status": "completed",
            "run_count": 1,
            "success_count": 1,
            "error_count": 0,
            "runs": [],
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--discovery-enrichment-file",
            str(enrichment_path),
            "--discovery-enrichment-source",
            "fundamentals_cache",
            "--run-once",
        ]
    )

    exit_code = run_cli(args, scheduler_func=fake_scheduler)

    inputs, _config = scheduler_calls[0]
    assert exit_code == 0
    assert inputs.discovery_enrichment_file == enrichment_path
    assert inputs.discovery_enrichment_source == "fundamentals_cache"


def test_scheduler_writes_summary_output_file(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    output_path = tmp_path / "summary.json"

    summary = run_longterm_scheduler(
        inputs=LongTermSchedulerInputs(profile_config=profile_path),
        config=LongTermSchedulerConfig(max_runs=1, interval_seconds=5),
        cycle_func=lambda **kwargs: {"status": "completed", "decision_ids": []},
        sleep_func=lambda seconds: None,
        summary_output_path=output_path,
    )

    assert summary.status == "completed"
    assert output_path.exists()
    assert '"status": "completed"' in output_path.read_text(encoding="utf-8")
