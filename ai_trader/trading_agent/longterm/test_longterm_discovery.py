import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.discovery import DiscoveryCandidate, DiscoveryEngine
from longterm.discovery_cli import build_parser, run_cli
from longterm.discovery_enrichment import apply_discovery_enrichment, load_discovery_enrichment_file
from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_text


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


def test_broad_listing_sources_become_enrichment_watchlist_not_rejected():
    result = DiscoveryEngine().build_queue(
        [{"symbol": "AAPL", "company_name": "Apple", "source": "nasdaq_listed"}],
        research_limit=5,
    )

    assert result.research_queue == []
    assert [candidate.symbol for candidate in result.watchlist] == ["AAPL"]
    assert result.watchlist[0].decision_reason == "Interesting but not strong enough for immediate research."
    assert result.rejected == []


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


def test_discovery_to_research_ideas_carries_enrichment_metrics_when_enriched():
    result = DiscoveryEngine().build_queue(
        [
            {
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "source": "sp500",
                "notes": ["Enriched from fundamentals_cache."],
                "revenue_growth_1y_pct": 16,
                "earnings_growth_1y_pct": 20,
                "return_on_capital_pct": 28,
                "gross_margin_pct": 68,
                "market_cap": 3_000_000_000_000,
                "category_leader": True,
            }
        ]
    )

    ideas = DiscoveryEngine.to_research_ideas(result.research_queue)

    assert "Enriched from fundamentals_cache." in ideas[0]["source_notes"]
    assert "Discovery metrics: market cap 3000000000000; revenue growth 16%; earnings growth 20%; return on capital 28%; gross margin 68%." in ideas[0]["source_notes"]


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
    watchlist_ideas_path = tmp_path / "watchlist_ideas.json"
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
                },
                {
                    "symbol": "AAPL",
                    "company_name": "Apple",
                    "source": "nasdaq_listed",
                },
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
            "--watchlist-ideas-output",
            str(watchlist_ideas_path),
            "--watchlist-limit",
            "1",
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
    watchlist_ideas = json.loads(watchlist_ideas_path.read_text(encoding="utf-8"))
    assert watchlist_ideas[0]["symbol"] == "AAPL"
    assert watchlist_ideas[0]["idea_source"] == "discovery_nasdaq_listed"


def test_discovery_cli_loads_local_source_file(tmp_path, capsys):
    source_path = tmp_path / "sp500.csv"
    source_path.write_text(
        "Symbol,Security,GICS Sector\nMSFT,Microsoft,Information Technology\n",
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--source-file",
            str(source_path),
            "--source",
            "sp500",
        ]
    )

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["watchlist"][0]["symbol"] == "MSFT"
    assert payload["watchlist"][0]["source"] == "sp500"


def test_load_discovery_enrichment_json_normalizes_metric_fields(tmp_path):
    path = tmp_path / "fundamentals.json"
    path.write_text(
        json.dumps(
            {
                "MSFT": {
                    "marketCap": "3000000000000",
                    "revenueGrowth": "16.5",
                    "earningsGrowth": "20",
                    "grossMargin": "68",
                    "returnOnCapital": "28",
                    "debtToEquity": "0.4",
                    "priceTrend6m": "12",
                    "categoryLeader": True,
                    "valuation": "fair",
                }
            }
        ),
        encoding="utf-8",
    )

    rows = load_discovery_enrichment_file(path)

    assert rows == {
        "MSFT": {
            "market_cap": 3_000_000_000_000.0,
            "revenue_growth_1y_pct": 16.5,
            "earnings_growth_1y_pct": 20.0,
            "gross_margin_pct": 68.0,
            "return_on_capital_pct": 28.0,
            "debt_to_equity": 0.4,
            "price_trend_6m_pct": 12.0,
            "category_leader": True,
            "valuation_label": "fair",
        }
    }


def test_apply_discovery_enrichment_merges_by_symbol_without_changing_source():
    candidates = [
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "source": "sp500",
            "notes": ["GICS Sector: Information Technology."],
        },
        {"symbol": "BRK.B", "company_name": "Berkshire Hathaway", "source": "sp500"},
    ]
    enrichment = {
        "msft": {
            "market_cap": 3_000_000_000_000,
            "revenue_growth_1y_pct": 16,
            "category_leader": True,
        }
    }

    enriched = apply_discovery_enrichment(candidates, enrichment, source="fundamentals_cache")

    assert enriched[0]["source"] == "sp500"
    assert enriched[0]["market_cap"] == 3_000_000_000_000.0
    assert enriched[0]["revenue_growth_1y_pct"] == 16.0
    assert enriched[0]["category_leader"] is True
    assert enriched[0]["notes"] == [
        "GICS Sector: Information Technology.",
        "Enriched from fundamentals_cache.",
    ]
    assert "market_cap" not in enriched[1]


def test_discovery_cli_applies_enrichment_file_before_scoring(tmp_path, capsys):
    source_path = tmp_path / "sp500.csv"
    enrichment_path = tmp_path / "fundamentals.json"
    source_path.write_text(
        "Symbol,Security,GICS Sector\nMSFT,Microsoft,Information Technology\n",
        encoding="utf-8",
    )
    enrichment_path.write_text(
        json.dumps(
            {
                "MSFT": {
                    "market_cap": 3_000_000_000_000,
                    "revenue_growth_1y_pct": 16,
                    "earnings_growth_1y_pct": 20,
                    "return_on_capital_pct": 28,
                    "gross_margin_pct": 68,
                    "price_trend_6m_pct": 12,
                    "category_leader": True,
                }
            }
        ),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--source-file",
            str(source_path),
            "--source",
            "sp500",
            "--enrichment-file",
            str(enrichment_path),
            "--enrichment-source",
            "fundamentals_cache",
        ]
    )

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["research_queue"][0]["symbol"] == "MSFT"
    assert "Enriched from fundamentals_cache." in payload["research_queue"][0]["notes"]


def test_load_sp500_style_csv_candidates(tmp_path):
    path = tmp_path / "sp500.csv"
    path.write_text(
        "Symbol,Security,GICS Sector\nMSFT,Microsoft,Information Technology\nBRK.B,Berkshire Hathaway,Financials\n",
        encoding="utf-8",
    )

    candidates = load_candidate_source_file(path, source="sp500")

    assert candidates == [
        {
            "symbol": "MSFT",
            "company_name": "Microsoft",
            "source": "sp500",
            "notes": ["GICS Sector: Information Technology."],
        },
        {
            "symbol": "BRK.B",
            "company_name": "Berkshire Hathaway",
            "source": "sp500",
            "notes": ["GICS Sector: Financials."],
        },
    ]


def test_load_etf_holdings_csv_candidates_with_weight_metadata(tmp_path):
    path = tmp_path / "qqq_holdings.csv"
    path.write_text(
        "Ticker,Name,Weight\nAAPL,Apple Inc.,8.9\nNVDA,NVIDIA Corp.,7.2\n",
        encoding="utf-8",
    )

    candidates = load_candidate_source_file(path, source="qqq")

    assert candidates[0]["symbol"] == "AAPL"
    assert candidates[0]["company_name"] == "Apple Inc."
    assert candidates[0]["source"] == "qqq"
    assert candidates[0]["source_score"] == 8.9
    assert "ETF/index weight: 8.9%." in candidates[0]["notes"]


def test_load_nasdaq_trader_pipe_listing_candidates(tmp_path):
    path = tmp_path / "nasdaqlisted.txt"
    path.write_text(
        "Symbol|Security Name|Market Category|ETF|Test Issue|Financial Status|Round Lot Size|File Creation Time\n"
        "MSFT|Microsoft Corporation|Q|N|N|N|100|20260430\n"
        "QQQ|Invesco QQQ Trust|G|Y|N|N|100|20260430\n"
        "TEST|Test Corp|Q|N|Y|N|100|20260430\n",
        encoding="utf-8",
    )

    candidates = load_candidate_source_file(path, source="nasdaq_listed")

    assert candidates == [
        {
            "symbol": "MSFT",
            "company_name": "Microsoft Corporation",
            "source": "nasdaq_listed",
            "notes": ["Market Category: Q."],
        }
    ]


def test_listing_loader_excludes_non_operating_security_types(tmp_path):
    path = tmp_path / "nasdaqlisted.txt"
    path.write_text(
        "Symbol|Security Name|Market Category|ETF|Test Issue|Financial Status|Round Lot Size|File Creation Time\n"
        "GOOD|Good Software Inc. Common Stock|Q|N|N|N|100|20260430\n"
        "SPAC|Blank Check Acquisition Corp. Class A|Q|N|N|N|100|20260430\n"
        "WRNT|Good Software Inc. Warrant|Q|N|N|N|100|20260430\n"
        "UNIT|Good Software Inc. Unit|Q|N|N|N|100|20260430\n"
        "PREF|Good Software Inc. Preferred Stock|Q|N|N|N|100|20260430\n",
        encoding="utf-8",
    )

    candidates = load_candidate_source_file(path, source="nasdaq_listed")

    assert [candidate["symbol"] for candidate in candidates] == ["GOOD"]


def test_load_remote_source_text_uses_same_normalization():
    text = (
        "Symbol|Security Name|Market Category|ETF|Test Issue|Financial Status|Round Lot Size|File Creation Time\n"
        "AAPL|Apple Inc.|Q|N|N|N|100|20260430\n"
    )

    candidates = load_candidate_source_text(text, source="nasdaq_listed")

    assert candidates == [
        {
            "symbol": "AAPL",
            "company_name": "Apple Inc.",
            "source": "nasdaq_listed",
            "notes": ["Market Category: Q."],
        }
    ]


def test_discovery_cli_loads_remote_source_url(monkeypatch, capsys):
    import longterm.discovery_cli as discovery_cli

    def fake_url_loader(url, *, source):
        assert url == "https://example.test/nasdaqlisted.txt"
        assert source == "nasdaq_listed"
        return [
            {
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "source": source,
                "source_score": 90,
                "market_cap": 3_000_000_000_000,
                "revenue_growth_1y_pct": 15,
                "earnings_growth_1y_pct": 25,
                "return_on_capital_pct": 25,
                "gross_margin_pct": 70,
                "category_leader": True,
            }
        ]

    monkeypatch.setattr(discovery_cli, "load_candidate_source_url", fake_url_loader)
    args = build_parser().parse_args(
        [
            "--source-url",
            "https://example.test/nasdaqlisted.txt",
            "--source",
            "nasdaq_listed",
        ]
    )

    exit_code = run_cli(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["research_queue"][0]["symbol"] == "MSFT"
    assert payload["research_queue"][0]["source"] == "nasdaq_listed"
