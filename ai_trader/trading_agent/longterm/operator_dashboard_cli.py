"""CLI for static long-term trader operator dashboards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from longterm.market_regime_snapshot import fetch_yfinance_history
from longterm.operator_dashboard import (
    build_operator_dashboard,
    build_operator_dashboard_html,
    build_operator_dashboard_markdown,
    build_operator_dashboard_site,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only long-term operator dashboard.")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--dashboard-file", default="")
    parser.add_argument("--evidence-file", default="")
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--price-history-file", default="")
    parser.add_argument("--fetch-price-history", action="store_true")
    parser.add_argument("--price-history-period", default="1y")
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--operator-status", default="")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--html-output", default="")
    parser.add_argument("--site-output-dir", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, price_history_fetcher=fetch_yfinance_history) -> int:
    action_plan = _load_json(args.action_plan) if args.action_plan else {}
    dashboard = (
        _load_json(args.dashboard_file)
        if args.dashboard_file
        else build_operator_dashboard(
            action_plan=action_plan,
            market_regime=_load_json(args.market_regime) if args.market_regime else None,
            operator_status=_load_json(args.operator_status) if args.operator_status else None,
        )
    )
    evidence_items = _load_json_list(args.evidence_file) if args.evidence_file else []
    portfolio_state = _load_json(args.portfolio_state) if args.portfolio_state else {}
    price_history = _load_json(args.price_history_file) if args.price_history_file else {}
    if args.fetch_price_history:
        price_history = _fetch_site_price_history(
            dashboard=dashboard,
            action_plan=action_plan,
            portfolio_state=portfolio_state,
            period=args.price_history_period,
            fetcher=price_history_fetcher,
            existing=price_history,
        )
    if args.report_output:
        output_path = Path(args.report_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(dashboard, indent=2, sort_keys=True), encoding="utf-8")
    if args.html_output:
        html_path = Path(args.html_output)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(build_operator_dashboard_html(dashboard), encoding="utf-8")
    site_summary: dict[str, Any] = {}
    if args.site_output_dir:
        site_dir = Path(args.site_output_dir)
        pages = build_operator_dashboard_site(
            dashboard=dashboard,
            action_plan=action_plan,
            portfolio_state=portfolio_state,
            evidence_items=evidence_items,
            price_history_by_symbol=price_history,
        )
        for relative_path, html in pages.items():
            page_path = site_dir / relative_path
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text(html, encoding="utf-8")
        site_summary = {
            "site_output_dir": str(site_dir),
            "site_page_count": len(pages),
            "site_pages": sorted(pages),
        }
    if args.json:
        print(json.dumps({**dashboard, **site_summary}, indent=2, sort_keys=True))
    else:
        if site_summary:
            print(f"Wrote dashboard site to {site_summary['site_output_dir']} ({site_summary['site_page_count']} pages).")
        else:
            print(build_operator_dashboard_markdown(dashboard), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _load_json_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("ideas") or payload.get("items") or []
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list in {path}.")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _fetch_site_price_history(
    *,
    dashboard: dict[str, Any],
    action_plan: dict[str, Any],
    portfolio_state: dict[str, Any],
    period: str,
    fetcher,
    existing: dict[str, Any],
) -> dict[str, Any]:
    price_history = dict(existing)
    symbols: list[str] = []
    for value in dashboard.get("paper_submit_candidates") or []:
        _append_symbol(symbols, value)
    for value in dashboard.get("parking_symbols") or []:
        _append_symbol(symbols, value)
    for intent in action_plan.get("intents") or []:
        if isinstance(intent, dict):
            _append_symbol(symbols, intent.get("symbol"))
    for holding in portfolio_state.get("holdings") or []:
        if isinstance(holding, dict):
            _append_symbol(symbols, holding.get("symbol"))
    for symbol in symbols:
        if symbol not in price_history:
            price_history[symbol] = fetcher(symbol, period)
    return price_history


def _append_symbol(symbols: list[str], value: Any) -> None:
    symbol = str(value or "").upper().strip()
    if symbol and symbol not in symbols:
        symbols.append(symbol)


__all__ = ["build_parser", "main", "run_cli"]
