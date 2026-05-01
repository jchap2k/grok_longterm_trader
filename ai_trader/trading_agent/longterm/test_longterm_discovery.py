import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.discovery import DiscoveryCandidate, DiscoveryEngine
from longterm.discovery_cli import build_parser, run_cli


def test_discovery_merges_duplicate_symbols_and_preserves_provenance():
    result = DiscoveryEngine().build_queue(
        [
            {
                "symbol": "nvda",
                "company_name": "Nvidia",
                "source": "manual_watchlist",
                "revenue_growth_1y_pct": 80,
                "earnings_growth_1y_pct": 100,
                "return_on_capital_pct": 35,
                "gross_margin_pct": 70,
                "market_cap": 2_000_000_000_000,
                "category_leader": True,
            },
            {
                "symbol": "NVDA",
                "source": "motley_fool_new_recommendations",
                "source_rank": 2,
                "source_score": 95,
                "notes": ["Premium source candidate."],
            },
        ]
    )

    top = result.research_queue[0]
    assert top.symbol == "NVDA"
    assert top.company_name == "Nvidia"
    assert top.discovery_id.startswith("DISC-NVDA-")
    assert top.source == "manual_watchlist+motley_fool_new_recommendations"
    assert top.source_metadata["sources"] == ["manual_watchlist", "motley_fool_new_recommendations"]
    assert "Premium source candidate." in top.notes


def test_discovery_separates_research_watchlist_and_rejected_candidates():
    result = DiscoveryEngine().build_queue(
        [
            {
                "symbol": "CRWD",
                "company_name": "CrowdStrike",
                "source": "motley_fool_quant_rankings",
                "source_rank": 3,
                "revenue_growth_1y_pct": 32,
                "earnings_growth_1y_pct": 25,
                "return_on_capital_pct": 18,
                "gross_margin_pct": 74,
                "market_cap": 80_000_000_000,
                "price_trend_6m_pct": 12,
                "category_leader": True,
            },
            {
                "symbol": "MID",
                "source": "screen_growth",
                "revenue_growth_1y_pct": 10,
                "market_cap": 4_000_000_000,
                "price_trend_6m_pct": 0,
            },
            {
                "symbol": "PENY",
                "source": "screen_growth",
                "market_cap": 150_000_000,
                "revenue_growth_1y_pct": -30,
                "debt_to_equity": 5,
                "price_trend_6m_pct": -70,
            },
        ],
        research_limit=5,
    )

    assert [candidate.symbol for candidate in result.research_queue] == ["CRWD"]
    assert [candidate.symbol for candidate in result.watchlist] == ["MID"]
    assert [candidate.symbol for candidate in result.rejected] == ["PENY"]
    assert "tiny market cap" in result.rejected[0].decision_reason.lower()


def test_discovery_to_research_ideas_maps_to_research_packet_fields():
    result = DiscoveryEngine().build_queue(
        [
            {
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "source": "quality_growth_screen",
                "revenue_growth_1y_pct": 16,
                "earnings_growth_1y_pct": 20,
                "return_on_capital_pct": 28,
                "gross_margin_pct": 68,
                "market_cap": 3_000_000_000_000,
                "valuation_label": "fair",
                "category_leader": True,
            }
        ]
    )

    ideas = DiscoveryEngine.to_research_ideas(result.research_queue)

    assert ideas == [
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "idea_source": "discovery_quality_growth_screen",
            "source_notes": [
                f"Discovery ID: {result.research_queue[0].discovery_id}.",
                "Discovery source(s): quality_growth_screen.",
                f"Discovery score: {result.research_queue[0].discovery_score:.1f}.",
                "Discovery decision: research_ready.",
            ],
            "business_summary": "Discovery candidate from quality_growth_screen; requires independent research.",
            "thesis_summary": f"Potential quality-growth candidate; discovery score {result.research_queue[0].discovery_score:.1f}.",
            "primary_growth_driver": "Requires research.",
            "industry_context": "Requires research.",
            "balance_sheet_assessment": "Requires research.",
        }
    ]


def test_discovery_module_stays_upstream_of_portfolio_and_benchmark_logic():
    source = Path(__file__).with_name("discovery.py").read_text(encoding="utf-8")

    assert "PortfolioState" not in source
    assert "PortfolioProfile" not in source
    assert "BenchmarkGuard" not in source
    assert "account_action_plan" not in source


def test_discovery_candidate_can_be_created_directly_for_manual_watchlist():
    candidate = DiscoveryCandidate(symbol="aapl", source="manual_watchlist", existing_watchlist=True)

    assert candidate.symbol == "AAPL"
    assert candidate.source_metadata["sources"] == ["manual_watchlist"]


def test_discovery_cli_outputs_buckets_and_research_idea_batch(tmp_path, capsys):
    candidates_path = tmp_path / "candidates.json"
    ideas_path = tmp_path / "research_ideas.json"
    candidates_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "MSFT",
                    "company_name": "Microsoft",
                    "source": "sp500",
                    "revenue_growth_1y_pct": 16,
                    "earnings_growth_1y_pct": 20,
                    "return_on_capital_pct": 28,
                    "gross_margin_pct": 68,
                    "market_cap": 3_000_000_000_000,
                    "category_leader": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--candidates",
            str(candidates_path),
            "--research-ideas-output",
            str(ideas_path),
        ]
    )

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    ideas = json.loads(ideas_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["research_queue"][0]["symbol"] == "MSFT"
    assert payload["research_queue"][0]["decision"] == "research_ready"
    assert ideas[0]["symbol"] == "MSFT"
    assert ideas[0]["idea_source"] == "discovery_sp500"
