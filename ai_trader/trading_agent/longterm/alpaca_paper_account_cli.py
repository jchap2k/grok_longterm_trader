"""CLI for read-only Alpaca paper account snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

from longterm.alpaca_paper_account import (
    AlpacaPaperAccountReader,
    PaperAccountSnapshot,
    paper_account_snapshot_to_portfolio_state,
)
from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read an Alpaca paper account snapshot for long-term planning."
    )
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--portfolio-state-output", default="")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    reader_factory: Callable[[], AlpacaPaperAccountReader] | None = None,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    reader = reader_factory() if reader_factory else _default_reader_factory()
    with redirect_stdout(sys.stderr):
        snapshot = reader.read_snapshot(profile=profile)
    portfolio_state = paper_account_snapshot_to_portfolio_state(snapshot)

    if args.portfolio_state_output:
        _write_portfolio_state(args.portfolio_state_output, portfolio_state)

    print(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _default_reader_factory() -> AlpacaPaperAccountReader:
    from brokers.alpaca_broker import AlpacaBroker

    return AlpacaPaperAccountReader(broker=AlpacaBroker(paper_trading=True), paper_trading=True)


def _write_portfolio_state(path: str | Path, state: PortfolioState) -> None:
    payload = {
        "cash": state.cash,
        "protected_symbols": state.protected_symbols,
        "holdings": [
            {
                "symbol": holding.symbol,
                "market_value": holding.market_value,
                "quantity": holding.quantity,
            }
            for holding in state.holdings
        ],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


__all__ = ["PaperAccountSnapshot", "build_parser", "main", "run_cli"]
