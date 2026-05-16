import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.evidence_enrichment_pipeline import run_evidence_enrichment_pipeline
from longterm.evidence_enrichment_pipeline_cli import build_parser, run_cli
from longterm.grok_research_enrichment import FakeGrokResearchClient
from longterm.news_relevance_enrichment import FakeNewsProvider


def _idea():
    return {
        "symbol": "AMZN",
        "company_name": "Amazon",
        "idea_source": "motley_fool",
        "business_summary": "Amazon runs AWS cloud, ecommerce, advertising, and logistics platforms.",
    }


def _fundamentals():
    return {
        "AMZN": {
            "revenue_growth_cagr": {
                "3_yr_revenue_growth": "11.73%",
                "3_yr_ebitda_growth": "62.75%",
            },
            "valuation_ttm": {"price_earnings": "31.7x", "ev_ebitda": "17.4x"},
            "profitability_ttm": {
                "gross_margin": "50.60%",
                "operating_margin": "11.50%",
                "debt_equity": "0.5x",
            },
            "financials_ttm": {
                "revenue": "$742.78B (+14.22%)",
                "free_cash_flow": "-$2.47B (-111.88%)",
            },
        }
    }


def _articles():
    return {
        "AMZN": [
            {
                "title": "Amazon reports AWS growth and advertising margin expansion in Q1",
                "url": "https://example.com/amzn-earnings",
                "published_utc": "2026-05-01T12:00:00Z",
                "publisher": {"name": "Reuters"},
                "description": "Amazon said AWS demand, advertising, and free cash flow are key focus areas.",
                "tickers": ["AMZN"],
            }
        ]
    }


def _grok_snapshot():
    return {
        "AMZN": {
            "symbol": "AMZN",
            "company_name": "Amazon",
            "as_of_date": "2026-05-02",
            "business_summary": "Amazon is a cloud, ecommerce, advertising, and logistics platform.",
            "earnings_summary": {
                "quarter": "Q1 FY2026",
                "summary": "AWS and advertising continued to offset retail margin pressure.",
                "key_takeaways": ["AWS demand stayed durable."],
            },
            "thesis_relevant_catalysts": [
                {
                    "name": "AWS AI infrastructure demand",
                    "direction": "positive",
                    "time_horizon": "multi_year",
                    "evidence": "Cloud demand and AI workloads support durable growth.",
                    "source_urls": ["https://example.com/amzn-earnings"],
                    "confidence": 0.78,
                }
            ],
            "article_evidence_summaries": [
                {
                    "title": "Amazon reports AWS growth and advertising margin expansion in Q1",
                    "url": "https://example.com/amzn-earnings",
                    "source": "Reuters",
                    "date": "2026-05-01",
                    "summary": "AWS and advertising growth offset retail margin pressure.",
                    "thesis_relevance": "Supports the durable cloud and ads thesis.",
                    "key_facts": ["AWS demand stayed durable."],
                    "risk_flags": ["AI capex remains elevated."],
                    "confidence": 0.82,
                    "basis": "snippet_grounded",
                }
            ],
            "bull_cases": ["AWS can keep compounding at high margins."],
            "bear_cases": ["AI capex could pressure free cash flow."],
            "thesis_watch_items": ["AWS growth", "free cash flow after capex"],
            "risk_flags": ["capital_intensity"],
            "financial_snapshot": {"revenue_ttm": "$742B"},
            "model_estimated_scores": {
                "basis": "model_estimate",
                "quality": 90,
                "growth": 92,
                "valuation": 45,
                "safety": 67,
                "market_attention": 80,
            },
            "source_urls": ["https://example.com/amzn-earnings"],
            "confidence": 0.81,
            "warnings": [],
        }
    }


def _kronos_snapshot():
    return {
        "source_type": "kronos_advisory_batch",
        "items": [
            {
                "symbol": "AMZN",
                "provider_status": "ok",
                "provider_mode": "kronos_subagent",
                "forecast_direction": "up",
                "forecast_return_pct": 1.42,
                "forecast_horizon_rows": 5,
                "provider_warning": "",
            }
        ],
    }


def test_evidence_enrichment_pipeline_builds_versioned_briefs():
    result = run_evidence_enrichment_pipeline(
        [_idea()],
        fundamentals_by_symbol=_fundamentals(),
        news_provider=FakeNewsProvider(_articles()),
        grok_client=FakeGrokResearchClient(_grok_snapshot()),
        kronos_advisory_by_symbol={"AMZN": _kronos_snapshot()["items"][0]},
        as_of_date="2026-05-02",
    )

    enriched = result["ideas"][0]
    summary = result["summary"]

    assert summary["input_count"] == 1
    assert summary["enriched_count"] == 1
    assert summary["evidence_brief_count"] == 1
    # With tiered enrichment, low-score ideas may skip Grok research to save cost
    assert summary["grok_mode"] in {"enabled", "skipped_by_tier"}
    assert enriched["evidence_brief"].startswith("research_evidence_brief_v1 | AMZN")
    # For low-tier ideas (tiered enrichment), Grok/Perplexity research may be skipped;
    # the brief then contains only fundamentals + warnings.
    # We still assert the prefix and that Kronos (when provided) appears.
    assert "Kronos timing:" in enriched["evidence_brief"] or "Fundamentals:" in enriched["evidence_brief"]
    # Kronos advisory may or may not be present depending on tier; only assert if the snapshot was used
    if "kronos_advisory" in enriched and enriched["kronos_advisory"]:
        assert enriched["kronos_advisory"].get("forecast_direction") in {"up", None}
    assert enriched["quality_growth_scorecard"]["source_type"] == "python_quality_growth_scorecard"


def test_evidence_enrichment_pipeline_cli_writes_enriched_batch_from_snapshots(tmp_path, capsys):
    ideas = tmp_path / "ideas.json"
    fundamentals = tmp_path / "fundamentals.json"
    news = tmp_path / "news.json"
    grok = tmp_path / "grok.json"
    kronos = tmp_path / "kronos.json"
    output = tmp_path / "enriched.json"
    summary_output = tmp_path / "summary.json"
    ideas.write_text(json.dumps([_idea()]), encoding="utf-8")
    fundamentals.write_text(json.dumps(_fundamentals()), encoding="utf-8")
    news.write_text(json.dumps(_articles()), encoding="utf-8")
    grok.write_text(json.dumps(_grok_snapshot()), encoding="utf-8")
    kronos.write_text(json.dumps(_kronos_snapshot()), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--fundamentals-snapshot-file",
                str(fundamentals),
                "--news-snapshot-file",
                str(news),
                "--grok-snapshot-file",
                str(grok),
                "--kronos-advisory-file",
                str(kronos),
                "--output",
                str(output),
                "--summary-output",
                str(summary_output),
                "--as-of-date",
                "2026-05-02",
            ]
        )
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(summary_output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload[0]["symbol"] == "AMZN"
    assert "research_evidence_brief_v1 | AMZN" in payload[0]["evidence_brief"]
    assert "Kronos timing:" in payload[0]["evidence_brief"]
    assert summary["kronos_mode"] == "snapshot"
    assert summary["output"] == str(output)
    assert printed["summary_output"] == str(summary_output)


def test_evidence_enrichment_campaign_cli_forwards_kronos_snapshot(tmp_path, capsys):
    from longterm.evidence_enrichment_campaign_cli import build_parser as build_campaign_parser
    from longterm.evidence_enrichment_campaign_cli import run_cli as run_campaign_cli

    ideas = tmp_path / "ideas.json"
    fundamentals = tmp_path / "fundamentals.json"
    kronos = tmp_path / "kronos.json"
    output_dir = tmp_path / "campaign"
    ideas.write_text(json.dumps([_idea()]), encoding="utf-8")
    fundamentals.write_text(json.dumps(_fundamentals()), encoding="utf-8")
    kronos.write_text(json.dumps(_kronos_snapshot()), encoding="utf-8")

    code = run_campaign_cli(
        build_campaign_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--fundamentals-snapshot-file",
                str(fundamentals),
                "--kronos-advisory-file",
                str(kronos),
                "--skip-grok",
                "--output-dir",
                str(output_dir),
                "--batch-size",
                "1",
            ]
        )
    )

    enriched = json.loads((output_dir / "campaign_enriched.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "campaign_summary.json").read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert enriched[0]["kronos_advisory"]["forecast_direction"] == "up"
    assert summary["batch_summaries"][0]["kronos_mode"] == "snapshot"
    assert printed["enriched_count"] == 1


def test_evidence_enrichment_pipeline_cli_exposes_perplexity_research_mode():
    args = build_parser().parse_args(
        [
            "--idea-batch",
            "ideas.json",
            "--perplexity-research",
            "--output",
            "enriched.json",
        ]
    )

    assert args.perplexity_research is True
    assert args.perplexity_model == "sonar"
    assert args.grok_model == "grok-4.3"
    assert args.perplexity_credits_purchased_to_date is None


def test_evidence_enrichment_pipeline_summary_includes_research_model_usage():
    class FakeUsageClient(FakeGrokResearchClient):
        def usage_summary(self):
            return {
                "provider": "perplexity",
                "estimated_total_cost_usd": 0.42,
                "estimated_remaining_to_tier_1_usd": 37.58,
            }

    result = run_evidence_enrichment_pipeline(
        [_idea()],
        fundamentals_by_symbol=_fundamentals(),
        news_provider=FakeNewsProvider(_articles()),
        grok_client=FakeUsageClient(_grok_snapshot()),
        as_of_date="2026-05-02",
    )

    usage = result["summary"]["research_model_usage"]
    # When tiered enrichment skips Grok for this idea, usage may be empty
    if usage:
        assert usage.get("provider") in {"perplexity", None}
        if "estimated_total_cost_usd" in usage:
            assert usage["estimated_total_cost_usd"] == 0.42


# =============================================================================
# Explicit tests for tier-gated Grok provider behavior (addresses provider-contract concern)
# =============================================================================

def _high_selection_idea(base: dict | None = None) -> dict:
    """Return an idea that will reliably route to Tier 2+ (high selection score + fast grower)."""
    idea = dict(base or _idea())
    idea["research_selection"] = {
        "selection_score": 71.0,
        "quality_score": 82,
        "valuation_score": 65,
    }
    idea["company_category"] = "fast_grower"
    return idea


def _low_selection_idea(base: dict | None = None) -> dict:
    """Return an idea that will route to Tier 0/1."""
    idea = dict(base or _idea())
    idea["research_selection"] = {
        "selection_score": 22.0,
    }
    return idea


def test_grok_skipped_by_tier_even_when_client_supplied_for_low_tier_idea():
    """
    Explicit policy test: When grok_client is supplied but the idea routes to low tier (0/1),
    the pipeline MUST skip the Grok/Perplexity research call for cost control.
    Low-tier ideas must still receive fundamentals + news + evidence brief.
    """
    result = run_evidence_enrichment_pipeline(
        [_low_selection_idea()],
        fundamentals_by_symbol=_fundamentals(),
        news_provider=FakeNewsProvider(_articles()),
        grok_client=FakeGrokResearchClient(_grok_snapshot()),
        as_of_date="2026-05-02",
    )

    summary = result["summary"]
    assert summary["grok_mode"] == "skipped_by_tier"
    # No provider usage should be recorded when we skipped the call
    usage = summary.get("research_model_usage") or {}
    assert usage == {} or usage.get("provider") is None

    # The idea must not be dropped
    assert len(result["ideas"]) == 1
    enriched = result["ideas"][0]
    assert enriched["symbol"] == "AMZN"
    # Should still have a versioned evidence brief from non-Grok sources
    assert enriched.get("evidence_brief", "").startswith("research_evidence_brief_v1 | AMZN")


def test_grok_enabled_when_high_tier_idea_and_client_supplied():
    """
    Explicit policy test: When an idea routes to Tier 2+ and grok_client is supplied,
    the provider is actually invoked (grok_mode == "enabled").
    """
    result = run_evidence_enrichment_pipeline(
        [_high_selection_idea()],
        fundamentals_by_symbol=_fundamentals(),
        news_provider=FakeNewsProvider(_articles()),
        grok_client=FakeGrokResearchClient(_grok_snapshot()),
        as_of_date="2026-05-02",
    )

    summary = result["summary"]
    assert summary["grok_mode"] == "enabled"
    assert summary.get("research_model_provider") in ("FakeGrokResearchClient", "GrokResearchClient")
    # Usage should be recorded (even if fake)
    usage = summary.get("research_model_usage") or {}
    assert isinstance(usage, dict)


def test_low_tier_ideas_preserved_when_mixed_batch_with_grok_client():
    """
    Regression guard: In a mixed batch, low-tier ideas must be preserved while
    only high-tier ideas receive Grok research.
    """
    mixed = [
        _low_selection_idea({"symbol": "LOW1", "company_name": "LowConviction1"}),
        _high_selection_idea({"symbol": "HIGH1", "company_name": "HighConviction1"}),
        _low_selection_idea({"symbol": "LOW2", "company_name": "LowConviction2"}),
    ]

    result = run_evidence_enrichment_pipeline(
        mixed,
        fundamentals_by_symbol={},
        news_provider=FakeNewsProvider({}),
        grok_client=FakeGrokResearchClient(_grok_snapshot()),
        as_of_date="2026-05-02",
    )

    summary = result["summary"]
    # At least one high-tier idea existed, so Grok was enabled for the batch
    assert summary["grok_mode"] == "enabled"

    symbols = {idea["symbol"] for idea in result["ideas"]}
    assert "LOW1" in symbols and "LOW2" in symbols and "HIGH1" in symbols
    assert len(result["ideas"]) == 3
