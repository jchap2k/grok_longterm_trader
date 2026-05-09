import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.account_action_plan import AccountActionPlanBuilder
from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.idle_cash_policy import MarketRegimeSnapshot
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _evidence_brief(symbol, *, warnings=""):
    lines = [
        f"research_evidence_brief_v1 | {symbol}",
        "Fundamentals: durable growth and acceptable leverage.",
        "Article evidence: primary-company article (source Reuters, confidence 0.8, basis snippet_grounded).",
        "Grok catalyst synthesis: long-term catalyst remains intact.",
    ]
    if warnings:
        lines.append(f"Warnings: {warnings}")
    return "\n".join(lines)


def _record(
    journal,
    symbol,
    recommendation="BUY",
    confidence=90,
    size=6,
    thesis="Good idea.",
    evidence_warnings="",
):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "benchmark_symbol": "FXAIX",
                "evidence_brief": _evidence_brief(symbol, warnings=evidence_warnings),
            }
        ),
        decision={
            "recommendation": recommendation,
            "confidence": confidence,
            "suggested_size_pct": size,
            "key_thesis": thesis,
        },
        candidate_price=100,
        benchmark_price=100,
    )


def test_account_action_plan_builds_allowed_buy_intent(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record(journal, "NVDA", recommendation="BUY", confidence=91, size=8)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder(generated_at_func=lambda: "2026-04-30T00:00:00+00:00").build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    payload = plan.to_dict()
    assert payload["schema_version"] == 1
    assert payload["mode"] == "dry_run"
    assert payload["status"] == "ready"
    assert payload["intents"][0]["intent_type"] == "BUY"
    assert payload["intents"][0]["symbol"] == "NVDA"
    assert payload["intents"][0]["order_intent"] == "BUY"
    assert payload["intents"][0]["trade_value"] == 2720.0
    assert payload["intents"][0]["allowed"] is True
    assert payload["intents"][0]["decision_id"] == decision_id
    assert payload["intents"][0]["risk_review"]["allowed"] is True
    assert payload["intents"][0]["risk_review"]["risk_level"] in {"low", "medium"}
    assert payload["intents"][0]["risk_review"]["buy_promotion"]["promotion_decision"] == "ACTIONABLE_BUY"


def test_account_action_plan_uses_staged_entry_size_when_margin_is_moderate(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = create_research_packet_from_idea(
        {
            "symbol": "ADBE",
            "benchmark_symbol": "FXAIX",
            "quality_score": 88,
            "valuation_score": 50,
            "evidence_brief": (
                "research_evidence_brief_v1 | ADBE\n"
                "Fundamentals: durable growth and acceptable leverage.\n"
                "Article evidence: primary-company article (source Reuters, confidence 0.8, basis snippet_grounded).\n"
                "Grok catalyst synthesis: long-term catalyst remains intact."
            ),
        }
    )
    journal.record_decision(
        packet,
        decision={
            "recommendation": "BUY",
            "confidence": 88,
            "suggested_size_pct": 6,
            "key_thesis": "Creative cloud durability.",
        },
        candidate_price=100,
        benchmark_price=100,
    )
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    buy = plan.to_dict()["intents"][0]
    assert buy["intent_type"] == "BUY"
    assert buy["trade_value"] == 680.0
    assert buy["target_value"] == 680.0
    assert buy["promotion_review"]["staged_entry_label"] == "starter_position"
    assert "staged" in buy["reason"].lower()


def test_account_action_plan_pauses_new_buy_but_keeps_review_intent(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    first = _record(journal, "NVDA", recommendation="BUY", confidence=91, size=8)
    second = _record(journal, "AAPL", recommendation="HOLD", confidence=80, size=4)
    journal.update_outcome(first, candidate_price=90, benchmark_price=110)
    journal.update_outcome(second, candidate_price=95, benchmark_price=105)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=5000,
        holdings=[{"symbol": "AAPL", "market_value": 3000}],
        protected_symbols=["FXAIX"],
    )

    plan = AccountActionPlanBuilder(benchmark_guard=BenchmarkGuard(min_decisions=2)).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert plan.status == "blocked"
    assert [intent.intent_type for intent in plan.intents] == ["BLOCKED", "REVIEW"]
    assert plan.intents[0].symbol == "NVDA"
    assert plan.intents[0].allowed is False
    assert "Pause new buys" in plan.intents[0].reason
    assert plan.intents[0].risk_review["allowed"] is False
    assert plan.intents[0].risk_review["veto_reasons"]
    assert plan.intents[1].symbol == "AAPL"


def test_account_action_plan_suppresses_capital_needed_when_active_sell_can_fund(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AAPL", recommendation="SELL", confidence=88, size=0)
    _record(journal, "NVDA", recommendation="BUY", confidence=91, size=8)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=500,
        holdings=[{"symbol": "AAPL", "market_value": 3000}],
        protected_symbols=["FXAIX"],
    )

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert plan.status == "blocked"
    assert plan.intents[0].intent_type == "BLOCKED"
    assert plan.intents[0].symbol == "NVDA"
    assert "fund the stronger idea" in plan.intents[0].reason
    assert not any(intent.intent_type == "CAPITAL_NEEDED" for intent in plan.intents)


def test_account_action_plan_surfaces_explicit_sell_for_held_active_position(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record(journal, "AAPL", recommendation="SELL", confidence=88, size=0)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=500,
        holdings=[{"symbol": "AAPL", "market_value": 3000}],
        protected_symbols=["FXAIX"],
    )

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    sell = [intent for intent in plan.intents if intent.symbol == "AAPL"][0]
    assert sell.intent_type == "SELL"
    assert sell.order_intent == "SELL"
    assert sell.trade_value == 3000.0
    assert sell.target_value == 0.0
    assert sell.allowed is True
    assert sell.decision_id == decision_id
    assert sell.risk_review["intent_type"] == "SELL"
    assert "active sleeve" in sell.reason.lower()


def test_account_action_plan_routes_pending_evidence_buy_to_review_not_buy(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(
        journal,
        "VEEV",
        recommendation="BUY",
        confidence=75,
        size=3,
        evidence_warnings="missing_earnings_article",
    )
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert [intent.intent_type for intent in plan.intents] == ["REVIEW"]
    assert plan.intents[0].symbol == "VEEV"
    assert plan.intents[0].order_intent == "NONE"
    assert plan.intents[0].risk_review["buy_promotion"]["promotion_decision"] == "WATCHLIST_PENDING_EVIDENCE"
    assert "missing_earnings_article" in plan.intents[0].reason


def test_account_action_plan_includes_rebalance_intent(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AAPL", recommendation="HOLD", confidence=65, size=4)
    _record(journal, "MSFT", recommendation="HOLD", confidence=80, size=4)
    _record(journal, "GOOG", recommendation="HOLD", confidence=82, size=4)
    _record(journal, "AMZN", recommendation="HOLD", confidence=84, size=4)
    _record(journal, "NVDA", recommendation="BUY", confidence=92, size=8)
    profile = PortfolioProfile(account_strategy_mode="roth_ira", tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=500,
        holdings=[{"symbol": "AAPL", "market_value": 5000}],
        protected_symbols=["FXAIX"],
    )

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    rebalance = [intent for intent in plan.intents if intent.intent_type == "REBALANCE"][0]
    assert rebalance.symbol == "NVDA"
    assert rebalance.source_symbol == "AAPL"
    assert rebalance.order_intent == "SELL_TO_FUND_BUY"
    assert rebalance.trade_value == 3640.0
    assert rebalance.allowed is True
    assert rebalance.risk_review["intent_type"] == "REBALANCE"


def test_account_action_plan_suppresses_broad_rebalance_for_taxable_profile(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AAPL", recommendation="HOLD", confidence=65, size=4)
    _record(journal, "MSFT", recommendation="HOLD", confidence=80, size=4)
    _record(journal, "GOOG", recommendation="HOLD", confidence=82, size=4)
    _record(journal, "AMZN", recommendation="HOLD", confidence=84, size=4)
    _record(journal, "NVDA", recommendation="BUY", confidence=92, size=8)
    profile = PortfolioProfile(account_strategy_mode="taxable", tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=500,
        holdings=[{"symbol": "AAPL", "market_value": 5000}],
        protected_symbols=["FXAIX"],
    )

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert not any(intent.intent_type == "REBALANCE" for intent in plan.intents)
    assert plan.status == "blocked"
    assert "taxable_broad_rebalance_suppressed" in plan.suppressed_reasons


def test_account_action_plan_blocks_protected_symbol_trade(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "FXAIX", recommendation="BUY", confidence=99, size=10)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=5000,
        holdings=[{"symbol": "FXAIX", "market_value": 34000}],
        protected_symbols=["FXAIX"],
    )

    plan = AccountActionPlanBuilder().build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert plan.status == "blocked"
    assert plan.intents[0].intent_type == "BLOCKED"
    assert plan.intents[0].symbol == "FXAIX"
    assert plan.intents[0].allowed is False
    assert "protected" in plan.intents[0].reason.lower()
    assert any("protected" in reason.lower() for reason in plan.intents[0].risk_review["veto_reasons"])


def test_account_action_plan_parks_leftover_cash_in_spy_during_normal_regime(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AMZN", recommendation="BUY", confidence=78, size=2.5)
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        defensive_parking_symbol="SPY",
    )
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder(
        market_regime=MarketRegimeSnapshot(risk_regime="normal")
    ).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert [intent.intent_type for intent in plan.intents] == ["BUY", "PARK_IDLE_CASH"]
    assert plan.intents[0].symbol == "AMZN"
    assert plan.intents[0].trade_value == 850.0
    parking = plan.intents[1]
    assert parking.symbol == "SPY"
    assert parking.order_intent == "BUY"
    assert parking.trade_value == 4150.0
    assert parking.allowed is True
    assert "idle active cash" in parking.reason


def test_account_action_plan_suppresses_idle_cash_parking_for_taxable_profile(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AMZN", recommendation="BUY", confidence=78, size=2.5)
    profile = PortfolioProfile(
        account_strategy_mode="taxable",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        defensive_parking_symbol="SPY",
    )
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder(
        market_regime=MarketRegimeSnapshot(risk_regime="normal")
    ).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert [intent.intent_type for intent in plan.intents] == ["BUY"]
    assert plan.status == "ready"
    assert "taxable_broad_parking_suppressed" in plan.suppressed_reasons
    assert "taxable_broad_parking_suppressed" not in plan.blocked_reasons


def test_account_action_plan_caps_parking_to_active_sleeve_budget(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AMZN", recommendation="BUY", confidence=78, size=2.5)
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        total_account_value=74000,
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        defensive_parking_symbol="SPY",
    )
    state = PortfolioState(cash=74000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder(
        market_regime=MarketRegimeSnapshot(risk_regime="normal")
    ).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    parking = [intent for intent in plan.intents if intent.intent_type == "PARK_IDLE_CASH"][0]
    assert plan.intents[0].trade_value == 850.0
    assert parking.trade_value == 33150.0


def test_account_action_plan_splits_idle_cash_when_uncertainty_is_elevated(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AMZN", recommendation="BUY", confidence=78, size=2.5)
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        defensive_parking_symbol="SPY",
        low_risk_parking_symbol="SGOV",
    )
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    plan = AccountActionPlanBuilder(
        market_regime=MarketRegimeSnapshot(risk_regime="elevated_uncertainty")
    ).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    parking = [intent for intent in plan.intents if intent.intent_type == "PARK_IDLE_CASH"]
    assert [(intent.symbol, intent.trade_value) for intent in parking] == [
        ("SPY", 2075.0),
        ("SGOV", 2075.0),
    ]


def test_account_action_plan_uses_sgov_not_tlt_when_vix_spikes_with_rising_yields(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AMZN", recommendation="BUY", confidence=78, size=2.5)
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        defensive_parking_symbol="SPY",
        low_risk_parking_symbol="SGOV",
        duration_hedge_symbol="TLT",
    )
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    regime = MarketRegimeSnapshot.from_signals(
        vix_level=35,
        spy_above_200d=False,
        ten_year_yield_trend="rising",
    )

    plan = AccountActionPlanBuilder(market_regime=regime).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    defensive = [intent for intent in plan.intents if intent.intent_type == "PARK_DEFENSIVE_CASH"]
    assert regime.risk_regime == "inflation_rate_shock"
    assert [(intent.symbol, intent.trade_value) for intent in defensive] == [("SGOV", 4150.0)]
    assert "TLT" not in [intent.symbol for intent in plan.intents]


def test_account_action_plan_caps_tlt_when_equity_panic_has_falling_yields(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AMZN", recommendation="BUY", confidence=78, size=2.5)
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=34000,
        protected_symbols=["FXAIX"],
        low_risk_parking_symbol="SGOV",
        duration_hedge_symbol="TLT",
    )
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    regime = MarketRegimeSnapshot.from_signals(
        vix_level=35,
        spy_above_200d=False,
        ten_year_yield_trend="falling",
    )

    plan = AccountActionPlanBuilder(market_regime=regime).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    defensive = [intent for intent in plan.intents if intent.intent_type == "PARK_DEFENSIVE_CASH"]
    assert regime.risk_regime == "equity_panic_falling_rates"
    assert [(intent.symbol, intent.trade_value) for intent in defensive] == [
        ("SGOV", 2905.0),
        ("TLT", 1245.0),
    ]


def test_account_action_plan_tightens_new_buy_size_under_macro_pressure(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "MSFT", recommendation="BUY", confidence=82, size=6.0)
    profile = PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=50000,
        protected_symbols=["FXAIX"],
        defensive_parking_symbol="SPY",
        low_risk_parking_symbol="SGOV",
    )
    state = PortfolioState(cash=10000, protected_symbols=["FXAIX"])
    regime = MarketRegimeSnapshot(
        risk_regime="normal",
        provider_status="ok",
        provider_mode="fredapi",
        yield_curve_spread=-0.2,
        credit_spread=5.5,
    )

    plan = AccountActionPlanBuilder(market_regime=regime).build(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    buy = [intent for intent in plan.intents if intent.symbol == "MSFT" and intent.intent_type == "BUY"][0]
    assert buy.trade_value == 1500.0
    assert "macro regime caution" in buy.reason.lower()
    assert buy.risk_review["macro_regime"]["sizing_caution"] == "tighten_new_buy_sizing"
