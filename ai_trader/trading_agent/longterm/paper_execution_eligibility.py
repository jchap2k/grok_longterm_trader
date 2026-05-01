"""Pre-submit paper execution eligibility checks.

This module is intentionally non-submitting. It evaluates whether a previously
recorded paper preview is fresh and safe enough for a future paper execution
boundary, but it never imports broker SDKs and never places orders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


class PaperExecutionEligibilityBuilder:
    """Build a machine-readable pre-6B paper execution eligibility contract."""

    def __init__(
        self,
        *,
        now_func: Callable[[], datetime] | None = None,
        max_preview_age_hours: int = 24,
        paper_execution_enabled: bool = False,
    ):
        self.now_func = now_func or (lambda: datetime.now(UTC))
        self.max_preview_age_hours = int(max_preview_age_hours)
        self.paper_execution_enabled = bool(paper_execution_enabled)

    def build(
        self,
        action_plan: Mapping[str, Any],
        *,
        ledger: PaperTradeLedger,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
    ) -> dict[str, Any]:
        previews = _latest_preview_by_decision(ledger)
        items = []
        for intent in action_plan.get("intents") or []:
            decision_id = str(intent.get("decision_id") or "")
            symbol = str(intent.get("symbol") or "").upper()
            preview = previews.get(decision_id)
            items.append(
                self._item_for_intent(
                    intent,
                    preview=preview,
                    symbol=symbol,
                    decision_id=decision_id,
                    profile=profile,
                    portfolio_state=portfolio_state,
                )
            )
        return {
            "schema_version": 1,
            "mode": "paper_execution_eligibility",
            "paper_execution_enabled": self.paper_execution_enabled,
            "max_preview_age_hours": self.max_preview_age_hours,
            "plan_id": str(action_plan.get("plan_id") or ""),
            "eligible_count": sum(1 for item in items if item["eligible"]),
            "blocked_count": sum(1 for item in items if not item["eligible"]),
            "items": items,
            "notes": [
                "Eligibility only. No Alpaca paper or live orders were submitted.",
                "A fresh ready preview is required before any future paper submission.",
            ],
        }

    def _item_for_intent(
        self,
        intent: Mapping[str, Any],
        *,
        preview: Mapping[str, Any] | None,
        symbol: str,
        decision_id: str,
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
    ) -> dict[str, Any]:
        blocked: list[str] = []
        status = "eligible"
        action = "PAPER_SUBMIT_READY"
        protected = set(profile.protected_symbols) | set(portfolio_state.protected_symbols)
        preview_age_hours = None
        preview_is_fresh = False

        if not self.paper_execution_enabled:
            blocked.append("paper execution disabled")
            status = "execution_disabled"
            action = "ENABLE_PAPER_EXECUTION_GATE"
        if not decision_id:
            blocked.append("missing decision_id")
            status = _first_block_status(status, "missing_decision")
            action = "FIX_TRACEABILITY"
        if symbol in protected:
            blocked.append(f"{symbol} is protected")
            status = _first_block_status(status, "protected_symbol")
            action = "BLOCKED_PROTECTED_SYMBOL"
        if str(intent.get("intent_type") or "").upper() not in {"BUY", "REBALANCE"}:
            blocked.append("intent is not executable")
            status = _first_block_status(status, "intent_not_executable")
            action = "NO_ORDER"
        if intent.get("allowed") is False:
            blocked.append(str(intent.get("reason") or "intent is blocked"))
            status = _first_block_status(status, "intent_blocked")
            action = "RESOLVE_INTENT_BLOCKER"
        if preview is None:
            blocked.append("missing paper preview")
            status = _first_block_status(status, "preview_missing")
            action = "BUILD_PAPER_PREVIEW"
        else:
            preview_age_hours = _age_hours(preview.get("timestamp"), self.now_func())
            preview_is_fresh = preview_age_hours <= self.max_preview_age_hours
            preview_status = str(preview.get("status") or "")
            if not preview_is_fresh:
                blocked.append("preview is stale")
                status = _first_block_status(status, "preview_stale")
                action = "REFRESH_PREVIEW"
            if preview_status == "blocked":
                blocked.extend(str(reason) for reason in (preview.get("blocked_reasons") or []))
                status = _first_block_status(status, "preview_blocked")
                action = "RESOLVE_PREVIEW_BLOCKER"
            elif preview_status == "no_order":
                blocked.append("preview row is no_order")
                status = _first_block_status(status, "preview_no_order")
                action = "NO_ORDER"
            elif preview_status != "ready":
                blocked.append(f"preview status is {preview_status or 'unknown'}")
                status = _first_block_status(status, "preview_not_ready")
                action = "REFRESH_PREVIEW"

        return {
            "decision_id": decision_id,
            "symbol": symbol,
            "intent_type": str(intent.get("intent_type") or ""),
            "eligible": not blocked,
            "status": "eligible" if not blocked else status,
            "action": action,
            "blocked_reasons": _dedupe(blocked),
            "preview_id": str((preview or {}).get("preview_id") or ""),
            "preview_log_id": str((preview or {}).get("preview_log_id") or ""),
            "preview_status": str((preview or {}).get("status") or ""),
            "preview_age_hours": round(preview_age_hours, 4) if preview_age_hours is not None else None,
            "preview_is_fresh": preview_is_fresh,
            "valid_until": _valid_until((preview or {}).get("timestamp"), self.max_preview_age_hours),
        }


def build_paper_execution_eligibility_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "## Paper Execution Eligibility",
        "",
        "Eligibility only. No paper or live orders were submitted.",
        "",
        "| Symbol | Status | Action | Fresh | Reasons |",
        "|---|---|---|---|---|",
    ]
    for item in payload.get("items") or []:
        reasons = "; ".join(str(reason) for reason in (item.get("blocked_reasons") or []))
        lines.append(
            "| {symbol} | {status} | {action} | {fresh} | {reasons} |".format(
                symbol=item.get("symbol") or "",
                status=item.get("status") or "",
                action=item.get("action") or "",
                fresh="yes" if item.get("preview_is_fresh") else "no",
                reasons=reasons.replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _latest_preview_by_decision(ledger: PaperTradeLedger) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in ledger.list_previews(limit=10000):
        decision_id = str(row.get("decision_id") or "")
        if decision_id and decision_id not in latest:
            latest[decision_id] = row
    return latest


def _age_hours(timestamp: Any, now: datetime) -> float:
    parsed = _parse_datetime(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    return max(0.0, (current - parsed).total_seconds() / 3600.0)


def _valid_until(timestamp: Any, max_age_hours: int) -> str:
    if not timestamp:
        return ""
    parsed = _parse_datetime(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed + timedelta(hours=max_age_hours)).isoformat()


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _first_block_status(current: str, candidate: str) -> str:
    return candidate if current == "eligible" else current


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


__all__ = ["PaperExecutionEligibilityBuilder", "build_paper_execution_eligibility_markdown"]
