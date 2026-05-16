"""Dry-run promotion review between first-pass BUY decisions and action planning."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.graham_risk import (
    StagedEntryPlan,
    classify_defensive_enterprising_mode,
    evaluate_permanent_loss_risk,
    evaluate_staged_entry,
    normalized_earnings_quality_label,
)
from longterm.risk.category_risk_policy import apply_category_risk_adjustment
from longterm.portfolio_state import PortfolioState
from longterm.reviewers import MarginOfSafetyReviewer
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea
from research.research_packet import ResearchPacket


ACTIONABLE_CONFIDENCE_THRESHOLD = 70
MIN_ACTIONABLE_EVIDENCE_SCORE = 70
MARGIN_OF_SAFETY_FOLLOWUP_THRESHOLD = 60


def _get_company_category_str(packet: Any) -> str:
    """Safely extract company_category whether packet is dict or ResearchPacket."""
    if isinstance(packet, ResearchPacket):
        return packet.company_category.value if packet.company_category else ""
    if isinstance(packet, Mapping):
        cat = packet.get("company_category")
        if isinstance(cat, str):
            return cat
        if cat is not None:
            try:
                return str(cat.value) if hasattr(cat, "value") else str(cat)
            except Exception:
                return str(cat)
    return ""



_OVERPAYMENT_MARKERS = (
    "extreme p/e",
    "extreme pe",
    "overvalued",
    "overpayment",
    "euphoria",
    "priced for perfection",
    "optimistic forward estimates",
    "valuation mistake",
)
_PERMANENT_LOSS_MARKERS = (
    "high leverage",
    "dilution",
    "weak cash conversion",
    "accounting",
    "fraud",
    "disruption",
    "refinancing risk",
    "thesis fragility",
)


@dataclass(frozen=True)
class BuyPromotionReview:
    """Operator-facing review of whether a first-pass BUY is actionable now."""

    symbol: str
    decision_id: str
    first_pass_action: str
    promotion_decision: str
    confidence: int
    suggested_size_pct: float
    evidence_score: float
    portfolio_fit_score: float
    valuation_fit_score: float
    margin_of_safety_score: float
    permanent_loss_score: float
    permanent_loss_flags: list[str]
    defensive_enterprising_mode: str
    staged_entry_size_pct: float
    staged_entry_label: str
    normalized_earnings_quality: str
    company_category: str = ""
    original_suggested_size_pct: float = 0.0          # LLM-suggested size before category adjustment
    category_risk_adjusted_size_pct: float = 0.0      # Size after Lynch category risk multiplier
    category_adjustment_applied: bool = False         # Whether the category multiplier was applied
    blockers: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BuyPromotionReviewer:
    """Evaluate first-pass BUY/ADD rows before they become actionable intents."""

    def evaluate_decision_row(
        self,
        row: Mapping[str, Any],
        *,
        packet: Mapping[str, Any],
        profile: PortfolioProfile,
        portfolio_state: PortfolioState,
    ) -> BuyPromotionReview:
        symbol = str(row.get("symbol") or packet.get("symbol") or "").upper()
        first_pass_action = str(row.get("recommendation") or row.get("action") or "").upper()
        confidence = _int(row.get("confidence"))
        suggested_size_pct = _float(row.get("suggested_size_pct"))
        evidence_brief = str(packet.get("evidence_brief") or "")
        protected = {str(item).upper() for item in (profile.protected_symbols or [])}
        protected.update(str(item).upper() for item in (portfolio_state.protected_symbols or []))
        protected.update(str(item).upper() for item in (packet.get("protected_symbols") or []))

        company_category = _get_company_category_str(packet)
        original_llm_size = suggested_size_pct

        # Category risk sizing is opt-in
        effective_suggested_size_pct = suggested_size_pct
        category_adjustment_applied = False
        adjustment_metadata = {}

        if getattr(profile, "enable_category_risk_sizing", False):
            effective_suggested_size_pct, adjustment_metadata = apply_category_risk_adjustment(
                suggested_size_pct,
                company_category,
            )
            category_adjustment_applied = adjustment_metadata.get("applied", False)

        blockers: list[str] = []
        followups: list[str] = []
        reasons: list[str] = []

        if first_pass_action not in {"BUY", "ADD"}:
            blockers.append("first_pass_not_buy_or_add")
            reasons.append("Only first-pass BUY/ADD rows are eligible for promotion.")
            return self._review(
                row,
                symbol=symbol,
                first_pass_action=first_pass_action or "UNKNOWN",
                promotion_decision="NOT_PROMOTED",
                confidence=confidence,
                suggested_size_pct=suggested_size_pct,
                evidence_score=_evidence_score(evidence_brief),
                portfolio_fit_score=_portfolio_fit_score(symbol, suggested_size_pct, portfolio_state),
                valuation_fit_score=_valuation_fit_score(packet),
                margin_of_safety_score=_margin_of_safety_score(packet),
                permanent_loss_score=_permanent_loss_score(packet),
                permanent_loss_flags=_permanent_loss_flags(packet),
                defensive_enterprising_mode="defensive_default",
                staged_entry_size_pct=0.0,
                staged_entry_label="not_applicable",
                normalized_earnings_quality=normalized_earnings_quality_label(packet),
                company_category=_get_company_category_str(packet),
                original_suggested_size_pct=suggested_size_pct,
                category_risk_adjusted_size_pct=suggested_size_pct,
                category_adjustment_applied=False,
                blockers=blockers,
                followups=followups,
                reasons=reasons,
            )

        if symbol in protected:
            blockers.append("protected_symbol")
            reasons.append("Protected symbols cannot be promoted into active-sleeve trade actions.")

        if suggested_size_pct <= 0:
            blockers.append("missing_positive_suggested_size")
            reasons.append("Suggested active-sleeve size must be positive.")

        if portfolio_state.holding_value(symbol) > 0:
            reasons.append("Candidate is already held; route to existing-position review instead of a new buy.")
            return self._review(
                row,
                symbol=symbol,
                first_pass_action=first_pass_action,
                promotion_decision="REVIEW_EXISTING_POSITION",
                confidence=confidence,
                suggested_size_pct=suggested_size_pct,
                evidence_score=_evidence_score(evidence_brief),
                portfolio_fit_score=_portfolio_fit_score(symbol, suggested_size_pct, portfolio_state),
                valuation_fit_score=_valuation_fit_score(packet),
                margin_of_safety_score=_margin_of_safety_score(packet),
                permanent_loss_score=_permanent_loss_score(packet),
                permanent_loss_flags=_permanent_loss_flags(packet),
                defensive_enterprising_mode="defensive_default",
                staged_entry_size_pct=0.0,
                staged_entry_label="not_applicable",
                normalized_earnings_quality=normalized_earnings_quality_label(packet),
                company_category=_get_company_category_str(packet),
                original_suggested_size_pct=suggested_size_pct,
                category_risk_adjusted_size_pct=suggested_size_pct,
                category_adjustment_applied=False,
                blockers=blockers,
                followups=followups,
                reasons=reasons,
            )

        evidence_score = _evidence_score(evidence_brief)
        portfolio_fit_score = _portfolio_fit_score(symbol, suggested_size_pct, portfolio_state)
        valuation_fit_score = _valuation_fit_score(packet)
        margin_of_safety_review = _margin_of_safety_review(packet)
        margin_of_safety_score = margin_of_safety_review.score
        permanent_loss_report = evaluate_permanent_loss_risk(packet)
        enable_cat_risk = getattr(profile, "enable_category_risk_sizing", False)
        staged_entry = evaluate_staged_entry(
            suggested_size_pct=effective_suggested_size_pct,
            margin_of_safety_score=margin_of_safety_score,
            risk_report=permanent_loss_report,
            company_category=company_category if enable_cat_risk else None,
            enable_category_risk_sizing=enable_cat_risk,
        )
        defensive_enterprising_mode = classify_defensive_enterprising_mode(
            {**dict(packet), "recommendation": first_pass_action},
            margin_of_safety_score=margin_of_safety_score,
            risk_report=permanent_loss_report,
        )
        earnings_quality = normalized_earnings_quality_label(packet)
        warning_text = _warning_text(packet)

        if confidence < ACTIONABLE_CONFIDENCE_THRESHOLD:
            followups.append("confidence_below_actionable_threshold")
            reasons.append(f"Confidence {confidence} is below actionable threshold {ACTIONABLE_CONFIDENCE_THRESHOLD}.")

        if "Article evidence:" not in evidence_brief:
            followups.append("missing_article_evidence")
            reasons.append("Evidence brief lacks article-level evidence summaries.")

        if "research_evidence_brief_v1" not in evidence_brief:
            followups.append("missing_versioned_evidence_brief")
            reasons.append("Versioned research evidence brief is missing.")

        if evidence_score < MIN_ACTIONABLE_EVIDENCE_SCORE:
            followups.append("evidence_score_below_actionable_threshold")
            reasons.append(f"Evidence score {evidence_score:g} is below actionable threshold {MIN_ACTIONABLE_EVIDENCE_SCORE}.")

        if _requires_margin_of_safety_followup(packet, margin_of_safety_review):
            followups.append("margin_of_safety_review")
            reasons.append(
                "Margin of safety review requires confirmation: "
                + "; ".join(margin_of_safety_review.objections[:2])
            )

        if permanent_loss_report.severity == "high":
            followups.append("permanent_loss_review")
            reasons.append(
                "Permanent capital-loss risks require confirmation: "
                + ", ".join(permanent_loss_report.flags[:4])
            )

        if earnings_quality == "needs_normalization":
            followups.append("normalized_earnings_review")
            reasons.append("Valuation relies on earnings that need normalization before action.")

        for marker in (
            "missing_source_urls",
            "low earnings confidence",
            "low earnings",
            "thin evidence",
            "missing_earnings_article",
        ):
            if marker in warning_text:
                followups.append(marker.replace(" ", "_"))
                reasons.append(f"Warning requires follow-up before action: {marker}.")

        # === Category-aware adjustments (Lynch-style) — opt-in only ===
        if getattr(profile, "enable_category_risk_sizing", False):
            superscore = 0
            try:
                superscore = float(packet.get("quality_growth_scorecard", {}).get("superscore", 0))
            except Exception:
                pass

            if company_category in ("cyclical", "turnaround", "asset_play"):
                # Stricter standards for harder-to-underwrite categories
                if evidence_score < MIN_ACTIONABLE_EVIDENCE_SCORE + 8:
                    followups.append("category_strict_evidence")
                    reasons.append(f"{company_category.title()} requires stronger evidence (score {evidence_score:.0f}).")

                if margin_of_safety_score < 68:
                    followups.append("category_strict_margin")
                    reasons.append(f"{company_category.title()} requires higher margin of safety.")

                # Cap starter size more aggressively for these categories
                if staged_entry.label == "starter_position" and staged_entry.recommended_size_pct > 1.5:
                    staged_entry = StagedEntryPlan(
                        label=staged_entry.label,
                        recommended_size_pct=1.5,
                        original_size_pct=staged_entry.original_size_pct,
                        reason=f"Category risk cap applied for {company_category}",
                    )

            elif company_category == "fast_grower" and superscore > 72:
                # High-quality fast growers can be slightly more lenient
                if "margin_of_safety_review" in followups and margin_of_safety_score > 55:
                    followups = [f for f in followups if f != "margin_of_safety_review"]
                    reasons = [r for r in reasons if "Margin of safety review" not in r]

        if blockers:
            promotion_decision = "BLOCKED"
        elif followups:
            promotion_decision = (
                "WATCHLIST_PENDING_EVIDENCE"
                if any(
                    "evidence" in item
                    or "source" in item
                    or item in {"missing_earnings_article", "low_earnings_confidence", "low_earnings"}
                    for item in followups
                )
                else "WATCHLIST_PENDING_CONFIRMATION"
            )
        else:
            promotion_decision = "ACTIONABLE_BUY"
            reasons.append("First-pass BUY cleared promotion review for dry-run account planning.")
            if staged_entry.recommended_size_pct < effective_suggested_size_pct:
                reasons.append(staged_entry.reason)

        return self._review(
            row,
            symbol=symbol,
            first_pass_action=first_pass_action,
            promotion_decision=promotion_decision,
            confidence=confidence,
            suggested_size_pct=effective_suggested_size_pct,
            evidence_score=evidence_score,
            portfolio_fit_score=portfolio_fit_score,
            valuation_fit_score=valuation_fit_score,
            margin_of_safety_score=margin_of_safety_score,
            permanent_loss_score=permanent_loss_report.score,
            permanent_loss_flags=permanent_loss_report.flags,
            defensive_enterprising_mode=defensive_enterprising_mode,
            staged_entry_size_pct=staged_entry.recommended_size_pct,
            staged_entry_label=staged_entry.label,
            normalized_earnings_quality=earnings_quality,
            company_category=company_category,
            original_suggested_size_pct=original_llm_size,
            category_risk_adjusted_size_pct=effective_suggested_size_pct,
            category_adjustment_applied=category_adjustment_applied,
            blockers=blockers,
            followups=_dedupe(followups),
            reasons=_dedupe(reasons),
        )

    def _review(
        self,
        row: Mapping[str, Any],
        *,
        symbol: str,
        first_pass_action: str,
        promotion_decision: str,
        confidence: int,
        suggested_size_pct: float,
        evidence_score: float,
        portfolio_fit_score: float,
        valuation_fit_score: float,
        margin_of_safety_score: float,
        permanent_loss_score: float,
        permanent_loss_flags: list[str],
        defensive_enterprising_mode: str,
        staged_entry_size_pct: float,
        staged_entry_label: str,
        normalized_earnings_quality: str,
        company_category: str = "",
        original_suggested_size_pct: float = 0.0,
        category_risk_adjusted_size_pct: float = 0.0,
        category_adjustment_applied: bool = False,
        blockers: list[str],
        followups: list[str],
        reasons: list[str],
    ) -> BuyPromotionReview:
        final_size = category_risk_adjusted_size_pct or original_suggested_size_pct or suggested_size_pct
        return BuyPromotionReview(
            symbol=symbol,
            decision_id=str(row.get("decision_id") or ""),
            first_pass_action=first_pass_action,
            promotion_decision=promotion_decision,
            confidence=confidence,
            suggested_size_pct=final_size,
            evidence_score=round(evidence_score, 2),
            portfolio_fit_score=round(portfolio_fit_score, 2),
            valuation_fit_score=round(valuation_fit_score, 2),
            margin_of_safety_score=round(margin_of_safety_score, 2),
            permanent_loss_score=round(permanent_loss_score, 2),
            permanent_loss_flags=_dedupe(permanent_loss_flags),
            defensive_enterprising_mode=defensive_enterprising_mode,
            staged_entry_size_pct=round(staged_entry_size_pct, 2),
            staged_entry_label=staged_entry_label,
            normalized_earnings_quality=normalized_earnings_quality,
            company_category=company_category,
            original_suggested_size_pct=original_suggested_size_pct or suggested_size_pct,
            category_risk_adjusted_size_pct=category_risk_adjusted_size_pct or final_size,
            category_adjustment_applied=category_adjustment_applied,
            blockers=_dedupe(blockers),
            followups=_dedupe(followups),
            reasons=_dedupe(reasons),
        )


def build_buy_promotion_reviews(
    journal: LongTermDecisionJournal,
    *,
    profile: PortfolioProfile,
    portfolio_state: PortfolioState,
    limit: int = 20,
    reviewer: BuyPromotionReviewer | None = None,
) -> list[BuyPromotionReview]:
    """Build promotion reviews from the latest recommendation table rows."""
    active_reviewer = reviewer or BuyPromotionReviewer()
    reviews: list[BuyPromotionReview] = []
    for row in journal.list_recommendation_table(limit=limit):
        packet = _load_packet(row)
        reviews.append(
            active_reviewer.evaluate_decision_row(
                row,
                packet=packet,
                profile=profile,
                portfolio_state=portfolio_state,
            )
        )
    return reviews


def build_buy_promotion_markdown(reviews: list[BuyPromotionReview]) -> str:
    lines = [
        "# Buy Promotion Review",
        "",
        "| Symbol | Category | Promotion | First Pass | Confidence | Size % | Entry Plan | Evidence | Valuation Fit | Margin Safety | Perm Loss | Mode | Blockers | Followups | Reasons |",
        "|---|---|---|---|---:|---:|---|---:|---:|---:|---|---|---|---|---|",
    ]
    for review in reviews:
        size_display = f"{review.suggested_size_pct:g}%"
        if review.original_suggested_size_pct and review.category_risk_adjusted_size_pct and abs(review.original_suggested_size_pct - review.category_risk_adjusted_size_pct) > 0.1:
            size_display = f"{review.category_risk_adjusted_size_pct:g}% (LLM suggested {review.original_suggested_size_pct:g}%)"

        lines.append(
            "| {symbol} | {category} | {promotion} | {first_pass} | {confidence} | {size_display} | {entry_label} ({entry_size:g}%) | {evidence:g} | {valuation:g} | {margin:g} | {perm_loss:g}: {flags} | {mode} | {blockers} | {followups} | {reasons} |".format(
                symbol=review.symbol,
                category=review.company_category or "-",
                promotion=review.promotion_decision,
                first_pass=review.first_pass_action,
                confidence=review.confidence,
                size_display=size_display,
                entry_label=review.staged_entry_label,
                entry_size=review.staged_entry_size_pct,
                evidence=review.evidence_score,
                valuation=review.valuation_fit_score,
                margin=review.margin_of_safety_score,
                perm_loss=review.permanent_loss_score,
                flags=_safe_cell(", ".join(review.permanent_loss_flags) or "none"),
                mode=review.defensive_enterprising_mode,
                blockers=_safe_cell(", ".join(review.blockers)),
                followups=_safe_cell(", ".join(review.followups)),
                reasons=_safe_cell("; ".join(review.reasons)),
            )
        )
    return "\n".join(lines) + "\n"


def _evidence_score(evidence_brief: str) -> float:
    if not evidence_brief:
        return 0.0
    score = 10.0
    weighted_checks = {
        "research_evidence_brief_v1": 15.0,
        "Fundamentals:": 15.0,
        "Scorecard:": 10.0,
        "Latest earnings:": 10.0,
        "Primary news:": 10.0,
        "Article evidence:": 30.0,
        "Grok catalyst synthesis:": 20.0,
    }
    score += sum(weight for check, weight in weighted_checks.items() if check in evidence_brief)
    if "Warnings:" in evidence_brief:
        score -= 5.0
    return max(0.0, min(100.0, score))


def _portfolio_fit_score(symbol: str, suggested_size_pct: float, portfolio_state: PortfolioState) -> float:
    if portfolio_state.holding_value(symbol) > 0:
        return 60.0
    if suggested_size_pct <= 0:
        return 0.0
    if suggested_size_pct <= 5:
        return 100.0
    if suggested_size_pct <= 8:
        return 80.0
    return 60.0


def _valuation_fit_score(packet: Mapping[str, Any]) -> float:
    value = packet.get("valuation_score")
    if value is not None:
        return max(0.0, min(100.0, _float(value)))
    evidence = str(packet.get("evidence_brief") or "").lower()
    if "valuation 0" in evidence or "valuation score 0" in evidence:
        return 0.0
    if "valuation" in evidence:
        return 60.0
    return 50.0


def _margin_of_safety_review(packet: Mapping[str, Any]):
    return MarginOfSafetyReviewer().review(create_research_packet_from_idea(dict(packet)))


def _margin_of_safety_score(packet: Mapping[str, Any]) -> float:
    return _margin_of_safety_review(packet).score


def _permanent_loss_score(packet: Mapping[str, Any]) -> float:
    return evaluate_permanent_loss_risk(packet).score


def _permanent_loss_flags(packet: Mapping[str, Any]) -> list[str]:
    return evaluate_permanent_loss_risk(packet).flags


def _requires_margin_of_safety_followup(packet: Mapping[str, Any], review: Any) -> bool:
    if review.score >= MARGIN_OF_SAFETY_FOLLOWUP_THRESHOLD:
        return False
    valuation_value = packet.get("valuation_score")
    if valuation_value is not None and _float(valuation_value) < 45:
        return True
    text = _packet_text(packet)
    return any(marker in text for marker in (*_OVERPAYMENT_MARKERS, *_PERMANENT_LOSS_MARKERS))


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


def _warning_text(packet: Mapping[str, Any]) -> str:
    pieces = [str(packet.get("evidence_brief") or "")]
    for key in (
        "fundamental_metrics",
        "quality_growth_scorecard",
        "latest_earnings_enrichment",
        "grok_research_enrichment",
    ):
        value = packet.get(key)
        if isinstance(value, Mapping):
            pieces.extend(str(item) for item in value.get("warnings") or [])
    return " ".join(pieces).lower()


def _load_packet(row: Mapping[str, Any]) -> Mapping[str, Any]:
    packet_json = row.get("packet_json")
    if isinstance(packet_json, str) and packet_json.strip():
        try:
            payload = json.loads(packet_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, Mapping):
            return payload
    return {
        "symbol": row.get("symbol") or "",
        "company_name": row.get("company_name") or "",
    }


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _safe_cell(value: str) -> str:
    return str(value or "").replace("|", "/")


__all__ = [
    "BuyPromotionReview",
    "BuyPromotionReviewer",
    "build_buy_promotion_reviews",
    "build_buy_promotion_markdown",
]
