import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.batch_intake import load_idea_batch
from longterm.cli import build_parser as build_research_parser, create_packets_from_args
from longterm.decision_journal import LongTermDecisionJournal
from longterm.email_sender import EmailSettings, SmtpEmailSender, load_email_settings
from longterm.journal_cli import build_parser as build_journal_parser, run_cli as run_journal_cli
from longterm.market_enrichment import enrich_prices
from longterm.motley_fool_intake import (
    MotleyFoolDashboardRow,
    default_motley_fool_sources,
    motley_rows_to_ideas,
    motley_table_payloads_to_ideas,
    normalize_motley_fool_dashboard,
    rows_from_table_payloads,
)
from longterm.recommendation_enrichment import CachedRecommendationEnricher
from longterm.capital_alert import build_capital_needed_alert, build_capital_needed_email
from longterm.portfolio_state import PortfolioState
from longterm.report_builder import RecommendationTableBuilder, build_markdown_report
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


class FakeQuoteProvider:
    def __init__(self, prices):
        self.prices = prices

    def get_price(self, symbol):
        return self.prices[symbol]


class FakeRecommendationEnricher:
    def __init__(self):
        self.calls = []

    def enrich(self, symbol):
        self.calls.append(symbol)
        return {
            "current_price": 905.25,
            "change_pct": 1.25,
            "market_cap": "$2.2T",
            "revenue_growth_1y_pct": 125.0,
            "estimated_return_range": "8% to 20%",
            "estimated_max_drawdown_pct": -42,
            "data_as_of": "2026-04-29",
        }


class FakeSmtp:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = None
        self.sent = None
        self.started_tls = False
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def sendmail(self, from_addr, to_addrs, message):
        self.sent = (from_addr, to_addrs, message)


def test_normalize_motley_fool_dashboard_merges_recommendations_and_rankings():
    candidates = normalize_motley_fool_dashboard(
        new_recommendations=[
            MotleyFoolDashboardRow(
                source_table="new_recommendations",
                symbol="AMZN",
                action="Buy",
                rec_date="03/19/26",
                risk_type="C",
                service="SA",
                discussion_count=37,
            )
        ],
        rankings=[
            MotleyFoolDashboardRow(
                source_table="stock_advisor_rankings",
                rank=5,
                symbol="AMZN",
                company="Amazon",
                price="$263.04",
                risk_type="C",
                discussion_count=37,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].symbol == "AMZN"
    assert candidates[0].company == "Amazon"
    assert candidates[0].action == "Buy"
    assert candidates[0].rank == 5
    assert candidates[0].price == "$263.04"
    assert candidates[0].source_tables == ["new_recommendations", "stock_advisor_rankings"]


def test_motley_fool_rows_convert_to_investigation_ideas_not_buy_orders():
    candidates = normalize_motley_fool_dashboard(
        new_recommendations=[
            MotleyFoolDashboardRow(
                source_table="new_recommendations",
                symbol="MOGA",
                action="Buy",
                rec_date="04/16/26",
                risk_type="C",
                service="SA",
                discussion_count=6,
            )
        ],
        rankings=[
            MotleyFoolDashboardRow(
                source_table="stock_advisor_rankings",
                rank=2,
                symbol="MOGA",
                company="Moog",
                price="$302.02",
                risk_type="C",
                discussion_count=6,
            )
        ],
    )

    ideas = motley_rows_to_ideas(candidates)

    assert ideas == [
        {
            "symbol": "MOGA",
            "company_name": "Moog",
            "idea_source": "motley_fool_dashboard",
            "source_notes": [
                "Motley Fool candidate; requires independent long-term research before any action.",
                "New recommendation action: Buy.",
                "Recommendation date: 04/16/26.",
                "Stock Advisor rank: 2.",
                "Motley Fool type/risk label: C.",
                "Reported price: $302.02.",
                "Discussion count: 6.",
            ],
        }
    ]


def test_default_motley_fool_sources_include_dashboard_new_recs_and_rankings():
    sources = default_motley_fool_sources()

    assert sources["dashboard"].url == "https://www.fool.com/premium?watchSymbols=NASDAQ%3ACRWD"
    assert sources["new_recommendations"].url == "https://www.fool.com/premium/new-recs"
    assert sources["analyst_rankings"].url == "https://www.fool.com/premium/rankings?type=ANALYST"
    assert sources["quant_rankings"].url == "https://www.fool.com/premium/rankings?type=QUANT"
    assert sources["quant_rankings"].label == "AI rankings"


def test_rows_from_table_payloads_extracts_dashboard_tables():
    new_recs, rankings = rows_from_table_payloads(
        [
            {
                "title": "New Recommendations",
                "headers": ["Symbol", "Action", "Rec Date", "Type", "Service", ""],
                "rows": [
                    ["MOGA", "Buy", "04/16/26", "C", "SA", "6"],
                    ["MKC", "Hold", "04/08/26", "C", "SA", "9"],
                ],
            },
            {
                "title": "Stock Advisor Rankings",
                "headers": ["#", "Symbol", "Company", "Price", "Type", ""],
                "rows": [
                    ["1.", "TSLA", "Tesla", "$372.86", "A", "99"],
                    ["2.", "MOGA", "Moog", "$302.02", "C", "6"],
                ],
            },
        ],
        ranking_source_table="stock_advisor_rankings",
    )

    assert new_recs[0].symbol == "MOGA"
    assert new_recs[0].action == "Buy"
    assert new_recs[0].discussion_count == 6
    assert rankings[0].rank == 1
    assert rankings[0].company == "Tesla"
    assert rankings[0].source_table == "stock_advisor_rankings"


def test_rows_from_table_payloads_cleans_expand_current_row_prefix():
    new_recs, _rankings = rows_from_table_payloads(
        [
            {
                "title": "New Recommendations",
                "headers": ["Symbol", "Action", "Rec Date", "Type", "Service", ""],
                "rows": [["EXPAND CURRENT ROW\nAMZN", "Buy", "03/19/26", "C", "SA", "37"]],
            }
        ],
        ranking_source_table="stock_advisor_rankings",
    )

    assert new_recs[0].symbol == "AMZN"


def test_rows_from_table_payloads_keeps_full_recommendation_company():
    new_recs, _rankings = rows_from_table_payloads(
        [
            {
                "title": "",
                "headers": [
                    "Symbol",
                    "Company",
                    "Action",
                    "Service",
                    "Return",
                    "Rec Date",
                    "Type",
                    "Est. Return",
                    "Est. Max Drawdown",
                    "Market Cap",
                    "",
                ],
                "rows": [
                    [
                        "EXPAND CURRENT ROW\nMOGA",
                        "Moog",
                        "Buy",
                        "SA",
                        "-2%",
                        "04/16/26",
                        "Cautious",
                        "-2%\nto\n14%",
                        "-34%",
                        "$9.58B",
                        "6",
                    ]
                ],
            }
        ],
        ranking_source_table="stock_advisor_rankings",
    )

    assert new_recs[0].symbol == "MOGA"
    assert new_recs[0].company == "Moog"


def test_motley_table_payloads_preserve_company_url_for_later_enrichment():
    ideas = motley_table_payloads_to_ideas(
        "new_recommendations",
        [
            {
                "title": "New Recommendations",
                "headers": ["Symbol", "Company", "Action", "Service", "Rec Date"],
                "rows": [["EXPAND CURRENT ROW\nAMZN", "Amazon", "Buy", "SA", "03/19/26"]],
                "row_links": [
                    [
                        "https://www.fool.com/premium/company/NASDAQ/AMZN/financials/summary",
                        "https://www.fool.com/premium/company/NASDAQ/AMZN/financials/summary",
                        "",
                        "",
                        "",
                    ]
                ],
            }
        ],
    )

    assert ideas[0]["motley_fool_company_url"] == (
        "https://www.fool.com/premium/company/NASDAQ/AMZN/financials/summary"
    )
    assert ideas[0]["motley_fool_exchange"] == "NASDAQ"
    assert ideas[0]["source_url"] == ideas[0]["motley_fool_company_url"]
    assert (
        "Motley Fool company URL: https://www.fool.com/premium/company/NASDAQ/AMZN/financials/summary."
        in ideas[0]["source_notes"]
    )


def test_motley_table_payloads_accept_numeric_company_url_from_live_tables():
    ideas = motley_table_payloads_to_ideas(
        "new_recommendations",
        [
            {
                "title": "New Recommendations",
                "headers": ["Symbol", "Company", "Action", "Service", "Rec Date"],
                "rows": [["EXPAND CURRENT ROW\nMOGA", "Moog", "Buy", "SA", "04/16/26"]],
                "row_links": [
                    [
                        "https://www.fool.com/premium/company/206462",
                        "",
                        "",
                        "https://www.fool.com/premium/my-services/stock-advisor",
                        "https://www.fool.com/premium/18/coverage/updates/2026/04/16/buy-moog-mission-critical-components-and-more",
                    ]
                ],
            }
        ],
    )

    assert ideas[0]["symbol"] == "MOGA"
    assert ideas[0]["motley_fool_company_url"] == "https://www.fool.com/premium/company/206462"
    assert ideas[0]["source_url"] == "https://www.fool.com/premium/company/206462"
    assert "motley_fool_exchange" not in ideas[0]


def test_rows_from_table_payloads_skips_zero_width_placeholder_rows():
    new_recs, _rankings = rows_from_table_payloads(
        [
            {
                "title": "",
                "headers": ["Symbol", "Company", "Action", "Service", "Rec Date"],
                "rows": [["\u200c", "\u200c", "\u200c", "\u200c", "\u200c"]],
            }
        ],
        ranking_source_table="stock_advisor_rankings",
    )

    assert new_recs == []


def test_motley_table_payloads_to_ideas_labels_quant_rankings():
    ideas = motley_table_payloads_to_ideas(
        "quant_rankings",
        [
            {
                "title": "Stock Advisor Rankings",
                "headers": ["#", "Symbol", "Company", "Price", "Type", ""],
                "rows": [["1.", "TSLA", "Tesla", "$372.86", "A", "99"]],
            }
        ],
    )

    assert ideas[0]["symbol"] == "TSLA"
    assert ideas[0]["idea_source"] == "motley_fool_quant_rankings"
    assert "Motley Fool source: AI rankings." in ideas[0]["source_notes"]
    assert "Stock Advisor rank: 1." in ideas[0]["source_notes"]


def test_motley_table_payloads_to_ideas_accepts_full_ranking_without_company():
    ideas = motley_table_payloads_to_ideas(
        "analyst_rankings",
        [
            {
                "title": "",
                "headers": [
                    "#",
                    "Symbol",
                    "Price",
                    "Change %",
                    "Previous Rank",
                    "Market Cap",
                    "Type",
                    "1Y Rev. Growth",
                    "Est. Return",
                    "Est. Max Drawdown",
                    "Times Rec'd",
                    "",
                ],
                "rows": [
                    [
                        "2.",
                        "MOGA",
                        "$302.02",
                        "-1.40%",
                        "-",
                        "$9.58B",
                        "Cautious",
                        "13.79%",
                        "-2%\nto\n14%",
                        "-34%",
                        "1",
                        "",
                    ]
                ],
            }
        ],
    )

    assert ideas[0]["symbol"] == "MOGA"
    assert ideas[0]["company_name"] == "MOGA"
    assert "Stock Advisor rank: 2." in ideas[0]["source_notes"]
    assert "Discussion count: 1." in ideas[0]["source_notes"]


def test_enrich_prices_fetches_candidate_and_benchmark_prices():
    packet = create_research_packet_from_idea(
        {"symbol": "aapl", "benchmark_symbol": "fxaix"}
    )
    prices = enrich_prices(
        packet,
        quote_provider=FakeQuoteProvider({"AAPL": 180.5, "FXAIX": 175.25}),
    )

    assert prices.candidate_symbol == "AAPL"
    assert prices.benchmark_symbol == "FXAIX"
    assert prices.candidate_price == 180.5
    assert prices.benchmark_price == 175.25


def test_load_idea_batch_from_json_list_inherits_profile(tmp_path):
    idea_path = tmp_path / "ideas.json"
    idea_path.write_text(
        json.dumps(
            [
                {"symbol": "aapl", "thesis_summary": "Ecosystem durability."},
                {"symbol": "msft", "thesis_summary": "Cloud durability."},
            ]
        ),
        encoding="utf-8",
    )
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
    )

    packets = load_idea_batch(idea_path, profile=profile, idea_source="batch_file")

    assert [packet.symbol for packet in packets] == ["AAPL", "MSFT"]
    assert packets[0].benchmark_symbol == "FXAIX"
    assert packets[1].idea_source == "batch_file"


def test_research_cli_creates_packets_from_batch_file(tmp_path):
    idea_path = tmp_path / "ideas.json"
    idea_path.write_text(
        json.dumps(
            [
                {"symbol": "aapl", "thesis_summary": "Ecosystem durability."},
                {"symbol": "msft", "thesis_summary": "Cloud durability."},
            ]
        ),
        encoding="utf-8",
    )
    parser = build_research_parser()
    args = parser.parse_args(["--idea-batch", str(idea_path)])

    packets = create_packets_from_args(args)

    assert [packet.symbol for packet in packets] == ["AAPL", "MSFT"]


def test_build_markdown_report_includes_benchmark_summary(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = create_research_packet_from_idea(
        {
            "symbol": "AAPL",
            "company_name": "Apple",
            "benchmark_symbol": "FXAIX",
        }
    )
    decision_id = journal.record_decision(
        packet,
        decision={
            "recommendation": "BUY",
            "confidence": 82,
            "suggested_size_pct": 6.5,
            "key_thesis": "Durable compounder.",
        },
        candidate_price=100,
        benchmark_price=100,
    )
    journal.update_outcome(decision_id, candidate_price=115, benchmark_price=110)

    report = build_markdown_report(journal)

    assert "# Long-Term Trader Decision Report" in report
    assert "Average excess return vs benchmark: 5.0%" in report
    assert "Review Due" in report
    assert "Data As Of" in report
    assert "| AAPL | BUY | 82 | 5.0% |" in report


def test_build_markdown_report_can_include_derived_review_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "MSFT",
                "benchmark_symbol": "FXAIX",
                "review_cadence": "monthly",
            }
        ),
        decision={"recommendation": "BUY", "confidence": 82, "suggested_size_pct": 5},
    )

    report = build_markdown_report(
        journal,
        review_status_by_symbol={
            "MSFT": {
                "review_due": True,
                "thesis_state": "healthy",
                "days_since_review": 40,
            }
        },
    )

    assert "MSFT" in report
    assert "| True | healthy |" in report


def test_build_markdown_report_can_auto_derive_review_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "MSFT",
                "benchmark_symbol": "FXAIX",
                "review_cadence": "monthly",
            }
        ),
        decision={"recommendation": "BUY", "confidence": 82, "suggested_size_pct": 5},
    )

    report = build_markdown_report(
        journal,
        review_status_today=date(2026, 4, 29),
        last_review_dates_by_symbol={"MSFT": date(2026, 3, 20)},
    )

    assert "MSFT" in report
    assert "| True | stale |" in report


def test_build_markdown_report_includes_decision_id_for_traceability(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={"recommendation": "BUY", "confidence": 91, "suggested_size_pct": 8},
    )

    report = build_markdown_report(journal)

    assert "Decision ID" in report
    assert decision_id[:8] in report


def test_recommendation_table_keeps_latest_ranked_candidates_with_links(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "AAPL", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 82,
            "suggested_size_pct": 5,
            "key_thesis": "Durable ecosystem.",
            "info_link": "https://example.com/aapl",
            "current_price": 180.5,
            "change_pct": -0.83,
            "service": "LongTerm",
            "rec_date": "04/29/26",
            "return_since_rec_pct": 4.2,
            "market_cap": "$2.8T",
            "risk_type": "Moderate",
            "revenue_growth_1y_pct": 7.1,
            "estimated_return_range": "5% to 14%",
            "estimated_max_drawdown_pct": -32,
            "discussion_count": 3,
        },
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 91,
            "suggested_size_pct": 8,
            "key_thesis": "AI infrastructure leader.",
            "info_link": "https://example.com/nvda",
        },
    )

    rows = journal.list_recommendation_table()

    assert [row["symbol"] for row in rows] == ["NVDA", "AAPL"]
    assert rows[0]["rank"] == 1
    assert rows[0]["previous_rank"] == "-"
    assert rows[1]["company_name"] == "AAPL"
    assert rows[1]["action"] == "BUY"
    assert rows[1]["service"] == "LongTerm"
    assert rows[1]["rec_date"] == "04/29/26"
    assert rows[1]["return_since_rec_pct"] == 4.2
    assert rows[1]["current_price"] == 180.5
    assert rows[1]["risk_type"] == "Moderate"
    assert rows[1]["times_recommended"] == 1
    assert rows[1]["discussion_count"] == 3
    assert rows[0]["reason"] == "AI infrastructure leader."
    assert rows[0]["info_link"] == "https://example.com/nvda"


def test_recommendation_table_prioritizes_actionable_buys_over_passive_holds(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "MSFT", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "HOLD",
            "confidence": 99,
            "suggested_size_pct": 0,
            "key_thesis": "Excellent company, but not an add candidate today.",
        },
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 85,
            "suggested_size_pct": 8,
            "key_thesis": "Actionable new active-sleeve candidate.",
        },
    )

    rows = journal.list_recommendation_table()

    assert [row["symbol"] for row in rows] == ["NVDA", "MSFT"]
    assert rows[0]["ranking_score"] > rows[1]["ranking_score"]
    assert "BUY" in rows[0]["rank_reason"]
    assert "HOLD" in rows[1]["rank_reason"]


def test_markdown_report_exposes_ranking_score_and_reason(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "MSFT", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "HOLD",
            "confidence": 99,
            "suggested_size_pct": 0,
            "key_thesis": "Excellent company, but not an add candidate today.",
        },
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 85,
            "suggested_size_pct": 8,
            "key_thesis": "Actionable new active-sleeve candidate.",
        },
    )

    report = build_markdown_report(journal)

    assert "Rank Score" in report
    assert "Rank Reason" in report
    assert "BUY recommendation, confidence 85" in report
    assert "HOLD recommendation, confidence 99" in report
    assert report.index("| 1 |") < report.index("| 2 |")


def test_recommendation_table_builder_enriches_without_mutating_journal(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 91,
            "suggested_size_pct": 8,
            "key_thesis": "AI infrastructure leader.",
        },
    )
    enricher = FakeRecommendationEnricher()

    rows = RecommendationTableBuilder(journal, enricher=enricher).build(limit=5)
    raw_rows = journal.list_recommendation_table(limit=5)

    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["current_price"] == 905.25
    assert rows[0]["market_cap"] == "$2.2T"
    assert rows[0]["estimated_return_range"] == "8% to 20%"
    assert rows[0]["data_as_of"] == "2026-04-29"
    assert raw_rows[0].get("data_as_of") is None
    assert enricher.calls == ["NVDA"]


def test_repeated_recommendations_increment_count_and_surface_new_information(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": ["Initial Stock Advisor recommendation."],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 88,
            "suggested_size_pct": 6,
            "key_thesis": "AI data center demand remains durable.",
        },
    )
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": [
                    "New information: Blackwell supply commentary improved.",
                    "New information: management raised data-center margin outlook.",
                ],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 8,
            "key_thesis": "Blackwell ramp improves long-term earnings power.",
        },
    )

    rows = journal.list_recommendation_table(limit=5)

    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["times_recommended"] == 2
    assert rows[0]["repeat_recommendation_count"] == 2
    assert rows[0]["new_information_count"] == 3
    assert "Blackwell supply commentary improved" in rows[0]["new_information_notes"][0]
    assert "Prior thesis: AI data center demand remains durable." in rows[0]["new_information_notes"]


def test_markdown_report_includes_repeat_recommendation_and_new_information_notes(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": ["Initial recommendation."],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 88,
            "suggested_size_pct": 6,
            "key_thesis": "AI data center demand remains durable.",
        },
    )
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "NVDA",
                "company_name": "Nvidia",
                "idea_source": "motley_fool",
                "business_summary": "AI accelerator platform.",
                "source_notes": ["New information: Blackwell supply commentary improved."],
            }
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 92,
            "suggested_size_pct": 8,
            "key_thesis": "Blackwell ramp improves long-term earnings power.",
        },
    )

    report = build_markdown_report(journal)

    assert "Times Rec'd" in report
    assert "New Info" in report
    assert "Blackwell supply commentary improved" in report


def test_recommendation_table_builder_marks_review_due_from_review_candidates(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": "AAPL",
                "benchmark_symbol": "FXAIX",
                "review_cadence": "monthly",
                "invalidation_conditions": ["Services growth materially slows"],
            }
        ),
        decision={"recommendation": "BUY", "confidence": 82, "suggested_size_pct": 5},
    )

    rows = RecommendationTableBuilder(
        journal,
        review_status_by_symbol={
            "AAPL": {
                "review_due": True,
                "thesis_state": "healthy",
                "days_since_review": 35,
            }
        },
    ).build(limit=5)

    assert rows[0]["review_due"] is True
    assert rows[0]["thesis_state"] == "healthy"
    assert rows[0]["days_since_review"] == 35


def test_cached_recommendation_enricher_reuses_daily_cache(tmp_path):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return {"current_price": 905.25, "market_cap": "$2.2T"}

    cache_path = tmp_path / "recommendation_enrichment_cache.json"
    enricher = CachedRecommendationEnricher(
        fetch=fetch,
        cache_path=cache_path,
        today="2026-04-29",
    )

    first = enricher.enrich("NVDA")
    second = enricher.enrich("NVDA")

    assert first == second
    assert calls == ["NVDA"]
    assert json.loads(cache_path.read_text(encoding="utf-8"))["NVDA"]["data_as_of"] == "2026-04-29"


def test_capital_needed_alert_uses_ranked_recommendation_table(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 91,
            "suggested_size_pct": 8,
            "key_thesis": "AI infrastructure leader.",
            "info_link": "https://example.com/nvda",
        },
    )

    alert = build_capital_needed_alert(
        journal,
        active_sleeve_value=34000,
        available_cash=500,
    )

    assert alert.should_alert is True
    assert alert.top_symbol == "NVDA"
    assert alert.estimated_capital_needed == 2220.0
    assert "https://example.com/nvda" in alert.markdown


def test_capital_needed_alert_suppressed_when_existing_holding_should_be_sold_first(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "AAPL", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "SELL",
            "confidence": 88,
            "suggested_size_pct": 0,
            "key_thesis": "Thesis broke.",
        },
    )
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 94,
            "suggested_size_pct": 8,
            "key_thesis": "AI infrastructure leader.",
        },
    )
    state = PortfolioState(
        cash=500,
        holdings=[{"symbol": "AAPL", "market_value": 3000}],
        protected_symbols=["FXAIX"],
    )

    alert = build_capital_needed_alert(
        journal,
        active_sleeve_value=34000,
        available_cash=500,
        portfolio_state=state,
    )

    assert alert.should_alert is False
    assert "sell/reduce" in alert.reason.lower()


def test_capital_needed_email_payload_is_informational_and_traceable(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": "BUY",
            "confidence": 91,
            "suggested_size_pct": 8,
            "key_thesis": "AI infrastructure leader.",
            "info_link": "https://example.com/nvda",
        },
    )

    email = build_capital_needed_email(
        journal,
        active_sleeve_value=34000,
        available_cash=500,
        recipient_email="user@example.com",
    )

    assert email.should_send is True
    assert email.recipient_email == "user@example.com"
    assert "Capital needed" in email.subject
    assert "NVDA" in email.subject
    assert "informational" in email.text_body.lower()
    assert "do not automatically deposit" in email.text_body.lower()
    assert decision_id[:8] in email.html_body
    assert "https://example.com/nvda" in email.html_body
    assert email.metadata["top_symbol"] == "NVDA"
    assert email.metadata["estimated_capital_needed"] == 2220.0


def test_smtp_email_sender_skips_when_disabled():
    sender = SmtpEmailSender(smtp_factory=FakeSmtp)
    email = build_capital_needed_email(
        LongTermDecisionJournal(),
        active_sleeve_value=34000,
        available_cash=5000,
        recipient_email="user@example.com",
    )

    result = sender.send(
        email,
        EmailSettings(
            enabled=False,
            email_to="user@example.com",
            email_from="bot@example.com",
            username="smtp-user",
            password="smtp-pass",
        ),
    )

    assert result.sent is False
    assert result.reason == "Email notifications disabled."


def test_smtp_email_sender_uses_brevo_tls_settings(tmp_path):
    FakeSmtp.instances = []
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    journal.record_decision(
        create_research_packet_from_idea({"symbol": "NVDA", "benchmark_symbol": "FXAIX"}),
        decision={"recommendation": "BUY", "confidence": 91, "suggested_size_pct": 8},
    )
    email = build_capital_needed_email(
        journal,
        active_sleeve_value=34000,
        available_cash=500,
        recipient_email="user@example.com",
    )

    result = SmtpEmailSender(smtp_factory=FakeSmtp).send(
        email,
        EmailSettings(
            enabled=True,
            email_to="user@example.com",
            email_from="bot@example.com",
            username="abc123@smtp-brevo.com",
            password="fake-smtp-password",
            smtp_host="smtp-relay.brevo.com",
            smtp_port=587,
        ),
    )

    smtp = FakeSmtp.instances[0]
    assert result.sent is True
    assert smtp.host == "smtp-relay.brevo.com"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.logged_in == ("abc123@smtp-brevo.com", "fake-smtp-password")
    assert smtp.sent[0] == "bot@example.com"
    assert smtp.sent[1] == ["user@example.com"]
    assert "Capital needed" in smtp.sent[2]
    assert "text/html" in smtp.sent[2]


def test_load_email_settings_uses_simple_bot_brevo_keys(tmp_path):
    path = tmp_path / "email_notifications.json"
    path.write_text(
        json.dumps(
            {
                "email_notifications": True,
                "email_to": "user@example.com",
                "email_from": "bot@example.com",
                "email_username": "abc123@smtp-brevo.com",
                "email_password": "fake-smtp-password",
                "email_smtp_host": "smtp-relay.brevo.com",
                "email_smtp_port": 587,
            }
        ),
        encoding="utf-8",
    )

    settings = load_email_settings(path)

    assert settings.enabled is True
    assert settings.email_to == "user@example.com"
    assert settings.email_from == "bot@example.com"
    assert settings.username == "abc123@smtp-brevo.com"
    assert settings.password == "fake-smtp-password"
    assert settings.smtp_host == "smtp-relay.brevo.com"
    assert settings.smtp_port == 587


def test_load_email_settings_defaults_to_trading_agent_config():
    settings = load_email_settings()

    assert settings.email_to == "jchap2k.swingtrader@gmail.com"
    assert settings.email_from == "jchap2k.swingtrader@gmail.com"
    assert settings.smtp_host == "smtp-relay.brevo.com"
    assert settings.smtp_port == 587


def test_journal_cli_report_outputs_markdown(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = create_research_packet_from_idea({"symbol": "AAPL", "benchmark_symbol": "FXAIX"})
    decision_id = journal.record_decision(
        packet,
        decision={"recommendation": "BUY", "confidence": 82},
        candidate_price=100,
        benchmark_price=100,
    )
    journal.update_outcome(decision_id, candidate_price=115, benchmark_price=110)
    parser = build_journal_parser()
    args = parser.parse_args(["report", "--journal-db", str(tmp_path / "journal.db")])

    exit_code = run_journal_cli(args)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "# Long-Term Trader Decision Report" in output
    assert "AAPL" in output
