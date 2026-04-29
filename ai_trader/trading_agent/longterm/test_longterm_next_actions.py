import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.next_actions import NextActionsPlanner, build_next_actions_markdown
from longterm.next_actions_cli import build_parser, run_cli
from longterm.portfolio_state import PortfolioState
from longterm.rebalance_planner import RebalancePlanner
from longterm.review_status import ReviewStatusBuilder
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol, recommendation="BUY", confidence=88, size=6, thesis="Good idea."):
    return journal.record_decision(
        create_research_packet_from_idea({"symbol": symbol, "benchmark_symbol": "FXAIX"}),
        decision={
            "recommendation": recommendation,
            "confidence": confidence,
            "suggested_size_pct": size,
            "key_thesis": thesis,
            "info_link": f"https://example.com/{symbol.lower()}",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def test_benchmark_guard_recommends_benchmark_when_active_lags_materially():
    guard = BenchmarkGuard(min_excess_return_pct=0.0, min_decisions=3)
    summary = {
        "evaluated_decisions": 4,
        "average_excess_return_pct": -4.25,
        "decisions_beating_benchmark": 1,
    }

    result = guard.evaluate(summary)

    assert result.should_pause_new_buys is True
    assert "FXAIX" in result.reason


def test_rebalance_planner_prefers_better_ranked_candidate_over_weaker_holding():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=250,
        holdings=[
            {"symbol": "AAPL", "market_value": 5000},
            {"symbol": "FXAIX", "market_value": 34000},
        ],
        protected_symbols=["FXAIX"],
    )
    recommendations = [
        {"symbol": "NVDA", "rank": 1, "confidence": 92, "suggested_size_pct": 8, "reason": "Stronger edge."},
        {"symbol": "AAPL", "rank": 8, "confidence": 65, "suggested_size_pct": 4, "reason": "Lower conviction."},
    ]

    proposal = RebalancePlanner().propose(
        recommendations,
        profile=profile,
        portfolio_state=state,
    )

    assert proposal.should_rebalance is True
    assert proposal.fund_from_symbol == "AAPL"
    assert proposal.target_symbol == "NVDA"
    assert proposal.proposed_sell_value == 3640.0


def test_rebalance_planner_blocks_new_rotation_when_benchmark_gate_pauses():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=250,
        holdings=[{"symbol": "AAPL", "market_value": 5000}],
        protected_symbols=["FXAIX"],
    )
    recommendations = [
        {"symbol": "NVDA", "rank": 1, "confidence": 92, "suggested_size_pct": 8},
        {"symbol": "AAPL", "rank": 8, "confidence": 65, "suggested_size_pct": 4},
    ]
    guard_result = BenchmarkGuard(min_decisions=3).evaluate(
        {
            "evaluated_decisions": 3,
            "average_excess_return_pct": -3.0,
            "decisions_beating_benchmark": 0,
        }
    )

    proposal = RebalancePlanner().propose(
        recommendations,
        profile=profile,
        portfolio_state=state,
        benchmark_guard_result=guard_result,
    )

    assert proposal.should_rebalance is False
    assert proposal.target_symbol == "NVDA"
    assert "pause new buys" in proposal.reason.lower()


def test_next_actions_planner_builds_prioritized_actions(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA", confidence=92, size=8, thesis="AI leader.")
    _record_decision(journal, "AAPL", confidence=72, size=4, thesis="Durable but lower conviction.")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=5000,
        holdings=[{"symbol": "AAPL", "market_value": 5000}, {"symbol": "FXAIX", "market_value": 34000}],
        protected_symbols=["FXAIX"],
    )

    actions = NextActionsPlanner().plan(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert actions[0].symbol == "NVDA"
    assert actions[0].category == "buy_candidate"
    assert actions[0].priority == 1
    assert any(action.category == "review_holding" and action.symbol == "AAPL" for action in actions)


def test_next_actions_planner_pauses_new_buy_candidates_when_benchmark_gate_blocks(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA", confidence=92, size=8, thesis="AI leader.")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])
    guard_result = BenchmarkGuard(min_decisions=3).evaluate(
        {
            "evaluated_decisions": 3,
            "average_excess_return_pct": -2.5,
            "decisions_beating_benchmark": 0,
        }
    )

    actions = NextActionsPlanner().plan(
        journal,
        profile=profile,
        portfolio_state=state,
        benchmark_guard_result=guard_result,
    )

    assert actions[0].symbol == "NVDA"
    assert actions[0].category == "paused_buy_candidate"
    assert actions[0].action == "PAUSED"
    assert "fxaix" in actions[0].reason.lower()


def test_next_actions_planner_surfaces_capital_needed_alert(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA", confidence=92, size=8, thesis="AI leader.")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=500, protected_symbols=["FXAIX"])

    actions = NextActionsPlanner().plan(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert actions[0].symbol == "NVDA"
    assert actions[0].category == "capital_needed"
    assert actions[0].action == "ALERT"
    assert "$2,220.00" in actions[0].reason


def test_next_actions_planner_uses_recommendation_table_builder_rows(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA", confidence=92, size=8, thesis="AI leader.")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    actions = NextActionsPlanner(
        review_status_by_symbol={"NVDA": {"review_due": True}}
    ).plan(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert actions[0].symbol == "NVDA"
    assert "review due" in actions[0].reason.lower()


def test_next_actions_markdown_includes_table_and_benchmark_gate(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    first = _record_decision(journal, "NVDA", confidence=90, size=8)
    second = _record_decision(journal, "AAPL", confidence=80, size=6)
    third = _record_decision(journal, "MSFT", confidence=78, size=5)
    journal.update_outcome(first, candidate_price=90, benchmark_price=110)
    journal.update_outcome(second, candidate_price=95, benchmark_price=105)
    journal.update_outcome(third, candidate_price=97, benchmark_price=102)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        benchmark_guard=BenchmarkGuard(min_decisions=3),
    )

    assert "# Long-Term Next Actions" in markdown
    assert "Pause new buys" in markdown
    assert "paused_buy_candidate" in markdown
    assert "| Priority | Category | Symbol | Action | Reason |" in markdown


def test_review_status_builder_marks_due_reviews_from_journal(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "MSFT", confidence=83)

    statuses = ReviewStatusBuilder(
        journal,
        today=date(2026, 4, 29),
        last_review_dates_by_symbol={"MSFT": date(2026, 3, 20)},
    ).build(limit=5)

    assert statuses["MSFT"]["review_due"] is True
    assert statuses["MSFT"]["days_since_review"] == 40
    assert statuses["MSFT"]["thesis_state"] == "healthy"


def test_decision_journal_can_list_review_candidates_since_date(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "AAPL", confidence=80)

    candidates = journal.list_review_candidates(limit=5)

    assert candidates[0]["symbol"] == "AAPL"
    assert "packet_json" in candidates[0]
    assert "decision_json" in candidates[0]


def test_next_actions_cli_outputs_markdown(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "NVDA", confidence=90, size=8)
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "holdings": []}), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(tmp_path / "journal.db"),
            "--portfolio-state",
            str(portfolio_path),
        ]
    )

    exit_code = run_cli(args)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "# Long-Term Next Actions" in output
    assert "NVDA" in output
