import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.kronos_advisory_batch import (
    build_kronos_batch_payload,
    load_symbols_from_args,
)
from longterm.kronos_advisory_batch_cli import build_parser, run_cli


def _ok_payload(symbol: str, forecast_return_pct: float = 1.25) -> dict:
    return {
        "schema_version": 1,
        "source_type": "kronos_advisory",
        "symbol": symbol,
        "provider_status": "ok",
        "provider_mode": "kronos_subagent",
        "provider_warning": "",
        "forecast_direction": "up",
        "forecast_return_pct": forecast_return_pct,
        "forecast_horizon_rows": 5,
        "policy_boundary": "advisory only",
    }


def test_load_symbols_from_args_supports_csv_and_idea_batch(tmp_path):
    ideas = tmp_path / "ideas.json"
    ideas.write_text(
        json.dumps(
            [
                {"symbol": "msft"},
                {"ticker": "NVDA"},
                {"symbol": "aapl"},
            ]
        ),
        encoding="utf-8",
    )

    args = build_parser().parse_args(
        [
            "--symbols",
            "aapl, AMZN",
            "--idea-batch",
            str(ideas),
            "--output",
            str(tmp_path / "out.json"),
        ]
    )

    assert load_symbols_from_args(args) == ["AAPL", "AMZN", "MSFT", "NVDA"]


def test_build_kronos_batch_payload_marks_partial_failures_degraded():
    payload = build_kronos_batch_payload(
        [
            _ok_payload("AAPL", 1.25),
            {
                "symbol": "NVDA",
                "provider_status": "unavailable",
                "provider_mode": "kronos_subprocess_failed",
                "provider_warning": "missing torch",
                "forecast_direction": "unavailable",
                "forecast_return_pct": None,
            },
        ]
    )

    assert payload["provider_status"] == "degraded"
    assert payload["ok_count"] == 1
    assert payload["unavailable_count"] == 1
    assert payload["items"][0]["symbol"] == "AAPL"
    assert payload["items"][0]["forecast_return_pct"] == 1.25
    assert payload["items"][1]["provider_warning"] == "missing torch"


def test_kronos_batch_cli_writes_summary_and_continues_after_symbol_error(tmp_path, capsys):
    output = tmp_path / "batch.json"

    def advisory_runner(symbol: str, _args) -> dict:
        if symbol == "NVDA":
            raise RuntimeError("simulated kronos failure")
        return _ok_payload(symbol, 0.95)

    code = run_cli(
        build_parser().parse_args(
            [
                "--symbols",
                "AAPL,NVDA",
                "--output",
                str(output),
            ]
        ),
        advisory_runner=advisory_runner,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["provider_status"] == "degraded"
    assert payload["symbol_count"] == 2
    assert payload["ok_count"] == 1
    assert payload["unavailable_count"] == 1
    assert payload["items"][1]["symbol"] == "NVDA"
    assert payload["items"][1]["provider_mode"] == "kronos_batch_symbol_error"
    assert "simulated kronos failure" in payload["items"][1]["provider_warning"]
    assert printed["output"] == str(output)
