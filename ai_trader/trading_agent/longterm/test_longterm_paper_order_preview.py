import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_order_preview import build_paper_order_preview, build_paper_order_preview_markdown
from longterm.paper_order_preview_cli import build_parser, run_cli
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def _actionable_promotion(symbol="NVDA"):
    return {
        "symbol": symbol,
        "promotion_decision": "ACTIONABLE_BUY",
        "is_orderable": True,
        "evidence_brief": "Versioned evidence packet includes thesis and article support.",
        "evidence_version": "2026-05-02T12:00:00Z",
        "blockers": [],
        "followups": [],
        "warnings": [],
    }


def test_paper_order_preview_builds_traceable_buy_preview():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-1",
        "benchmark_gate_reason": "Active sleeve is clearing FXAIX.",
        "intents": [
            {
                "symbol": "NVDA",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 2720,
                "target_value": 2720,
                "allowed": True,
                "reason": "High conviction.",
                "decision_id": "decision-nvda",
                "promotion_review": _actionable_promotion("NVDA"),
                "risk_review": {
                    "allowed": True,
                    "risk_level": "medium",
                    "review_due": False,
                    "thesis_state": "healthy",
                },
            }
        ],
    }

    preview = build_paper_order_preview(plan, portfolio_state=state, profile=profile)

    assert preview["order_submission_enabled"] is False
    assert preview["preview_count"] == 1
    row = preview["previews"][0]
    assert row["symbol"] == "NVDA"
    assert row["side"] == "buy"
    assert row["notional"] == 2720.0
    assert row["allowed"] is True
    assert row["decision_id"] == "decision-nvda"
    assert row["plan_id"] == "plan-1"
    assert row["trade_id"] is None
    assert row["cash_shortfall"] == 0.0
    assert row["benchmark_gate_reason"] == "Active sleeve is clearing FXAIX."
    assert row["buy_promotion_decision"] == "ACTIONABLE_BUY"
    assert row["promotion_review"]["promotion_decision"] == "ACTIONABLE_BUY"


def test_paper_order_preview_blocks_stock_buy_without_actionable_promotion():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-promo",
        "intents": [
            {
                "symbol": "VEEV",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 1000,
                "allowed": True,
                "decision_id": "decision-veev",
                "promotion_review": {
                    "promotion_decision": "WATCHLIST_PENDING_EVIDENCE",
                    "is_orderable": False,
                    "blockers": ["missing_article_evidence"],
                    "followups": ["Capture earnings article."],
                },
            },
            {
                "symbol": "MSFT",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 1000,
                "allowed": True,
                "decision_id": "decision-msft",
            },
        ],
    }

    preview = build_paper_order_preview(plan, portfolio_state=state, profile=profile)
    by_symbol = {row["symbol"]: row for row in preview["previews"]}

    assert preview["allowed_count"] == 0
    assert preview["blocked_count"] == 2
    assert by_symbol["VEEV"]["buy_promotion_decision"] == "WATCHLIST_PENDING_EVIDENCE"
    assert "buy_promotion_not_actionable" in by_symbol["VEEV"]["blocked_reasons"]
    assert "missing_buy_promotion_review" in by_symbol["MSFT"]["blocked_reasons"]


def test_paper_order_preview_blocks_protected_and_cash_shortfall():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=1000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-2",
        "intents": [
            {
                "symbol": "FXAIX",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 2500,
                "target_value": 2500,
                "allowed": True,
                "decision_id": "decision-fxaix",
            },
            {
                "symbol": "MSFT",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 2500,
                "target_value": 2500,
                "allowed": True,
                "decision_id": "decision-msft",
            },
        ],
    }

    preview = build_paper_order_preview(plan, portfolio_state=state, profile=profile)
    by_symbol = {row["symbol"]: row for row in preview["previews"]}

    assert by_symbol["FXAIX"]["allowed"] is False
    assert "protected" in by_symbol["FXAIX"]["blocked_reasons"][0].lower()
    assert by_symbol["MSFT"]["allowed"] is False
    assert by_symbol["MSFT"]["cash_shortfall"] == 1500.0


def test_paper_order_preview_whole_share_buy_uses_explicit_price_map():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-whole",
        "intents": [
            {
                "symbol": "NVDA",
                "intent_type": "BUY",
                "order_intent": "BUY",
                "trade_value": 2720,
                "allowed": True,
                "decision_id": "decision-nvda",
                "promotion_review": _actionable_promotion("NVDA"),
            }
        ],
    }

    preview = build_paper_order_preview(
        plan,
        portfolio_state=state,
        profile=profile,
        order_model="whole_share",
        price_map={"NVDA": 910.0},
    )

    row = preview["previews"][0]
    assert preview["order_model"] == "whole_share"
    assert row["order_type"] == "market_quantity_preview"
    assert row["requested_notional"] == 2720.0
    assert row["estimated_price"] == 910.0
    assert row["quantity"] == 2
    assert row["notional"] == 1820.0
    assert row["size_variance"] == -900.0
    assert row["allowed"] is True


def test_paper_order_preview_whole_share_blocks_missing_price_and_sub_one_share():
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-whole-blocked",
        "intents": [
            {"symbol": "MSFT", "intent_type": "BUY", "trade_value": 1000, "allowed": True},
            {
                "symbol": "NVDA",
                "intent_type": "BUY",
                "trade_value": 100,
                "allowed": True,
                "promotion_review": _actionable_promotion("NVDA"),
            },
        ],
    }

    preview = build_paper_order_preview(
        plan,
        portfolio_state=state,
        profile=profile,
        order_model="whole_share",
        price_map={"NVDA": 910.0},
    )
    by_symbol = {row["symbol"]: row for row in preview["previews"]}

    assert by_symbol["MSFT"]["allowed"] is False
    assert "missing_price_for_whole_share_preview" in by_symbol["MSFT"]["blocked_reasons"]
    assert by_symbol["NVDA"]["allowed"] is False
    assert "whole_share_quantity_below_one" in by_symbol["NVDA"]["blocked_reasons"]


def test_paper_order_preview_splits_rebalance_into_sell_and_buy_legs():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=500,
        protected_symbols=["FXAIX"],
        holdings=[{"symbol": "AAPL", "market_value": 5000}],
    )
    plan = {
        "plan_id": "plan-3",
        "intents": [
            {
                "symbol": "NVDA",
                "source_symbol": "AAPL",
                "intent_type": "REBALANCE",
                "order_intent": "SELL_TO_FUND_BUY",
                "trade_value": 3600,
                "allowed": True,
                "decision_id": "decision-nvda",
                "risk_review": {"allowed": True, "risk_level": "medium"},
            }
        ],
    }

    preview = build_paper_order_preview(plan, portfolio_state=state, profile=profile)

    assert [row["side"] for row in preview["previews"]] == ["sell", "buy"]
    assert preview["previews"][0]["transaction_id"] == preview["previews"][1]["transaction_id"]
    assert preview["previews"][0]["symbol"] == "AAPL"
    assert preview["previews"][0]["paired_symbol"] == "NVDA"
    assert preview["previews"][1]["symbol"] == "NVDA"
    assert preview["previews"][1]["paired_symbol"] == "AAPL"
    assert all(row["allowed"] for row in preview["previews"])


def test_paper_order_preview_whole_share_rebalance_preview_does_not_crash():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=500,
        protected_symbols=["FXAIX"],
        holdings=[{"symbol": "AAPL", "market_value": 5000}],
    )
    plan = {
        "plan_id": "plan-whole-rebalance",
        "intents": [
            {
                "symbol": "NVDA",
                "source_symbol": "AAPL",
                "intent_type": "REBALANCE",
                "order_intent": "SELL_TO_FUND_BUY",
                "trade_value": 1800,
                "allowed": True,
                "decision_id": "decision-nvda",
            }
        ],
    }

    preview = build_paper_order_preview(
        plan,
        portfolio_state=state,
        profile=profile,
        order_model="whole_share",
        price_map={"AAPL": 200.0, "NVDA": 900.0},
    )

    assert [row["order_type"] for row in preview["previews"]] == [
        "market_quantity_preview",
        "market_quantity_preview",
    ]
    assert preview["previews"][0]["quantity"] == 9
    assert preview["previews"][1]["quantity"] == 2


def test_paper_order_preview_turns_review_and_blocked_intents_into_no_order_rows():
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=1000, protected_symbols=["FXAIX"])
    plan = {
        "intents": [
            {"symbol": "AAPL", "intent_type": "REVIEW", "allowed": True, "reason": "Review due."},
            {"symbol": "NVDA", "intent_type": "BLOCKED", "allowed": False, "reason": "Benchmark paused."},
        ]
    }

    preview = build_paper_order_preview(plan, portfolio_state=state, profile=profile)

    assert [row["side"] for row in preview["previews"]] == ["none", "none"]
    assert preview["previews"][0]["order_type"] == "no_order"
    assert preview["previews"][1]["allowed"] is False


def test_paper_order_preview_excludes_parking_intents_without_blocking_simple_buy():
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    plan = {
        "plan_id": "plan-parking",
        "intents": [
            {
                "symbol": "AMZN",
                "intent_type": "BUY",
                "trade_value": 850,
                "allowed": True,
                "promotion_review": _actionable_promotion("AMZN"),
            },
            {
                "symbol": "SPY",
                "intent_type": "PARK_IDLE_CASH",
                "order_intent": "BUY",
                "trade_value": 4150,
                "allowed": True,
                "reason": "Normal regime parking.",
            },
        ],
    }

    preview = build_paper_order_preview(plan, portfolio_state=state, profile=profile)

    by_symbol = {row["symbol"]: row for row in preview["previews"]}
    assert preview["allowed_count"] == 1
    assert preview["blocked_count"] == 0
    assert preview["no_order_count"] == 1
    assert by_symbol["AMZN"]["side"] == "buy"
    assert by_symbol["SPY"]["side"] == "none"
    assert by_symbol["SPY"]["order_type"] == "excluded_v1"
    assert "planning-only parking intent" in by_symbol["SPY"]["reason"].lower()


def test_paper_order_preview_cli_outputs_markdown_and_json(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"], "tradable_capital": 34000}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"], "holdings": []}), encoding="utf-8")
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "plan-cli",
                "intents": [
                    {
                        "symbol": "NVDA",
                        "intent_type": "BUY",
                        "order_intent": "BUY",
                        "trade_value": 1000,
                        "allowed": True,
                        "promotion_review": _actionable_promotion("NVDA"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    parser = build_parser()

    markdown_args = parser.parse_args(
        ["--profile-config", str(profile_path), "--portfolio-state", str(portfolio_path), "--action-plan", str(plan_path)]
    )
    assert run_cli(markdown_args) == 0
    assert "# Paper Order Preview" in capsys.readouterr().out

    json_args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--json",
        ]
    )
    assert run_cli(json_args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["previews"][0]["symbol"] == "NVDA"
    assert build_paper_order_preview_markdown(payload).startswith("# Paper Order Preview")

    price_map_path = tmp_path / "price_map.json"
    price_map_path.write_text(json.dumps({"NVDA": 500}), encoding="utf-8")
    whole_args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--order-model",
            "whole_share",
            "--price-map",
            str(price_map_path),
            "--json",
        ]
    )
    assert run_cli(whole_args) == 0
    whole_payload = json.loads(capsys.readouterr().out)
    assert whole_payload["previews"][0]["order_type"] == "market_quantity_preview"
    assert whole_payload["previews"][0]["quantity"] == 2
