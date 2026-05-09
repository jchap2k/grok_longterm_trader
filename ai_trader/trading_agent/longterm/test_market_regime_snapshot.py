import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.market_regime_snapshot import (
    build_market_regime_snapshot_from_fred_histories,
    build_market_regime_snapshot,
    build_market_regime_snapshot_from_histories,
    market_regime_to_dict,
)
from longterm.market_regime_snapshot_cli import build_parser, run_cli


def _series(values):
    return [{"date": f"2026-01-{index + 1:02d}", "close": value} for index, value in enumerate(values)]


def test_market_regime_snapshot_detects_equity_panic_with_falling_yields():
    result = build_market_regime_snapshot_from_histories(
        vix_history=_series([22, 28, 35]),
        spy_history=_series([500] * 200 + [450]),
        ten_year_yield_history=_series([50, 49, 47, 45, 43]),
    )

    assert result.risk_regime == "equity_panic_falling_rates"
    assert result.vix_level == 35
    assert result.spy_above_200d is False
    assert result.ten_year_yield_trend == "falling"


def test_market_regime_snapshot_detects_rate_shock_when_yields_rise():
    result = build_market_regime_snapshot_from_histories(
        vix_history=_series([24, 30, 34]),
        spy_history=_series([500] * 200 + [450]),
        ten_year_yield_history=_series([39, 40, 42, 44, 46]),
    )

    assert result.risk_regime == "inflation_rate_shock"
    assert result.ten_year_yield_trend == "rising"


def test_market_regime_snapshot_detects_normal_constructive_market():
    result = build_market_regime_snapshot_from_histories(
        vix_history=_series([15, 14, 16]),
        spy_history=_series([450] * 200 + [500]),
        ten_year_yield_history=_series([42, 42, 42, 42]),
    )

    assert result.risk_regime == "normal"
    assert result.spy_above_200d is True
    assert result.ten_year_yield_trend == "stable"


def test_market_regime_snapshot_cli_writes_compatible_json_from_snapshot_file(tmp_path, capsys):
    snapshot = tmp_path / "market_history.json"
    output = tmp_path / "market_regime.json"
    snapshot.write_text(
        json.dumps(
            {
                "vix": _series([19, 23, 24]),
                "spy": _series([500] * 200 + [480]),
                "ten_year_yield": _series([42, 42, 42]),
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--snapshot-file",
                str(snapshot),
                "--output",
                str(output),
            ]
        )
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["risk_regime"] == "elevated_uncertainty"
    assert payload["vix_level"] == 24
    assert printed["output"] == str(output)


def test_market_regime_snapshot_accepts_injected_fetcher():
    def fetcher(symbol, period):
        histories = {
            "^VIX": _series([18, 20, 23]),
            "SPY": _series([500] * 200 + [475]),
            "^TNX": _series([42, 42, 42]),
        }
        return histories[symbol]

    result = build_market_regime_snapshot(fetch_history=fetcher)

    assert result.risk_regime == "elevated_uncertainty"


def test_fred_snapshot_uses_inflation_pressure_to_avoid_false_duration_hedge():
    result = build_market_regime_snapshot_from_fred_histories(
        fred_histories={
            "VIXCLS": _series([22, 28, 36]),
            "SP500": _series([5000] * 200 + [4600]),
            "DGS10": _series([4.8, 4.6, 4.4, 4.2]),
            "CPIAUCSL": _series([300, 302, 304, 306, 309, 312, 315]),
            "T10Y2Y": _series([-0.5, -0.4, -0.2]),
            "BAMLH0A0HYM2": _series([3.2, 3.4, 3.8]),
        }
    )

    payload = market_regime_to_dict(result)

    assert result.risk_regime == "inflation_rate_shock"
    assert result.ten_year_yield_trend == "falling"
    assert payload["source_type"] == "fredapi_market_regime_snapshot"
    assert payload["provider_status"] == "ok"
    assert payload["provider_mode"] == "fredapi"
    assert payload["macro_regime_label"] == "inflation_rate_shock"
    assert payload["inflation_pressure"] is True
    assert payload["yield_curve_spread"] == -0.2
    assert payload["credit_spread"] == 3.8
    assert payload["macro_signals"]["thresholds"]["credit_spread_elevated_pct"] == 5.0
    assert payload["macro_signals"]["interpretation"]["VIXCLS"]["allowed_uses"] == [
        "volatility stress context",
        "review cadence",
        "parking posture",
    ]
    assert "advisory only" in payload["macro_signals"]["policy_boundary"]
    assert "inflation pressure=True" in payload["reason"]


def test_market_regime_snapshot_cli_supports_fredapi_provider_with_injected_fetcher(tmp_path, capsys):
    output = tmp_path / "market_regime.json"

    def fetcher(series_id, _api_key=None):
        histories = {
            "VIXCLS": _series([18, 19, 20]),
            "SP500": _series([4500] * 200 + [5000]),
            "DGS10": _series([4.0, 4.01, 4.02]),
        }
        return histories.get(series_id, [])

    code = run_cli(
        build_parser().parse_args(
            [
                "--provider",
                "fredapi",
                "--output",
                str(output),
            ]
        ),
        fred_fetcher=fetcher,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["risk_regime"] == "normal"
    assert payload["source_type"] == "fredapi_market_regime_snapshot"
    assert payload["provider_status"] == "ok"
    assert payload["provider_mode"] == "fredapi"
    assert printed["mode"] == "fredapi"


def test_market_regime_snapshot_cli_falls_back_to_yfinance_when_fredapi_fails(tmp_path, capsys, monkeypatch):
    output = tmp_path / "market_regime.json"

    def broken_fred_fetcher(_series_id, _api_key=None):
        raise ValueError("Internal Server Error")

    def yfinance_fetcher(symbol, _period):
        histories = {
            "^VIX": _series([17, 18, 19]),
            "SPY": _series([450] * 200 + [500]),
            "^TNX": _series([4.0, 4.0, 4.0]),
        }
        return histories[symbol]

    monkeypatch.setattr("longterm.market_regime_snapshot_cli.fetch_yfinance_history", yfinance_fetcher)

    code = run_cli(
        build_parser().parse_args(
            [
                "--provider",
                "fredapi",
                "--output",
                str(output),
            ]
        ),
        fred_fetcher=broken_fred_fetcher,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["risk_regime"] == "normal"
    assert payload["source_type"] == "market_regime_snapshot"
    assert payload["provider_status"] == "degraded_fallback"
    assert payload["provider_mode"] == "fredapi_fallback_yfinance"
    assert payload["provider_warning"]
    assert printed["mode"] == "fredapi_fallback_yfinance"
    assert printed["provider_status"] == "degraded_fallback"
    assert "FRED provider unavailable" in payload["reason"]


def test_market_regime_snapshot_cli_writes_safe_unavailable_snapshot_when_all_providers_fail(
    tmp_path, capsys, monkeypatch
):
    output = tmp_path / "market_regime.json"

    def broken_fred_fetcher(_series_id, _api_key=None):
        raise ValueError("Internal Server Error")

    def broken_yfinance_fetcher(_symbol, _period):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr("longterm.market_regime_snapshot_cli.fetch_yfinance_history", broken_yfinance_fetcher)

    code = run_cli(
        build_parser().parse_args(
            [
                "--provider",
                "fredapi",
                "--output",
                str(output),
            ]
        ),
        fred_fetcher=broken_fred_fetcher,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["risk_regime"] == "market_data_unavailable"
    assert payload["provider_status"] == "unavailable"
    assert payload["provider_mode"] == "fredapi_unavailable"
    assert payload["provider_warning"]
    assert printed["mode"] == "fredapi_unavailable"
    assert "providers unavailable" in payload["reason"]
