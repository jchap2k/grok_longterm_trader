"""Deterministic quality-growth scorecard for long-term research ideas."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping


def enrich_idea_with_quality_growth_scorecard(idea: Mapping[str, Any], *, as_of_date: str | None = None) -> dict[str, Any]:
    """Attach an auditable Python-derived quality-growth scorecard."""
    payload = dict(idea)
    payload["symbol"] = str(payload.get("symbol") or "").upper()
    scorecard = build_quality_growth_scorecard(payload, as_of_date=as_of_date)
    payload["quality_growth_scorecard"] = scorecard
    payload["quality_score"] = scorecard["quality_score"]
    payload["valuation_score"] = scorecard["valuation_score"]
    notes = _note_list(payload.get("source_notes"))
    notes.append(
        "Python quality-growth scorecard: deterministic composite from fundamental metrics and relevant-news context."
    )
    payload["source_notes"] = _dedupe(notes)
    return payload


def enrich_ideas_with_quality_growth_scorecard(
    ideas: list[Mapping[str, Any]],
    *,
    as_of_date: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = ideas[:limit] if limit is not None else ideas
    return [enrich_idea_with_quality_growth_scorecard(idea, as_of_date=as_of_date) for idea in selected]


def build_quality_growth_scorecard(idea: Mapping[str, Any], *, as_of_date: str | None = None) -> dict[str, Any]:
    """Build a Fool-like but non-proprietary long-term scorecard."""
    metrics = dict(idea.get("fundamental_metrics") or {})
    news = [dict(item) for item in idea.get("relevant_news") or [] if isinstance(item, Mapping)]
    quality, quality_reasons = _quality_score(metrics)
    growth, growth_reasons = _growth_score(metrics)
    valuation, valuation_reasons = _valuation_score(metrics)
    valuation_sanity, valuation_sanity_reasons = _valuation_sanity_score(metrics)
    safety, safety_reasons = _safety_score(metrics)
    attention, attention_reasons = _market_attention_score(news)
    superscore = round(
        (quality * 0.28)
        + (growth * 0.24)
        + (valuation * 0.18)
        + (safety * 0.16)
        + (attention * 0.14),
        1,
    )
    reasons = [
        *quality_reasons,
        *growth_reasons,
        *valuation_reasons,
        *valuation_sanity_reasons,
        *safety_reasons,
        *attention_reasons,
    ]
    return {
        "symbol": str(idea.get("symbol") or metrics.get("symbol") or "").upper(),
        "as_of_date": as_of_date or date.today().isoformat(),
        "source_type": "python_quality_growth_scorecard",
        "basis": "deterministic_model",
        "superscore": superscore,
        "quality_score": round(quality, 1),
        "growth_score": round(growth, 1),
        "valuation_score": round(valuation, 1),
        "valuation_sanity_score": round(valuation_sanity, 1),
        "valuation_sanity_reasons": valuation_sanity_reasons,
        "safety_score": round(safety, 1),
        "market_attention_score": round(attention, 1),
        "investing_type": _investing_type(superscore, quality, valuation, safety),
        "estimated_drawdown_band": _drawdown_band(safety, valuation, attention),
        "score_reasons": reasons,
        "warnings": _warnings(metrics),
    }


def _quality_score(metrics: Mapping[str, Any]) -> tuple[float, list[str]]:
    profitability = metrics.get("profitability_ttm") or {}
    gross = _pct(profitability.get("gross_margin"))
    op = _pct(profitability.get("operating_margin"))
    fcf = _pct(profitability.get("free_cash_flow_margin"))
    roe = _pct(profitability.get("return_on_equity"))
    score = 0.0
    reasons = []
    score += _bucket(gross, [(60, 30), (40, 25), (25, 18), (15, 10)], "gross margin", reasons)
    score += _bucket(op, [(30, 25), (20, 22), (10, 14), (5, 8)], "operating margin", reasons)
    score += _bucket(fcf, [(25, 20), (15, 17), (8, 10), (3, 5)], "free cash flow margin", reasons)
    score += _bucket(roe, [(30, 25), (20, 22), (12, 14), (5, 7)], "return on equity", reasons)
    return min(100.0, score), reasons


def _growth_score(metrics: Mapping[str, Any]) -> tuple[float, list[str]]:
    growth = metrics.get("revenue_growth_cagr") or {}
    revenue = _pct(growth.get("3_yr_revenue_growth"))
    ebitda = _pct(growth.get("3_yr_ebitda_growth"))
    eps = _pct(growth.get("3_yr_eps_growth"))
    fcf = _pct(growth.get("3_yr_fcf_per_share_growth"))
    score = 0.0
    reasons = []
    score += _bucket(revenue, [(25, 30), (15, 25), (8, 17), (3, 8)], "revenue growth", reasons)
    score += _bucket(ebitda, [(25, 25), (15, 20), (8, 12), (3, 6)], "EBITDA growth", reasons)
    score += _bucket(eps, [(20, 20), (12, 16), (5, 9), (0, 4)], "EPS growth", reasons)
    score += _bucket(fcf, [(20, 25), (12, 20), (5, 12), (0, 5)], "FCF/share growth", reasons)
    return min(100.0, score), reasons


def _valuation_score(metrics: Mapping[str, Any]) -> tuple[float, list[str]]:
    valuation = metrics.get("valuation_ttm") or {}
    pe = _multiple(valuation.get("price_earnings"))
    ev_ebitda = _multiple(valuation.get("ev_ebitda"))
    p_fcf = _multiple(valuation.get("price_free_cash_flow"))
    peg = _multiple(valuation.get("price_earnings_growth_5yr"))
    reasons = []
    score = 0.0
    score += _inverse_bucket(pe, [(20, 28), (35, 22), (60, 12), (100, 6)], "P/E", reasons)
    score += _inverse_bucket(ev_ebitda, [(15, 25), (25, 19), (45, 10), (80, 5)], "EV/EBITDA", reasons)
    score += _inverse_bucket(p_fcf, [(20, 25), (35, 18), (60, 9), (100, 4)], "P/FCF", reasons)
    score += _inverse_bucket(peg, [(1.5, 22), (2.5, 16), (4, 8), (8, 4)], "PEG", reasons)
    return min(100.0, score), reasons


def _valuation_sanity_score(metrics: Mapping[str, Any]) -> tuple[float, list[str]]:
    """Add Graham/Greenblatt-style valuation context without replacing valuation_score."""
    valuation = metrics.get("valuation_ttm") or {}
    profitability = metrics.get("profitability_ttm") or {}
    growth = metrics.get("revenue_growth_cagr") or {}
    financials = metrics.get("financials_ttm") or {}
    pe = _multiple(valuation.get("price_earnings"))
    p_fcf = _multiple(valuation.get("price_free_cash_flow"))
    peg = _multiple(valuation.get("price_earnings_growth_5yr"))
    roic = _pct(
        profitability.get("return_on_invested_capital")
        or profitability.get("return_on_capital")
        or profitability.get("return_on_capital_employed")
    )
    fcf_growth = _pct(growth.get("3_yr_fcf_per_share_growth"))
    total_cash = _compact_to_number(financials.get("total_cash"))
    total_debt = _compact_to_number(financials.get("total_debt"))
    score = 35.0
    reasons: list[str] = []

    if p_fcf is not None and p_fcf > 0:
        fcf_yield = 100.0 / p_fcf
        if fcf_yield >= 5:
            score += 20
            reasons.append(f"FCF yield {fcf_yield:.1f}% supports valuation sanity")
        elif fcf_yield >= 3:
            score += 12
            reasons.append(f"FCF yield {fcf_yield:.1f}% is acceptable but not cheap")
        else:
            score -= 12
            reasons.append(f"FCF yield {fcf_yield:.1f}% leaves little cash-flow cushion")

    if pe is not None and pe > 0:
        earnings_yield = 100.0 / pe
        if earnings_yield >= 4:
            score += 14
            reasons.append(f"earnings yield {earnings_yield:.1f}% supports price discipline")
        elif earnings_yield < 1.5:
            score -= 10
            reasons.append(f"earnings yield {earnings_yield:.1f}% is thin")

    if peg is not None and peg > 0:
        if peg <= 1.5:
            score += 12
            reasons.append("PEG supports growth-adjusted valuation")
        elif peg > 4:
            score -= 10
            reasons.append("PEG is stretched versus growth")

    if roic is not None:
        if roic >= 20:
            score += 12
            reasons.append("high return on invested capital supports premium tolerance")
        elif roic < 8:
            score -= 8
            reasons.append("low return on invested capital weakens valuation support")

    if fcf_growth is not None:
        if fcf_growth >= 10:
            score += 7
            reasons.append("FCF/share growth supports normalized cash-flow durability")
        elif fcf_growth < 0:
            score -= 7
            reasons.append("negative FCF/share growth weakens normalized valuation support")

    if total_cash is not None and total_debt is not None:
        if total_cash >= total_debt:
            score += 5
            reasons.append("cash exceeds debt in valuation sanity check")
        elif total_debt > total_cash * 2:
            score -= 8
            reasons.append("debt materially exceeds cash in valuation sanity check")

    if not reasons:
        reasons.append("valuation sanity has limited normalized earnings or cash-flow inputs")
    return max(0.0, min(100.0, score)), reasons


def _safety_score(metrics: Mapping[str, Any]) -> tuple[float, list[str]]:
    profitability = metrics.get("profitability_ttm") or {}
    financials = metrics.get("financials_ttm") or {}
    debt_equity = _multiple(profitability.get("debt_equity"))
    fcf_margin = _pct(profitability.get("free_cash_flow_margin"))
    total_cash = _compact_to_number(financials.get("total_cash"))
    total_debt = _compact_to_number(financials.get("total_debt"))
    reasons = []
    score = 25.0
    score += _inverse_bucket(debt_equity, [(0.3, 25), (0.7, 18), (1.2, 8), (2.0, 2)], "debt/equity", reasons)
    if debt_equity is not None and debt_equity > 1.2:
        reasons.append("elevated debt/equity")
    score += _bucket(fcf_margin, [(20, 25), (12, 18), (6, 10), (0, 3)], "FCF margin", reasons)
    if total_cash is not None and total_debt is not None:
        if total_cash >= total_debt:
            score += 15
            reasons.append("cash exceeds debt")
        elif total_debt > total_cash * 2:
            score -= 10
            reasons.append("debt meaningfully exceeds cash")
    return max(0.0, min(100.0, score)), reasons


def _market_attention_score(news: list[Mapping[str, Any]]) -> tuple[float, list[str]]:
    if not news:
        return 25.0, ["limited recent high-signal news"]
    score = 35.0
    reasons = []
    high_impact = 0
    source_boost = 0
    for item in news[:5]:
        relevance = _num(item.get("relevance_score")) or 0.0
        score += min(12.0, relevance * 10)
        if "High" in str(item.get("impact_category") or ""):
            high_impact += 1
            score += 6
        source = str(item.get("source") or "").lower()
        if any(name in source for name in ("reuters", "bloomberg", "yahoo finance", "wall street journal")):
            source_boost += 1
            score += 4
    if high_impact:
        reasons.append(f"{high_impact} high-impact relevant news item(s)")
    if source_boost:
        reasons.append(f"{source_boost} quality-source news item(s)")
    return min(100.0, score), reasons


def _investing_type(superscore: float, quality: float, valuation: float, safety: float) -> str:
    if superscore >= 82 and quality >= 75 and safety >= 65:
        return "Cautious Compounder" if valuation >= 55 else "Premium Compounder"
    if superscore >= 65 and quality >= 60:
        return "Moderate Compounder"
    if superscore >= 50 and quality >= 45:
        return "Aggressive Growth"
    return "Speculative / Watchlist"


def _drawdown_band(safety: float, valuation: float, attention: float) -> str:
    if safety >= 75 and valuation >= 55:
        return "-20% to -35%"
    if safety >= 55 and valuation >= 35:
        return "-30% to -45%"
    if attention >= 70 and valuation < 35:
        return "-45% to -65%"
    return "-40% to -60%"


def _bucket(value: float | None, tiers: list[tuple[float, float]], label: str, reasons: list[str]) -> float:
    if value is None:
        return 0.0
    for threshold, points in tiers:
        if value >= threshold:
            if points >= tiers[0][1] * 0.65:
                reasons.append(f"strong {label}")
            return points
    return 0.0


def _inverse_bucket(value: float | None, tiers: list[tuple[float, float]], label: str, reasons: list[str]) -> float:
    if value is None or value <= 0:
        return 0.0
    for threshold, points in tiers:
        if value <= threshold:
            if points >= tiers[0][1] * 0.65:
                reasons.append(f"reasonable {label}")
            return points
    reasons.append(f"expensive {label}")
    return 0.0


def _warnings(metrics: Mapping[str, Any]) -> list[str]:
    warnings = []
    if not metrics:
        warnings.append("missing_fundamental_metrics")
    if (metrics.get("source_type") or "") != "python_fundamental_metrics":
        warnings.append("unexpected_fundamental_metrics_source")
    return warnings


def _pct(value: Any) -> float | None:
    return _num(str(value).replace("%", "")) if value not in ("", None, "N/A") else None


def _multiple(value: Any) -> float | None:
    return _num(str(value).replace("x", "")) if value not in ("", None, "N/A") else None


def _num(value: Any) -> float | None:
    if value in ("", None, "N/A"):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("%", "").replace("x", "").strip())
    except ValueError:
        return None


def _compact_to_number(value: Any) -> float | None:
    if value in ("", None, "N/A"):
        return None
    text = str(value).split(" (")[0].replace("$", "").replace(",", "").strip()
    multiplier = 1.0
    if text.endswith("T"):
        multiplier = 1_000_000_000_000
        text = text[:-1]
    elif text.endswith("B"):
        multiplier = 1_000_000_000
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]
    number = _num(text)
    return None if number is None else number * multiplier


def _note_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


__all__ = [
    "build_quality_growth_scorecard",
    "enrich_idea_with_quality_growth_scorecard",
    "enrich_ideas_with_quality_growth_scorecard",
]
