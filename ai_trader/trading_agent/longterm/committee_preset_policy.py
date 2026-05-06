"""Advisory committee preset routing for Grok 4.3 cost/depth control."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROUTINE_PRESET = "decision_4"
ESCALATION_PRESET = "decision_6"
NORMAL_REGIMES = {"", "normal", "constructive", "low_volatility", "stable"}


@dataclass(frozen=True)
class CommitteePresetPolicyConfig:
    """Configurable thresholds for deciding when a wider committee is justified."""

    routine_preset: str = ROUTINE_PRESET
    escalation_preset: str = ESCALATION_PRESET
    large_position_pct: float = 0.05
    elevated_vix: float = 25.0
    borderline_valuation_min: float = 35.0
    borderline_valuation_max: float = 55.0


def build_committee_preset_recommendation(
    *,
    action_plan: Mapping[str, Any] | None = None,
    research_items: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    market_regime: Mapping[str, Any] | None = None,
    active_sleeve_value: float | None = None,
    config: CommitteePresetPolicyConfig | None = None,
) -> dict[str, Any]:
    """Return an advisory preset recommendation without calling an LLM or broker.

    The output is intentionally conservative: it can recommend escalating from
    `decision_4` to `decision_6`, but it never changes any order/action plan and
    never authorizes submission.
    """
    resolved = config or CommitteePresetPolicyConfig()
    plan = dict(action_plan or {})
    regime = dict(market_regime or {})
    items = _normalize_research_items(research_items)
    active_value = _float_value(active_sleeve_value)

    reasons: list[str] = []
    affected_symbols: list[str] = []
    intent_count = 0
    orderable_intent_count = 0

    for intent in _iter_intents(plan):
        intent_count += 1
        symbol = _symbol(intent)
        intent_type = str(intent.get("intent_type") or "").upper()
        order_intent = str(intent.get("order_intent") or "").upper()
        if order_intent and order_intent != "NONE":
            orderable_intent_count += 1
        if intent_type == "REBALANCE" or order_intent == "SELL_TO_FUND_BUY":
            _add_reason(reasons, affected_symbols, f"complex_rebalance_decision:{symbol}", symbol)
        pct = _trade_pct(intent, active_value)
        if pct >= resolved.large_position_pct and symbol:
            _add_reason(reasons, affected_symbols, f"large_position_change:{symbol}", symbol)

    risk_regime = str(regime.get("risk_regime") or "").lower().strip()
    if risk_regime not in NORMAL_REGIMES:
        _add_reason(reasons, affected_symbols, f"choppy_macro_regime:{risk_regime}", "")
    vix = _optional_float(regime.get("vix_level"))
    if vix is not None and vix >= resolved.elevated_vix:
        _add_reason(reasons, affected_symbols, f"vix_elevated:{vix:.1f}", "")

    for item in items:
        symbol = _symbol(item)
        valuation = _valuation_score(item)
        if valuation is not None and resolved.borderline_valuation_min <= valuation <= resolved.borderline_valuation_max:
            _add_reason(reasons, affected_symbols, f"borderline_valuation:{symbol}", symbol)
        if _is_new_unproven_thesis(item):
            _add_reason(reasons, affected_symbols, f"new_unproven_thesis:{symbol}", symbol)

    escalation_required = bool(reasons)
    return {
        "schema_version": 1,
        "mode": "committee_preset_policy",
        "order_submission_enabled": False,
        "default_preset": resolved.routine_preset,
        "recommended_preset": resolved.escalation_preset if escalation_required else resolved.routine_preset,
        "escalation_required": escalation_required,
        "escalation_reasons": reasons,
        "affected_symbols": affected_symbols,
        "inputs_summary": {
            "intent_count": intent_count,
            "orderable_intent_count": orderable_intent_count,
            "research_item_count": len(items),
            "active_sleeve_value": active_value,
            "risk_regime": risk_regime or "unknown",
            "vix_level": vix,
        },
        "policy_config": {
            "routine_preset": resolved.routine_preset,
            "escalation_preset": resolved.escalation_preset,
            "large_position_pct": resolved.large_position_pct,
            "elevated_vix": resolved.elevated_vix,
            "borderline_valuation_min": resolved.borderline_valuation_min,
            "borderline_valuation_max": resolved.borderline_valuation_max,
        },
        "notes": [
            "Advisory-only committee routing. No LLM, broker, or provider calls were made.",
            "decision_4 remains the default; decision_6 is reserved for explicit complexity signals.",
        ],
    }


def load_json_mapping(path: str | Path | None) -> dict[str, Any]:
    """Load an optional JSON object, returning an empty object for blank paths."""
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Expected JSON object in {path}.")
    return dict(payload)


def load_research_items(path: str | Path | None) -> list[dict[str, Any]]:
    """Load optional research rows from a JSON object/list artifact."""
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [dict(item) for item in _normalize_research_items(payload)]


def write_committee_preset_recommendation(payload: Mapping[str, Any], path: str | Path) -> None:
    """Persist a committee routing artifact."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8")


def _iter_intents(action_plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    intents = action_plan.get("intents") or []
    return [item for item in intents if isinstance(item, Mapping)]


def _normalize_research_items(payload: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        for key in ("selected", "items", "research_queue", "rows", "candidates"):
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return [item for item in value if isinstance(item, Mapping)]
        if payload.get("symbol"):
            return [payload]
        return []
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return [item for item in payload if isinstance(item, Mapping)]
    return []


def _valuation_score(item: Mapping[str, Any]) -> float | None:
    for path in (
        ("quality_growth_scorecard", "analysis", "valuation"),
        ("moneyball_hidden_gems", "analysis", "valuation"),
        ("scorecard", "analysis", "valuation"),
        ("analysis", "valuation"),
        ("valuation_score",),
    ):
        value = _nested_value(item, path)
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _nested_value(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _is_new_unproven_thesis(item: Mapping[str, Any]) -> bool:
    if item.get("new_or_unproven_thesis") is True:
        return True
    if item.get("known_position") or item.get("currently_held"):
        return False
    prior_count = _optional_float(item.get("prior_decision_count"))
    source_count = _optional_float(item.get("source_recommendation_count"))
    if prior_count is None or source_count is None:
        return False
    return prior_count <= 0 and source_count <= 1


def _trade_pct(intent: Mapping[str, Any], active_sleeve_value: float) -> float:
    if active_sleeve_value <= 0:
        return 0.0
    trade_value = _float_value(intent.get("trade_value") or intent.get("target_value"))
    return trade_value / active_sleeve_value


def _symbol(payload: Mapping[str, Any]) -> str:
    return str(payload.get("symbol") or "").upper()


def _float_value(value: Any) -> float:
    parsed = _optional_float(value)
    return parsed if parsed is not None else 0.0


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _add_reason(reasons: list[str], symbols: list[str], reason: str, symbol: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)
    if symbol and symbol not in symbols:
        symbols.append(symbol)


__all__ = [
    "CommitteePresetPolicyConfig",
    "build_committee_preset_recommendation",
    "load_json_mapping",
    "load_research_items",
    "write_committee_preset_recommendation",
]
