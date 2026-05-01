"""Supervised Alpaca paper execution boundary for long-term BUY previews."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from longterm.benchmark_guard import BenchmarkGuard
from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from longterm.review_status import ReviewStatusBuilder
from portfolio.portfolio_profile import PortfolioProfile

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"
SUBMITTED_BROKER_STATUSES = {"new", "accepted", "pending_new"}
REJECTED_BROKER_STATUSES = {"rejected", "canceled", "cancelled", "expired"}


class PaperSubmitBroker(Protocol):
    def submit_notional_order(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        client_order_id: str,
        time_in_force: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ActiveRulesReference:
    path: str
    sha256: str
    excerpt: str


class ActiveRulesLoader:
    """Load the immutable rules reference used in paper execution audits."""

    def __init__(self, path: str | Path = DEFAULT_RULES_PATH):
        self.path = Path(path)

    def load(self, *, required: bool = True) -> ActiveRulesReference:
        if not self.path.exists():
            if required:
                raise FileNotFoundError(f"active rules file not found: {self.path}")
            return ActiveRulesReference(path=str(self.path), sha256="", excerpt="")
        text = self.path.read_text(encoding="utf-8")
        return ActiveRulesReference(
            path=str(self.path),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            excerpt=_rules_excerpt(text),
        )


class AlpacaPaperSubmitAdapter:
    """Tiny paper-only adapter for Alpaca notional market orders."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str = "https://paper-api.alpaca.markets",
        paper_mode: bool = True,
    ):
        if not paper_mode:
            raise ValueError("paper_mode must be true for long-term paper execution.")
        if "paper-api" not in str(base_url):
            raise ValueError("Alpaca paper execution requires a paper-api base URL.")
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.base_url = base_url
        self.paper_mode = True

    @classmethod
    def from_env(cls) -> "AlpacaPaperSubmitAdapter":
        return cls(
            api_key=os.getenv("ALPACA_API_KEY"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
            paper_mode=True,
        )

    def submit_notional_order(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        client_order_id: str,
        time_in_force: str,
    ) -> Mapping[str, Any]:
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest
        except Exception as exc:  # pragma: no cover - depends on optional SDK
            raise RuntimeError("Alpaca trading SDK is required for paper submission.") from exc
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Alpaca paper API credentials are required.")
        client = TradingClient(self.api_key, self.secret_key, paper=True)
        request = MarketOrderRequest(
            symbol=symbol,
            notional=float(notional),
            side=OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY if time_in_force.lower() == "day" else TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        order = client.submit_order(request)
        return {
            "id": str(getattr(order, "id", "") or ""),
            "status": str(getattr(order, "status", "") or ""),
        }


class PaperExecutionBoundary:
    """Final supervised boundary before Alpaca paper submission."""

    def __init__(
        self,
        *,
        now_func: Callable[[], datetime] | None = None,
        max_preview_age_hours: int = 24,
        min_confidence: int = 70,
        rules_path: str | Path = DEFAULT_RULES_PATH,
        benchmark_guard: BenchmarkGuard | None = None,
    ):
        self.now_func = now_func or (lambda: datetime.now(UTC))
        self.max_preview_age_hours = int(max_preview_age_hours)
        self.min_confidence = int(min_confidence)
        self.rules_loader = ActiveRulesLoader(rules_path)
        self.benchmark_guard = benchmark_guard or BenchmarkGuard()

    def run(
        self,
        action_plan: Mapping[str, Any],
        *,
        journal: LongTermDecisionJournal,
        ledger: PaperTradeLedger,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        broker: PaperSubmitBroker | None = None,
        submit: bool = False,
        audit_output: str | Path | None = None,
    ) -> dict[str, Any]:
        rules = self.rules_loader.load(required=bool(submit))
        attempt_id = str(uuid.uuid4())
        items = self._build_items(
            action_plan,
            journal=journal,
            ledger=ledger,
            profile=profile,
            portfolio_state=portfolio_state,
            rules=rules,
            submission_attempt_id=attempt_id,
        )
        result = {
            "schema_version": 1,
            "mode": "paper_execution_boundary",
            "paper_mode": True,
            "live_mode": False,
            "submit_requested": bool(submit),
            "order_submission_enabled": bool(submit),
            "submission_attempt_id": attempt_id,
            "plan_id": str(action_plan.get("plan_id") or ""),
            "active_rules": {
                "path": rules.path,
                "sha256": rules.sha256,
                "excerpt": rules.excerpt,
            },
            "ready_count": sum(1 for item in items if item["ready_to_submit"]),
            "blocked_count": sum(1 for item in items if not item["ready_to_submit"]),
            "submitted_count": 0,
            "rejected_count": 0,
            "items": items,
            "notes": [
                "Supervised Alpaca paper execution boundary.",
                "V1 submits simple BUY previews only; rebalance and sell previews are blocked.",
                "No live orders and no scheduler automation.",
            ],
        }
        if audit_output:
            Path(audit_output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        if not submit:
            return result
        if broker is None:
            raise ValueError("A paper broker is required when submit=True.")
        self._submit_ready_items(result, ledger=ledger, broker=broker, rules=rules)
        if audit_output:
            Path(audit_output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result

    def _build_items(
        self,
        action_plan: Mapping[str, Any],
        *,
        journal: LongTermDecisionJournal,
        ledger: PaperTradeLedger,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        rules: ActiveRulesReference,
        submission_attempt_id: str,
    ) -> list[dict[str, Any]]:
        previews = _latest_preview_by_decision(ledger)
        review_status = ReviewStatusBuilder(journal).build(limit=10000)
        benchmark = self.benchmark_guard.evaluate(journal.summarize_benchmark_performance())
        items = []
        for intent in action_plan.get("intents") or []:
            decision_id = str(intent.get("decision_id") or "")
            preview = previews.get(decision_id)
            symbol = str((preview or intent).get("symbol") or "").upper()
            client_order_id = deterministic_client_order_id(
                preview_id=str((preview or {}).get("preview_id") or ""),
                preview_log_id=str((preview or {}).get("preview_log_id") or ""),
                plan_id=str(action_plan.get("plan_id") or (preview or {}).get("plan_id") or ""),
                decision_id=decision_id,
            )
            item = self._audit_item(
                intent,
                preview=preview,
                symbol=symbol,
                decision_id=decision_id,
                client_order_id=client_order_id,
                submission_attempt_id=submission_attempt_id,
                journal=journal,
                ledger=ledger,
                profile=profile,
                portfolio_state=portfolio_state,
                review_status=review_status,
                benchmark_paused=benchmark.should_pause_new_buys,
                benchmark_reason=benchmark.reason,
                rules=rules,
            )
            items.append(item)
        return items

    def _audit_item(
        self,
        intent: Mapping[str, Any],
        *,
        preview: Mapping[str, Any] | None,
        symbol: str,
        decision_id: str,
        client_order_id: str,
        submission_attempt_id: str,
        journal: LongTermDecisionJournal,
        ledger: PaperTradeLedger,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
        review_status: Mapping[str, Mapping[str, Any]],
        benchmark_paused: bool,
        benchmark_reason: str,
        rules: ActiveRulesReference,
    ) -> dict[str, Any]:
        blocked: list[str] = []
        protected = set(profile.protected_symbols) | set(portfolio_state.protected_symbols)
        if not decision_id:
            blocked.append("missing_decision_id")
        decision = _decision_or_none(journal, decision_id)
        if decision is None:
            blocked.append("missing_decision_journal_row")
        elif str(decision.get("recommendation") or "").upper() not in {"BUY", "ADD"}:
            blocked.append("recommendation_not_buy_or_add")
        elif int(decision.get("confidence") or 0) < self.min_confidence:
            blocked.append("confidence_below_minimum")
        if preview is None:
            blocked.append("missing_preview")
        else:
            if str(preview.get("status") or "") != "ready":
                blocked.append(f"preview_{preview.get('status') or 'not_ready'}")
            if not preview.get("allowed"):
                blocked.append("preview_not_allowed")
            if str(preview.get("side") or "").lower() != "buy":
                blocked.append("rebalance_blocked_v1")
            if str(preview.get("transaction_id") or ""):
                blocked.append("rebalance_blocked_v1")
            if _preview_age_hours(preview.get("timestamp"), self.now_func()) > self.max_preview_age_hours:
                blocked.append("preview_stale")
        if str(intent.get("intent_type") or "").upper() != "BUY":
            blocked.append("rebalance_blocked_v1")
        if str(intent.get("order_intent") or "BUY").upper() not in {"BUY", ""}:
            blocked.append("rebalance_blocked_v1")
        if symbol in protected:
            blocked.append("protected_symbol")
        if benchmark_paused:
            blocked.append("benchmark_guard_paused")
        status = review_status.get(symbol, {})
        thesis_state = str(status.get("thesis_state") or "").lower()
        if thesis_state in {"broken", "weakening", "stale"}:
            blocked.append(f"thesis_state_{thesis_state}")
        if bool(status.get("review_due")):
            blocked.append("review_due")
        notional = float((preview or {}).get("notional") or intent.get("trade_value") or 0.0)
        if notional <= 0:
            blocked.append("notional_not_positive")
        if notional > float(portfolio_state.cash or 0.0):
            blocked.append("insufficient_cash")
        if ledger.has_submitted_execution(
            preview_id=str((preview or {}).get("preview_id") or ""),
            client_order_id=client_order_id,
        ):
            blocked.append("duplicate_submission")
        blocked = _dedupe(blocked)
        return {
            "decision_id": decision_id,
            "symbol": symbol,
            "side": str((preview or {}).get("side") or ""),
            "notional": notional,
            "preview_id": str((preview or {}).get("preview_id") or ""),
            "preview_log_id": str((preview or {}).get("preview_log_id") or ""),
            "plan_id": str((preview or {}).get("plan_id") or intent.get("plan_id") or ""),
            "client_order_id": client_order_id,
            "submission_attempt_id": submission_attempt_id,
            "ready_to_submit": not blocked,
            "status": "ready_to_submit" if not blocked else "submit_blocked",
            "blocked_reasons": blocked,
            "benchmark_guard_reason": benchmark_reason,
            "review_status": dict(status),
            "active_rules_hash": rules.sha256,
        }

    def _submit_ready_items(
        self,
        result: dict[str, Any],
        *,
        ledger: PaperTradeLedger,
        broker: PaperSubmitBroker,
        rules: ActiveRulesReference,
    ) -> None:
        for item in result["items"]:
            if not item["ready_to_submit"]:
                ledger.record_execution_event(_event_for_item(item, status="submit_blocked", rules=rules))
                continue
            try:
                response = broker.submit_notional_order(
                    symbol=item["symbol"],
                    side="buy",
                    notional=float(item["notional"]),
                    client_order_id=item["client_order_id"],
                    time_in_force="day",
                )
                broker_status = _normalize_broker_status(response.get("status"))
                broker_order_id = str(response.get("id") or response.get("order_id") or "")
                if broker_status in SUBMITTED_BROKER_STATUSES:
                    item["status"] = "submitted"
                    item["broker_status"] = broker_status
                    item["broker_order_id"] = broker_order_id
                    ledger.record_execution_event(
                        _event_for_item(
                            item,
                            status="submitted",
                            broker_order_id=broker_order_id,
                            broker_status=broker_status,
                            rules=rules,
                        )
                    )
                    result["submitted_count"] += 1
                elif broker_status in REJECTED_BROKER_STATUSES:
                    self._record_rejected(result, ledger, item, rules, f"broker status {broker_status}", broker_status)
                else:
                    self._record_rejected(result, ledger, item, rules, f"unknown broker status {broker_status}", broker_status)
            except Exception as exc:
                self._record_rejected(result, ledger, item, rules, str(exc), "")
        result["blocked_count"] = sum(1 for item in result["items"] if item.get("status") == "submit_blocked")

    def _record_rejected(
        self,
        result: dict[str, Any],
        ledger: PaperTradeLedger,
        item: dict[str, Any],
        rules: ActiveRulesReference,
        error: str,
        broker_status: str,
    ) -> None:
        item["status"] = "rejected"
        item["error"] = error
        item["broker_status"] = broker_status
        ledger.record_execution_event(
            _event_for_item(
                item,
                status="rejected",
                error=error,
                broker_status=broker_status,
                rules=rules,
            )
        )
        result["rejected_count"] += 1


def deterministic_client_order_id(
    *,
    preview_id: str,
    preview_log_id: str,
    plan_id: str,
    decision_id: str,
) -> str:
    raw = "|".join([preview_id, preview_log_id, plan_id, decision_id])
    return "lt-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_paper_execution_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Execution Boundary",
        "",
        "Supervised paper execution only. No live orders and no scheduler automation.",
        "",
        f"- Submit requested: `{str(result.get('submit_requested')).lower()}`",
        f"- Paper mode: `{str(result.get('paper_mode')).lower()}`",
        f"- Live mode: `{str(result.get('live_mode')).lower()}`",
        f"- Ready: {result.get('ready_count', 0)}",
        f"- Submitted: {result.get('submitted_count', 0)}",
        f"- Blocked: {result.get('blocked_count', 0)}",
        f"- Rejected: {result.get('rejected_count', 0)}",
        f"- Active rules hash: `{(result.get('active_rules') or {}).get('sha256') or ''}`",
        "",
        "| Symbol | Status | Notional | Reasons |",
        "| --- | --- | ---: | --- |",
    ]
    for item in result.get("items") or []:
        lines.append(
            "| {symbol} | {status} | ${notional:,.2f} | {reasons} |".format(
                symbol=item.get("symbol") or "",
                status=item.get("status") or "",
                notional=float(item.get("notional") or 0.0),
                reasons="; ".join(str(reason) for reason in (item.get("blocked_reasons") or [])).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _event_for_item(
    item: Mapping[str, Any],
    *,
    status: str,
    rules: ActiveRulesReference,
    broker_order_id: str = "",
    broker_status: str = "",
    error: str = "",
) -> dict[str, Any]:
    return {
        "decision_id": item.get("decision_id") or "",
        "preview_log_id": item.get("preview_log_id") or "",
        "preview_id": item.get("preview_id") or "",
        "plan_id": item.get("plan_id") or "",
        "broker_order_id": broker_order_id,
        "symbol": item.get("symbol") or "",
        "side": item.get("side") or "buy",
        "notional": item.get("notional") or 0.0,
        "status": status,
        "error": error or "; ".join(str(reason) for reason in (item.get("blocked_reasons") or [])),
        "submission_attempt_id": item.get("submission_attempt_id") or "",
        "client_order_id": item.get("client_order_id") or "",
        "broker_status": broker_status,
        "blocked_reasons": list(item.get("blocked_reasons") or []),
        "paper_mode": True,
        "live_mode": False,
        "active_rules_hash": rules.sha256,
        "active_rules_excerpt": rules.excerpt,
        "revalidation": dict(item),
    }


def _latest_preview_by_decision(ledger: PaperTradeLedger) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in ledger.list_previews(limit=10000):
        decision_id = str(row.get("decision_id") or "")
        if decision_id and decision_id not in latest:
            latest[decision_id] = row
    return latest


def _decision_or_none(journal: LongTermDecisionJournal, decision_id: str) -> dict[str, Any] | None:
    if not decision_id:
        return None
    try:
        return journal.get_decision(decision_id)
    except KeyError:
        return None


def _preview_age_hours(timestamp: Any, now: datetime) -> float:
    if not timestamp:
        return 999999.0
    text = str(timestamp)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    return max(0.0, (current - parsed).total_seconds() / 3600.0)


def _rules_excerpt(text: str) -> str:
    start = text.find("<action_planning_safety>")
    end = text.find("</next_actions_and_benchmark_gate>")
    if start >= 0 and end >= 0:
        return text[start : end + len("</next_actions_and_benchmark_gate>")].strip()
    return text[:1200].strip()


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalize_broker_status(value: Any) -> str:
    text = str(value or "").lower().strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


__all__ = [
    "ActiveRulesLoader",
    "AlpacaPaperSubmitAdapter",
    "PaperExecutionBoundary",
    "build_paper_execution_markdown",
    "deterministic_client_order_id",
]
