import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.kronos_advisory import (
    build_kronos_advisory_payload,
    classify_forecast_direction,
)
from longterm.kronos_advisory_cli import build_parser, run_cli


def test_classify_forecast_direction_uses_small_deadband():
    assert classify_forecast_direction(1.25) == "up"
    assert classify_forecast_direction(-1.25) == "down"
    assert classify_forecast_direction(0.35) == "flat"


def test_build_kronos_advisory_payload_summarizes_forecast():
    payload = build_kronos_advisory_payload(
        symbol="aapl",
        last_close=293.32,
        forecast=[
            {"date": "2026-05-11", "close": 292.16},
            {"date": "2026-05-12", "close": 289.95},
        ],
        model="NeoQuasar/Kronos-small",
        tokenizer="NeoQuasar/Kronos-Tokenizer-base",
        device="cpu",
        lookback_rows=256,
        timing_seconds={"predict": 0.41},
    )

    assert payload["symbol"] == "AAPL"
    assert payload["provider_status"] == "ok"
    assert payload["forecast_direction"] == "down"
    assert payload["forecast_return_pct"] == -1.149
    assert payload["forecast"][0]["close_return_from_last_pct"] == -0.395
    assert "advisory" in payload["policy_boundary"]


def test_kronos_advisory_cli_writes_unavailable_artifact_when_subagent_fails(tmp_path, capsys):
    output = tmp_path / "kronos.json"

    def failing_runner(_command, **_kwargs):
        return subprocess.CompletedProcess(_command, 2, stdout="", stderr="missing torch")

    code = run_cli(
        build_parser().parse_args(
            [
                "--symbol",
                "AAPL",
                "--output",
                str(output),
                "--kronos-root",
                "S:\\LLM_files\\other_github\\Kronos",
                "--kronos-python",
                "S:\\LLM_files\\other_github\\Kronos\\.venv\\Scripts\\python.exe",
            ]
        ),
        subprocess_runner=failing_runner,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["provider_status"] == "unavailable"
    assert payload["provider_mode"] == "kronos_subprocess_failed"
    assert "missing torch" in payload["provider_warning"]
    assert summary["provider_status"] == "unavailable"


def test_kronos_advisory_cli_normalizes_worker_output(tmp_path, capsys):
    output = tmp_path / "kronos.json"

    def successful_runner(command, **_kwargs):
        worker_output = Path(command[command.index("--output") + 1])
        worker_output.write_text(
            json.dumps(
                {
                    "symbol": "AAPL",
                    "last_close": 293.32,
                    "model": "NeoQuasar/Kronos-small",
                    "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
                    "device": "cpu",
                    "lookback_rows": 256,
                    "timing_seconds": {"predict": 0.41},
                    "forecast": [
                        {"date": "2026-05-11", "close": 292.16},
                        {"date": "2026-05-12", "close": 289.95},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    code = run_cli(
        build_parser().parse_args(
            [
                "--symbol",
                "AAPL",
                "--output",
                str(output),
                "--kronos-root",
                "S:\\LLM_files\\other_github\\Kronos",
                "--kronos-python",
                "S:\\LLM_files\\other_github\\Kronos\\.venv\\Scripts\\python.exe",
            ]
        ),
        subprocess_runner=successful_runner,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["provider_status"] == "ok"
    assert payload["forecast_direction"] == "down"
    assert payload["forecast_return_pct"] == -1.149
    assert summary["mode"] == "kronos_subagent"
