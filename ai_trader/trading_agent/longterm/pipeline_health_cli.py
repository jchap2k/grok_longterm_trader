"""CLI for read-only research-to-paper pipeline artifact health checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from longterm.research_to_paper_pipeline import build_pipeline_artifact_rollup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a saved no-submit research-to-paper pipeline summary."
    )
    parser.add_argument("--pipeline-summary", required=True)
    parser.add_argument("--pipeline-scheduler-summary", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument(
        "--require-artifact",
        action="append",
        default=[],
        help="Require an artifact key to be present and non-empty in the pipeline summary.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    report = build_pipeline_health_report(
        pipeline_summary=args.pipeline_summary,
        pipeline_scheduler_summary=args.pipeline_scheduler_summary,
        required_artifacts=args.require_artifact,
    )
    if args.report_output:
        output = Path(args.report_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Pipeline artifact health: {report['status']}")
        print(f"Pipeline summary: {report['pipeline_summary']}")
        if report["missing_required_artifacts"]:
            print("Missing required artifacts: " + ", ".join(report["missing_required_artifacts"]))
    return 0 if report["status"] == "ready" else 1


def build_pipeline_health_report(
    *,
    pipeline_summary: str | Path,
    pipeline_scheduler_summary: str | Path = "",
    required_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    summary_path = Path(pipeline_summary)
    summary = _read_json_object(summary_path)
    artifact_paths = summary.get("artifact_paths") if isinstance(summary.get("artifact_paths"), dict) else {}
    missing_required = [
        key for key in (required_artifacts or []) if not str(artifact_paths.get(key, "")).strip()
    ]
    rollup = build_pipeline_artifact_rollup(artifact_paths)
    health = rollup["health"]
    status = "ready"
    if health["status"] != "ready" or missing_required:
        status = "attention_required"
    followup_next_action = str(
        ((rollup.get("portfolio_news_monitor") or {}).get("followup_review_next_action") or "")
    )
    return {
        "schema_version": 1,
        "mode": "pipeline_artifact_health",
        "status": status,
        "pipeline_summary": str(summary_path),
        "pipeline_scheduler_summary": str(pipeline_scheduler_summary or ""),
        "pipeline_status": str(summary.get("status", "")),
        "order_submission_enabled": bool(summary.get("order_submission_enabled", False)),
        "missing_required_artifacts": missing_required,
        "resource_controls": _latest_resource_controls(pipeline_scheduler_summary),
        "health": health,
        "rollup": rollup,
        "next_safe_action": (
            "review_missing_or_malformed_artifacts"
            if status == "attention_required"
            else followup_next_action or "artifacts_ready_for_dashboard_or_scheduler"
        ),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"artifact_paths": {}, "status": "missing_pipeline_summary"}
    except json.JSONDecodeError as exc:
        return {
            "artifact_paths": {},
            "status": "malformed_pipeline_summary",
            "error": str(exc),
        }
    return data if isinstance(data, dict) else {"artifact_paths": {}, "status": "invalid_pipeline_summary"}


def _latest_resource_controls(path: str | Path) -> dict[str, Any]:
    if not str(path or "").strip():
        return {}
    summary = _read_json_object(Path(path))
    runs = summary.get("runs") if isinstance(summary.get("runs"), list) else []
    for item in reversed(runs):
        if not isinstance(item, dict):
            continue
        controls = item.get("resource_controls")
        if isinstance(controls, dict):
            return dict(controls)
    return {}


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "build_pipeline_health_report", "main", "run_cli"]
