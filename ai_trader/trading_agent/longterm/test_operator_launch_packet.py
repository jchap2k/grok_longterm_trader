import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.operator_launch_packet import (
    build_operator_launch_packet,
    build_operator_launch_packet_markdown,
)
from longterm.operator_launch_packet_cli import build_parser, run_cli


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_launch_packet_summarizes_ready_candidates_and_parking():
    packet = build_operator_launch_packet(
        dashboard={
            "agent_advisory": {"state": "ready_for_supervised_paper_review"},
            "paper_submit_candidates": ["MSFT", "MA"],
            "parking_symbols": ["SPY"],
            "market_regime": {"risk_regime": "normal"},
        },
        candidate_plan={
            "excluded_count": 4,
            "intents": [
                {
                    "intent_type": "BUY",
                    "symbol": "MSFT",
                    "allowed": True,
                    "trade_value": 1613.02,
                    "quantity": 4,
                    "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                },
                {
                    "intent_type": "BUY",
                    "symbol": "MA",
                    "allowed": True,
                    "trade_value": 991.05,
                    "quantity": 2,
                    "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                },
            ],
        },
        monday_check={"ready_for_review": True, "blockers": [], "blocker_count": 0},
        runbook={"steps": [{"step_id": "supervised_submit", "requires_explicit_reveal": True}]},
        site_index="C:\\Temp\\operator_dashboard_site\\index.html",
    )

    assert packet["launch_state"] == "ready_for_supervised_review"
    assert packet["order_submission_enabled"] is False
    assert packet["candidate_symbols"] == ["MSFT", "MA"]
    assert packet["parking_symbols"] == ["SPY"]
    assert packet["submit_command_revealed"] is False
    assert packet["candidate_count"] == 2
    assert packet["excluded_intent_count"] == 4
    assert packet["required_conditions"][0].startswith("Review the static dashboard")
    assert packet["artifacts"]["site_index"].endswith("index.html")

    markdown = build_operator_launch_packet_markdown(packet)
    assert "# Monday Launch Packet" in markdown
    assert "| BUY | MSFT | 4 | $1,613.02 | ACTIONABLE_BUY |" in markdown
    assert "SPY" in markdown
    assert "Order submission enabled: `false`" in markdown
    assert "Do Not Do" in markdown


def test_launch_packet_blocks_when_monday_check_has_blockers():
    packet = build_operator_launch_packet(
        dashboard={"agent_advisory": {"state": "blocked_preflight"}, "paper_submit_candidates": ["MSFT"]},
        candidate_plan={"intents": [{"intent_type": "BUY", "symbol": "MSFT", "allowed": True}]},
        monday_check={"ready_for_review": False, "blockers": ["paper_account_not_clean"], "blocker_count": 1},
    )

    assert packet["launch_state"] == "blocked"
    assert packet["ready_for_supervised_review"] is False
    assert packet["blockers"] == ["paper_account_not_clean"]
    assert "paper_account_not_clean" in build_operator_launch_packet_markdown(packet)


def test_launch_packet_can_overlay_whole_share_preview_quantities():
    packet = build_operator_launch_packet(
        dashboard={"paper_submit_candidates": ["MSFT"]},
        candidate_plan={
            "intents": [
                {
                    "intent_type": "BUY",
                    "symbol": "MSFT",
                    "allowed": True,
                    "trade_value": 1700,
                    "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                }
            ]
        },
        monday_check={"ready_for_review": True, "blockers": [], "blocker_count": 0},
        workflow_smoke={
            "preview": {
                "previews": [
                    {
                        "symbol": "MSFT",
                        "quantity": 4,
                        "notional": 1613.02,
                        "estimated_price": 403.255,
                    }
                ]
            }
        },
    )

    candidate = packet["paper_submit_candidates"][0]
    assert candidate["quantity"] == 4
    assert candidate["notional"] == 1613.02
    assert candidate["estimated_price"] == 403.255


def test_launch_packet_cli_writes_markdown_and_json(tmp_path, capsys):
    dashboard = _write_json(
        tmp_path / "dashboard.json",
        {
            "agent_advisory": {"state": "ready_for_supervised_paper_review"},
            "paper_submit_candidates": ["MA"],
            "parking_symbols": ["SPY"],
            "market_regime": {"risk_regime": "normal"},
        },
    )
    candidate_plan = _write_json(
        tmp_path / "candidate_plan.json",
        {
            "intents": [
                {
                    "intent_type": "BUY",
                    "symbol": "MA",
                    "allowed": True,
                    "trade_value": 991.05,
                    "quantity": 2,
                    "promotion_review": {"promotion_decision": "ACTIONABLE_BUY"},
                }
            ]
        },
    )
    monday_check = _write_json(
        tmp_path / "monday_check.json",
        {"ready_for_review": True, "blockers": [], "blocker_count": 0},
    )
    workflow_smoke = _write_json(
        tmp_path / "workflow_smoke.json",
        {"preview": {"previews": [{"symbol": "MA", "quantity": 2, "notional": 991.05}]}},
    )
    markdown_output = tmp_path / "launch_packet.md"
    json_output = tmp_path / "launch_packet.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--dashboard-file",
                str(dashboard),
                "--candidate-plan",
                str(candidate_plan),
                "--monday-check",
                str(monday_check),
                "--workflow-smoke",
                str(workflow_smoke),
                "--site-index",
                "C:\\Temp\\site\\index.html",
                "--output",
                str(markdown_output),
                "--json-output",
                str(json_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(json_output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["candidate_symbols"] == ["MA"]
    assert printed["paper_submit_candidates"][0]["quantity"] == 2
    assert saved["launch_state"] == "ready_for_supervised_review"
    assert "Monday Launch Packet" in markdown_output.read_text(encoding="utf-8")
