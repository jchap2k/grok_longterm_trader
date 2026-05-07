import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_news_monitor_cli import build_parser, run_cli
from longterm.portfolio_news_monitor import PortfolioNewsMonitorInputs, build_portfolio_news_monitor_report
from longterm.portfolio_state import PortfolioState
from research.research_packet import ResearchPacket


def _fixed_now() -> datetime:
    return datetime(2026, 5, 6, 16, 30, tzinfo=timezone.utc)


def test_portfolio_news_monitor_queues_relevant_portfolio_and_watchlist_news():
    portfolio = PortfolioState(
        holdings=[
            {"symbol": "AAPL", "market_value": 2500},
            {"symbol": "FXAIX", "market_value": 10000},
        ],
        protected_symbols=["FXAIX"],
    )
    articles = {
        "AAPL": [
            {
                "title": "Apple earnings show services revenue and margin growth",
                "url": "https://example.com/aapl-earnings",
                "published_utc": "2026-05-05T12:00:00Z",
                "description": "Apple reported stronger revenue, profit margin, and cash flow guidance.",
                "publisher": {"name": "Reuters"},
                "tickers": ["AAPL"],
            },
            {
                "title": "Why Apple stock is moving today",
                "url": "https://example.com/aapl-moving",
                "published_utc": "2026-05-05T13:00:00Z",
                "description": "Broad price-action recap.",
                "publisher": {"name": "Blog"},
                "tickers": ["AAPL"],
            },
        ],
        "MSFT": [
            {
                "title": "Microsoft cloud contract expands AI platform backlog",
                "url": "https://example.com/msft-cloud-ai",
                "published_utc": "2026-05-05T14:00:00Z",
                "description": "Microsoft signed a large customer deal for cloud AI software.",
                "publisher": {"name": "Reuters"},
                "tickers": ["MSFT"],
            }
        ],
        "FXAIX": [
            {
                "title": "FXAIX broad market roundup",
                "url": "https://example.com/fxaix",
                "published_utc": "2026-05-05T14:00:00Z",
                "description": "Index fund recap.",
                "publisher": {"name": "Blog"},
                "tickers": ["FXAIX"],
            }
        ],
    }

    report = build_portfolio_news_monitor_report(
        PortfolioNewsMonitorInputs(
            portfolio_state=portfolio,
            watchlist_ideas=[
                {
                    "symbol": "MSFT",
                    "company_name": "Microsoft",
                    "business_summary": "Cloud and AI software platform.",
                    "thesis_summary": "Azure AI platform growth.",
                }
            ],
            articles_by_symbol=articles,
            relevance_threshold=0.55,
            max_articles_per_symbol=3,
        ),
        now_func=_fixed_now,
    )

    assert report["order_submission_enabled"] is False
    assert report["generated_at"] == "2026-05-06T16:30:00Z"
    assert report["monitored_symbols"] == ["AAPL", "MSFT"]
    assert report["articles_checked"] == 3
    assert [row["symbol"] for row in report["enrichment_needed_queue"]] == ["AAPL", "MSFT"]
    assert report["enrichment_needed_queue"][0]["trigger_type"] == "portfolio_news"
    assert report["enrichment_needed_queue"][1]["trigger_type"] == "watchlist_news"
    assert all(row["next_step"] == "schedule_deeper_enrichment" for row in report["enrichment_needed_queue"])
    assert all(row["llm_escalation_allowed"] is False for row in report["enrichment_needed_queue"])
    assert "aapl-moving" not in json.dumps(report)
    assert "FXAIX" not in report["monitored_symbols"]


def test_portfolio_news_monitor_links_latest_journal_decision_for_held_symbol(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = journal.record_decision(
        ResearchPacket(
            symbol="AAPL",
            company_name="Apple",
            business_summary="Consumer technology and services platform.",
            thesis_summary="Services durability.",
            idea_source="unit_test",
        ),
        decision={
            "recommendation": "BUY",
            "confidence": 82,
            "suggested_size_pct": 3.0,
            "key_thesis": "Services and ecosystem durability.",
        },
    )

    report = build_portfolio_news_monitor_report(
        PortfolioNewsMonitorInputs(
            portfolio_state=PortfolioState(holdings=[{"symbol": "AAPL", "market_value": 2500}]),
            articles_by_symbol={
                "AAPL": [
                    {
                        "title": "Apple earnings guidance improves services revenue outlook",
                        "url": "https://example.com/aapl-services",
                        "published_utc": "2026-05-05T12:00:00Z",
                        "description": "Revenue, guidance, profit, and cash flow were stronger than expected.",
                        "publisher": {"name": "Reuters"},
                        "tickers": ["AAPL"],
                    }
                ]
            },
            journal_db=journal.db_path,
            relevance_threshold=0.55,
        ),
        now_func=_fixed_now,
    )

    queue_row = report["enrichment_needed_queue"][0]
    assert queue_row["linked_decision_id"] == decision_id
    assert queue_row["latest_recommendation"] == "BUY"
    assert queue_row["company_name"] == "Apple"
    assert queue_row["business_context"] == "Services and ecosystem durability."
    assert queue_row["thesis_impact_hint"] in {"potential_confirmation", "potential_invalidation", "review_required"}


def test_portfolio_news_monitor_missing_optional_inputs_warns_without_submission():
    report = build_portfolio_news_monitor_report(
        PortfolioNewsMonitorInputs(
            portfolio_state=None,
            watchlist_ideas=[],
            articles_by_symbol={},
        ),
        now_func=_fixed_now,
    )

    assert report["status"] == "completed"
    assert report["order_submission_enabled"] is False
    assert report["monitored_symbols"] == []
    assert report["enrichment_needed_queue"] == []
    assert "no_symbols_to_monitor" in report["warnings"]


def test_portfolio_news_monitor_cli_writes_report_from_local_artifacts(tmp_path, capsys):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"holdings": [{"symbol": "AAPL", "market_value": 2500}], "protected_symbols": []}),
        encoding="utf-8",
    )
    news_path = tmp_path / "news.json"
    news_path.write_text(
        json.dumps(
            {
                "AAPL": [
                    {
                        "title": "Apple earnings guidance improves services revenue",
                        "url": "https://example.com/aapl",
                        "published_utc": "2026-05-05T12:00:00Z",
                        "description": "Revenue, profit, margin, and cash flow guidance improved.",
                        "publisher": {"name": "Reuters"},
                        "tickers": ["AAPL"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "portfolio_news_monitor.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--portfolio-state",
                str(portfolio_path),
                "--snapshot-file",
                str(news_path),
                "--output",
                str(output_path),
                "--as-of-date",
                "2026-05-06",
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["output"] == str(output_path)
    assert saved["order_submission_enabled"] is False
    assert saved["generated_at"] == "2026-05-06T00:00:00Z"
    assert saved["enrichment_needed_queue"][0]["symbol"] == "AAPL"
    assert saved["enrichment_needed_queue"][0]["generated_at"] == "2026-05-06T00:00:00Z"
