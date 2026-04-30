import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.account_action_plan import AccountActionPlanBuilder
from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record(journal, symbol, recommendation="BUY", confidence=90, size=6, thesis="Good idea."):
    return journal.record_decision(
        create_research_packet_from_idea({"symbol": symbol, "benchmark_symbol": "FXAIX"}),
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


def test_account_action_plan_includes_rebalance_intent(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record(journal, "AAPL", recommendation="HOLD", confidence=65, size=4)
    _record(journal, "MSFT", recommendation="HOLD", confidence=80, size=4)
    _record(journal, "GOOG", recommendation="HOLD", confidence=82, size=4)
    _record(journal, "AMZN", recommendation="HOLD", confidence=84, size=4)
    _record(journal, "NVDA", recommendation="BUY", confidence=92, size=8)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
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
