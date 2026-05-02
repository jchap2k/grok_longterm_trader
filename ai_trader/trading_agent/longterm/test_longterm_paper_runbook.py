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
    )

    assert runbook["mode"] == "paper_runbook"
    assert runbook["order_submission_enabled"] is False
    assert runbook["artifacts"]["workflow_smoke"] == "artifacts\\paper_workflow_smoke.json"
    assert runbook["artifacts"]["paper_smoke_readiness"] == "artifacts\\paper_smoke_readiness.json"
    assert "--portfolio-state-output portfolio.json" in runbook["steps"][0]["command"]
    assert runbook["steps"][0]["save_stdout_to"] == "artifacts\\paper_snapshot.json"
    assert [step["step_id"] for step in runbook["steps"]] == [
        "snapshot",
        "workflow_smoke",
        "paper_smoke_readiness",
        "runbook_check",
        "supervised_submit",
        "status_refresh",
        "paper_trading_verification",
        "live_readiness_bundle",
    ]
    assert "--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY" in runbook["steps"][4]["command"]
    assert "--report-output artifacts\\paper_smoke_readiness.json" in runbook["steps"][2]["command"]
    assert "--workflow-smoke artifacts\\paper_workflow_smoke.json" in runbook["steps"][3]["command"]
    assert "--action-plan account_action_plan.json" in runbook["steps"][3]["command"]
    assert "--report-output artifacts\\paper_runbook_check.json" in runbook["steps"][3]["command"]
    assert "--runbook-check artifacts\\paper_runbook_check.json" in runbook["steps"][4]["command"]
    assert "--report-output artifacts\\live_readiness_bundle.json" in runbook["steps"][7]["command"]
    assert "Monday Paper Trading Runbook" in build_paper_runbook_markdown(runbook)


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
            "--expected-cash",
            "74000",
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "paper_runbook"
    assert payload["expected_cash"] == 74000
    assert payload["steps"][0]["step_id"] == "snapshot"
