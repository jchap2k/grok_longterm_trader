"""CLI for read-only Alpaca paper order status refresh."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from longterm.paper_order_status_refresh import (
    PaperOrderStatusBroker,
    PaperOrderStatusRefresh,
    build_empty_paper_order_status_refresh,
    build_paper_order_status_refresh_markdown,
    has_submitted_paper_orders,
)
from longterm.paper_trade_ledger import PaperTradeLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh submitted Alpaca paper order statuses.")
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    broker_factory: Callable[[], PaperOrderStatusBroker] | None = None,
) -> int:
    ledger = PaperTradeLedger(args.ledger_db)
    if not has_submitted_paper_orders(ledger, limit=args.limit):
        payload = build_empty_paper_order_status_refresh()
        _emit_payload(args, payload)
        return 0
    broker = broker_factory() if broker_factory else _default_broker()
    payload = PaperOrderStatusRefresh().run(
        ledger=ledger,
        broker=broker,
        limit=args.limit,
    )
    _emit_payload(args, payload)
    return 0 if payload.get("error_count", 0) == 0 else 1


def _emit_payload(args: argparse.Namespace, payload: dict) -> None:
    if args.report_output:
        Path(args.report_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(build_paper_order_status_refresh_markdown(payload), end="")


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _default_broker() -> PaperOrderStatusBroker:
    from brokers.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker(paper_trading=True)
    if not broker.connect():
        raise RuntimeError("Could not connect to Alpaca paper account.")
    return broker


__all__ = ["build_parser", "main", "run_cli"]
