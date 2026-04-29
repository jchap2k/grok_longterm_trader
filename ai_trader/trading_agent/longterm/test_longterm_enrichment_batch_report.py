import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.batch_intake import load_idea_batch
from longterm.cli import build_parser as build_research_parser, create_packets_from_args
from longterm.decision_journal import LongTermDecisionJournal
from longterm.journal_cli import build_parser as build_journal_parser, run_cli as run_journal_cli
from longterm.market_enrichment import enrich_prices
from longterm.recommendation_enrichment import CachedRecommendationEnricher
from longterm.capital_alert import build_capital_needed_alert
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
    assert "| True | healthy |" in report


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
