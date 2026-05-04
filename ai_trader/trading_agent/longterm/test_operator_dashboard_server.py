import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.operator_dashboard_server import (
    build_dashboard_manifest,
    build_dashboard_pages_from_manifest,
    load_dashboard_manifest,
    resolve_dashboard_request,
)
from longterm.operator_dashboard_server_cli import build_parser, run_cli


def test_dashboard_manifest_records_traceability_and_rules_hash(tmp_path):
    rules = tmp_path / "active_rules.txt"
    rules.write_text("protect FXAIX", encoding="utf-8")

    manifest = build_dashboard_manifest(
        action_plan=tmp_path / "action_plan.json",
        portfolio_state=tmp_path / "portfolio.json",
        active_rules_path=rules,
        decision_journal_path=tmp_path / "journal.db",
        campaign_id="campaign-1",
    )

    assert manifest["schema_version"] == 1
    assert manifest["campaign_id"] == "campaign-1"
    assert manifest["decision_journal_path"].endswith("journal.db")
    assert manifest["active_rules_hash"]
    assert manifest["generated_at"].endswith("Z")


def test_live_dashboard_filters_protected_symbol_from_actionable_candidates(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(
        json.dumps(
            {
                "intents": [
                    {"intent_type": "BUY", "symbol": "FXAIX", "allowed": True, "trade_value": 1000},
                    {"intent_type": "BUY", "symbol": "MSFT", "allowed": True, "trade_value": 1000},
                ]
            }
        ),
        encoding="utf-8",
    )
    portfolio.write_text(
        json.dumps(
            {
                "cash": 1000,
                "protected_symbols": ["FXAIX"],
                "holdings": [{"symbol": "FXAIX", "quantity": 5, "market_value": 500}],
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(build_dashboard_manifest(action_plan=action_plan, portfolio_state=portfolio)),
        encoding="utf-8",
    )

    pages = build_dashboard_pages_from_manifest(load_dashboard_manifest(manifest_path))
    summary_status, _, summary_body = resolve_dashboard_request(manifest_path, "/api/summary.json")
    summary = json.loads(summary_body.decode("utf-8"))

    assert summary_status == 200
    assert summary["paper_submit_candidates"] == ["MSFT"]
    assert "tickers/MSFT.html" in pages
    assert "tickers/FXAIX.html" in pages
    assert "FXAIX" in pages["index.html"]


def test_resolve_dashboard_request_serves_index_ticker_manifest_and_404(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(
        json.dumps({"intents": [{"intent_type": "BUY", "symbol": "MSFT", "allowed": True}]}),
        encoding="utf-8",
    )
    portfolio.write_text(json.dumps({"protected_symbols": ["FXAIX"], "holdings": []}), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(build_dashboard_manifest(action_plan=action_plan, portfolio_state=portfolio)),
        encoding="utf-8",
    )

    index_status, index_type, index_body = resolve_dashboard_request(manifest_path, "/")
    ticker_status, ticker_type, ticker_body = resolve_dashboard_request(manifest_path, "/tickers/MSFT.html")
    manifest_status, manifest_type, manifest_body = resolve_dashboard_request(manifest_path, "/api/manifest.json")
    missing_status, _, _ = resolve_dashboard_request(manifest_path, "/nope")

    assert index_status == 200
    assert index_type == "text/html; charset=utf-8"
    assert b"Long-Term Trader Dashboard" in index_body
    assert ticker_status == 200
    assert ticker_type == "text/html; charset=utf-8"
    assert b"MSFT" in ticker_body
    assert manifest_status == 200
    assert manifest_type == "application/json; charset=utf-8"
    assert json.loads(manifest_body.decode("utf-8"))["schema_version"] == 1
    assert missing_status == 404


def test_dashboard_server_cli_can_write_manifest_without_serving(tmp_path, capsys):
    rules = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "action_plan.json"
    manifest = tmp_path / "dashboard_manifest.json"
    rules.write_text("rules", encoding="utf-8")
    action_plan.write_text("{}", encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--manifest",
                str(manifest),
                "--write-manifest",
                "--write-manifest-only",
                "--action-plan",
                str(action_plan),
                "--active-rules",
                str(rules),
                "--campaign-id",
                "campaign-2",
                "--json",
            ]
        ),
        server_func=lambda **kwargs: None,
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["served"] is False
    assert saved["campaign_id"] == "campaign-2"
    assert saved["active_rules_hash"]
