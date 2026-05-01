"""Build non-submitting paper order previews from dry-run account plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


@dataclass(frozen=True)
class PaperOrderPreview:
    preview_id: str
    plan_id: str
    symbol: str
    side: str
    order_type: str
    notional: float
    allowed: bool
    reason: str
    decision_id: str = ""
    intent_type: str = ""
    paired_symbol: str = ""
    source_symbol: str = ""
    transaction_id: str = ""
    trade_id: str | None = None
    cash_shortfall: float = 0.0
    benchmark_gate_reason: str = ""
    risk_level: str = ""
    review_due: bool | None = None
    thesis_state: str = ""
    order_submission_enabled: bool = False
    blocked_reasons: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blocked_reasons"] = list(self.blocked_reasons or [])
        return payload


def build_paper_order_preview(
    action_plan: Mapping[str, Any],
    *,
    portfolio_state: PortfolioState,
    profile: PortfolioProfile,
) -> dict[str, Any]:
    """Build paper-order-shaped previews without submitting anything."""
    plan_id = str(action_plan.get("plan_id") or "")
    benchmark_reason = str(action_plan.get("benchmark_gate_reason") or "")
    protected = set(profile.protected_symbols) | set(portfolio_state.protected_symbols)
    previews: list[PaperOrderPreview] = []
    for index, intent in enumerate(action_plan.get("intents") or [], start=1):
        previews.extend(
            _previews_for_intent(
                intent,
                index=index,
                plan_id=plan_id,
                benchmark_reason=benchmark_reason,
                portfolio_state=portfolio_state,
                protected_symbols=protected,
            )
        )
    rows = [preview.to_dict() for preview in previews]
    return {
        "schema_version": 1,
        "mode": "paper_order_preview",
        "order_submission_enabled": False,
        "plan_id": plan_id,
        "preview_count": len(rows),
        "allowed_count": sum(1 for row in rows if row["allowed"]),
        "blocked_count": sum(1 for row in rows if not row["allowed"]),
        "previews": rows,
        "notes": [
            "Preview only. No Alpaca paper or live orders were submitted.",
            "Every preview must be revalidated at the future execution boundary.",
        ],
    }


def build_paper_order_preview_markdown(preview: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Order Preview",
        "",
        "Preview only. No paper or live orders were submitted.",
        "",
        f"- Plan: `{preview.get('plan_id') or 'n/a'}`",
        f"- Order submission enabled: `{str(preview.get('order_submission_enabled')).lower()}`",
        f"- Preview rows: {preview.get('preview_count')}",
        f"- Blocked rows: {preview.get('blocked_count')}",
        "",
        "| Symbol | Side | Notional | Allowed | Decision | Reason |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for row in preview.get("previews") or []:
        lines.append(
            "| {symbol} | {side} | ${notional:,.2f} | {allowed} | {decision} | {reason} |".format(
                symbol=row.get("symbol") or "",
                side=row.get("side") or "",
                notional=float(row.get("notional") or 0.0),
                allowed="yes" if row.get("allowed") else "no",
                decision=row.get("decision_id") or "",
                reason=str(row.get("reason") or "").replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _previews_for_intent(
    intent: Mapping[str, Any],
    *,
    index: int,
    plan_id: str,
    benchmark_reason: str,
    portfolio_state: PortfolioState,
    protected_symbols: set[str],
) -> list[PaperOrderPreview]:
    intent_type = str(intent.get("intent_type") or "").upper()
    if intent_type == "BUY":
        return [
            _buy_preview(
                intent,
                index=index,
                plan_id=plan_id,
                benchmark_reason=benchmark_reason,
                portfolio_state=portfolio_state,
                protected_symbols=protected_symbols,
            )
        ]
    if intent_type == "REBALANCE":
        return _rebalance_previews(
            intent,
            index=index,
            plan_id=plan_id,
            benchmark_reason=benchmark_reason,
            portfolio_state=portfolio_state,
            protected_symbols=protected_symbols,
        )
    return [
        _no_order_preview(
            intent,
            index=index,
            plan_id=plan_id,
            benchmark_reason=benchmark_reason,
        )
    ]


def _buy_preview(
    intent: Mapping[str, Any],
    *,
    index: int,
    plan_id: str,
    benchmark_reason: str,
    portfolio_state: PortfolioState,
    protected_symbols: set[str],
) -> PaperOrderPreview:
    symbol = _symbol(intent)
    notional = _intent_notional(intent)
    blocked = _common_blocks(intent, symbol, protected_symbols)
    cash_shortfall = max(0.0, round(notional - portfolio_state.cash, 2))
    if cash_shortfall > 0:
        blocked.append(f"Insufficient cash for preview; short ${cash_shortfall:,.2f}.")
    return _preview(
        intent,
        preview_id=f"{plan_id or 'plan'}-{index:03d}-buy",
        plan_id=plan_id,
        symbol=symbol,
        side="buy",
        order_type="market_notional_preview",
        notional=notional,
        allowed=not blocked,
        reason=_reason(intent, blocked),
        cash_shortfall=cash_shortfall,
        benchmark_reason=benchmark_reason,
        blocked=blocked,
    )


def _rebalance_previews(
    intent: Mapping[str, Any],
    *,
    index: int,
    plan_id: str,
    benchmark_reason: str,
    portfolio_state: PortfolioState,
    protected_symbols: set[str],
) -> list[PaperOrderPreview]:
    target = _symbol(intent)
    source = str(intent.get("source_symbol") or "").upper()
    notional = _intent_notional(intent)
    blocked = _common_blocks(intent, target, protected_symbols)
    if not source:
        blocked.append("Missing source symbol for rebalance preview.")
    if source in protected_symbols:
        blocked.append(f"{source} is protected and cannot fund a rebalance.")
    if portfolio_state.holding_value(source) < notional:
        blocked.append(f"Insufficient {source} value to fund ${notional:,.2f}.")
    allowed = not blocked
    sell = _preview(
        intent,
        preview_id=f"{plan_id or 'plan'}-{index:03d}-sell",
        plan_id=plan_id,
        symbol=source,
        side="sell",
        order_type="market_notional_preview",
        notional=notional,
        allowed=allowed,
        reason=_reason(intent, blocked),
        paired_symbol=target,
        source_symbol=source,
        transaction_id=f"{plan_id or 'plan'}-{index:03d}-rebalance",
        benchmark_reason=benchmark_reason,
        blocked=blocked,
    )
    buy = _preview(
        intent,
        preview_id=f"{plan_id or 'plan'}-{index:03d}-buy",
        plan_id=plan_id,
        symbol=target,
        side="buy",
        order_type="market_notional_preview",
        notional=notional,
        allowed=allowed,
        reason=_reason(intent, blocked),
        paired_symbol=source,
        source_symbol=source,
        transaction_id=f"{plan_id or 'plan'}-{index:03d}-rebalance",
        benchmark_reason=benchmark_reason,
        blocked=blocked,
    )
    return [sell, buy]


def _no_order_preview(
    intent: Mapping[str, Any],
    *,
    index: int,
    plan_id: str,
    benchmark_reason: str,
) -> PaperOrderPreview:
    allowed = bool(intent.get("allowed")) and str(intent.get("intent_type") or "").upper() == "REVIEW"
    blocked = [] if allowed else [str(intent.get("reason") or "Intent is not orderable.")]
    return _preview(
        intent,
        preview_id=f"{plan_id or 'plan'}-{index:03d}-none",
        plan_id=plan_id,
        symbol=_symbol(intent),
        side="none",
        order_type="no_order",
        notional=0.0,
        allowed=allowed,
        reason=str(intent.get("reason") or "No paper order preview for this intent."),
        benchmark_reason=benchmark_reason,
        blocked=blocked,
    )


def _preview(
    intent: Mapping[str, Any],
    *,
    preview_id: str,
    plan_id: str,
    symbol: str,
    side: str,
    order_type: str,
    notional: float,
    allowed: bool,
    reason: str,
    benchmark_reason: str,
    blocked: list[str],
    paired_symbol: str = "",
    source_symbol: str = "",
    transaction_id: str = "",
    cash_shortfall: float = 0.0,
) -> PaperOrderPreview:
    risk = dict(intent.get("risk_review") or {})
    return PaperOrderPreview(
        preview_id=preview_id,
        plan_id=plan_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        notional=round(float(notional or 0.0), 2),
        allowed=bool(allowed),
        reason=reason,
        decision_id=str(intent.get("decision_id") or ""),
        intent_type=str(intent.get("intent_type") or ""),
        paired_symbol=paired_symbol,
        source_symbol=source_symbol,
        transaction_id=transaction_id,
        trade_id=None,
        cash_shortfall=round(float(cash_shortfall or 0.0), 2),
        benchmark_gate_reason=benchmark_reason,
        risk_level=str(risk.get("risk_level") or ""),
        review_due=risk.get("review_due"),
        thesis_state=str(risk.get("thesis_state") or ""),
        blocked_reasons=blocked,
    )


def _common_blocks(
    intent: Mapping[str, Any],
    symbol: str,
    protected_symbols: set[str],
) -> list[str]:
    blocked = []
    if not intent.get("allowed", False):
        blocked.append(str(intent.get("reason") or "Intent is not allowed."))
    if symbol in protected_symbols:
        blocked.append(f"{symbol} is protected and cannot be traded.")
    if _intent_notional(intent) <= 0:
        blocked.append("Preview notional must be positive.")
    risk = dict(intent.get("risk_review") or {})
    if risk and not risk.get("allowed", True):
        blocked.extend(str(item) for item in risk.get("veto_reasons") or [])
    return [item for item in blocked if item]


def _intent_notional(intent: Mapping[str, Any]) -> float:
    return float(intent.get("trade_value") or intent.get("target_value") or 0.0)


def _symbol(intent: Mapping[str, Any]) -> str:
    return str(intent.get("symbol") or "").upper()


def _reason(intent: Mapping[str, Any], blocked: list[str]) -> str:
    if blocked:
        return "; ".join(blocked)
    return str(intent.get("reason") or "Preview passed dry-run checks.")


__all__ = ["PaperOrderPreview", "build_paper_order_preview", "build_paper_order_preview_markdown"]
