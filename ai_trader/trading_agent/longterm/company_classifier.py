"""Lynch-style company classification helpers."""

from typing import Any, Mapping

from research.research_packet import CompanyCategory


def classify_company(
    revenue_growth_pct: float,
    earnings_growth_pct: float,
    *,
    is_cyclical: bool = False,
    turnaround_signals: bool = False,
    asset_play_signals: bool = False,
) -> CompanyCategory:
    """Classify a company into a Lynch-style long-term research bucket."""
    if turnaround_signals:
        return CompanyCategory.TURNAROUND
    if asset_play_signals:
        return CompanyCategory.ASSET_PLAY
    if is_cyclical:
        return CompanyCategory.CYCLICAL

    average_growth = (float(revenue_growth_pct) + float(earnings_growth_pct)) / 2.0
    if average_growth >= 20.0:
        return CompanyCategory.FAST_GROWER
    if average_growth >= 7.0:
        return CompanyCategory.STALWART
    return CompanyCategory.SLOW_GROWER


def classify_from_idea(idea: Mapping[str, Any]) -> CompanyCategory:
    """
    Robust classifier that extracts the best available growth and context signals
    from the data structures present in the long-term research pipeline.

    Tries multiple sources for growth rates and includes secondary signals
    for Turnaround, Asset Play, and Cyclical detection.
    """
    # === Explicit override signals (highest priority) ===
    if _has_turnaround_signals(idea):
        return CompanyCategory.TURNAROUND
    if _has_asset_play_signals(idea):
        return CompanyCategory.ASSET_PLAY
    if _is_explicitly_cyclical(idea):
        return CompanyCategory.CYCLICAL

    # === Gather growth metrics from all likely locations ===
    rev_growth, eps_growth = _extract_growth_rates(idea)

    # === Secondary context for borderline cases ===
    if _looks_like_turnaround(idea, rev_growth, eps_growth):
        return CompanyCategory.TURNAROUND

    if _looks_like_asset_play(idea):
        return CompanyCategory.ASSET_PLAY

    if _looks_like_cyclical(idea):
        return CompanyCategory.CYCLICAL

    # === Final classification based on growth ===
    return classify_company(rev_growth, eps_growth)


def _extract_growth_rates(idea: Mapping[str, Any]) -> tuple[float, float]:
    """Extract the best available revenue and earnings growth figures."""
    scorecard = idea.get("quality_growth_scorecard") or {}
    metrics = (
        idea.get("fundamental_metrics")
        or scorecard.get("fundamental_metrics")
        or idea.get("metrics")
        or {}
    )

    # Revenue growth — prefer longer-term CAGR, then recent
    growth_block = metrics.get("revenue_growth_cagr") or metrics.get("growth") or {}
    rev_candidates = [
        growth_block.get("3_yr_revenue_growth"),
        growth_block.get("5_yr_revenue_growth"),
        growth_block.get("revenue_cagr_3y"),
        metrics.get("revenue_growth_ttm"),
        idea.get("revenue_growth_pct"),
        idea.get("revenue_cagr"),
    ]
    rev = _best_numeric(rev_candidates)

    # Earnings growth
    eps_candidates = [
        growth_block.get("3_yr_eps_growth"),
        growth_block.get("3_yr_ebitda_growth"),
        growth_block.get("5_yr_eps_growth"),
        metrics.get("eps_growth_ttm"),
        idea.get("earnings_growth_pct"),
        idea.get("eps_cagr"),
    ]
    eps = _best_numeric(eps_candidates)

    return rev, eps


def _best_numeric(candidates: list[Any]) -> float:
    for c in candidates:
        if c is None:
            continue
        try:
            val = float(c)
            if val == val:  # not NaN
                return val
        except (TypeError, ValueError):
            continue
    return 0.0


def _has_turnaround_signals(idea: Mapping[str, Any]) -> bool:
    keys = ["turnaround_signals", "is_turnaround", "turnaround_candidate"]
    return any(bool(idea.get(k)) for k in keys)


def _has_asset_play_signals(idea: Mapping[str, Any]) -> bool:
    keys = ["asset_play_signals", "is_asset_play", "net_cash_position", "asset_value_focus"]
    if any(bool(idea.get(k)) for k in keys):
        return True
    # Look for common asset play language in notes or thesis
    text = " ".join(str(x).lower() for x in (idea.get("thesis", ""), idea.get("thesis_summary", ""), str(idea.get("source_notes", ""))))
    asset_keywords = ["net cash", "asset value", "book value", "liquidation", "hidden assets"]
    return any(kw in text for kw in asset_keywords)


def _is_explicitly_cyclical(idea: Mapping[str, Any]) -> bool:
    if idea.get("is_cyclical"):
        return True
    cat = str(idea.get("company_category") or "").lower()
    return cat == "cyclical"


def _looks_like_turnaround(idea: Mapping[str, Any], rev_growth: float, eps_growth: float) -> bool:
    """Detect improving but still low/negative growth with positive recent inflection."""
    recent_improvement = bool(idea.get("earnings_inflection") or idea.get("recent_turnaround"))
    low_but_improving = (rev_growth < 5 or eps_growth < 3) and (rev_growth > -5 or eps_growth > -5)
    return recent_improvement or (low_but_improving and _has_turnaround_language(idea))


def _looks_like_asset_play(idea: Mapping[str, Any]) -> bool:
    text = " ".join(str(x).lower() for x in (
        idea.get("business_summary", ""),
        idea.get("thesis", ""),
        str(idea.get("source_notes", [])),
    ))
    return any(kw in text for kw in ["net cash", "cash rich", "asset rich", "liquidation value", "hidden value"])


def _looks_like_cyclical(idea: Mapping[str, Any]) -> bool:
    text = " ".join(str(x).lower() for x in (
        idea.get("business_summary", ""),
        idea.get("industry", ""),
        str(idea.get("source_notes", [])),
    ))
    cyclical_words = ["cyclical", "commodity", "housing", "auto", "steel", "oil", "chemical", "paper"]
    return any(word in text for word in cyclical_words)


def _has_turnaround_language(idea: Mapping[str, Any]) -> bool:
    text = " ".join(str(x).lower() for x in (
        idea.get("thesis", ""),
        str(idea.get("source_notes", [])),
        idea.get("business_summary", ""),
    ))
    return any(phrase in text for phrase in ["turning around", "inflection", "recovery", "restructuring", "new management"])
