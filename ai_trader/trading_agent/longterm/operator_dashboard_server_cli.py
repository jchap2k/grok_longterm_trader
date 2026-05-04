"""CLI for serving a live read-only operator dashboard from a manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.operator_dashboard_server import build_dashboard_manifest, serve_dashboard_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a live read-only long-term operator dashboard.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--write-manifest-only", action="store_true")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--operator-status", default="")
    parser.add_argument("--evidence-file", default="")
    parser.add_argument("--price-history-file", default="")
    parser.add_argument("--decision-journal", default="")
    parser.add_argument("--active-rules", default="")
    parser.add_argument("--lessons-snapshot", default="")
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, server_func=serve_dashboard_manifest) -> int:
    manifest_path = Path(args.manifest)
    if args.write_manifest:
        manifest = build_dashboard_manifest(
            action_plan=args.action_plan,
            portfolio_state=args.portfolio_state,
            market_regime=args.market_regime,
            operator_status=args.operator_status,
            evidence_file=args.evidence_file,
            price_history_file=args.price_history_file,
            decision_journal_path=args.decision_journal,
            active_rules_path=args.active_rules,
            lessons_snapshot_path=args.lessons_snapshot,
            campaign_id=args.campaign_id,
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    payload = {
        "mode": "operator_dashboard_server",
        "manifest": str(manifest_path),
        "url": f"http://{args.host}:{args.port}/",
        "order_submission_enabled": False,
        "served": not args.write_manifest_only,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Serving read-only dashboard at {payload['url']}")
    if args.write_manifest_only:
        return 0
    server_func(manifest_path=manifest_path, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
