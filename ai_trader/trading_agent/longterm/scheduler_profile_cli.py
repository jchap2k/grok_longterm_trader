"""Render local no-submit scheduler JSON profiles from safe templates."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from longterm.pipeline_scheduler_cli import (
    _config_arg_specs,
    parse_args as parse_scheduler_args,
    validate_resolved_scheduler_config,
)


LONGTERM_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = LONGTERM_DIR / "configs" / "ongoing_no_submit_scheduler.example.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and optionally validate a local no-submit scheduler JSON profile."
    )
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--output-profile", required=True)
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a scheduler profile args key. Keys must match longterm_pipeline_scheduler argparse dest names.",
    )
    parser.add_argument(
        "--enable",
        action="append",
        default=[],
        metavar="KEY",
        help="Set a boolean scheduler profile args key to true.",
    )
    parser.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="KEY",
        help="Set a boolean scheduler profile args key to false.",
    )
    parser.add_argument(
        "--validate-after-write",
        action="store_true",
        help="Validate the written profile and write summary_output if the profile supplies one.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    payload = _load_profile_template(args.template)
    profile_args = dict(payload.get("args") or {})
    _apply_overrides(profile_args, set_items=args.set, enable_items=args.enable, disable_items=args.disable)
    profile_args["validate_config_only"] = True
    payload["args"] = profile_args

    output_profile = Path(args.output_profile).expanduser().resolve()
    output_profile.parent.mkdir(parents=True, exist_ok=True)
    output_profile.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    validation_summary = ""
    validation_payload: dict[str, Any] | None = None
    if args.validate_after_write:
        scheduler_args = parse_scheduler_args(["--config-file", str(output_profile)])
        validation_payload = validate_resolved_scheduler_config(scheduler_args)
        validation_summary = _write_validation_summary_if_requested(profile_args, validation_payload)

    result = {
        "schema_version": 1,
        "mode": "scheduler_profile_render",
        "status": "ready",
        "profile": str(output_profile),
        "validation_summary": validation_summary,
        "order_submission_enabled": False,
        "next_safe_action": "review_profile_then_run_longterm_pipeline_scheduler_config_file",
    }
    if validation_payload is not None:
        result["validation_status"] = validation_payload.get("status", "")
        result["resource_controls"] = validation_payload.get("resource_controls", {})

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Scheduler profile ready.")
        print(f"Profile: {output_profile}")
        if validation_summary:
            print(f"Validation summary: {validation_summary}")
        print("Validation-only mode is enabled; no scheduler run folders were created.")
    return 0


def _load_profile_template(template: str | Path) -> dict[str, Any]:
    path = Path(template).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--template must contain a JSON object.")
    raw_args = payload.get("args")
    if raw_args is None:
        payload["args"] = {}
    elif not isinstance(raw_args, dict):
        raise ValueError("--template 'args' must be a JSON object.")
    return payload


def _apply_overrides(
    profile_args: dict[str, Any],
    *,
    set_items: list[str],
    enable_items: list[str],
    disable_items: list[str],
) -> None:
    allowed = _config_arg_specs()
    for item in set_items:
        key, value = _split_set_item(item)
        _require_allowed_arg(key, allowed)
        profile_args[key] = _coerce_value(value)
    for key in enable_items:
        _require_allowed_arg(key, allowed)
        profile_args[key] = True
    for key in disable_items:
        _require_allowed_arg(key, allowed)
        profile_args[key] = False


def _split_set_item(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise ValueError("--set values must be formatted as KEY=VALUE.")
    key, value = item.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError("--set values must include a non-empty key.")
    return key, value


def _require_allowed_arg(key: str, allowed: dict[str, tuple[str, str]]) -> None:
    if key not in allowed:
        raise ValueError(f"Unknown scheduler config arg: {key}")


def _coerce_value(value: str) -> Any:
    text = value.strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return value


def _write_validation_summary_if_requested(
    profile_args: dict[str, Any],
    validation_payload: dict[str, Any],
) -> str:
    raw_summary = str(profile_args.get("summary_output") or "").strip()
    if not raw_summary:
        return ""
    summary_path = Path(raw_summary).expanduser().resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(validation_payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(summary_path)


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
