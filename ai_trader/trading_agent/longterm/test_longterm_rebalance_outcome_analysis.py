import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.rebalance_outcome_analysis import (
    RebalanceOutcomeAnalyzer,
    build_rebalance_outcome_markdown,
)
from longterm.rebalance_outcome_analysis_cli import build_parser, run_cli
from longterm.review_status import review_risk_bucket
from research.intake import create_research_packet_from_idea


def _record_decision(
    journal,
    symbol,
    *,
    confidence=80,
    recommendation="BUY",
    candidate_price=100,
    benchmark_price=100,
):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "company_name": symbol,
                "idea_source": "unit_test",
                "business_summary": "Durable business under review.",
                "benchmark_symbol": "FXAIX",
            }
        ),
        decision={
            "recommendation": recommendation,
            "confidence": confidence,
            "suggested_size_pct": 5,
            "key_thesis": "Durable compounder if thesis holds.",
        },
        candidate_price=candidate_price,
        benchmark_price=benchmark_price,
    )


def test_review_risk_bucket_is_shared_for_rebalance_and_outcome_analysis():
    assert review_risk_bucket({"review_due": False, "thesis_state": "healthy"}) == "healthy"
    assert review_risk_bucket({"review_due": True, "thesis_state": "healthy"}) == "review_due"
    assert review_risk_bucket({"review_due": True, "thesis_state": "stale"}) == "stale"
    assert review_risk_bucket({"review_due": False, "thesis_state": "weakening"}) == "weakening"
    assert review_risk_bucket({"review_due": False, "thesis_state": "broken"}) == "broken"
    assert review_risk_bucket({}) == "unreviewed"


def test_rebalance_outcome_analyzer_summarizes_excess_return_by_thesis_bucket(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    weak_id = _record_decision(journal, "AAPL", confidence=80)
    healthy_id = _record_decision(journal, "MSFT", confidence=90)
    pending_id = _record_decision(journal, "NVDA", confidence=95)
    journal.update_outcome(weak_id, candidate_price=94, benchmark_price=100)
    journal.update_outcome(healthy_id, candidate_price=112, benchmark_price=104)
    journal.record_thesis_review(
        symbol="AAPL",
        thesis_state="weakening",
        evidence=["Margin pressure and slowing growth."],
        decision_id=weak_id,
    )
    journal.record_thesis_review(
        symbol="MSFT",
        thesis_state="healthy",
        evidence=["Thesis remains intact."],
        decision_id=healthy_id,
    )

    report = RebalanceOutcomeAnalyzer(journal).build(limit=10)
    buckets = {bucket.bucket: bucket for bucket in report.bucket_summaries}

    assert report.evaluated_decisions == 2
    assert report.pending_outcomes == 1
    assert buckets["weakening"].evaluated_count == 1
    assert buckets["weakening"].average_excess_return_pct == -6.0
    assert buckets["weakening"].beat_rate_pct == 0.0
    assert buckets["healthy"].average_excess_return_pct == 8.0
    assert buckets["healthy"].confidence_weighted_excess_return_pct == 8.0
    assert buckets["unreviewed"].pending_count == 1
    assert pending_id


def test_rebalance_outcome_markdown_flags_buckets_that_support_review_adjustments(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    weak_id = _record_decision(journal, "AAPL", confidence=80)
    journal.update_outcome(weak_id, candidate_price=94, benchmark_price=100)
    journal.record_thesis_review(symbol="AAPL", thesis_state="weakening", decision_id=weak_id)

    markdown = build_rebalance_outcome_markdown(RebalanceOutcomeAnalyzer(journal).build())

    assert "# Rebalance Outcome Analysis" in markdown
    assert "| weakening | 1 | 0 | -6.00% | 0.00% | -6.00% |" in markdown
    assert "supports giving review-risk holdings a higher source score" in markdown


def test_rebalance_outcome_cli_outputs_json(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal, "MSFT", confidence=90)
    journal.update_outcome(decision_id, candidate_price=112, benchmark_price=104)
    parser = build_parser()
    args = parser.parse_args(["--journal-db", str(journal.db_path), "--json"])

    exit_code = run_cli(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["evaluated_decisions"] == 1
    assert payload["bucket_summaries"][0]["bucket"] == "unreviewed"
