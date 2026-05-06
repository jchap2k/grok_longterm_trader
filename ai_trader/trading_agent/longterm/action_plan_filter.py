"""Filters for deriving safe paper-submit candidate plans from full action plans."""

from __future__ import annotations

from typing import Any, Mapping

from portfolio.portfolio_profile import PortfolioProfile


def build_paper_submit_candidate_plan(
    action_plan: Mapping[str, Any],
    *,
    profile: PortfolioProfile | None = None,
) -> dict[str, Any]:
    """Keep supervised Stage 6B paper BUY candidates, including approved parking."""
    kept: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for intent in action_plan.get("intents") or []:
        if not isinstance(intent, Mapping):
            _increment(excluded, "INVALID_INTENT")
            continue
        reason = _exclusion_reason(intent, profile=profile)
        if reason:
            _increment(excluded, reason)
            continue
        kept.append(_normalized_kept_intent(intent, action_plan=action_plan))
    return {
        **{key: value for key, value in action_plan.items() if key != "intents"},
        "schema_version": max(int(action_plan.get("schema_version") or 1), 1),
        "filter_mode": "stage6b_actionable_buys_and_approved_parking",
        "source_plan_id": str(action_plan.get("plan_id") or ""),
        "order_submission_enabled": False,
        "intents": kept,
        "kept_count": len(kept),
        "excluded_count": sum(excluded.values()),
        "excluded_summary": excluded,
        "notes": [
            "Filtered plan only. It does not submit broker orders.",
            "Stage 6B keeps simple ACTIONABLE_BUY stock BUY intents plus approved non-taxable parking BUY intents.",
            "Review, rebalance, blocked, taxable parking, unapproved parking, and non-actionable intents remain visible in the source action plan.",
        ],
    }


def _exclusion_reason(intent: Mapping[str, Any], *, profile: PortfolioProfile | None) -> str:
    intent_type = str(intent.get("intent_type") or "").upper()
    order_intent = str(intent.get("order_intent") or "").upper()
    if _is_parking_intent(intent_type):
        if profile is None or not profile.is_non_taxable:
            return "PARKING_TAXABLE_ACCOUNT"
        if not profile.is_approved_parking_symbol(str(intent.get("symbol") or "")):
            return "PARKING_SYMBOL_NOT_APPROVED"
        if not bool(intent.get("allowed")) or order_intent != "BUY" or _intent_notional(intent) <= 0:
            return "PARKING_NOT_ALLOWED_OR_NOT_BUY"
        return ""
    if intent_type != "BUY":
        return intent_type or "NON_BUY"
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    if not bool(intent.get("allowed")) or order_intent != "BUY" or promotion.get("promotion_decision") != "ACTIONABLE_BUY":
        return "BUY_NOT_ALLOWED_OR_NOT_ACTIONABLE"
    return ""


def _normalized_kept_intent(intent: Mapping[str, Any], *, action_plan: Mapping[str, Any]) -> dict[str, Any]:
    kept = dict(intent)
    intent_type = str(kept.get("intent_type") or "").upper()
    if _is_parking_intent(intent_type):
        symbol = str(kept.get("symbol") or "").upper()
        kept["symbol"] = symbol
        kept["decision_id"] = str(kept.get("decision_id") or _parking_decision_id(action_plan, symbol))
        kept["parking_execution"] = True
    return kept


def _parking_decision_id(action_plan: Mapping[str, Any], symbol: str) -> str:
    plan_id = str(action_plan.get("plan_id") or "plan")
    return f"parking-{plan_id}-{symbol}"


def _intent_notional(intent: Mapping[str, Any]) -> float:
    try:
        return float(intent.get("trade_value") or intent.get("target_value") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_parking_intent(intent_type: str) -> bool:
    return intent_type in {"PARK_IDLE_CASH", "PARK_DEFENSIVE_CASH"}


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


__all__ = ["build_paper_submit_candidate_plan"]
