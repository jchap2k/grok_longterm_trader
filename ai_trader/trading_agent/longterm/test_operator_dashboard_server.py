import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.operator_dashboard_server import (
    build_api_usage_from_manifest,
    build_dashboard_manifest,
    build_dashboard_pages_from_manifest,
    build_pipeline_health_from_manifest,
    build_portfolio_summary_from_manifest,
    build_scheduler_config_validation_from_manifest,
    find_latest_dashboard_manifest,
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
        pipeline_summary=tmp_path / "pipeline_summary.json",
        scheduler_policy=tmp_path / "scheduler_policy.json",
        committee_preset_policy=tmp_path / "committee_preset_policy.json",
        active_rules_path=rules,
        decision_journal_path=tmp_path / "journal.db",
        campaign_id="campaign-1",
    )

    assert manifest["schema_version"] == 1
    assert manifest["campaign_id"] == "campaign-1"
    assert manifest["decision_journal_path"].endswith("journal.db")
    assert manifest["pipeline_summary"].endswith("pipeline_summary.json")
    assert manifest["scheduler_policy"].endswith("scheduler_policy.json")
    assert manifest["committee_preset_policy"].endswith("committee_preset_policy.json")
    assert manifest["scheduler_config_validation"] == ""
    assert manifest["api_usage"] == ""
    assert manifest["active_rules_hash"]
    assert manifest["generated_at"].endswith("Z")


def test_dashboard_server_exposes_api_usage_from_pipeline_summary(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    portfolio.write_text(json.dumps({"holdings": []}), encoding="utf-8")
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "research_model_usage": {
                    "provider": "perplexity",
                    "model": "sonar",
                    "request_count": 4,
                    "prompt_tokens": 12000,
                    "completion_tokens": 4500,
                    "estimated_total_cost_usd": 0.047,
                    "credits_purchased_to_date_usd": 12.0,
                    "tier_1_credit_target_usd": 50.0,
                    "estimated_remaining_to_tier_1_usd": 37.95,
                    "console_check_required": True,
                },
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            build_dashboard_manifest(
                action_plan=action_plan,
                portfolio_state=portfolio,
                pipeline_summary=pipeline_summary,
            )
        ),
        encoding="utf-8",
    )

    usage = build_api_usage_from_manifest(load_dashboard_manifest(manifest_path))
    status, content_type, body = resolve_dashboard_request(manifest_path, "/api/api-usage.json")
    index_status, _, index_body = resolve_dashboard_request(manifest_path, "/")
    api = json.loads(body.decode("utf-8"))
    html = index_body.decode("utf-8")

    assert usage["status"] == "available"
    assert usage["totals"]["request_count"] == 4
    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert api["providers"][0]["provider"] == "perplexity"
    assert api["providers"][0]["model"] == "sonar"
    assert api["totals"]["estimated_total_cost_usd"] == 0.047
    assert api["tier_tracking"]["estimated_remaining_to_tier_1_usd"] == 37.95
    assert index_status == 200
    assert "API Usage" in html
    assert "Perplexity" in html
    assert "Tier 1 Remaining" in html


def test_dashboard_server_exposes_scheduler_policy_from_manifest(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    scheduler_policy = tmp_path / "scheduler_policy.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    portfolio.write_text(json.dumps({"holdings": []}), encoding="utf-8")
    scheduler_policy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "pipeline_scheduler_policy",
                "recommended_mode": "panic_regime_reassessment",
                "urgency": "high",
                "reasons": ["vix_panic_threshold"],
                "warnings": ["active_rules_changed"],
                "affected_symbols": ["ADBE", "MSFT"],
                "next_safe_action": "rerun_market_regime_and_next_actions_no_submit",
                "order_submission_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            build_dashboard_manifest(
                action_plan=action_plan,
                portfolio_state=portfolio,
                scheduler_policy=scheduler_policy,
            )
        ),
        encoding="utf-8",
    )

    status, content_type, body = resolve_dashboard_request(manifest_path, "/api/scheduler-policy.json")
    summary_status, _, summary_body = resolve_dashboard_request(manifest_path, "/api/summary.json")
    index_status, _, index_body = resolve_dashboard_request(manifest_path, "/")
    payload = json.loads(body.decode("utf-8"))
    summary = json.loads(summary_body.decode("utf-8"))
    html = index_body.decode("utf-8")

    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert payload["recommended_mode"] == "panic_regime_reassessment"
    assert payload["order_submission_enabled"] is False
    assert summary_status == 200
    assert summary["scheduler_policy"]["recommended_mode"] == "panic_regime_reassessment"
    assert index_status == 200
    assert "Scheduler Policy" in html
    assert "Panic Regime Reassessment" in html
    assert "High" in html
    assert "ADBE, MSFT" in html
    assert "Advisory only" in html


def test_dashboard_server_exposes_pipeline_health_from_manifest(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    selected = tmp_path / "selected.json"
    preview = tmp_path / "paper_preview.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    portfolio.write_text(json.dumps({"holdings": []}), encoding="utf-8")
    selected.write_text(json.dumps([{"symbol": "ADBE"}]), encoding="utf-8")
    preview.write_text(json.dumps({"ready_count": 1}), encoding="utf-8")
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "order_submission_enabled": False,
                "artifact_paths": {
                    "research_queue_selected": str(selected),
                    "paper_preview": str(preview),
                },
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            build_dashboard_manifest(
                action_plan=action_plan,
                portfolio_state=portfolio,
                pipeline_summary=pipeline_summary,
            )
        ),
        encoding="utf-8",
    )

    summary = build_pipeline_health_from_manifest(load_dashboard_manifest(manifest_path))
    status, content_type, body = resolve_dashboard_request(manifest_path, "/api/pipeline-health.json")
    api = json.loads(body.decode("utf-8"))

    assert summary["status"] == "ready"
    assert summary["rollup"]["research_selection"]["selected_symbols"] == ["ADBE"]
    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert api["status"] == "ready"
    assert api["order_submission_enabled"] is False


def test_dashboard_server_exposes_scheduler_resource_controls_from_manifest(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    selected = tmp_path / "selected.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    scheduler_summary = tmp_path / "pipeline_scheduler_summary.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    portfolio.write_text(json.dumps({"holdings": []}), encoding="utf-8")
    selected.write_text(json.dumps([{"symbol": "MSFT"}]), encoding="utf-8")
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "order_submission_enabled": False,
                "artifact_paths": {"research_queue_selected": str(selected)},
            }
        ),
        encoding="utf-8",
    )
    scheduler_summary.write_text(
        json.dumps(
            {
                "status": "planned",
                "runs": [
                    {
                        "status": "planned",
                        "resource_controls": {
                            "provider_mode": "perplexity",
                            "paid_provider_enabled": True,
                            "research_max_pass_count": 25,
                            "generated_committee_batches": True,
                            "generated_committee_max_batches": 1,
                            "bounded": True,
                            "estimated_cost_usd": "unknown",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            build_dashboard_manifest(
                action_plan=action_plan,
                portfolio_state=portfolio,
                pipeline_summary=pipeline_summary,
                pipeline_scheduler_summary=scheduler_summary,
            )
        ),
        encoding="utf-8",
    )

    status, content_type, body = resolve_dashboard_request(manifest_path, "/api/pipeline-health.json")
    api = json.loads(body.decode("utf-8"))

    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert api["resource_controls"]["provider_mode"] == "perplexity"
    assert api["resource_controls"]["generated_committee_max_batches"] == 1
    assert api["resource_controls"]["bounded"] is True


def test_dashboard_server_exposes_scheduler_config_validation_from_manifest(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    validation = tmp_path / "scheduler_profile_validation.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    portfolio.write_text(json.dumps({"holdings": []}), encoding="utf-8")
    validation.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "pipeline_scheduler_config_validation",
                "status": "ready",
                "config_file": str(tmp_path / "ongoing_no_submit_scheduler.local.json"),
                "preset": "ongoing-no-submit",
                "order_submission_enabled": False,
                "resource_controls": {
                    "provider_mode": "perplexity",
                    "research_max_pass_count": 25,
                    "generated_committee_max_batches": 1,
                    "bounded": True,
                },
                "next_safe_action": "run_scheduler_profile_when_operator_window_is_approved",
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            build_dashboard_manifest(
                action_plan=action_plan,
                portfolio_state=portfolio,
                scheduler_config_validation=validation,
            )
        ),
        encoding="utf-8",
    )

    summary = build_scheduler_config_validation_from_manifest(load_dashboard_manifest(manifest_path))
    status, content_type, body = resolve_dashboard_request(manifest_path, "/api/scheduler-config-validation.json")
    summary_status, _, summary_body = resolve_dashboard_request(manifest_path, "/api/summary.json")
    index_status, _, index_body = resolve_dashboard_request(manifest_path, "/")
    api = json.loads(body.decode("utf-8"))
    dashboard_summary = json.loads(summary_body.decode("utf-8"))
    html = index_body.decode("utf-8")

    assert summary["status"] == "ready"
    assert summary["resource_controls"]["bounded"] is True
    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert api["mode"] == "pipeline_scheduler_config_validation"
    assert api["config_file"].endswith("ongoing_no_submit_scheduler.local.json")
    assert api["order_submission_enabled"] is False
    assert summary_status == 200
    assert dashboard_summary["scheduler_config_validation"]["status"] == "ready"
    assert index_status == 200
    assert "Scheduler Profile" in html
    assert "Ready" in html
    assert "Perplexity" in html
    assert "ongoing_no_submit_scheduler.local.json" in html


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


def test_canonical_dashboard_manifest_uses_newest_sibling_manifest_for_live_refresh(tmp_path):
    old_action_plan = tmp_path / "old_action_plan.json"
    old_portfolio = tmp_path / "old_portfolio.json"
    new_action_plan = tmp_path / "new_action_plan.json"
    new_portfolio = tmp_path / "new_portfolio.json"
    canonical_manifest = tmp_path / "dashboard_manifest.json"
    after_submit_manifest = tmp_path / "dashboard_after_submit_manifest.json"
    old_action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    old_portfolio.write_text(json.dumps({"protected_symbols": ["FXAIX"], "holdings": []}), encoding="utf-8")
    new_action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    new_portfolio.write_text(
        json.dumps(
            {
                "protected_symbols": ["FXAIX"],
                "holdings": [{"symbol": "ADBE", "quantity": 3, "market_value": 755}],
            }
        ),
        encoding="utf-8",
    )
    old_manifest = build_dashboard_manifest(action_plan=old_action_plan, portfolio_state=old_portfolio)
    new_manifest = build_dashboard_manifest(action_plan=new_action_plan, portfolio_state=new_portfolio)
    old_manifest["generated_at"] = "2026-05-04T14:00:00Z"
    new_manifest["generated_at"] = "2026-05-04T14:17:00Z"
    canonical_manifest.write_text(json.dumps(old_manifest), encoding="utf-8")
    after_submit_manifest.write_text(json.dumps(new_manifest), encoding="utf-8")

    status, _, body = resolve_dashboard_request(canonical_manifest, "/")

    assert status == 200
    html = body.decode("utf-8")
    assert "ADBE" in html
    assert "No current portfolio holdings were supplied" not in html


def test_auto_manifest_root_uses_newest_recursive_manifest(tmp_path):
    old_dir = tmp_path / "run_1"
    new_dir = tmp_path / "latest_operator_surface"
    old_dir.mkdir()
    new_dir.mkdir()
    old_action_plan = old_dir / "action_plan.json"
    old_portfolio = old_dir / "portfolio.json"
    new_action_plan = new_dir / "action_plan.json"
    new_portfolio = new_dir / "portfolio.json"
    old_manifest = old_dir / "dashboard_manifest.json"
    new_manifest = new_dir / "dashboard_manifest.json"
    old_action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    old_portfolio.write_text(json.dumps({"protected_symbols": ["FXAIX"], "holdings": []}), encoding="utf-8")
    new_action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    new_portfolio.write_text(
        json.dumps({"holdings": [{"symbol": "CME", "quantity": 1, "market_value": 300}]}),
        encoding="utf-8",
    )
    old_payload = build_dashboard_manifest(action_plan=old_action_plan, portfolio_state=old_portfolio)
    new_payload = build_dashboard_manifest(action_plan=new_action_plan, portfolio_state=new_portfolio)
    old_payload["generated_at"] = "2026-05-04T14:00:00Z"
    new_payload["generated_at"] = "2026-05-04T15:00:00Z"
    old_manifest.write_text(json.dumps(old_payload), encoding="utf-8")
    new_manifest.write_text(json.dumps(new_payload), encoding="utf-8")

    latest = find_latest_dashboard_manifest(tmp_path)
    status, _, body = resolve_dashboard_request("", "/", auto_manifest_root=tmp_path)

    assert latest == new_manifest.resolve()
    assert status == 200
    html = body.decode("utf-8")
    assert "CME" in html
    assert "No current portfolio holdings were supplied" not in html


def test_portfolio_api_returns_current_values_and_gain_totals(tmp_path):
    action_plan = tmp_path / "action_plan.json"
    portfolio = tmp_path / "portfolio.json"
    manifest_path = tmp_path / "dashboard_manifest.json"
    action_plan.write_text(json.dumps({"intents": []}), encoding="utf-8")
    portfolio.write_text(
        json.dumps(
            {
                "cash": 2500,
                "protected_symbols": ["FXAIX"],
                "holdings": [
                    {
                        "symbol": "MSFT",
                        "quantity": 2,
                        "avg_entry_price": 300,
                        "current_price": 350,
                        "market_value": 700,
                    },
                    {
                        "symbol": "FXAIX",
                        "quantity": 10,
                        "original_purchase_total_cost": 1000,
                        "market_value": 1200,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(build_dashboard_manifest(action_plan=action_plan, portfolio_state=portfolio)),
        encoding="utf-8",
    )

    summary = build_portfolio_summary_from_manifest(load_dashboard_manifest(manifest_path))
    status, content_type, body = resolve_dashboard_request(manifest_path, "/api/portfolio.json")
    api = json.loads(body.decode("utf-8"))

    assert summary["holding_count"] == 2
    assert summary["totals"]["original_purchase_total_cost"] == 1600
    assert summary["totals"]["current_total_value"] == 1900
    assert summary["totals"]["gain_amount"] == 300
    assert round(summary["totals"]["gain_percent"], 2) == 18.75
    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert api["holdings"][0]["symbol"] == "FXAIX"
    assert api["holdings"][1]["symbol"] == "MSFT"
    assert api["totals"]["cash"] == 2500
    assert api["holdings"][1]["gain_percent"] == 16.666666666666664


def test_dashboard_server_cli_can_write_manifest_without_serving(tmp_path, capsys):
    rules = tmp_path / "active_rules.txt"
    action_plan = tmp_path / "action_plan.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    committee_preset_policy = tmp_path / "committee_preset_policy.json"
    scheduler_config_validation = tmp_path / "scheduler_profile_validation.json"
    manifest = tmp_path / "dashboard_manifest.json"
    rules.write_text("rules", encoding="utf-8")
    action_plan.write_text("{}", encoding="utf-8")
    pipeline_summary.write_text(json.dumps({"artifact_paths": {}}), encoding="utf-8")
    committee_preset_policy.write_text(json.dumps({"recommended_preset": "decision_4"}), encoding="utf-8")
    scheduler_config_validation.write_text(json.dumps({"status": "ready"}), encoding="utf-8")

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
                "--pipeline-summary",
                str(pipeline_summary),
                "--committee-preset-policy",
                str(committee_preset_policy),
                "--scheduler-config-validation",
                str(scheduler_config_validation),
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
    assert saved["pipeline_summary"] == str(pipeline_summary)
    assert saved["committee_preset_policy"] == str(committee_preset_policy)
    assert saved["scheduler_config_validation"] == str(scheduler_config_validation)
    assert saved["active_rules_hash"]
