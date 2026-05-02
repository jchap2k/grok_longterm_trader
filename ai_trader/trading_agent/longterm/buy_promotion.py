"""Dry-run promotion review between first-pass BUY decisions and action planning."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


ACTIONABLE_CONFIDENCE_THRESHOLD = 70
MIN_ACTIONABLE_EVIDENCE_SCORE = 70


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
                blockers=blockers,
                followups=followups,
                reasons=reasons,
            )

        evidence_score = _evidence_score(evidence_brief)
        portfolio_fit_score = _portfolio_fit_score(symbol, suggested_size_pct, portfolio_state)
        valuation_fit_score = _valuation_fit_score(packet)
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

        if blockers:
            promotion_decision = "BLOCKED"
        elif followups:
            promotion_decision = (
                "WATCHLIST_PENDING_EVIDENCE"
                if any("evidence" in item or "source" in item or "earnings" in item for item in followups)
                else "WATCHLIST_PENDING_CONFIRMATION"
            )
        else:
            promotion_decision = "ACTIONABLE_BUY"
            reasons.append("First-pass BUY cleared promotion review for dry-run account planning.")

        return self._review(
            row,
            symbol=symbol,
            first_pass_action=first_pass_action,
            promotion_decision=promotion_decision,
            confidence=confidence,
            suggested_size_pct=suggested_size_pct,
            evidence_score=evidence_score,
            portfolio_fit_score=portfolio_fit_score,
            valuation_fit_score=valuation_fit_score,
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
        blockers: list[str],
        followups: list[str],
        reasons: list[str],
    ) -> BuyPromotionReview:
        return BuyPromotionReview(
            symbol=symbol,
            decision_id=str(row.get("decision_id") or ""),
            first_pass_action=first_pass_action,
            promotion_decision=promotion_decision,
            confidence=confidence,
            suggested_size_pct=suggested_size_pct,
            evidence_score=round(evidence_score, 2),
            portfolio_fit_score=round(portfolio_fit_score, 2),
            valuation_fit_score=round(valuation_fit_score, 2),
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
        "| Symbol | Promotion | First Pass | Confidence | Size % | Evidence | Portfolio Fit | Valuation Fit | Blockers | Followups | Reasons |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for review in reviews:
        lines.append(
            "| {symbol} | {promotion} | {first_pass} | {confidence} | {size:g} | {evidence:g} | {portfolio:g} | {valuation:g} | {blockers} | {followups} | {reasons} |".format(
                symbol=review.symbol,
                promotion=review.promotion_decision,
                first_pass=review.first_pass_action,
                confidence=review.confidence,
                size=review.suggested_size_pct,
                evidence=review.evidence_score,
                portfolio=review.portfolio_fit_score,
                valuation=review.valuation_fit_score,
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
