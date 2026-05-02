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


def _record_decision(
    journal,
    symbol,
    recommendation="BUY",
    confidence=88,
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
        benchmark_guard_result=BenchmarkGuard().evaluate(
            {
                "evaluated_decisions": 5,
                "average_excess_return_pct": 2.0,
                "decisions_beating_benchmark": 4,
            }
        ),
    )

    assert proposal.should_rebalance is True
    assert proposal.fund_from_symbol == "AAPL"
    assert proposal.target_symbol == "NVDA"
    assert proposal.proposed_sell_value == 3640.0
    assert proposal.source_current_value == 5000.0
    assert proposal.source_target_value == 1360.0
    assert proposal.source_rank == 8
    assert proposal.target_rank == 1
    assert proposal.rank_gap == 7
    assert proposal.target_suggested_size_pct == 8.0
    assert proposal.source_decision_id == ""
    assert proposal.target_decision_id == ""
    assert "Active sleeve is clearing" in proposal.benchmark_guard_reason


def test_rebalance_planner_exposes_decision_traceability():
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
        {
            "decision_id": "decision-nvda-full",
            "symbol": "NVDA",
            "rank": 1,
            "confidence": 92,
            "suggested_size_pct": 8,
        },
        {
            "decision_id": "decision-aapl-full",
            "symbol": "AAPL",
            "rank": 8,
            "confidence": 65,
            "suggested_size_pct": 4,
        },
    ]

    proposal = RebalancePlanner().propose(
        recommendations,
        profile=profile,
        portfolio_state=state,
    )

    assert proposal.source_decision_id == "decision-aapl-full"
    assert proposal.target_decision_id == "decision-nvda-full"


def test_rebalance_planner_exposes_review_status_context():
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
        {"symbol": "NVDA", "rank": 1, "confidence": 92, "suggested_size_pct": 8},
        {"symbol": "AAPL", "rank": 8, "confidence": 65, "suggested_size_pct": 4},
    ]

    proposal = RebalancePlanner().propose(
        recommendations,
        profile=profile,
        portfolio_state=state,
        review_status_by_symbol={
            "NVDA": {"review_due": False, "thesis_state": "healthy"},
            "AAPL": {"review_due": True, "thesis_state": "stale"},
        },
    )

    assert proposal.source_review_due is True
    assert proposal.source_thesis_state == "stale"
    assert proposal.target_review_due is False
    assert proposal.target_thesis_state == "healthy"


def test_rebalance_planner_prefers_review_risk_source_over_raw_weakest_rank():
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=250,
        holdings=[
            {"symbol": "AAPL", "market_value": 5000},
            {"symbol": "MSFT", "market_value": 5000},
            {"symbol": "FXAIX", "market_value": 34000},
        ],
        protected_symbols=["FXAIX"],
    )
    recommendations = [
        {"symbol": "NVDA", "rank": 1, "confidence": 92, "suggested_size_pct": 8},
        {"symbol": "MSFT", "rank": 6, "confidence": 70, "suggested_size_pct": 4},
        {"symbol": "AAPL", "rank": 8, "confidence": 65, "suggested_size_pct": 4},
    ]

    proposal = RebalancePlanner().propose(
        recommendations,
        profile=profile,
        portfolio_state=state,
        review_status_by_symbol={
            "MSFT": {"review_due": True, "thesis_state": "stale"},
            "AAPL": {"review_due": False, "thesis_state": "healthy"},
        },
    )

    assert proposal.should_rebalance is True
    assert proposal.fund_from_symbol == "MSFT"
    assert proposal.source_rank == 6
    assert proposal.source_review_adjustment == 3
    assert proposal.source_rebalance_score == 9
    assert proposal.rebalance_score_gap == 8
    assert "review risk" in proposal.reason.lower()


def test_rebalance_planner_never_sources_protected_holdings():
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
        {"symbol": "NVDA", "rank": 1, "confidence": 92, "suggested_size_pct": 8},
        {"symbol": "FXAIX", "rank": 99, "confidence": 10, "suggested_size_pct": 0},
        {"symbol": "AAPL", "rank": 2, "confidence": 80, "suggested_size_pct": 4},
    ]

    proposal = RebalancePlanner().propose(
        recommendations,
        profile=profile,
        portfolio_state=state,
        min_rank_gap=3,
    )

    assert proposal.should_rebalance is False
    assert proposal.fund_from_symbol == "AAPL"
    assert proposal.fund_from_symbol != "FXAIX"


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


def test_next_actions_planner_routes_pending_evidence_buy_to_enrichment(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(
        journal,
        "VEEV",
        confidence=75,
        size=3,
        thesis="Vertical SaaS quality but earnings evidence is thin.",
        evidence_warnings="missing_earnings_article",
    )
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    actions = NextActionsPlanner().plan(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert actions[0].symbol == "VEEV"
    assert actions[0].category == "buy_promotion_pending_evidence"
    assert actions[0].action == "ENRICH"
    assert "missing_earnings_article" in actions[0].reason


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


def test_next_actions_markdown_can_auto_include_review_due_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "MSFT", confidence=83, size=5)
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        review_status_today=date(2026, 4, 29),
        last_review_dates_by_symbol={"MSFT": date(2026, 3, 20)},
    )

    assert "MSFT" in markdown
    assert "Review due before committing new capital." in markdown


def test_next_actions_markdown_includes_deferred_research_queue(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        deferred_research_queue=[
            {
                "symbol": "TSLA",
                "missing_fields": ["company_name", "idea_source", "research_context"],
                "provenance_bucket": "manual",
                "suggested_next_step": "enrich_candidate_before_research",
                "suggested_enrichment_command": (
                    "python scripts/run_longterm_discovery.py --candidates path\\to\\candidates.json"
                ),
            }
        ],
    )

    assert "## Deferred Research Queue" in markdown
    assert "| TSLA | company_name, idea_source, research_context | manual | enrich_candidate_before_research |" in markdown
    assert "python scripts/run_longterm_discovery.py" in markdown


def test_next_actions_markdown_includes_account_action_plan_parking_intents(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=33150, protected_symbols=["FXAIX"])

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        account_action_plan={
            "intents": [
                {
                    "symbol": "SPY",
                    "intent_type": "PARK_IDLE_CASH",
                    "order_intent": "BUY",
                    "trade_value": 33150.0,
                    "allowed": True,
                    "reason": "Normal regime parking.",
                }
            ]
        },
    )

    assert "## Account Action Plan Intents" in markdown
    assert "| SPY | PARK_IDLE_CASH | BUY | $33,150.00 | yes | Normal regime parking. |" in markdown


def test_next_actions_cli_includes_persisted_deferred_research_items(tmp_path, capsys):
    journal_db = tmp_path / "journal.db"
    journal = LongTermDecisionJournal(journal_db)
    journal.record_deferred_research_item(
        {
            "symbol": "TSLA",
            "missing_fields": ["company_name"],
            "provenance_bucket": "manual",
            "suggested_next_step": "enrich_candidate_before_research",
        }
    )
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"cash": 5000, "holdings": [], "protected_symbols": ["FXAIX"]}),
        encoding="utf-8",
    )
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps({"tradable_capital": 34000, "protected_symbols": ["FXAIX"]}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal_db),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
        ]
    )

    exit_code = run_cli(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "## Deferred Research Queue" in output
    assert "TSLA" in output


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
    assert statuses["MSFT"]["thesis_state"] == "stale"


def test_review_status_builder_uses_recorded_thesis_review_as_last_review(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "MSFT", confidence=83)
    journal.record_thesis_review(
        symbol="MSFT",
        thesis_state="healthy",
        status="reviewed",
        review_notes="Reviewed after earnings; thesis remains intact.",
        evidence=["Cloud growth remains durable"],
    )

    statuses = ReviewStatusBuilder(
        journal,
        today=date.today(),
    ).build(limit=5)

    assert statuses["MSFT"]["review_due"] is False
    assert statuses["MSFT"]["thesis_state"] == "healthy"
    assert statuses["MSFT"]["days_since_review"] == 0


def test_review_status_builder_preserves_recorded_broken_review_until_new_decision(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "MSFT", confidence=83)
    journal.record_thesis_review(
        symbol="MSFT",
        thesis_state="broken",
        status="reviewed",
        review_notes="Invalidation condition was confirmed.",
        evidence=["Cloud growth materially slows"],
    )

    statuses = ReviewStatusBuilder(journal, today=date.today()).build(limit=5)

    assert statuses["MSFT"]["thesis_state"] == "broken"
    assert "latest recorded thesis review" in statuses["MSFT"]["review_reason"].lower()


def test_next_actions_elevates_broken_held_thesis_to_urgent_review(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    _record_decision(journal, "AAPL", confidence=72, size=4, thesis="Durable but lower conviction.")
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(
        cash=5000,
        holdings=[{"symbol": "AAPL", "market_value": 5000}],
        protected_symbols=["FXAIX"],
    )

    actions = NextActionsPlanner(
        review_status_by_symbol={
            "AAPL": {
                "review_due": False,
                "thesis_state": "broken",
                "review_reason": "Latest recorded thesis review marked the thesis broken.",
            }
        }
    ).plan(
        journal,
        profile=profile,
        portfolio_state=state,
    )

    assert actions[0].symbol == "AAPL"
    assert actions[0].category == "urgent_review_holding"
    assert "broken" in actions[0].reason.lower()


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
