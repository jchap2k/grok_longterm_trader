import json
from datetime import datetime, timezone

from longterm.position_review_queue import (
    PositionReviewQueueInputs,
    build_position_review_queue_report,
    run_cli,
)
from longterm.portfolio_state import PortfolioState


def test_position_review_queue_captures_sell_reduce_and_rebalance_intents():
    report = build_position_review_queue_report(
        PositionReviewQueueInputs(
            portfolio_state=PortfolioState(
                cash=5000,
                holdings=[
                    {"symbol": "ADBE", "market_value": 1000, "quantity": 2},
                    {"symbol": "MSFT", "market_value": 2000, "quantity": 5},
                ],
            ),
            action_plan={
                "plan_id": "plan-1",
                "intents": [
                    {
                        "symbol": "ADBE",
                        "intent_type": "SELL",
                        "order_intent": "SELL",
                        "decision_id": "decision-adbe",
                        "reason": "Thesis broken.",
                    },
                    {
                        "symbol": "MSFT",
                        "intent_type": "REBALANCE",
                        "order_intent": "SELL",
                        "decision_id": "decision-msft",
                        "source_symbol": "MSFT",
                        "target_symbol": "MA",
                        "reason": "Fund new higher-ranked candidate.",
                    },
                ],
            },
        ),
        now_func=lambda: datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc),
    )

    assert report["order_submission_enabled"] is False
    assert report["llm_calls_enabled"] is False
    assert report["broker_calls_enabled"] is False
    assert report["review_count"] == 2
    by_symbol = {row["symbol"]: row for row in report["review_queue"]}
    assert by_symbol["ADBE"]["review_type"] == "sell_review"
    assert by_symbol["ADBE"]["decision_id"] == "decision-adbe"
    assert by_symbol["ADBE"]["portfolio_market_value"] == 1000
    assert by_symbol["MSFT"]["review_type"] == "rebalance_review"
    assert by_symbol["MSFT"]["target_symbol"] == "MA"
    assert report["counts_by_review_type"] == {"rebalance_review": 1, "sell_review": 1}


def test_position_review_queue_escalates_portfolio_news_and_excludes_protected_symbols():
    report = build_position_review_queue_report(
        PositionReviewQueueInputs(
            portfolio_state=PortfolioState(
                cash=0,
                protected_symbols=["FXAIX"],
                holdings=[
                    {"symbol": "ADBE", "market_value": 1000, "quantity": 2},
                    {"symbol": "FXAIX", "market_value": 9000, "quantity": 40},
                ],
            ),
            portfolio_news_monitor={
                "status": "completed",
                "enrichment_needed_queue": [
                    {
                        "symbol": "ADBE",
                        "trigger_type": "portfolio_news",
                        "impact_category": "Regulatory - High",
                        "relevance_score": 0.91,
                        "title": "Adobe faces major regulatory review",
                        "url": "https://example.test/adbe",
                        "linked_decision_id": "decision-adbe",
                        "thesis_impact_hint": "review_required",
                    },
                    {
                        "symbol": "FXAIX",
                        "trigger_type": "portfolio_news",
                        "impact_category": "Macro - High",
                        "relevance_score": 0.99,
                        "title": "Protected benchmark noise",
                        "url": "https://example.test/fxaix",
                        "linked_decision_id": "decision-fxaix",
                        "thesis_impact_hint": "review_required",
                    },
                ],
            },
        ),
        now_func=lambda: datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc),
    )

    assert report["review_count"] == 1
    assert report["excluded_protected_symbols"] == ["FXAIX"]
    row = report["review_queue"][0]
    assert row["symbol"] == "ADBE"
    assert row["review_type"] == "thesis_news_review"
    assert row["severity"] == "high"
    assert row["trigger_source"] == "portfolio_news_monitor"
    assert row["decision_id"] == "decision-adbe"
    assert row["article_title"] == "Adobe faces major regulatory review"


def test_position_review_queue_cli_writes_json_report(tmp_path):
    portfolio = tmp_path / "portfolio.json"
    action_plan = tmp_path / "action_plan.json"
    output = tmp_path / "position_review_queue.json"
    portfolio.write_text(
        json.dumps({"holdings": [{"symbol": "ADBE", "market_value": 1000}], "protected_symbols": []}),
        encoding="utf-8",
    )
    action_plan.write_text(
        json.dumps({"intents": [{"symbol": "ADBE", "intent_type": "SELL", "order_intent": "SELL"}]}),
        encoding="utf-8",
    )

    code = run_cli(
        [
            "--portfolio-state",
            str(portfolio),
            "--action-plan",
            str(action_plan),
            "--output",
            str(output),
            "--json",
        ]
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert saved["status"] == "completed"
    assert saved["review_count"] == 1
