"""CLI for building the read-only Monday launch packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from longterm.operator_launch_packet import (
    build_operator_launch_packet,
    build_operator_launch_packet_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only Monday launch packet.")
    parser.add_argument("--dashboard-file", required=True)
    parser.add_argument("--candidate-plan", required=True)
    parser.add_argument("--monday-check", required=True)
    parser.add_argument("--workflow-smoke", default="")
    parser.add_argument("--runbook", default="")
    parser.add_argument("--site-index", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--json-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    packet = build_operator_launch_packet(
        dashboard=_load_json(args.dashboard_file),
        candidate_plan=_load_json(args.candidate_plan),
        monday_check=_load_json(args.monday_check),
        workflow_smoke=_load_json(args.workflow_smoke) if args.workflow_smoke else {},
        runbook=_load_json(args.runbook) if args.runbook else {},
        site_index=args.site_index,
    )
    markdown = build_operator_launch_packet_markdown(packet)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        print(markdown, end="")
    return 0 if packet.get("ready_for_supervised_review") else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


__all__ = ["build_parser", "main", "run_cli"]
