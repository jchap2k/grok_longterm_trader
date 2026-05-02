"""Python-computed fundamental metrics for long-term research enrichment."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable, Mapping


def enrich_idea_with_fundamental_metrics(
    idea: Mapping[str, Any],
    raw_metrics: Mapping[str, Any],
    *,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Attach deterministic Fool-like financial metric sections to one idea."""
    payload = dict(idea)
    payload["symbol"] = str(payload.get("symbol") or raw_metrics.get("symbol") or "").upper()
    metrics = normalize_fundamental_metrics(raw_metrics, as_of_date=as_of_date)
    payload["fundamental_metrics"] = metrics
    payload["valuation_score"] = _valuation_score(metrics)
    payload["quality_score"] = _quality_score(metrics)
    balance_sheet = _balance_sheet_assessment(metrics)
    if balance_sheet:
        payload["balance_sheet_assessment"] = balance_sheet
    notes = _note_list(payload.get("source_notes"))
    notes.append(
        "Python fundamental metrics: growth CAGR, TTM valuation, profitability, and financial tables computed from provider data."
    )
    payload["source_notes"] = _dedupe(notes)
    return payload


def enrich_ideas_with_fundamental_metrics(
    ideas: list[Mapping[str, Any]],
    metrics_by_symbol: Mapping[str, Mapping[str, Any]],
    *,
    as_of_date: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Apply symbol-keyed raw fundamentals to a batch of ideas."""
    normalized = {str(symbol).upper(): dict(value) for symbol, value in metrics_by_symbol.items()}
    selected = ideas[:limit] if limit is not None else ideas
    enriched = []
    for idea in selected:
        symbol = str(idea.get("symbol") or "").upper()
        raw = normalized.get(symbol)
        if raw:
            enriched.append(enrich_idea_with_fundamental_metrics(idea, raw, as_of_date=as_of_date))
        else:
            enriched.append(dict(idea))
    return enriched


def normalize_fundamental_metrics(raw: Mapping[str, Any], *, as_of_date: str | None = None) -> dict[str, Any]:
    """Normalize raw provider data into Fool-like financial table sections."""
    symbol = str(raw.get("symbol") or "").upper()
    annual = [dict(item) for item in raw.get("annual") or [] if isinstance(item, Mapping)]
    annual.sort(key=lambda item: item.get("fiscal_year") or item.get("year") or 0)
    ttm = dict(raw.get("ttm") or {})
    previous_ttm = dict(raw.get("previous_ttm") or {})
    market_cap = _num(raw.get("market_cap"))
    enterprise_value = _num(raw.get("enterprise_value")) or market_cap
    shares = _num(raw.get("shares_outstanding"))

    return {
        "symbol": symbol,
        "as_of_date": str(raw.get("as_of_date") or as_of_date or date.today().isoformat()),
        "source_type": "python_fundamental_metrics",
        "currency": str(raw.get("currency") or "USD"),
        "revenue_growth_cagr": _growth_cagr_table(annual),
        "valuation_ttm": _valuation_table(
            ttm,
            market_cap=market_cap,
            enterprise_value=enterprise_value,
            shares_outstanding=shares,
            earnings_growth_5y_pct=_num(raw.get("earnings_growth_5y_pct")),
        ),
        "profitability_ttm": _profitability_table(ttm),
        "financials_ttm": _financials_table(ttm, previous_ttm),
        "warnings": _warnings(raw, annual, ttm),
    }


def fetch_yfinance_fundamental_metrics(
    symbol: str,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Fetch raw fundamentals from yfinance into the provider-neutral schema."""
    normalized_symbol = symbol.upper()
    if ticker_factory is None:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("Install yfinance to fetch live fundamental metrics.") from exc
        ticker_factory = yf.Ticker

    ticker = ticker_factory(normalized_symbol)
    info = dict(getattr(ticker, "info", {}) or {})
    financials = getattr(ticker, "financials", None)
    cashflow = getattr(ticker, "cashflow", None)
    balance_sheet = getattr(ticker, "balance_sheet", None)
    years = _frame_columns(financials)
    annual = []
    for column in reversed(years[:4]):
        annual.append(
            {
                "fiscal_year": str(column)[:4],
                "revenue": _frame_value(financials, column, ["Total Revenue", "Operating Revenue"]),
                "operating_income": _frame_value(financials, column, ["Operating Income"]),
                "eps": _frame_value(financials, column, ["Diluted EPS", "Basic EPS"]),
                "ebitda": _frame_value(financials, column, ["EBITDA"]),
                "free_cash_flow": _frame_value(cashflow, column, ["Free Cash Flow"]),
                "shares_outstanding": _num(info.get("sharesOutstanding")),
            }
        )

    latest = years[0] if years else None
    previous = years[1] if len(years) > 1 else None
    raw = {
        "symbol": normalized_symbol,
        "as_of_date": date.today().isoformat(),
        "currency": info.get("currency") or "USD",
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "enterprise_value": info.get("enterpriseValue"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "earnings_growth_5y_pct": _earnings_growth_pct(info),
        "annual": annual,
        "ttm": _yfinance_period_payload(financials, cashflow, balance_sheet, latest),
        "previous_ttm": _yfinance_period_payload(financials, cashflow, balance_sheet, previous),
    }
    return raw


def format_compact_value(value: Any, *, currency_symbol: str = "$") -> str:
    """Format provider values like Fool-style compact currency strings."""
    number = _num(value)
    if number is None:
        return "N/A"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000_000:
        return f"{sign}{currency_symbol}{number / 1_000_000_000_000:.2f}T"
    if number >= 1_000_000_000:
        return f"{sign}{currency_symbol}{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{sign}{currency_symbol}{number / 1_000_000:.2f}M"
    return f"{sign}{currency_symbol}{number:,.2f}"


def _growth_cagr_table(annual: list[Mapping[str, Any]]) -> dict[str, str]:
    if len(annual) < 2:
        return {}
    start = annual[0]
    end = annual[-1]
    years = max(1, len(annual) - 1)
    return {
        "3_yr_revenue_growth": _format_pct(_cagr(start.get("revenue"), end.get("revenue"), years)),
        "3_yr_operating_income_growth": _format_pct(_cagr(start.get("operating_income"), end.get("operating_income"), years)),
        "3_yr_eps_growth": _format_pct(_cagr(start.get("eps"), end.get("eps"), years)),
        "3_yr_ebitda_growth": _format_pct(_cagr(start.get("ebitda"), end.get("ebitda"), years)),
        "3_yr_fcf_per_share_growth": _format_pct(_cagr(_fcf_per_share(start), _fcf_per_share(end), years)),
    }


def _valuation_table(
    ttm: Mapping[str, Any],
    *,
    market_cap: float | None,
    enterprise_value: float | None,
    shares_outstanding: float | None,
    earnings_growth_5y_pct: float | None,
) -> dict[str, str]:
    net_income = _num(ttm.get("net_income"))
    ebitda = _num(ttm.get("ebitda"))
    free_cash_flow = _num(ttm.get("free_cash_flow"))
    total_equity = _num(ttm.get("total_equity"))
    pe = _safe_div(market_cap, net_income)
    return {
        "price_earnings": _format_multiple(pe),
        "ev_ebitda": _format_multiple(_safe_div(enterprise_value, ebitda)),
        "price_free_cash_flow": _format_multiple(_safe_div(market_cap, free_cash_flow)),
        "price_book_value": _format_multiple(_safe_div(market_cap, total_equity)),
        "price_earnings_growth_5yr": _format_multiple(_safe_div(pe, earnings_growth_5y_pct)),
    }


def _profitability_table(ttm: Mapping[str, Any]) -> dict[str, str]:
    revenue = _num(ttm.get("revenue"))
    total_debt = _num(ttm.get("total_debt")) or 0.0
    total_equity = _num(ttm.get("total_equity"))
    total_cash = _num(ttm.get("total_cash")) or 0.0
    invested_capital = None
    if total_equity is not None:
        invested_capital = total_equity + total_debt - total_cash
    return {
        "gross_margin": _format_pct(_ratio_pct(ttm.get("gross_profit"), revenue)),
        "operating_margin": _format_pct(_ratio_pct(ttm.get("operating_income"), revenue)),
        "free_cash_flow_margin": _format_pct(_ratio_pct(ttm.get("free_cash_flow"), revenue)),
        "return_on_equity": _format_pct(_ratio_pct(ttm.get("net_income"), total_equity)),
        "return_on_capital": _format_pct(_ratio_pct(ttm.get("operating_income"), total_debt + (total_equity or 0.0))),
        "return_on_invested_capital": _format_pct(_ratio_pct(ttm.get("operating_income"), invested_capital)),
        "return_on_capital_employed": _format_pct(_ratio_pct(ttm.get("operating_income"), invested_capital)),
        "debt_equity": _format_multiple(_safe_div(total_debt, total_equity)),
    }


def _financials_table(ttm: Mapping[str, Any], previous_ttm: Mapping[str, Any]) -> dict[str, str]:
    fields = {
        "revenue": "revenue",
        "ebitda": "ebitda",
        "net_income": "net_income",
        "capital_expenditure": "capital_expenditure",
        "free_cash_flow": "free_cash_flow",
        "total_debt": "total_debt",
        "total_equity": "total_equity",
        "total_cash": "total_cash",
    }
    table = {}
    for output, source in fields.items():
        value = _num(ttm.get(source))
        if value is None:
            continue
        yoy = _yoy_pct(value, previous_ttm.get(source))
        table[output] = f"{format_compact_value(value)} ({_format_signed_pct(yoy)})" if yoy is not None else format_compact_value(value)
    return table


def _balance_sheet_assessment(metrics: Mapping[str, Any]) -> str:
    financials = metrics.get("financials_ttm") or {}
    profitability = metrics.get("profitability_ttm") or {}
    parts = []
    for label, key in (("Total Debt", "total_debt"), ("Total Cash", "total_cash")):
        value = str(financials.get(key) or "").split(" (")[0]
        if value:
            parts.append(f"{label}: {value}")
    debt_equity = profitability.get("debt_equity")
    if debt_equity:
        parts.append(f"Debt/Equity: {debt_equity}")
    return "; ".join(parts)


def _valuation_score(metrics: Mapping[str, Any]) -> float:
    pe = _multiple_value((metrics.get("valuation_ttm") or {}).get("price_earnings"))
    pfcf = _multiple_value((metrics.get("valuation_ttm") or {}).get("price_free_cash_flow"))
    if pe is None and pfcf is None:
        return 50.0
    values = [value for value in (pe, pfcf) if value is not None and value > 0]
    if not values:
        return 20.0
    avg = sum(values) / len(values)
    if avg <= 20:
        return 80.0
    if avg <= 40:
        return 60.0
    if avg <= 80:
        return 40.0
    return 20.0


def _quality_score(metrics: Mapping[str, Any]) -> float:
    profitability = metrics.get("profitability_ttm") or {}
    gross = _pct_value(profitability.get("gross_margin")) or 0.0
    op = _pct_value(profitability.get("operating_margin")) or 0.0
    roe = _pct_value(profitability.get("return_on_equity")) or 0.0
    debt = _multiple_value(profitability.get("debt_equity"))
    score = 10.0
    if gross >= 40:
        score += 25
    elif gross >= 30:
        score += 15
    elif gross >= 20:
        score += 10
    if op >= 20:
        score += 25
    elif op >= 10:
        score += 15
    elif op >= 5:
        score += 5
    if roe >= 15:
        score += 20
    elif roe >= 8:
        score += 10
    elif roe >= 4:
        score += 5
    if debt is not None and debt <= 0.5:
        score += 10
    return max(0.0, min(100.0, score))


def _warnings(raw: Mapping[str, Any], annual: list[Mapping[str, Any]], ttm: Mapping[str, Any]) -> list[str]:
    warnings = []
    if len(annual) < 4:
        warnings.append("fewer_than_4_annual_periods_for_3yr_cagr")
    if not ttm:
        warnings.append("missing_ttm_financials")
    if _num(raw.get("market_cap")) is None:
        warnings.append("missing_market_cap")
    return warnings


def _frame_columns(frame: Any) -> list[Any]:
    if frame is None or getattr(frame, "empty", False):
        return []
    columns = getattr(frame, "columns", None)
    if columns is None:
        return []
    return list(columns)


def _frame_value(frame: Any, column: Any, row_names: list[str]) -> float | None:
    if frame is None or column is None or getattr(frame, "empty", False):
        return None
    for row_name in row_names:
        try:
            locator = getattr(frame, "loc")
            row = locator[row_name] if hasattr(locator, "__getitem__") else locator(row_name)
            if isinstance(row, Mapping) or hasattr(row, "get"):
                return _num(row.get(column))
            return _num(row[_frame_columns(frame).index(column)])
        except (KeyError, IndexError, ValueError, TypeError, AttributeError):
            continue
    return None


def _yfinance_period_payload(financials: Any, cashflow: Any, balance_sheet: Any, column: Any) -> dict[str, Any]:
    if column is None:
        return {}
    revenue = _frame_value(financials, column, ["Total Revenue", "Operating Revenue"])
    gross_profit = _frame_value(financials, column, ["Gross Profit"])
    operating_income = _frame_value(financials, column, ["Operating Income"])
    ebitda = _frame_value(financials, column, ["EBITDA"])
    net_income = _frame_value(financials, column, ["Net Income", "Net Income Common Stockholders"])
    return {
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "ebitda": ebitda,
        "net_income": net_income,
        "capital_expenditure": _frame_value(cashflow, column, ["Capital Expenditure", "Capital Expenditures"]),
        "free_cash_flow": _frame_value(cashflow, column, ["Free Cash Flow"]),
        "total_debt": _frame_value(balance_sheet, column, ["Total Debt"]),
        "total_equity": _frame_value(balance_sheet, column, ["Stockholders Equity", "Total Equity Gross Minority Interest"]),
        "total_cash": _frame_value(balance_sheet, column, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]),
    }


def _earnings_growth_pct(info: Mapping[str, Any]) -> float | None:
    value = _num(info.get("earningsGrowth") if info.get("earningsGrowth") is not None else info.get("earningsQuarterlyGrowth"))
    if value is None:
        return None
    return value * 100 if abs(value) <= 5 else value


def _cagr(start: Any, end: Any, years: int) -> float | None:
    start_value = _num(start)
    end_value = _num(end)
    if start_value is None or end_value is None or start_value <= 0 or end_value <= 0 or years <= 0:
        return None
    return ((end_value / start_value) ** (1 / years) - 1) * 100


def _fcf_per_share(row: Mapping[str, Any]) -> float | None:
    return _safe_div(_num(row.get("free_cash_flow")), _num(row.get("shares_outstanding")))


def _ratio_pct(numerator: Any, denominator: Any) -> float | None:
    return _safe_div(_num(numerator), _num(denominator), multiplier=100)


def _yoy_pct(value: float, previous: Any) -> float | None:
    previous_value = _num(previous)
    if previous_value in (None, 0):
        return None
    return ((value - previous_value) / abs(previous_value)) * 100


def _safe_div(numerator: Any, denominator: Any, *, multiplier: float = 1.0) -> float | None:
    num = _num(numerator)
    den = _num(denominator)
    if num is None or den in (None, 0):
        return None
    value = (num / den) * multiplier
    if not math.isfinite(value):
        return None
    return value


def _format_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def _format_signed_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _format_multiple(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f}x"


def _num(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        parsed = float(str(value).replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _multiple_value(value: Any) -> float | None:
    return _num(str(value).replace("x", "")) if value not in ("", None) else None


def _pct_value(value: Any) -> float | None:
    return _num(str(value).replace("%", "")) if value not in ("", None) else None


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
    "enrich_idea_with_fundamental_metrics",
    "enrich_ideas_with_fundamental_metrics",
    "fetch_yfinance_fundamental_metrics",
    "format_compact_value",
    "normalize_fundamental_metrics",
]
