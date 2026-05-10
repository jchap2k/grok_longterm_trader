"""CLI wrapper for optional Kronos advisory subagent runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable

from longterm.kronos_advisory import (
    build_kronos_advisory_payload,
    build_unavailable_kronos_advisory,
)


DEFAULT_KRONOS_ROOT = "S:\\LLM_files\\other_github\\Kronos"
DEFAULT_KRONOS_PYTHON = "S:\\LLM_files\\other_github\\Kronos\\.venv\\Scripts\\python.exe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Kronos as an optional advisory subagent.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kronos-root", default=DEFAULT_KRONOS_ROOT)
    parser.add_argument("--kronos-python", default=DEFAULT_KRONOS_PYTHON)
    parser.add_argument("--provider", choices=["yfinance"], default="yfinance")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--lookback", type=int, default=256)
    parser.add_argument("--pred-len", type=int, default=5)
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    subprocess_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    worker_script = Path(__file__).with_name("kronos_advisory_worker.py")

    with tempfile.TemporaryDirectory(prefix="longterm_kronos_") as temp_dir:
        worker_output = Path(temp_dir) / "kronos_worker_output.json"
        command = [
            str(args.kronos_python),
            str(worker_script),
            "--symbol",
            str(args.symbol).upper(),
            "--output",
            str(worker_output),
            "--kronos-root",
            str(args.kronos_root),
            "--provider",
            args.provider,
            "--period",
            args.period,
            "--interval",
            args.interval,
            "--lookback",
            str(args.lookback),
            "--pred-len",
            str(args.pred_len),
            "--model",
            args.model,
            "--tokenizer",
            args.tokenizer,
            "--device",
            args.device,
        ]
        try:
            result = subprocess_runner(
                command,
                cwd=str(args.kronos_root),
                capture_output=True,
                text=True,
                timeout=max(1, int(args.timeout_seconds or 1)),
            )
        except Exception as exc:
            payload = build_unavailable_kronos_advisory(
                symbol=args.symbol,
                provider_mode="kronos_subprocess_error",
                provider_warning=_safe_error(exc),
            )
            _write_and_print(output_path, payload, mode="kronos_subagent")
            return 0

        if result.returncode != 0:
            payload = build_unavailable_kronos_advisory(
                symbol=args.symbol,
                provider_mode="kronos_subprocess_failed",
                provider_warning=(result.stderr or result.stdout or f"exit_code={result.returncode}"),
            )
            _write_and_print(output_path, payload, mode="kronos_subagent")
            return 0

        try:
            worker_payload = json.loads(worker_output.read_text(encoding="utf-8"))
            payload = build_kronos_advisory_payload(
                symbol=str(worker_payload.get("symbol") or args.symbol),
                last_close=float(worker_payload["last_close"]),
                forecast=[dict(item) for item in worker_payload.get("forecast") or []],
                model=str(worker_payload.get("model") or args.model),
                tokenizer=str(worker_payload.get("tokenizer") or args.tokenizer),
                device=str(worker_payload.get("device") or args.device),
                lookback_rows=int(worker_payload.get("lookback_rows") or args.lookback),
                timing_seconds=dict(worker_payload.get("timing_seconds") or {}),
            )
        except Exception as exc:
            payload = build_unavailable_kronos_advisory(
                symbol=args.symbol,
                provider_mode="kronos_worker_output_invalid",
                provider_warning=_safe_error(exc),
            )
        _write_and_print(output_path, payload, mode="kronos_subagent")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _write_and_print(output_path: Path, payload: dict[str, Any], *, mode: str) -> None:
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"mode": mode, "output": str(output_path), **payload}, indent=2, sort_keys=True))


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message[:500]}"


__all__ = ["build_parser", "main", "run_cli"]
