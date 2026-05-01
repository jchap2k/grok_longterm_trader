import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_reconciliation import (
    build_paper_reconciliation_markdown,
    reconcile_paper_account,
)
from longterm.paper_reconciliation_cli import build_parser as build_reconcile_parser
from longterm.paper_reconciliation_cli import run_cli as run_reconcile_cli
from longterm.portfolio_state import PortfolioState
from longterm.research_campaign import summarize_research_campaign
from longterm.research_packet_enrichment import enrich_research_idea, enrich_research_ideas
from research.intake import create_research_packet_from_idea


def test_research_packet_enrichment_scores_and_fills_packet_context():
    idea = {
        "symbol": "MSFT",
        "company_name": "Microsoft",
        "idea_source": "sp500",
    }
    enriched = enrich_research_idea(
        idea,
        {
            "business_summary": "Cloud and software platform.",
            "revenue_growth_1y_pct": 15,
            "gross_margin_pct": 68,
            "debt_to_equity": 0.3,
            "valuation_label": "reasonable",
        },
        enrichment_source="fundamentals_cache",
    )
    packet = create_research_packet_from_idea(enriched)

    assert enriched["completeness_bucket"] == "ready"
    assert enriched["completeness_score"] >= 80
    assert enriched["missing_fields"] == []
    assert "fundamentals_cache" in " ".join(enriched["source_notes"])
    assert packet.is_minimally_complete_for_research() is True
    assert packet.business_summary == "Cloud and software platform."


def test_research_packet_enrichment_marks_thin_ideas_for_more_work():
    enriched = enrich_research_idea({"symbol": "XYZ"}, {}, enrichment_source="empty_cache")

    assert enriched["completeness_bucket"] == "needs_enrichment"
    assert "company_name" in enriched["missing_fields"]
    assert "idea_source" in enriched["missing_fields"]
    assert "research_context" in enriched["missing_fields"]


def test_enrich_research_ideas_uses_symbol_keyed_cache():
    ideas = [{"symbol": "msft", "company_name": "Microsoft", "idea_source": "manual"}]
    enriched = enrich_research_ideas(
        ideas,
        {"MSFT": {"business_summary": "Software platform."}},
        enrichment_source="unit_cache",
    )

    assert enriched[0]["symbol"] == "MSFT"
    assert enriched[0]["business_summary"] == "Software platform."


def test_research_campaign_summary_counts_statuses_and_next_batch():
    manifest = {
        "campaign_id": "campaign-1",
        "batches": [
            {"batch_id": "research-batch-001", "status": "completed", "idea_count": 2},
            {
                "batch_id": "research-batch-002",
                "status": "deferred",
                "idea_count": 3,
                "notes": "Needs enrichment.",
            },
            {
                "batch_id": "research-batch-003",
                "status": "pending",
                "idea_count": 4,
                "batch_path": "batches/research-batch-003.json",
            },
        ],
    }

    summary = summarize_research_campaign(manifest)

    assert summary["total_ideas"] == 9
    assert summary["status_counts"]["completed"] == 1
    assert summary["status_counts"]["deferred"] == 1
    assert summary["completion_pct"] == 33.33
    assert summary["next_batch"]["batch_id"] == "research-batch-003"
    assert summary["blocked_batches"][0]["batch_id"] == "research-batch-002"


def test_paper_reconciliation_compares_actual_to_action_plan_and_protected_symbols():
    actual = PortfolioState(
        cash=1000,
        protected_symbols=["FXAIX"],
        holdings=[
            {"symbol": "FXAIX", "market_value": 34000, "quantity": 100},
            {"symbol": "AAPL", "market_value": 2000, "quantity": 10},
            {"symbol": "TSLA", "market_value": 500, "quantity": 2},
        ],
    )
    plan = {
        "intents": [
            {
                "symbol": "MSFT",
                "intent_type": "BUY",
                "target_value": 3000,
                "allowed": True,
            },
            {
                "symbol": "AAPL",
                "intent_type": "REVIEW",
                "target_value": 1500,
                "allowed": True,
            },
        ]
    }

    report = reconcile_paper_account(
        actual,
        action_plan=plan,
        expected_cash=1200,
        protected_symbols=["FXAIX"],
    )

    assert report["mode"] == "dry_run_reconciliation"
    assert report["cash_delta"] == -200.0
    assert report["missing_target_symbols"] == ["MSFT"]
    assert report["extra_symbols"] == ["TSLA"]
    assert report["mismatched_holdings"][0]["symbol"] == "AAPL"
    assert report["protected_symbol_status"][0]["status"] == "present"
    assert report["order_submission_enabled"] is False


def test_paper_reconciliation_markdown_and_cli(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "cash": 1000,
                "protected_symbols": ["FXAIX"],
                "holdings": [{"symbol": "FXAIX", "market_value": 34000, "quantity": 100}],
            }
        ),
        encoding="utf-8",
    )
    plan_path.write_text(json.dumps({"intents": [{"symbol": "MSFT", "target_value": 3000}]}), encoding="utf-8")
    parser = build_reconcile_parser()
    args = parser.parse_args(
        [
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--expected-cash",
            "1000",
        ]
    )

    assert run_reconcile_cli(args) == 0
    output = capsys.readouterr().out
    direct = build_paper_reconciliation_markdown(
        reconcile_paper_account(PortfolioState.from_file(portfolio_path), action_plan={"intents": []})
    )

    assert "# Paper Account Reconciliation" in output
    assert "# Paper Account Reconciliation" in direct
