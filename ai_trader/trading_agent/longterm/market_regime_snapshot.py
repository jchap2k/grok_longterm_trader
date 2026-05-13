"""Build market-regime snapshots for idle-cash parking decisions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
import json
import os
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from longterm.idle_cash_policy import MarketRegimeSnapshot
from longterm.macro_regime_interpreter import interpret_macro_regime


HistoryFetcher = Callable[[str, str], list[Mapping[str, Any]]]
FredHistoryFetcher = Callable[[str, str | None], list[Mapping[str, Any]]]

DEFAULT_VIX_SYMBOL = "^VIX"
DEFAULT_SPY_SYMBOL = "SPY"
DEFAULT_TEN_YEAR_YIELD_SYMBOL = "^TNX"
DEFAULT_FRED_VIX_SERIES = "VIXCLS"
DEFAULT_FRED_SP500_SERIES = "SP500"
DEFAULT_FRED_TEN_YEAR_SERIES = "DGS10"
DEFAULT_FRED_CPI_SERIES = "CPIAUCSL"
DEFAULT_FRED_YIELD_CURVE_SERIES = "T10Y2Y"
DEFAULT_FRED_CREDIT_SPREAD_SERIES = "BAMLH0A0HYM2"
FRED_REST_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
VIX_ELEVATED_THRESHOLD = 22.0
VIX_STRESS_THRESHOLD = 30.0
CPI_ANNUALIZED_PRESSURE_THRESHOLD_PCT = 4.0
CREDIT_SPREAD_ELEVATED_THRESHOLD_PCT = 5.0
FRED_PROVIDER_OK_MODES = {"fredapi", "fred_rest", "fredapi_rest_fallback"}
FRED_REST_HISTORY_DAYS = {
    DEFAULT_FRED_SP500_SERIES: 420,
    DEFAULT_FRED_CPI_SERIES: 750,
}
DEFAULT_FRED_REST_HISTORY_DAYS = 120


def build_market_regime_snapshot(
    *,
    fetch_history: HistoryFetcher,
    vix_symbol: str = DEFAULT_VIX_SYMBOL,
    spy_symbol: str = DEFAULT_SPY_SYMBOL,
    ten_year_yield_symbol: str = DEFAULT_TEN_YEAR_YIELD_SYMBOL,
    period: str = "1y",
) -> MarketRegimeSnapshot:
    """Fetch market histories and classify the current broad-market regime."""
    return build_market_regime_snapshot_from_histories(
        vix_history=fetch_history(vix_symbol, period),
        spy_history=fetch_history(spy_symbol, period),
        ten_year_yield_history=fetch_history(ten_year_yield_symbol, period),
    )


def build_market_regime_snapshot_from_histories(
    *,
    vix_history: Iterable[Mapping[str, Any]],
    spy_history: Iterable[Mapping[str, Any]],
    ten_year_yield_history: Iterable[Mapping[str, Any]],
) -> MarketRegimeSnapshot:
    """Classify market regime from VIX, SPY trend, and 10Y yield trend histories."""
    vix_rows = list(vix_history or [])
    spy_rows = list(spy_history or [])
    yield_rows = list(ten_year_yield_history or [])
    vix_level = _last_close(vix_rows)
    spy_closes = _closes(spy_rows)
    spy_above_200d = spy_closes[-1] > _simple_average(spy_closes[-200:]) if len(spy_closes) >= 200 else None
    yield_trend = _yield_trend(yield_rows)
    base = MarketRegimeSnapshot.from_signals(
        vix_level=vix_level,
        spy_above_200d=spy_above_200d,
        ten_year_yield_trend=yield_trend,
    )
    return MarketRegimeSnapshot(
        risk_regime=base.risk_regime,
        vix_level=vix_level,
        spy_above_200d=spy_above_200d,
        ten_year_yield_trend=yield_trend,
        reason=_reason(base, vix_level=vix_level, spy_above_200d=spy_above_200d, yield_trend=yield_trend),
    )


def build_market_regime_snapshot_from_fred_histories(
    *,
    fred_histories: Mapping[str, Iterable[Mapping[str, Any]]],
    vix_series: str = DEFAULT_FRED_VIX_SERIES,
    sp500_series: str = DEFAULT_FRED_SP500_SERIES,
    ten_year_series: str = DEFAULT_FRED_TEN_YEAR_SERIES,
    cpi_series: str = DEFAULT_FRED_CPI_SERIES,
    yield_curve_series: str = DEFAULT_FRED_YIELD_CURVE_SERIES,
    credit_spread_series: str = DEFAULT_FRED_CREDIT_SPREAD_SERIES,
    provider_mode: str = "fredapi",
) -> MarketRegimeSnapshot:
    """Classify market regime from FRED histories and macro stress signals."""
    vix_rows = list(fred_histories.get(vix_series) or [])
    sp500_rows = list(fred_histories.get(sp500_series) or [])
    ten_year_rows = list(fred_histories.get(ten_year_series) or [])
    cpi_rows = list(fred_histories.get(cpi_series) or [])
    yield_curve_rows = list(fred_histories.get(yield_curve_series) or [])
    credit_spread_rows = list(fred_histories.get(credit_spread_series) or [])

    vix_level = _last_close(vix_rows)
    sp500_closes = _closes(sp500_rows)
    sp500_above_200d = (
        sp500_closes[-1] > _simple_average(sp500_closes[-200:]) if len(sp500_closes) >= 200 else None
    )
    yield_trend = _yield_trend(ten_year_rows)
    inflation_pressure = _inflation_pressure(cpi_rows)
    yield_curve_spread = _last_close(yield_curve_rows)
    credit_spread = _last_close(credit_spread_rows)

    base = MarketRegimeSnapshot.from_signals(
        vix_level=vix_level,
        spy_above_200d=sp500_above_200d,
        ten_year_yield_trend=yield_trend,
        inflation_pressure=inflation_pressure,
    )
    macro_signals = {
        "fred_series": {
            "vix": vix_series,
            "equity_index": sp500_series,
            "ten_year_yield": ten_year_series,
            "inflation": cpi_series,
            "yield_curve": yield_curve_series,
            "credit_spread": credit_spread_series,
        },
        "observations": _fred_observation_metadata(
            {
                vix_series: vix_rows,
                sp500_series: sp500_rows,
                ten_year_series: ten_year_rows,
                cpi_series: cpi_rows,
                yield_curve_series: yield_curve_rows,
                credit_spread_series: credit_spread_rows,
            }
        ),
        "interpretation": _fred_interpretation_metadata(
            vix_series=vix_series,
            sp500_series=sp500_series,
            ten_year_series=ten_year_series,
            cpi_series=cpi_series,
            yield_curve_series=yield_curve_series,
            credit_spread_series=credit_spread_series,
        ),
        "thresholds": _fred_threshold_metadata(),
        "policy_boundary": "FRED macro-regime fields are advisory only; they may inform review cadence, parking posture, sizing caution, and committee context, but never directly authorize orders.",
        "inflation_pressure": inflation_pressure,
        "yield_curve_inverted": yield_curve_spread is not None and yield_curve_spread < 0,
        "credit_spread_elevated": (
            credit_spread is not None and credit_spread >= CREDIT_SPREAD_ELEVATED_THRESHOLD_PCT
        ),
    }
    interpretation = interpret_macro_regime(
        {
            "risk_regime": base.risk_regime,
            "provider_status": "ok",
            "provider_mode": provider_mode,
            "vix_level": vix_level,
            "spy_above_200d": sp500_above_200d,
            "ten_year_yield_trend": yield_trend,
            "inflation_pressure": inflation_pressure,
            "yield_curve_spread": yield_curve_spread,
            "credit_spread": credit_spread,
            "macro_signals": macro_signals,
        }
    )
    macro_signals["regime_interpretation"] = interpretation
    reason = " ".join(
        [
            _reason(base, vix_level=vix_level, spy_above_200d=sp500_above_200d, yield_trend=yield_trend),
            f"FRED inflation pressure={inflation_pressure}.",
            f"Yield curve spread={yield_curve_spread if yield_curve_spread is not None else 'unknown'}.",
            f"High-yield credit spread={credit_spread if credit_spread is not None else 'unknown'}.",
        ]
    )
    return MarketRegimeSnapshot(
        risk_regime=base.risk_regime,
        vix_level=vix_level,
        spy_above_200d=sp500_above_200d,
        ten_year_yield_trend=yield_trend,
        reason=reason,
        inflation_pressure=inflation_pressure,
        yield_curve_spread=yield_curve_spread,
        credit_spread=credit_spread,
        macro_signals=macro_signals,
        macro_regime_label=interpretation["macro_regime_label"],
        provider_status="ok",
        provider_mode=provider_mode,
    )


def build_fred_market_regime_snapshot(
    *,
    fetch_fred_history: FredHistoryFetcher | None = None,
    api_key: str | None = None,
    vix_series: str = DEFAULT_FRED_VIX_SERIES,
    sp500_series: str = DEFAULT_FRED_SP500_SERIES,
    ten_year_series: str = DEFAULT_FRED_TEN_YEAR_SERIES,
    cpi_series: str = DEFAULT_FRED_CPI_SERIES,
    yield_curve_series: str = DEFAULT_FRED_YIELD_CURVE_SERIES,
    credit_spread_series: str = DEFAULT_FRED_CREDIT_SPREAD_SERIES,
    provider_mode: str = "fredapi",
) -> MarketRegimeSnapshot:
    """Fetch FRED series through fredapi and classify the macro regime."""
    fetcher = fetch_fred_history or fetch_fredapi_history
    series_ids = [
        vix_series,
        sp500_series,
        ten_year_series,
        cpi_series,
        yield_curve_series,
        credit_spread_series,
    ]
    histories = {series_id: fetcher(series_id, api_key) for series_id in series_ids}
    return build_market_regime_snapshot_from_fred_histories(
        fred_histories=histories,
        vix_series=vix_series,
        sp500_series=sp500_series,
        ten_year_series=ten_year_series,
        cpi_series=cpi_series,
        yield_curve_series=yield_curve_series,
        credit_spread_series=credit_spread_series,
        provider_mode=provider_mode,
    )


def fetch_yfinance_history(symbol: str, period: str = "1y") -> list[dict[str, Any]]:
    """Fetch daily close history from yfinance for a symbol."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install yfinance to build live market-regime snapshots.") from exc

    history = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    rows: list[dict[str, Any]] = []
    if history is None or history.empty:
        return rows
    for index, row in history.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        rows.append({"date": str(index)[:10], "close": float(close)})
    return rows


def fetch_fredapi_history(series_id: str, api_key: str | None = None) -> list[dict[str, Any]]:
    """Fetch an observation history from FRED through the optional fredapi package."""
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("Set FRED_API_KEY to build FRED market-regime snapshots.")
    try:
        from fredapi import Fred
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install fredapi to build FRED market-regime snapshots.") from exc

    series = Fred(api_key=key).get_series(series_id)
    rows: list[dict[str, Any]] = []
    if series is None:
        return rows
    for index, value in series.dropna().items():
        try:
            rows.append({"date": str(index)[:10], "close": float(value)})
        except (TypeError, ValueError):
            continue
    return rows


def fetch_fred_rest_history(
    series_id: str,
    api_key: str | None = None,
    *,
    observation_start: str | None = None,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch a bounded FRED observation history directly through the REST API."""
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("Set FRED_API_KEY to build FRED market-regime snapshots.")
    start = observation_start or _fred_rest_observation_start(series_id)
    params = urllib.parse.urlencode(
        {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "asc",
            "observation_start": start,
        }
    )
    request = urllib.request.Request(
        f"{FRED_REST_OBSERVATIONS_URL}?{params}",
        headers={"User-Agent": "grok-longterm-trader-fred/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(response.read().decode(charset, errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = _fred_http_error_detail(exc)
        raise RuntimeError(f"FRED REST HTTP {exc.code}: {detail}") from exc
    rows: list[dict[str, Any]] = []
    for item in payload.get("observations") or []:
        if not isinstance(item, Mapping):
            continue
        value = item.get("value")
        if value in {None, "", "."}:
            continue
        try:
            row = {
                "date": str(item.get("date") or ""),
                "close": float(value),
            }
        except (TypeError, ValueError):
            continue
        if item.get("realtime_start"):
            row["realtime_start"] = str(item.get("realtime_start"))
        if item.get("realtime_end"):
            row["realtime_end"] = str(item.get("realtime_end"))
        rows.append(row)
    return rows


def market_regime_to_dict(snapshot: MarketRegimeSnapshot) -> dict[str, Any]:
    """Serialize a snapshot to the JSON format consumed by run_longterm_cycle."""
    payload = {
        "risk_regime": snapshot.risk_regime,
        "vix_level": snapshot.vix_level,
        "spy_above_200d": snapshot.spy_above_200d,
        "ten_year_yield_trend": snapshot.ten_year_yield_trend,
        "reason": snapshot.reason,
        "source_type": "market_regime_snapshot",
        "schema_version": 1,
        "macro_regime_label": snapshot.macro_regime_label or snapshot.risk_regime,
        "provider_status": snapshot.provider_status or "ok",
        "provider_mode": snapshot.provider_mode or "",
        "provider_warning": snapshot.provider_warning or "",
    }
    if (
        snapshot.inflation_pressure
        or snapshot.yield_curve_spread is not None
        or snapshot.credit_spread is not None
        or snapshot.macro_signals
    ):
        payload.update(
            {
                "source_type": "fredapi_market_regime_snapshot",
                "inflation_pressure": snapshot.inflation_pressure,
                "yield_curve_spread": snapshot.yield_curve_spread,
                "credit_spread": snapshot.credit_spread,
                "macro_signals": snapshot.macro_signals or {},
                "macro_regime_interpretation": interpret_macro_regime(
                    {
                        "risk_regime": snapshot.risk_regime,
                        "vix_level": snapshot.vix_level,
                        "spy_above_200d": snapshot.spy_above_200d,
                        "ten_year_yield_trend": snapshot.ten_year_yield_trend,
                        "inflation_pressure": snapshot.inflation_pressure,
                        "yield_curve_spread": snapshot.yield_curve_spread,
                        "credit_spread": snapshot.credit_spread,
                        "provider_status": snapshot.provider_status or "ok",
                        "provider_mode": snapshot.provider_mode or "",
                        "provider_warning": snapshot.provider_warning or "",
                        "macro_signals": snapshot.macro_signals or {},
                    }
                ),
            }
        )
    return payload


def _last_close(rows: list[Mapping[str, Any]]) -> float | None:
    closes = _closes(rows)
    return closes[-1] if closes else None


def is_fred_provider_ok(provider_mode: str, provider_status: str) -> bool:
    return str(provider_status or "").lower() == "ok" and str(provider_mode or "").lower() in FRED_PROVIDER_OK_MODES


def _fred_rest_observation_start(series_id: str) -> str:
    days = FRED_REST_HISTORY_DAYS.get(series_id, DEFAULT_FRED_REST_HISTORY_DAYS)
    return (datetime.now(UTC).date() - timedelta(days=days)).isoformat()


def _fred_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(4000)
        return raw.decode("utf-8", errors="replace")[:1000]
    except Exception:
        return str(exc)


def _closes(rows: Iterable[Mapping[str, Any]]) -> list[float]:
    closes: list[float] = []
    for row in rows:
        value = row.get("close") if isinstance(row, Mapping) else None
        try:
            if value is not None:
                closes.append(float(value))
        except (TypeError, ValueError):
            continue
    return closes


def _simple_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _yield_trend(rows: list[Mapping[str, Any]], *, lookback: int = 20, flat_threshold_pct: float = 1.0) -> str:
    closes = _closes(rows)
    if len(closes) < 2:
        return ""
    start = closes[-min(lookback, len(closes))]
    end = closes[-1]
    if start == 0:
        return "stable"
    change_pct = ((end - start) / abs(start)) * 100
    if change_pct >= flat_threshold_pct:
        return "rising"
    if change_pct <= -flat_threshold_pct:
        return "falling"
    return "stable"


def _inflation_pressure(rows: list[Mapping[str, Any]], *, months: int = 6, annualized_threshold_pct: float = 4.0) -> bool:
    closes = _closes(rows)
    if len(closes) <= months:
        return False
    start = closes[-months - 1]
    end = closes[-1]
    if start <= 0:
        return False
    annualized = (((end / start) ** (12 / months)) - 1) * 100
    return annualized >= annualized_threshold_pct


def _fred_threshold_metadata() -> dict[str, Any]:
    return {
        "vix_elevated": VIX_ELEVATED_THRESHOLD,
        "vix_stress": VIX_STRESS_THRESHOLD,
        "cpi_annualized_pressure_pct": CPI_ANNUALIZED_PRESSURE_THRESHOLD_PCT,
        "yield_curve_inverted_threshold": 0.0,
        "credit_spread_elevated_pct": CREDIT_SPREAD_ELEVATED_THRESHOLD_PCT,
        "equity_index_trend": "latest close above 200-day simple moving average",
    }


def _fred_interpretation_metadata(
    *,
    vix_series: str,
    sp500_series: str,
    ten_year_series: str,
    cpi_series: str,
    yield_curve_series: str,
    credit_spread_series: str,
) -> dict[str, Any]:
    return {
        vix_series: {
            "meaning": "CBOE volatility index close; proxy for near-term equity volatility stress.",
            "allowed_uses": ["volatility stress context", "review cadence", "parking posture"],
            "not_allowed": ["standalone buy/sell trigger"],
        },
        sp500_series: {
            "meaning": "Broad U.S. equity price-index trend proxy.",
            "allowed_uses": ["market trend context", "parking posture"],
            "not_allowed": ["standalone benchmark proof"],
        },
        ten_year_series: {
            "meaning": "10-year Treasury constant maturity yield; proxy for long-rate and duration pressure.",
            "allowed_uses": ["duration-risk context", "cost-of-capital context"],
            "not_allowed": ["automatic duration hedge without equity-panic confirmation"],
        },
        cpi_series: {
            "meaning": "Consumer Price Index level; recent annualized change is used as inflation-pressure context.",
            "allowed_uses": ["pricing-power review", "margin-of-safety caution", "duration-risk context"],
            "not_allowed": ["standalone recession signal"],
        },
        yield_curve_series: {
            "meaning": "10-year Treasury yield minus 2-year Treasury yield; negative values flag curve inversion.",
            "allowed_uses": ["late-cycle caution", "review cadence"],
            "not_allowed": ["standalone liquidation trigger"],
        },
        credit_spread_series: {
            "meaning": "ICE BofA high-yield option-adjusted spread; proxy for credit stress.",
            "allowed_uses": ["credit-stress context", "review cadence", "sizing caution"],
            "not_allowed": ["standalone broad sell trigger"],
        },
    }


def _fred_observation_metadata(histories: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    observations: dict[str, dict[str, Any]] = {}
    for series_id, rows in histories.items():
        observations[series_id] = {
            "last_observation_date": _last_date(rows),
            "observation_count": len(rows),
        }
    return observations


def _last_date(rows: list[Mapping[str, Any]]) -> str:
    for row in reversed(rows):
        value = row.get("date") if isinstance(row, Mapping) else None
        if value:
            return str(value)
    return ""


def _macro_regime_label(
    *,
    risk_regime: str,
    yield_curve_spread: float | None,
    credit_spread: float | None,
) -> str:
    if risk_regime == "market_data_unavailable":
        return "market_data_unavailable"
    if risk_regime == "inflation_rate_shock":
        return "inflation_rate_shock"
    if risk_regime == "equity_panic_falling_rates":
        return "equity_panic_falling_rates"
    if credit_spread is not None and credit_spread >= CREDIT_SPREAD_ELEVATED_THRESHOLD_PCT:
        return "credit_stress"
    if yield_curve_spread is not None and yield_curve_spread < 0:
        return "late_cycle_caution"
    return risk_regime or "normal"


def _reason(
    base: MarketRegimeSnapshot,
    *,
    vix_level: float | None,
    spy_above_200d: bool | None,
    yield_trend: str,
) -> str:
    pieces = [
        base.reason or "Regime classified from market inputs.",
        f"VIX={vix_level if vix_level is not None else 'unknown'}.",
        f"SPY above 200d={spy_above_200d if spy_above_200d is not None else 'unknown'}.",
        f"10Y yield trend={yield_trend or 'unknown'}.",
    ]
    return " ".join(pieces)


__all__ = [
    "DEFAULT_FRED_CPI_SERIES",
    "DEFAULT_FRED_CREDIT_SPREAD_SERIES",
    "DEFAULT_FRED_SP500_SERIES",
    "DEFAULT_FRED_TEN_YEAR_SERIES",
    "DEFAULT_FRED_VIX_SERIES",
    "DEFAULT_FRED_YIELD_CURVE_SERIES",
    "DEFAULT_SPY_SYMBOL",
    "DEFAULT_TEN_YEAR_YIELD_SYMBOL",
    "DEFAULT_VIX_SYMBOL",
    "FRED_PROVIDER_OK_MODES",
    "build_fred_market_regime_snapshot",
    "build_market_regime_snapshot",
    "build_market_regime_snapshot_from_fred_histories",
    "build_market_regime_snapshot_from_histories",
    "fetch_fredapi_history",
    "fetch_fred_rest_history",
    "fetch_yfinance_history",
    "is_fred_provider_ok",
    "market_regime_to_dict",
]
