import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.market_regime_snapshot import (
    build_market_regime_snapshot,
    build_market_regime_snapshot_from_histories,
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
