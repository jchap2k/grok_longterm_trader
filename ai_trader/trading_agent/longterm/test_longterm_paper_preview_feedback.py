import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.next_actions import build_next_actions_markdown
from longterm.next_actions_cli import build_parser, run_cli
from longterm.journal_cli import build_parser as build_journal_parser
from longterm.journal_cli import run_cli as run_journal_cli
from longterm.paper_preview_status import PaperPreviewStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.report_builder import RecommendationTableBuilder
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def _record_decision(journal, symbol="NVDA", recommendation="BUY", confidence=92, size=8):
    return journal.record_decision(
        create_research_packet_from_idea(
            {
                "symbol": symbol,
                "company_name": symbol,
                "idea_source": "unit_test",
                "business_summary": "Durable business.",
                "benchmark_symbol": "FXAIX",
            }
        ),
        decision={
            "recommendation": recommendation,
            "confidence": confidence,
            "suggested_size_pct": size,
            "key_thesis": "Strong long-term setup.",
        },
        candidate_price=100,
        benchmark_price=100,
    )


def _preview_payload(decision_id, *, symbol="NVDA", allowed=True, side="buy"):
    return {
        "plan_id": "plan-1",
        "order_submission_enabled": False,
        "previews": [
            {
                "preview_id": f"preview-{symbol.lower()}",
                "plan_id": "plan-1",
                "decision_id": decision_id,
                "transaction_id": "",
                "trade_id": None,
                "symbol": symbol,
                "side": side,
                "order_type": "market_notional_preview" if side != "none" else "no_order",
                "notional": 1000 if side != "none" else 0,
                "allowed": allowed,
                "reason": "Preview passed." if allowed else "cash shortfall",
                "blocked_reasons": [] if allowed else ["cash shortfall"],
            }
        ],
    }


def test_paper_preview_status_builder_maps_by_decision_and_symbol(tmp_path):
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    preview_log_id = ledger.record_preview(_preview_payload("decision-1", symbol="NVDA"))

    status = PaperPreviewStatusBuilder(ledger).build()

    assert status.by_decision_id["decision-1"]["paper_preview_status"] == "ready"
    assert status.by_decision_id["decision-1"]["paper_preview_log_id"] == preview_log_id
    assert status.by_symbol["NVDA"]["paper_preview_ready_count"] == 1


def test_recommendation_table_hydrates_paper_preview_status(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(_preview_payload(decision_id))
    status = PaperPreviewStatusBuilder(ledger).build()

    rows = RecommendationTableBuilder(
        journal,
        paper_preview_status_by_decision=status.by_decision_id,
        paper_preview_status_by_symbol=status.by_symbol,
    ).build(limit=10)

    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["paper_preview_status"] == "ready"
    assert rows[0]["paper_preview_ready_count"] == 1
    assert rows[0]["paper_preview_id"] == "preview-nvda"


def test_markdown_report_and_journal_cli_include_paper_preview_status(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(_preview_payload(decision_id))
    parser = build_journal_parser()
    args = parser.parse_args(
        [
            "report",
            "--journal-db",
            str(journal.db_path),
            "--paper-ledger-db",
            str(ledger.db_path),
        ]
    )

    assert run_journal_cli(args) == 0
    output = capsys.readouterr().out

    assert "Paper Preview" in output
    assert "ready" in output
    assert "preview-nvda" in output


def test_next_actions_surfaces_ready_paper_preview_instead_of_plain_buy(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(_preview_payload(decision_id))
    status = PaperPreviewStatusBuilder(ledger).build()
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        paper_preview_status_by_decision=status.by_decision_id,
        paper_preview_status_by_symbol=status.by_symbol,
    )

    assert "paper_preview_ready" in markdown
    assert "BUY_PREVIEW_READY" in markdown
    assert "preview-nvda" in markdown
    assert "buy_candidate" not in markdown


def test_next_actions_surfaces_blocked_paper_preview(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(_preview_payload(decision_id, allowed=False))
    status = PaperPreviewStatusBuilder(ledger).build()
    profile = PortfolioProfile(tradable_capital=34000, protected_symbols=["FXAIX"])
    state = PortfolioState(cash=5000, protected_symbols=["FXAIX"])

    markdown = build_next_actions_markdown(
        journal,
        profile=profile,
        portfolio_state=state,
        paper_preview_status_by_decision=status.by_decision_id,
        paper_preview_status_by_symbol=status.by_symbol,
    )

    assert "paper_preview_blocked" in markdown
    assert "NEEDS_ATTENTION" in markdown
    assert "cash shortfall" in markdown


def test_next_actions_cli_accepts_paper_ledger_db(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    decision_id = _record_decision(journal)
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    ledger.record_preview(_preview_payload(decision_id))
    profile_path = tmp_path / "profile.json"
    portfolio_path = tmp_path / "portfolio.json"
    profile_path.write_text(json.dumps({"tradable_capital": 34000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"], "holdings": []}), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_path),
            "--paper-ledger-db",
            str(ledger.db_path),
        ]
    )

    assert run_cli(args) == 0
    assert "paper_preview_ready" in capsys.readouterr().out
