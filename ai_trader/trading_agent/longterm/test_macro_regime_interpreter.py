import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.macro_regime_interpreter import interpret_macro_regime


def test_macro_regime_interpreter_flags_late_cycle_credit_stress_and_review_trigger():
    result = interpret_macro_regime(
        {
            "risk_regime": "normal",
            "provider_status": "ok",
            "provider_mode": "fredapi",
            "vix_level": 24.2,
            "spy_above_200d": False,
            "inflation_pressure": True,
            "yield_curve_spread": -0.31,
            "credit_spread": 5.4,
            "macro_signals": {
                "thresholds": {
                    "vix_elevated": 22.0,
                    "vix_stress": 30.0,
                    "yield_curve_inverted_threshold": 0.0,
                    "credit_spread_elevated_pct": 5.0,
                }
            },
        }
    )

    assert result["macro_regime_label"] == "credit_stress"
    assert result["severity"] == "high"
    assert result["review_trigger"] is True
    assert result["sizing_caution"] == "tighten_new_buy_sizing"
    assert result["new_buy_posture"] == "pause_or_reduce_new_buys_unless_exceptional"
    assert "yield_curve_inverted" in result["reasons"]
    assert "credit_spread_elevated" in result["reasons"]
    assert result["provider_healthy"] is True


def test_macro_regime_interpreter_marks_fallback_as_provider_attention():
    result = interpret_macro_regime(
        {
            "risk_regime": "normal",
            "provider_status": "degraded_fallback",
            "provider_mode": "fredapi_fallback_yfinance",
            "provider_warning": "FRED provider unavailable.",
        }
    )

    assert result["macro_regime_label"] == "provider_attention"
    assert result["provider_healthy"] is False
    assert result["review_trigger"] is False
    assert "provider_status_degraded_fallback" in result["reasons"]


def test_macro_regime_interpreter_treats_fred_rest_as_healthy():
    result = interpret_macro_regime(
        {
            "risk_regime": "normal",
            "provider_status": "ok",
            "provider_mode": "fredapi_rest_fallback",
            "vix_level": 18.0,
            "spy_above_200d": True,
        }
    )

    assert result["macro_regime_label"] == "normal"
    assert result["provider_healthy"] is True
    assert not any(reason.startswith("provider_status_") for reason in result["reasons"])
