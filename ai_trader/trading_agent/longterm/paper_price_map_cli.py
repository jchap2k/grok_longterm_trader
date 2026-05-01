"""CLI for read-only paper preview price-map generation."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.paper_price_map import (
    build_price_map_from_action_plan,
    build_price_map_markdown,
)
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a price map for whole-share paper previews.")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--price-map-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    quote_provider_factory: Callable[[], object] | None = None,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    action_plan = _load_json(args.action_plan)
    provider = quote_provider_factory() if quote_provider_factory else _default_quote_provider()
    with redirect_stdout(sys.stderr):
        result = build_price_map_from_action_plan(
            action_plan,
            quote_provider=provider,
            protected_symbols=set(profile.protected_symbols),
        ).to_dict()
    if args.price_map_output:
        _write_json(args.price_map_output, result["price_map"])
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(build_price_map_markdown(result), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _default_quote_provider() -> object:
    from brokers.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker(paper_trading=True)
    if not broker.connect():
        raise RuntimeError("Could not connect to Alpaca paper data for quotes.")
    return _DisconnectingQuoteProvider(broker)


class _DisconnectingQuoteProvider:
    def __init__(self, broker: object):
        self.broker = broker

    def get_quote(self, symbol: str) -> object:
        return self.broker.get_quote(symbol)

    def __del__(self) -> None:
        disconnect = getattr(self.broker, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
            except Exception:
                pass


def _load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _write_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


__all__ = ["build_parser", "main", "run_cli"]
