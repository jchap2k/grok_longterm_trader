"""Build non-submitting paper order previews from dry-run account plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
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
    requested_notional: float = 0.0
    quantity: int | None = None
    estimated_price: float = 0.0
    size_variance: float = 0.0
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
    buy_promotion_decision: str = ""
    promotion_review: dict[str, Any] | None = None
    order_submission_enabled: bool = False
    blocked_reasons: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blocked_reasons"] = list(self.blocked_reasons or [])
        payload["promotion_review"] = dict(self.promotion_review or {})
        return payload


def build_paper_order_preview(
    action_plan: Mapping[str, Any],
    *,
    portfolio_state: PortfolioState,
    profile: PortfolioProfile,
    order_model: str = "notional",
    price_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build paper-order-shaped previews without submitting anything."""
    plan_id = str(action_plan.get("plan_id") or "")
    benchmark_reason = str(action_plan.get("benchmark_gate_reason") or "")
    protected = set(profile.protected_symbols) | set(portfolio_state.protected_symbols)
    normalized_order_model = _normalize_order_model(order_model)
    normalized_price_map = _normalize_price_map(price_map or {})
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
                order_model=normalized_order_model,
                price_map=normalized_price_map,
            )
        )
    rows = [preview.to_dict() for preview in previews]
    return {
        "schema_version": 2,
        "mode": "paper_order_preview",
        "order_submission_enabled": False,
        "order_model": normalized_order_model,
        "plan_id": plan_id,
        "preview_count": len(rows),
        "allowed_count": sum(1 for row in rows if row["side"] != "none" and row["allowed"]),
        "blocked_count": sum(1 for row in rows if row["side"] != "none" and not row["allowed"]),
        "no_order_count": sum(1 for row in rows if row["side"] == "none"),
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
    order_model: str,
    price_map: Mapping[str, float],
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
                order_model=order_model,
                price_map=price_map,
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
            order_model=order_model,
            price_map=price_map,
        )
    if _is_v1_excluded_parking_intent(intent_type):
        return [
            _excluded_v1_preview(
                intent,
                index=index,
                plan_id=plan_id,
                benchmark_reason=benchmark_reason,
            )
        ]
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
    order_model: str,
    price_map: Mapping[str, float],
) -> PaperOrderPreview:
    symbol = _symbol(intent)
    requested_notional = _intent_notional(intent)
    blocked = _common_blocks(intent, symbol, protected_symbols)
    promotion_review = _promotion_review_from_intent(intent)
    promotion_blocker = _buy_promotion_blocker(promotion_review)
    if promotion_blocker:
        blocked.append(promotion_blocker)
    order_type = "market_notional_preview"
    notional = requested_notional
    quantity: int | None = None
    estimated_price = 0.0
    if order_model == "whole_share":
        order_type = "market_quantity_preview"
        estimated_price = float(price_map.get(symbol) or 0.0)
        if estimated_price <= 0:
            blocked.append("missing_price_for_whole_share_preview")
            notional = 0.0
            quantity = 0
        else:
            quantity = int(floor(requested_notional / estimated_price))
            notional = round(quantity * estimated_price, 2)
            if quantity < 1:
                blocked.append("whole_share_quantity_below_one")
    cash_shortfall = max(0.0, round(notional - portfolio_state.cash, 2))
    if cash_shortfall > 0:
        blocked.append(f"Insufficient cash for preview; short ${cash_shortfall:,.2f}.")
    return _preview(
        intent,
        preview_id=f"{plan_id or 'plan'}-{index:03d}-buy",
        plan_id=plan_id,
        symbol=symbol,
        side="buy",
        order_type=order_type,
        notional=notional,
        requested_notional=requested_notional,
        quantity=quantity,
        estimated_price=estimated_price,
        allowed=not blocked,
        reason=_reason(intent, blocked),
        cash_shortfall=cash_shortfall,
        benchmark_reason=benchmark_reason,
        blocked=blocked,
        promotion_review=promotion_review,
    )


def _rebalance_previews(
    intent: Mapping[str, Any],
    *,
    index: int,
    plan_id: str,
    benchmark_reason: str,
    portfolio_state: PortfolioState,
    protected_symbols: set[str],
    order_model: str,
    price_map: Mapping[str, float],
) -> list[PaperOrderPreview]:
    target = _symbol(intent)
    source = str(intent.get("source_symbol") or "").upper()
    notional = _intent_notional(intent)
    blocked = _common_blocks(intent, target, protected_symbols)
    order_type = "market_notional_preview"
    target_price = 0.0
    target_quantity: int | None = None
    source_price = 0.0
    source_quantity: int | None = None
    if order_model == "whole_share":
        order_type = "market_quantity_preview"
        target_price = float(price_map.get(target) or 0.0)
        source_price = float(price_map.get(source) or 0.0)
        if target_price <= 0:
            blocked.append("missing_target_price_for_whole_share_preview")
        if source_price <= 0:
            blocked.append("missing_source_price_for_whole_share_preview")
        if target_price > 0:
            target_quantity = int(floor(notional / target_price))
            if target_quantity < 1:
                blocked.append("whole_share_target_quantity_below_one")
        if source_price > 0:
            source_quantity = int(floor(notional / source_price))
            if source_quantity < 1:
                blocked.append("whole_share_source_quantity_below_one")
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
        order_type=order_type,
        notional=notional,
        requested_notional=notional,
        quantity=source_quantity,
        estimated_price=source_price,
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
        order_type=order_type,
        notional=notional,
        requested_notional=notional,
        quantity=target_quantity,
        estimated_price=target_price,
        allowed=allowed,
        reason=_reason(intent, blocked),
        paired_symbol=source,
        source_symbol=source,
        transaction_id=f"{plan_id or 'plan'}-{index:03d}-rebalance",
        benchmark_reason=benchmark_reason,
        blocked=blocked,
    )
    return [sell, buy]


def _excluded_v1_preview(
    intent: Mapping[str, Any],
    *,
    index: int,
    plan_id: str,
    benchmark_reason: str,
) -> PaperOrderPreview:
    return _preview(
        intent,
        preview_id=f"{plan_id or 'plan'}-{index:03d}-excluded",
        plan_id=plan_id,
        symbol=_symbol(intent),
        side="none",
        order_type="excluded_v1",
        notional=0.0,
        allowed=False,
        reason="Planning-only parking intent is excluded from Stage 6B supervised paper submission.",
        benchmark_reason=benchmark_reason,
        blocked=[],
    )


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
    requested_notional: float | None = None,
    quantity: int | None = None,
    estimated_price: float = 0.0,
    allowed: bool,
    reason: str,
    benchmark_reason: str,
    blocked: list[str],
    paired_symbol: str = "",
    source_symbol: str = "",
    transaction_id: str = "",
    cash_shortfall: float = 0.0,
    promotion_review: Mapping[str, Any] | None = None,
) -> PaperOrderPreview:
    risk = dict(intent.get("risk_review") or {})
    promotion = dict(promotion_review or _promotion_review_from_intent(intent))
    return PaperOrderPreview(
        preview_id=preview_id,
        plan_id=plan_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        notional=round(float(notional or 0.0), 2),
        requested_notional=round(float(requested_notional if requested_notional is not None else notional or 0.0), 2),
        quantity=quantity,
        estimated_price=round(float(estimated_price or 0.0), 4),
        size_variance=round(float(notional or 0.0) - float(requested_notional if requested_notional is not None else notional or 0.0), 2),
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
        buy_promotion_decision=str(promotion.get("promotion_decision") or ""),
        promotion_review=promotion,
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


def _normalize_order_model(order_model: str) -> str:
    value = str(order_model or "notional").lower().strip()
    if value in {"notional", "market_notional"}:
        return "notional"
    if value in {"whole_share", "whole-share", "quantity"}:
        return "whole_share"
    raise ValueError("order_model must be 'notional' or 'whole_share'.")


def _normalize_price_map(price_map: Mapping[str, Any]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, value in price_map.items():
        try:
            price = float(value or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        prices[str(symbol or "").upper()] = price
    return prices


def _symbol(intent: Mapping[str, Any]) -> str:
    return str(intent.get("symbol") or "").upper()


def _reason(intent: Mapping[str, Any], blocked: list[str]) -> str:
    if blocked:
        return "; ".join(blocked)
    return str(intent.get("reason") or "Preview passed dry-run checks.")


def _is_v1_excluded_parking_intent(intent_type: str) -> bool:
    return intent_type in {"PARK_IDLE_CASH", "PARK_DEFENSIVE_CASH"}


def _promotion_review_from_intent(intent: Mapping[str, Any]) -> dict[str, Any]:
    review = intent.get("promotion_review")
    if isinstance(review, Mapping):
        return dict(review)
    risk = intent.get("risk_review")
    if isinstance(risk, Mapping):
        nested = risk.get("buy_promotion")
        if isinstance(nested, Mapping):
            return dict(nested)
    return {}


def _buy_promotion_blocker(review: Mapping[str, Any]) -> str:
    if not review:
        return "missing_buy_promotion_review"
    if str(review.get("promotion_decision") or "") != "ACTIONABLE_BUY":
        return "buy_promotion_not_actionable"
    return ""


__all__ = ["PaperOrderPreview", "build_paper_order_preview", "build_paper_order_preview_markdown"]
