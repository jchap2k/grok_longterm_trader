import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_runbook import build_paper_runbook, build_paper_runbook_markdown
from longterm.paper_runbook_cli import build_parser, run_cli


def test_paper_runbook_lists_monday_artifacts_and_ordered_commands():
    runbook = build_paper_runbook(
        journal_db="journal.db",
        ledger_db="paper_ledger.db",
        portfolio_state="portfolio.json",
        action_plan="account_action_plan.json",
        output_dir="artifacts",
        expected_cash=74000,
        profile_config="profile.json",
    )

    assert runbook["mode"] == "paper_runbook"
    assert runbook["order_submission_enabled"] is False
    assert runbook["artifacts"]["workflow_smoke"] == "artifacts\\paper_workflow_smoke.json"
    assert runbook["artifacts"]["paper_smoke_readiness"] == "artifacts\\paper_smoke_readiness.json"
    assert "--profile-config profile.json" in runbook["steps"][0]["command"]
    assert "--portfolio-state-output portfolio.json" in runbook["steps"][0]["command"]
    assert "--profile-config profile.json" in runbook["steps"][1]["command"]
    assert runbook["steps"][0]["save_stdout_to"] == "artifacts\\paper_snapshot.json"
    assert [step["step_id"] for step in runbook["steps"]] == [
        "snapshot",
        "workflow_smoke",
        "paper_smoke_readiness",
        "runbook_check",
        "supervised_submit",
        "status_refresh",
        "paper_cleanup_reminder",
        "paper_trading_verification",
        "live_readiness_bundle",
    ]
    assert runbook["steps"][4]["requires_explicit_reveal"] is True
    assert "--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY" not in runbook["steps"][4]["command"]
    assert "redacted" in runbook["steps"][4]["command"].lower()
    assert "--report-output artifacts\\paper_smoke_readiness.json" in runbook["steps"][2]["command"]
    assert "--workflow-smoke artifacts\\paper_workflow_smoke.json" in runbook["steps"][3]["command"]
    assert "--action-plan account_action_plan.json" in runbook["steps"][3]["command"]
    assert "--report-output artifacts\\paper_runbook_check.json" in runbook["steps"][3]["command"]
    assert "sell or cancel the temporary paper position" in runbook["steps"][6]["title"].lower()
    assert "--report-output artifacts\\live_readiness_bundle.json" in runbook["steps"][8]["command"]
    assert "Monday Paper Trading Runbook" in build_paper_runbook_markdown(runbook)


def test_paper_runbook_reveals_supervised_submit_only_when_requested():
    runbook = build_paper_runbook(
        journal_db="journal.db",
        ledger_db="paper_ledger.db",
        portfolio_state="portfolio.json",
        action_plan="account_action_plan.json",
        output_dir="artifacts",
        expected_cash=74000,
        profile_config="profile.json",
        include_submit_command=True,
    )

    submit_step = runbook["steps"][4]

    assert submit_step["requires_explicit_reveal"] is False
    assert "--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY" in submit_step["command"]
    assert "--runbook-check artifacts\\paper_runbook_check.json" in submit_step["command"]
    assert "--profile-config profile.json" in submit_step["command"]


def test_paper_runbook_cli_outputs_json(tmp_path, capsys):
    args = build_parser().parse_args(
        [
            "--journal-db",
            "journal.db",
            "--ledger-db",
            "paper_ledger.db",
            "--portfolio-state",
            "portfolio.json",
            "--action-plan",
            "account_action_plan.json",
            "--output-dir",
            str(tmp_path / "artifacts"),
            "--profile-config",
            "profile.json",
            "--expected-cash",
            "74000",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "paper_runbook"
    assert payload["expected_cash"] == 74000
    assert payload["profile_config"] == "profile.json"
    assert payload["steps"][0]["step_id"] == "snapshot"
    assert payload["steps"][4]["requires_explicit_reveal"] is True
    assert "redacted" in payload["steps"][4]["command"].lower()


def test_paper_runbook_cli_can_explicitly_reveal_submit_command(tmp_path, capsys):
    args = build_parser().parse_args(
        [
            "--journal-db",
            "journal.db",
            "--ledger-db",
            "paper_ledger.db",
            "--portfolio-state",
            "portfolio.json",
            "--action-plan",
            "account_action_plan.json",
            "--output-dir",
            str(tmp_path / "artifacts"),
            "--include-submit-command",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["include_submit_command"] is True
    assert "--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY" in payload["steps"][4]["command"]
