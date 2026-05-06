import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.committee_batch_runner import run_committee_batch_dir
from longterm.committee_batch_runner_cli import build_parser, run_cli


def _write_batch(path: Path, symbol: str) -> None:
    path.write_text(json.dumps([{"symbol": symbol, "company_name": symbol}]), encoding="utf-8")


def test_committee_batch_runner_runs_batches_in_order_and_writes_summary(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    commands: list[str] = []

    def fake_runner(command: str) -> tuple[int, str, str]:
        commands.append(command)
        return 0, '{"decision_journal_refs": ["decision_1"]}', ""

    result = run_committee_batch_dir(
        committee_batch_dir=batch_dir,
        output_dir=tmp_path / "out",
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        market_regime_file=tmp_path / "market.json",
        motley_fool_config=tmp_path / "missing_fool.json",
        agent_preset="decision_6",
        profile_config=tmp_path / "profile.json",
        campaign_id="campaign-alpha",
        command_runner=fake_runner,
    )

    assert [item["batch_id"] for item in result["batches"]] == ["research-batch-001", "research-batch-002"]
    assert result["status"] == "completed"
    assert result["completed_count"] == 2
    assert result["failed_count"] == 0
    assert result["campaign_id"] == "campaign-alpha"
    assert "research-batch-001.json" in commands[0]
    assert "research-batch-002.json" in commands[1]
    assert "--submit-paper-orders" not in "\n".join(commands)
    assert Path(result["batches"][0]["cycle_output"]).exists()
    assert Path(result["summary_output"]).exists()


def test_committee_batch_runner_resume_skips_completed_batches(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    summary = {
        "batches": [
            {
                "batch_id": "research-batch-001",
                "status": "passed",
                "cycle_output": str(output_dir / "research-batch-001_cycle.json"),
            }
        ]
    }
    (output_dir / "committee_batch_run_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    commands: list[str] = []

    result = run_committee_batch_dir(
        committee_batch_dir=batch_dir,
        output_dir=output_dir,
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        resume=True,
        command_runner=lambda command: commands.append(command) or (0, "{}", ""),
    )

    assert len(commands) == 1
    assert "research-batch-002.json" in commands[0]
    assert result["skipped_count"] == 1
    assert result["completed_count"] == 1
    assert result["batches"][0]["status"] == "skipped_resume"


def test_committee_batch_runner_stops_on_first_failure(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")

    result = run_committee_batch_dir(
        committee_batch_dir=batch_dir,
        output_dir=tmp_path / "out",
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        command_runner=lambda command: (3, "", "boom"),
    )

    assert result["status"] == "failed"
    assert result["failed_count"] == 1
    assert result["completed_count"] == 0
    assert len(result["batches"]) == 1
    assert "stage_failed" in result["batches"][0]["blocker"]


def test_committee_batch_runner_writes_incremental_resume_summary(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")
    output_dir = tmp_path / "out"
    summary = output_dir / "committee_batch_run_summary.json"
    seen_during_second_batch: dict[str, object] = {}

    def fake_runner(command: str) -> tuple[int, str, str]:
        if "research-batch-002.json" in command:
            seen_during_second_batch.update(json.loads(summary.read_text(encoding="utf-8")))
        return 0, "{}", ""

    result = run_committee_batch_dir(
        committee_batch_dir=batch_dir,
        output_dir=output_dir,
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        command_runner=fake_runner,
    )

    assert seen_during_second_batch["status"] == "running"
    assert seen_during_second_batch["completed_count"] == 1
    assert seen_during_second_batch["remaining_count"] == 1
    assert result["status"] == "completed"
    assert result["remaining_count"] == 0


def test_committee_batch_runner_max_batches_pauses_after_next_pending_batch(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")
    _write_batch(batch_dir / "research-batch-003.json", "ADBE")
    commands: list[str] = []

    result = run_committee_batch_dir(
        committee_batch_dir=batch_dir,
        output_dir=tmp_path / "out",
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        max_batches=1,
        command_runner=lambda command: commands.append(command) or (0, "{}", ""),
    )

    saved = json.loads(Path(result["summary_output"]).read_text(encoding="utf-8"))
    assert result["status"] == "partial"
    assert result["completed_count"] == 1
    assert result["remaining_count"] == 2
    assert len(commands) == 1
    assert "research-batch-001.json" in commands[0]
    assert saved["status"] == "partial"
    assert saved["remaining_count"] == 2


def test_committee_batch_runner_max_batches_resume_skips_completed_and_runs_next(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")
    _write_batch(batch_dir / "research-batch-003.json", "ADBE")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "committee_batch_run_summary.json").write_text(
        json.dumps({"batches": [{"batch_id": "research-batch-001", "status": "passed"}]}),
        encoding="utf-8",
    )
    commands: list[str] = []

    result = run_committee_batch_dir(
        committee_batch_dir=batch_dir,
        output_dir=output_dir,
        journal_db=tmp_path / "journal.db",
        portfolio_state=tmp_path / "portfolio.json",
        resume=True,
        max_batches=1,
        command_runner=lambda command: commands.append(command) or (0, "{}", ""),
    )

    assert result["status"] == "partial"
    assert result["skipped_count"] == 1
    assert result["completed_count"] == 1
    assert result["remaining_count"] == 1
    assert len(commands) == 1
    assert "research-batch-002.json" in commands[0]


def test_committee_batch_runner_cli_prints_json_summary(tmp_path, capsys):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    summary = tmp_path / "summary.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--committee-batch-dir",
                str(batch_dir),
                "--output-dir",
                str(tmp_path / "out"),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--portfolio-state",
                str(tmp_path / "portfolio.json"),
                "--summary-output",
                str(summary),
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["status"] == "planned"
    assert saved["batch_count"] == 1
    assert saved["batches"][0]["status"] == "planned"


def test_committee_batch_runner_cli_accepts_partial_max_batches(tmp_path, capsys):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", "MSFT")
    _write_batch(batch_dir / "research-batch-002.json", "NVDA")

    code = run_cli(
        build_parser().parse_args(
            [
                "--committee-batch-dir",
                str(batch_dir),
                "--output-dir",
                str(tmp_path / "out"),
                "--journal-db",
                str(tmp_path / "journal.db"),
                "--portfolio-state",
                str(tmp_path / "portfolio.json"),
                "--max-batches",
                "1",
                "--print-plan-only",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["status"] == "partial"
    assert printed["remaining_count"] == 1
