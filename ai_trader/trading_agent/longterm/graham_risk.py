"""Graham-style risk helpers for long-term review and action planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from longterm.portfolio_state import Holding


@dataclass(frozen=True)
class PermanentLossRiskReport:
    """Permanent-capital-loss risk flags derived from packet evidence."""

    score: float
    severity: str
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StagedEntryPlan:
    """Suggested Graham-style entry sizing for an otherwise actionable idea."""

    label: str
    recommended_size_pct: float
    original_size_pct: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MrMarketReview:
    """Review prompt created by a large quote move in an existing holding."""

    review_due: bool
    category: str
    reason: str
    gain_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FLAG_MARKERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "overpayment",
        (
            "extreme p/e",
            "extreme pe",
            "overvalued",
            "overpayment",
            "euphoria",
            "priced for perfection",
            "optimistic forward estimates",
            "valuation mistake",
        ),
        "Price may assume too much future success.",
    ),
    (
        "leverage",
        ("high leverage", "levered balance sheet", "debt burden", "debt/equity"),
        "Leverage can turn volatility into permanent capital loss.",
    ),
    (
        "refinancing_risk",
        ("refinancing risk", "maturity wall", "debt maturity", "higher interest expense"),
        "Refinancing pressure can impair normalized earnings power.",
    ),
    (
        "weak_cash_conversion",
        ("weak cash conversion", "negative free cash flow", "fcf burn", "cash conversion"),
        "Reported earnings may not convert cleanly into owner cash.",
    ),
    (
        "dilution",
        ("dilution", "share dilution", "stock based compensation", "sbc"),
        "Dilution can transfer upside away from current owners.",
    ),
    (
        "accounting_quality",
        ("accounting", "non-gaap", "adjusted earnings", "one-time", "restatement"),
        "Accounting quality needs normalization before trusting valuation.",
    ),
    (
        "business_disruption",
        ("disruption", "competitive threat", "product disruption", "secular decline"),
        "Business-model disruption can break the thesis rather than just the quote.",
    ),
)


def evaluate_permanent_loss_risk(packet: Mapping[str, Any]) -> PermanentLossRiskReport:
    """Flag routes to permanent capital loss without making a trade decision."""
    text = _packet_text(packet)
    flags: list[str] = []
    reasons: list[str] = []
    for flag, markers, reason in _FLAG_MARKERS:
        if any(marker in text for marker in markers):
            flags.append(flag)
            reasons.append(reason)

    valuation_score = _number(packet.get("valuation_score"))
    if valuation_score is not None and valuation_score < 35 and "overpayment" not in flags:
        flags.append("overpayment")
        reasons.append("Low valuation score leaves little visible Graham-style cushion.")

    score = max(0.0, 100.0 - (len(flags) * 14.0))
    if "overpayment" in flags:
        score -= 10.0
    if {"leverage", "weak_cash_conversion"}.issubset(set(flags)):
        score -= 8.0
    score = max(0.0, min(100.0, score))

    if score < 50 or len(flags) >= 4:
        severity = "high"
    elif score < 75 or len(flags) >= 2:
        severity = "medium"
    elif flags:
        severity = "low"
    else:
        severity = "none"

    return PermanentLossRiskReport(
        score=round(score, 2),
        severity=severity,
        flags=_dedupe(flags),
        reasons=_dedupe(reasons) or ["No obvious permanent-capital-loss flags were found."],
    )


def classify_defensive_enterprising_mode(
    packet: Mapping[str, Any],
    *,
    margin_of_safety_score: float,
    risk_report: PermanentLossRiskReport,
) -> str:
    """Label whether an active idea has earned enterprising treatment."""
    recommendation = str(packet.get("recommendation") or packet.get("action") or "").upper()
    quality_score = _number(packet.get("quality_score")) or 0.0
    evidence = str(packet.get("evidence_brief") or "")
    if risk_report.severity == "high" or (margin_of_safety_score < 45 and risk_report.flags):
        return "speculative_watchlist"
    if recommendation and recommendation not in {"BUY", "ADD", "HOLD"}:
        return "defensive_default"
    if quality_score >= 70 or "Article evidence:" in evidence or "research_evidence_brief_v1" in evidence:
        return "enterprising_candidate"
    return "defensive_default"


def evaluate_staged_entry(
    *,
    suggested_size_pct: float,
    margin_of_safety_score: float,
    risk_report: PermanentLossRiskReport,
) -> StagedEntryPlan:
    """Prefer starter sizing when quality is promising but price/risk cushion is thin."""
    original = max(0.0, float(suggested_size_pct or 0.0))
    if original <= 0:
        return StagedEntryPlan("no_entry", 0.0, original, "No positive active-sleeve size was suggested.")
    if risk_report.severity == "high":
        return StagedEntryPlan(
            "confirm_before_entry",
            0.0,
            original,
            "Permanent-loss or overpayment risk requires confirmation before entry.",
        )
    if risk_report.severity == "medium" or 60 <= margin_of_safety_score < 75:
        return StagedEntryPlan(
            "starter_position",
            round(min(original, 2.0), 2),
            original,
            "Use a staged starter position until the margin of safety is clearer.",
        )
    return StagedEntryPlan(
        "target_position",
        round(original, 2),
        original,
        "Margin of safety and permanent-loss checks support the suggested size.",
    )


def mr_market_review_trigger(
    holding: Holding,
    *,
    drawdown_threshold_pct: float = -25.0,
    rally_threshold_pct: float = 40.0,
) -> MrMarketReview:
    """Use large quote moves as review prompts, not automatic trading orders."""
    gain_percent = _holding_gain_percent(holding)
    if gain_percent <= drawdown_threshold_pct:
        return MrMarketReview(
            review_due=True,
            category="mr_market_drawdown_review",
            gain_percent=round(gain_percent, 2),
            reason=(
                f"Mr. Market quote is {gain_percent:.2f}% below cost; review whether this is "
                "a better quote or a broken thesis before selling or adding."
            ),
        )
    if gain_percent >= rally_threshold_pct:
        return MrMarketReview(
            review_due=True,
            category="mr_market_rally_review",
            gain_percent=round(gain_percent, 2),
            reason=(
                f"Mr. Market quote is {gain_percent:.2f}% above cost; review valuation, "
                "margin of safety, and trailing-profit protection before adding or trimming."
            ),
        )
    return MrMarketReview(
        review_due=False,
        category="mr_market_no_action",
        gain_percent=round(gain_percent, 2),
        reason="No Graham-style price-move review trigger is active.",
    )


def normalized_earnings_quality_label(packet: Mapping[str, Any]) -> str:
    """Describe whether valuation evidence is normalized or forward-promotional."""
    text = _packet_text(packet)
    weak_markers = ("one-time", "non-gaap", "adjusted earnings", "optimistic forward", "hoped-for earnings")
    support_markers = ("normalized earnings", "normalized free cash flow", "free cash flow", "cash conversion")
    if any(marker in text for marker in weak_markers):
        return "needs_normalization"
    if any(marker in text for marker in support_markers):
        return "normalized_support"
    return "not_explicit"


def _holding_gain_percent(holding: Holding) -> float:
    if holding.unrealized_pnl_percent:
        return holding.unrealized_pnl_percent
    cost = holding.original_purchase_total_cost
    if cost <= 0 and holding.avg_entry_price > 0 and holding.quantity > 0:
        cost = holding.avg_entry_price * holding.quantity
    current = holding.market_value
    if current <= 0 and holding.current_price > 0 and holding.quantity > 0:
        current = holding.current_price * holding.quantity
    if cost <= 0:
        return 0.0
    return ((current - cost) / cost) * 100.0


def _packet_text(packet: Mapping[str, Any]) -> str:
    pieces: list[str] = []
    for value in packet.values():
        if isinstance(value, Mapping):
            pieces.extend(str(item) for item in value.values())
        elif isinstance(value, (list, tuple, set)):
            pieces.extend(str(item) for item in value)
        else:
            pieces.append(str(value))
    return " ".join(pieces).lower()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
