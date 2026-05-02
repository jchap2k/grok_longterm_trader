import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_execution import (
    AlpacaPaperSubmitAdapter,
    PaperExecutionBoundary,
    build_paper_execution_markdown,
    deterministic_client_order_id,
)
from longterm.paper_execution_cli import build_parser, run_cli
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea
from longterm.paper_runbook_check import hash_action_plan


class FakePaperBroker:
    def __init__(self, *, status="pending_new", order_id="paper-order-1", fail=False):
        self.status = status
        self.order_id = order_id
        self.fail = fail
        self.calls = []

    def submit_notional_order(self, *, symbol, side, notional, client_order_id, time_in_force):
        self.calls.append(
            {
                "order_model": "notional",
                "symbol": symbol,
                "side": side,
                "notional": notional,
                "client_order_id": client_order_id,
                "time_in_force": time_in_force,
            }
        )
        if self.fail:
            raise TimeoutError("network timeout")
        return {"id": self.order_id, "status": self.status}

    def submit_quantity_order(self, *, symbol, side, quantity, client_order_id, time_in_force):
        self.calls.append(
            {
                "order_model": "quantity",
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "client_order_id": client_order_id,
                "time_in_force": time_in_force,
            }
        )
        if self.fail:
            raise TimeoutError("network timeout")
        return {"id": self.order_id, "status": self.status}


def _record_decision(
    journal,
    *,
    symbol="NVDA",
    recommendation="BUY",
    confidence=92,
    candidate_price=100,
    benchmark_price=100,
):
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
            "suggested_size_pct": 5,
            "key_thesis": "Strong long-term setup.",
        },
        candidate_price=candidate_price,
        benchmark_price=benchmark_price,
    )


def _action_plan(decision_id, *, symbol="NVDA", intent_type="BUY", allowed=True, trade_value=1000):
    return {
        "schema_version": 1,
        "plan_id": "plan-1",
        "mode": "dry_run",
        "status": "ready",
        "benchmark_gate_reason": "Benchmark gate allows new buys.",
        "intents": [
            {
                "symbol": symbol,
                "intent_type": intent_type,
                "order_intent": "BUY",
                "trade_value": trade_value,
                "target_value": trade_value,
                "allowed": allowed,
                "reason": "Candidate is ready.",
                "decision_id": decision_id,
            }
        ],
    }


def _record_preview(
    ledger,
    decision_id,
    *,
    symbol="NVDA",
    side="buy",
    notional=1000,
    allowed=True,
    timestamp=None,
    transaction_id="",
    order_type=None,
    quantity=None,
):
    order_type = order_type or ("market_notional_preview" if side != "none" else "no_order")
    row = {
        "preview_id": f"preview-{symbol.lower()}-{side}",
        "plan_id": "plan-1",
        "decision_id": decision_id,
        "transaction_id": transaction_id,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "notional": notional,
        "allowed": allowed,
        "blocked_reasons": [] if allowed else ["cash shortfall"],
    }
    if quantity is not None:
        row["quantity"] = quantity
    return ledger.record_preview(
        {
            "plan_id": "plan-1",
            "order_submission_enabled": False,
            "previews": [row],
        },
        timestamp=timestamp,
    )


def _boundary(tmp_path, **kwargs):
    return PaperExecutionBoundary(
        now_func=lambda: datetime(2026, 5, 1, 12, tzinfo=UTC),
        rules_path=Path("ai_trader/rules/active_rules.txt"),
        **kwargs,
    )


def test_paper_execution_dry_run_builds_audit_but_does_not_submit(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    broker = FakePaperBroker()

    result = _boundary(tmp_path).run(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=broker,
        submit=False,
    )

    assert result["submit_requested"] is False
    assert result["ready_count"] == 1
    assert result["submitted_count"] == 0
    assert broker.calls == []
    assert ledger.list_execution_events(limit=10) == []
    assert "Paper Execution Boundary" in build_paper_execution_markdown(result)
    assert result["active_rules"]["sha256"]


def test_paper_execution_markdown_includes_excluded_count():
    markdown = build_paper_execution_markdown(
        {
            "submit_requested": False,
            "paper_mode": True,
            "live_mode": False,
            "ready_count": 1,
            "submitted_count": 0,
            "blocked_count": 0,
            "excluded_count": 1,
            "rejected_count": 0,
            "active_rules": {"sha256": "abc123"},
            "items": [
                {"symbol": "AMZN", "status": "ready_to_submit", "notional": 813.03, "blocked_reasons": []},
                {"symbol": "SPY", "status": "excluded_v1", "notional": 0.0, "blocked_reasons": []},
            ],
        }
    )

    assert "- Excluded: 1" in markdown
    assert "| SPY | excluded_v1 | $0.00 |  |" in markdown


def test_paper_execution_submit_records_submitted_with_deterministic_client_order_id(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    broker = FakePaperBroker(status="pending_new", order_id="alpaca-paper-1")

    result = _boundary(tmp_path).run(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=broker,
        submit=True,
    )

    events = ledger.list_execution_events(limit=10)
    assert result["submitted_count"] == 1
    assert len(broker.calls) == 1
    assert broker.calls[0]["client_order_id"] == events[0]["event_json"]["client_order_id"]
    assert events[0]["status"] == "submitted"
    assert events[0]["broker_order_id"] == "alpaca-paper-1"
    assert events[0]["event_json"]["broker_status"] == "pending_new"
    assert events[0]["event_json"]["paper_mode"] is True
    assert events[0]["event_json"]["live_mode"] is False
    assert events[0]["event_json"]["submission_attempt_id"] == result["submission_attempt_id"]
    assert events[0]["event_json"]["active_rules_hash"] == result["active_rules"]["sha256"]


def test_paper_execution_normalizes_alpaca_enum_statuses_as_submitted(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    broker = FakePaperBroker(status="OrderStatus.PENDING_NEW", order_id="alpaca-paper-enum-1")

    result = _boundary(tmp_path).run(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=broker,
        submit=True,
    )

    events = ledger.list_execution_events(limit=10)
    assert result["submitted_count"] == 1
    assert result["rejected_count"] == 0
    assert events[0]["status"] == "submitted"
    assert events[0]["broker_order_id"] == "alpaca-paper-enum-1"
    assert events[0]["event_json"]["broker_status"] == "pending_new"


def test_paper_execution_submit_uses_quantity_order_for_whole_share_preview(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(
        ledger,
        decision_id,
        order_type="market_quantity_preview",
        quantity=2,
        notional=1820,
    )
    broker = FakePaperBroker(status="pending_new", order_id="alpaca-paper-qty-1")

    result = _boundary(tmp_path).run(
        _action_plan(decision_id, trade_value=1820),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=broker,
        submit=True,
    )

    events = ledger.list_execution_events(limit=10)
    assert result["submitted_count"] == 1
    assert broker.calls == [
        {
            "order_model": "quantity",
            "symbol": "NVDA",
            "side": "buy",
            "quantity": 2.0,
            "client_order_id": events[0]["event_json"]["client_order_id"],
            "time_in_force": "day",
        }
    ]
    assert events[0]["event_json"]["quantity"] == 2.0
    assert events[0]["event_json"]["order_type"] == "market_quantity_preview"


def test_paper_execution_blocks_quantity_preview_without_positive_quantity(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(
        ledger,
        decision_id,
        order_type="market_quantity_preview",
        quantity=0,
        notional=0,
    )

    result = _boundary(tmp_path).run(
        _action_plan(decision_id, trade_value=1000),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=FakePaperBroker(),
        submit=True,
    )

    assert result["submitted_count"] == 0
    assert "quantity_not_positive" in result["items"][0]["blocked_reasons"]
    assert ledger.list_execution_events(limit=1)[0]["status"] == "submit_blocked"


def test_paper_execution_duplicate_preview_is_blocked_before_broker_call(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)

    first_broker = FakePaperBroker(order_id="alpaca-paper-1")
    first = _boundary(tmp_path).run(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=first_broker,
        submit=True,
    )
    second_broker = FakePaperBroker(order_id="alpaca-paper-2")
    second = _boundary(tmp_path).run(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=second_broker,
        submit=True,
    )

    assert first["submitted_count"] == 1
    assert second["submitted_count"] == 0
    assert second["blocked_count"] == 1
    assert second_broker.calls == []
    latest = ledger.list_execution_events(limit=1)[0]
    assert latest["status"] == "submit_blocked"
    assert "duplicate_submission" in latest["event_json"]["blocked_reasons"]


def test_paper_execution_hard_blocks_protected_and_rebalance_previews(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    protected_decision = _record_decision(journal, symbol="FXAIX")
    rebalance_decision = _record_decision(journal, symbol="NVDA")
    _record_preview(ledger, protected_decision, symbol="FXAIX")
    _record_preview(ledger, rebalance_decision, symbol="AAPL", side="sell", transaction_id="rebalance-1")
    plan = {
        "plan_id": "plan-1",
        "intents": [
            {"symbol": "FXAIX", "intent_type": "BUY", "allowed": True, "decision_id": protected_decision},
            {"symbol": "NVDA", "intent_type": "REBALANCE", "allowed": True, "decision_id": rebalance_decision},
        ],
    }

    result = _boundary(tmp_path).run(
        plan,
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"], holdings=[{"symbol": "AAPL", "market_value": 2000}]),
        submit=True,
        broker=FakePaperBroker(),
    )
    reasons = [reason for item in result["items"] for reason in item["blocked_reasons"]]

    assert result["submitted_count"] == 0
    assert "protected_symbol" in reasons
    assert "rebalance_blocked_v1" in reasons
    assert {event["status"] for event in ledger.list_execution_events(limit=10)} == {"submit_blocked"}


def test_paper_execution_excludes_parking_intents_without_blocking_simple_buy(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal, symbol="AMZN")
    _record_preview(ledger, decision_id, symbol="AMZN", notional=850)
    plan = {
        "plan_id": "plan-1",
        "intents": [
            {"symbol": "AMZN", "intent_type": "BUY", "allowed": True, "decision_id": decision_id},
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
    broker = FakePaperBroker(status="pending_new", order_id="alpaca-paper-amzn")

    result = _boundary(tmp_path).run(
        plan,
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        submit=True,
        broker=broker,
    )

    assert result["ready_count"] == 1
    assert result["blocked_count"] == 0
    assert result["excluded_count"] == 1
    assert result["submitted_count"] == 1
    assert [call["symbol"] for call in broker.calls] == ["AMZN"]
    by_symbol = {item["symbol"]: item for item in result["items"]}
    assert by_symbol["SPY"]["status"] == "excluded_v1"
    assert ledger.list_execution_events(limit=10)[0]["symbol"] == "AMZN"


def test_paper_execution_blocks_benchmark_review_decision_quality_and_cash(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    benchmark_id = _record_decision(journal, symbol="BMK")
    review_id = _record_decision(journal, symbol="REV")
    low_conf_id = _record_decision(journal, symbol="LOW", confidence=61)
    cash_id = _record_decision(journal, symbol="CSH")
    for index in range(5):
        decision_id = _record_decision(journal, symbol=f"LAG{index}")
        journal.update_outcome(decision_id, candidate_price=90, benchmark_price=110)
    journal.record_thesis_review(symbol="REV", thesis_state="broken", status="reviewed", decision_id=review_id)
    _record_preview(ledger, benchmark_id, symbol="BMK")
    _record_preview(ledger, review_id, symbol="REV")
    _record_preview(ledger, low_conf_id, symbol="LOW")
    _record_preview(ledger, cash_id, symbol="CSH", notional=9000)
    plan = {
        "plan_id": "plan-1",
        "intents": [
            {"symbol": "BMK", "intent_type": "BUY", "allowed": True, "decision_id": benchmark_id},
            {"symbol": "REV", "intent_type": "BUY", "allowed": True, "decision_id": review_id},
            {"symbol": "LOW", "intent_type": "BUY", "allowed": True, "decision_id": low_conf_id},
            {"symbol": "CSH", "intent_type": "BUY", "allowed": True, "decision_id": cash_id},
        ],
    }

    result = _boundary(tmp_path).run(
        plan,
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=1000, protected_symbols=["FXAIX"]),
        submit=True,
        broker=FakePaperBroker(),
    )
    by_symbol = {item["symbol"]: item for item in result["items"]}

    assert by_symbol["BMK"]["status"] == "submit_blocked"
    assert "benchmark_guard_paused" in by_symbol["BMK"]["blocked_reasons"]
    assert "thesis_state_broken" in by_symbol["REV"]["blocked_reasons"]
    assert "confidence_below_minimum" in by_symbol["LOW"]["blocked_reasons"]
    assert "insufficient_cash" in by_symbol["CSH"]["blocked_reasons"]
    assert result["submitted_count"] == 0


def test_paper_execution_records_rejected_on_broker_timeout(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)

    result = _boundary(tmp_path).run(
        _action_plan(decision_id),
        journal=journal,
        ledger=ledger,
        profile=PortfolioProfile(protected_symbols=["FXAIX"]),
        portfolio_state=PortfolioState(cash=5000, protected_symbols=["FXAIX"]),
        broker=FakePaperBroker(fail=True),
        submit=True,
    )

    event = ledger.list_execution_events(limit=1)[0]
    assert result["rejected_count"] == 1
    assert event["status"] == "rejected"
    assert "network timeout" in event["error"]


def test_paper_execution_cli_writes_audit_output_before_submit(tmp_path, capsys):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    audit_path = tmp_path / "audit.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--audit-output",
            str(audit_path),
            "--json",
        ]
    )

    assert run_cli(args, broker_factory=lambda: FakePaperBroker()) == 0
    payload = json.loads(capsys.readouterr().out)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert payload["submit_requested"] is False
    assert audit["ready_count"] == 1
    assert ledger.list_execution_events(limit=10) == []


def test_paper_execution_cli_real_submit_path_refreshes_paper_account_state(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id, notional=4000)
    stale_portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    runbook_check_path = tmp_path / "paper_runbook_check.json"
    stale_portfolio_path.write_text(json.dumps({"cash": 500, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id, trade_value=4000)), encoding="utf-8")
    runbook_check_path.write_text(
        json.dumps(
            {
                "ready_for_supervised_submit": True,
                "plan_id": "plan-1",
                "action_plan_hash": hash_action_plan(_action_plan(decision_id, trade_value=4000)),
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    fake_broker = FakePaperBroker()
    calls = {"refreshed": 0}

    def fake_fresh_state(profile):
        calls["refreshed"] += 1
        return PortfolioState(cash=8000, protected_symbols=profile.protected_symbols)

    monkeypatch.setattr(cli, "_fresh_alpaca_paper_state", fake_fresh_state, raising=False)
    monkeypatch.setattr(cli.AlpacaPaperSubmitAdapter, "from_env", staticmethod(lambda: fake_broker))
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(stale_portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--runbook-check",
            str(runbook_check_path),
            "--json",
        ]
    )

    assert run_cli(args, market_clock_factory=lambda: True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls["refreshed"] == 1
    assert payload["submitted_count"] == 1
    assert fake_broker.calls[0]["notional"] == 4000.0


def test_paper_execution_cli_blocks_submit_when_market_is_closed_before_refresh(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    runbook_check_path = tmp_path / "paper_runbook_check.json"
    action_plan = _action_plan(decision_id)
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(action_plan), encoding="utf-8")
    runbook_check_path.write_text(
        json.dumps(
            {
                "ready_for_supervised_submit": True,
                "plan_id": "plan-1",
                "action_plan_hash": hash_action_plan(action_plan),
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    calls = {"refreshed": 0, "broker": 0}
    monkeypatch.setattr(
        cli,
        "_fresh_alpaca_paper_state",
        lambda profile: calls.__setitem__("refreshed", calls["refreshed"] + 1)
        or PortfolioState(cash=8000, protected_symbols=profile.protected_symbols),
        raising=False,
    )
    monkeypatch.setattr(
        cli.AlpacaPaperSubmitAdapter,
        "from_env",
        staticmethod(lambda: calls.__setitem__("broker", calls["broker"] + 1) or FakePaperBroker()),
    )
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--runbook-check",
            str(runbook_check_path),
            "--json",
        ]
    )

    assert run_cli(args, market_clock_factory=lambda: False) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "paper_execution_market_closed"
    assert payload["order_submission_enabled"] is False
    assert "market_closed" in payload["blockers"]
    assert calls == {"refreshed": 0, "broker": 0}
    assert ledger.list_execution_events(limit=10) == []


def test_paper_execution_cli_submit_requires_confirmation_token(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    calls = {"refreshed": 0, "broker": 0}

    def fake_fresh_state(profile):
        calls["refreshed"] += 1
        return PortfolioState(cash=8000, protected_symbols=profile.protected_symbols)

    monkeypatch.setattr(cli, "_fresh_alpaca_paper_state", fake_fresh_state, raising=False)
    monkeypatch.setattr(
        cli.AlpacaPaperSubmitAdapter,
        "from_env",
        staticmethod(lambda: calls.__setitem__("broker", calls["broker"] + 1) or FakePaperBroker()),
    )
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--json",
        ]
    )

    assert run_cli(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "paper_execution_submit_confirmation"
    assert payload["submit_requested"] is True
    assert payload["order_submission_enabled"] is False
    assert "missing_or_invalid_confirm_paper_submit" in payload["blockers"]
    assert calls == {"refreshed": 0, "broker": 0}
    assert ledger.list_execution_events(limit=10) == []


def test_paper_execution_cli_submit_requires_ready_matching_runbook_check(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    runbook_check_path = tmp_path / "paper_runbook_check.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    runbook_check_path.write_text(
        json.dumps({"ready_for_supervised_submit": False, "plan_id": "plan-1"}),
        encoding="utf-8",
    )
    calls = {"refreshed": 0, "broker": 0}

    monkeypatch.setattr(
        cli,
        "_fresh_alpaca_paper_state",
        lambda profile: calls.__setitem__("refreshed", calls["refreshed"] + 1)
        or PortfolioState(cash=8000, protected_symbols=profile.protected_symbols),
        raising=False,
    )
    monkeypatch.setattr(
        cli.AlpacaPaperSubmitAdapter,
        "from_env",
        staticmethod(lambda: calls.__setitem__("broker", calls["broker"] + 1) or FakePaperBroker()),
    )
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--runbook-check",
            str(runbook_check_path),
            "--json",
        ]
    )

    assert run_cli(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "paper_execution_submit_precheck"
    assert "runbook_check_not_ready" in payload["blockers"]
    assert "runbook_check_missing_action_plan_hash" in payload["blockers"]
    assert calls == {"refreshed": 0, "broker": 0}
    assert ledger.list_execution_events(limit=10) == []


def test_paper_execution_cli_blocks_missing_and_mismatched_runbook_check_before_refresh(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    runbook_check_path = tmp_path / "paper_runbook_check.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    runbook_check_path.write_text(
        json.dumps(
            {
                "ready_for_supervised_submit": True,
                "plan_id": "other-plan",
                "action_plan_hash": hash_action_plan(_action_plan(decision_id)),
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    calls = {"refreshed": 0}
    monkeypatch.setattr(
        cli,
        "_fresh_alpaca_paper_state",
        lambda profile: calls.__setitem__("refreshed", calls["refreshed"] + 1)
        or PortfolioState(cash=8000, protected_symbols=profile.protected_symbols),
        raising=False,
    )

    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--runbook-check",
            str(runbook_check_path),
            "--json",
        ]
    )

    assert run_cli(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert "runbook_check_plan_mismatch" in payload["blockers"]
    assert calls == {"refreshed": 0}


def test_paper_execution_cli_blocks_action_plan_hash_mismatch_before_refresh(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    runbook_check_path = tmp_path / "paper_runbook_check.json"
    action_plan = _action_plan(decision_id)
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(action_plan), encoding="utf-8")
    runbook_check_path.write_text(
        json.dumps(
            {
                "ready_for_supervised_submit": True,
                "plan_id": "plan-1",
                "action_plan_hash": hash_action_plan({**action_plan, "intents": []}),
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    calls = {"refreshed": 0}
    monkeypatch.setattr(
        cli,
        "_fresh_alpaca_paper_state",
        lambda profile: calls.__setitem__("refreshed", calls["refreshed"] + 1)
        or PortfolioState(cash=8000, protected_symbols=profile.protected_symbols),
        raising=False,
    )
    args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--runbook-check",
            str(runbook_check_path),
            "--json",
        ]
    )

    assert run_cli(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert "runbook_check_action_plan_hash_mismatch" in payload["blockers"]
    assert calls == {"refreshed": 0}


def test_paper_execution_cli_blocks_missing_and_stale_runbook_check_before_refresh(tmp_path, capsys, monkeypatch):
    import longterm.paper_execution_cli as cli

    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    ledger = PaperTradeLedger(tmp_path / "paper.db")
    decision_id = _record_decision(journal)
    _record_preview(ledger, decision_id)
    portfolio_path = tmp_path / "portfolio.json"
    plan_path = tmp_path / "plan.json"
    stale_check_path = tmp_path / "stale_runbook_check.json"
    portfolio_path.write_text(json.dumps({"cash": 5000, "protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps(_action_plan(decision_id)), encoding="utf-8")
    stale_check_path.write_text(
        json.dumps(
            {
                "ready_for_supervised_submit": True,
                "plan_id": "plan-1",
                "action_plan_hash": hash_action_plan(_action_plan(decision_id)),
                "generated_at": (datetime.now(UTC) - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    calls = {"refreshed": 0}
    monkeypatch.setattr(
        cli,
        "_fresh_alpaca_paper_state",
        lambda profile: calls.__setitem__("refreshed", calls["refreshed"] + 1)
        or PortfolioState(cash=8000, protected_symbols=profile.protected_symbols),
        raising=False,
    )

    missing_args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--json",
        ]
    )
    stale_args = build_parser().parse_args(
        [
            "--journal-db",
            str(journal.db_path),
            "--ledger-db",
            str(ledger.db_path),
            "--portfolio-state",
            str(portfolio_path),
            "--action-plan",
            str(plan_path),
            "--submit-paper-orders",
            "--confirm-paper-submit",
            "SUPERVISED_PAPER_BUY_ONLY",
            "--runbook-check",
            str(stale_check_path),
            "--json",
        ]
    )

    assert run_cli(missing_args) == 2
    missing_payload = json.loads(capsys.readouterr().out)
    assert "runbook_check_missing" in missing_payload["blockers"]
    assert run_cli(stale_args) == 2
    stale_payload = json.loads(capsys.readouterr().out)
    assert "runbook_check_stale" in stale_payload["blockers"]
    assert calls == {"refreshed": 0}


def test_alpaca_paper_submit_adapter_rejects_non_paper_base_url():
    try:
        AlpacaPaperSubmitAdapter(api_key="key", secret_key="secret", base_url="https://api.alpaca.markets", paper_mode=True)
    except ValueError as exc:
        assert "paper-api" in str(exc)
    else:
        raise AssertionError("Expected live Alpaca base URL to be rejected.")

    try:
        AlpacaPaperSubmitAdapter(
            api_key="key",
            secret_key="secret",
            base_url="https://paper-api.alpaca.markets",
            paper_mode=False,
        )
    except ValueError as exc:
        assert "paper_mode" in str(exc)
    else:
        raise AssertionError("Expected paper_mode=False to be rejected.")


def test_deterministic_client_order_id_is_stable():
    first = deterministic_client_order_id(
        preview_id="preview-1",
        preview_log_id="log-1",
        plan_id="plan-1",
        decision_id="decision-1",
    )
    second = deterministic_client_order_id(
        preview_id="preview-1",
        preview_log_id="log-1",
        plan_id="plan-1",
        decision_id="decision-1",
    )

    assert first == second
    assert first.startswith("lt-")
    assert len(first) <= 48
