"""Filters for deriving safe paper-submit candidate plans from full action plans."""

from __future__ import annotations

from typing import Any, Mapping


def build_paper_submit_candidate_plan(action_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only Stage 6B V1 simple actionable stock BUY intents."""
    kept: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for intent in action_plan.get("intents") or []:
        if not isinstance(intent, Mapping):
            _increment(excluded, "INVALID_INTENT")
            continue
        reason = _exclusion_reason(intent)
        if reason:
            _increment(excluded, reason)
            continue
        kept.append(dict(intent))
    return {
        **{key: value for key, value in action_plan.items() if key != "intents"},
        "schema_version": max(int(action_plan.get("schema_version") or 1), 1),
        "filter_mode": "stage6b_simple_actionable_stock_buys",
        "source_plan_id": str(action_plan.get("plan_id") or ""),
        "order_submission_enabled": False,
        "intents": kept,
        "kept_count": len(kept),
        "excluded_count": sum(excluded.values()),
        "excluded_summary": excluded,
        "notes": [
            "Filtered plan only. It does not submit broker orders.",
            "Stage 6B V1 keeps simple ACTIONABLE_BUY stock BUY intents only.",
            "Parking, review, rebalance, blocked, and non-actionable intents remain visible in the source action plan.",
        ],
    }


def _exclusion_reason(intent: Mapping[str, Any]) -> str:
    intent_type = str(intent.get("intent_type") or "").upper()
    order_intent = str(intent.get("order_intent") or "").upper()
    if intent_type != "BUY":
        return intent_type or "NON_BUY"
    promotion = intent.get("promotion_review") if isinstance(intent.get("promotion_review"), Mapping) else {}
    if not bool(intent.get("allowed")) or order_intent != "BUY" or promotion.get("promotion_decision") != "ACTIONABLE_BUY":
        return "BUY_NOT_ALLOWED_OR_NOT_ACTIONABLE"
    return ""


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


__all__ = ["build_paper_submit_candidate_plan"]
