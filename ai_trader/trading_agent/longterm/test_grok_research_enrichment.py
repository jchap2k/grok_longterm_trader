import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.grok_research_enrichment import (
    FakeGrokResearchClient,
    build_grok_research_messages,
    enrich_idea_with_grok_research,
    enrich_ideas_with_grok_research,
    normalize_grok_research_result,
)
from longterm.grok_research_enrichment_cli import build_parser, run_cli
from research.intake import create_research_packet_from_idea


def _raw_enrichment(symbol: str = "AMZN") -> dict:
    return {
        "symbol": symbol,
        "company_name": "Amazon",
        "as_of_date": "2026-05-02",
        "business_summary": "Amazon is a cloud, ecommerce, advertising, and logistics platform.",
        "earnings_summary": {
            "quarter": "Q1 FY2026",
            "summary": "AWS and advertising continued to offset retail margin pressure.",
            "key_takeaways": ["AWS growth stayed durable.", "Capex remains a valuation watch item."],
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
        "bull_cases": ["AWS can keep compounding at high margins."],
        "bear_cases": ["AI capex could pressure free cash flow."],
        "thesis_watch_items": ["AWS growth", "free cash flow after capex"],
        "risk_flags": ["capital_intensity"],
        "financial_snapshot": {
            "revenue_ttm": "$742B",
            "revenue_growth_yoy": "14%",
            "operating_margin_ttm": "11.5%",
            "free_cash_flow_ttm": "-$2.5B",
        },
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


def test_normalize_grok_research_result_requires_source_urls_and_model_estimate_scores():
    normalized = normalize_grok_research_result(
        _raw_enrichment(),
        idea={"symbol": "amzn", "company_name": "Amazon"},
        as_of_date="2026-05-02",
    )

    assert normalized["symbol"] == "AMZN"
    assert normalized["source_type"] == "grok_research_enrichment"
    assert normalized["model_estimated_scores"]["basis"] == "model_estimate"
    assert normalized["source_urls"] == ["https://example.com/amzn-earnings"]
    assert normalized["warnings"] == []


def test_unsourced_grok_research_result_is_warned_not_silently_trusted():
    raw = _raw_enrichment()
    raw["source_urls"] = []
    raw["thesis_relevant_catalysts"][0]["source_urls"] = []

    normalized = normalize_grok_research_result(
        raw,
        idea={"symbol": "AMZN", "company_name": "Amazon"},
        as_of_date="2026-05-02",
    )

    assert "missing_source_urls" in normalized["warnings"]
    assert normalized["confidence"] <= 0.5


def test_enrich_idea_with_grok_research_fills_packet_context_and_keeps_finnhub_facts_visible():
    client = FakeGrokResearchClient({"AMZN": _raw_enrichment()})
    idea = {"symbol": "AMZN", "company_name": "Amazon", "idea_source": "sp500"}
    free_facts = {
        "market_cap": "$2.8T",
        "finnhub_profile": {"finnhubIndustry": "Internet Retail"},
    }

    enriched = enrich_idea_with_grok_research(
        idea,
        client=client,
        free_facts=free_facts,
        as_of_date="2026-05-02",
    )
    packet = create_research_packet_from_idea(enriched)

    assert packet.is_minimally_complete_for_research() is True
    assert packet.business_summary.startswith("Amazon is a cloud")
    assert "AWS AI infrastructure demand" in packet.thesis_summary
    assert any("Finnhub/free facts supplied" in note for note in packet.source_notes)
    assert any("Grok research enrichment" in note for note in packet.source_notes)
    assert enriched["grok_research_enrichment"]["model_estimated_scores"]["basis"] == "model_estimate"


def test_enrich_ideas_with_grok_research_uses_symbol_keyed_free_facts():
    client = FakeGrokResearchClient({"AMZN": _raw_enrichment()})

    enriched = enrich_ideas_with_grok_research(
        [{"symbol": "amzn", "company_name": "Amazon", "idea_source": "manual"}],
        client=client,
        free_facts_by_symbol={"AMZN": {"revenue": "$742B"}},
        as_of_date="2026-05-02",
    )

    assert enriched[0]["symbol"] == "AMZN"
    assert enriched[0]["grok_research_enrichment"]["free_facts"]["revenue"] == "$742B"


def test_prompt_asks_for_source_backed_catalysts_not_motley_fool_impersonation():
    messages = build_grok_research_messages(
        {"symbol": "AMZN", "company_name": "Amazon"},
        free_facts={"market_cap": "$2.8T"},
        as_of_date="2026-05-02",
    )
    joined = "\n".join(message["content"] for message in messages)

    assert "source-backed" in joined
    assert "Do not claim to be Motley Fool" in joined
    assert "Finnhub/free factual inputs" in joined
    assert "model_estimate" in joined


def test_grok_research_enrichment_cli_can_normalize_offline_snapshots(tmp_path, capsys):
    idea_path = tmp_path / "ideas.json"
    facts_path = tmp_path / "facts.json"
    snapshot_path = tmp_path / "snapshots.json"
    output_path = tmp_path / "enriched.json"
    idea_path.write_text(
        json.dumps([{"symbol": "AMZN", "company_name": "Amazon", "idea_source": "manual"}]),
        encoding="utf-8",
    )
    facts_path.write_text(json.dumps({"AMZN": {"market_cap": "$2.8T"}}), encoding="utf-8")
    snapshot_path.write_text(json.dumps({"AMZN": _raw_enrichment()}), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(idea_path),
                "--facts-file",
                str(facts_path),
                "--snapshot-file",
                str(snapshot_path),
                "--output",
                str(output_path),
                "--as-of-date",
                "2026-05-02",
            ]
        )
    )

    assert code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["grok_research_enrichment"]["symbol"] == "AMZN"
    assert summary["enriched_count"] == 1
    assert summary["mode"] == "snapshot_file"
