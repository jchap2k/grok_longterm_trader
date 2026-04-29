"""CLI helpers for dry-run long-term action planning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.action_planner import ActionPlanner, PlannedAction
from longterm.cli import DEFAULT_PROFILE_PATH
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a non-executing long-term action plan.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--decision-file", required=True)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    return parser


def _load_json_object(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def _planned_action_to_dict(plan: PlannedAction) -> dict:
    return {
        "symbol": plan.symbol,
        "action": plan.action,
        "order_intent": plan.order_intent,
        "target_value": plan.target_value,
        "trade_value": plan.trade_value,
        "cash_shortfall": plan.cash_shortfall,
        "allowed": plan.allowed,
        "capital_needed_alert": plan.capital_needed_alert,
        "reason": plan.reason,
    }


def run_cli(args: argparse.Namespace) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    portfolio_state = PortfolioState.from_file(args.portfolio_state, profile=profile)
    decision = _load_json_object(args.decision_file)
    packet = create_research_packet_from_idea({"symbol": args.symbol}, profile=profile)
    plan = ActionPlanner().plan(
        packet,
        profile=profile,
        portfolio_state=portfolio_state,
        decision=decision,
    )
    print(json.dumps(_planned_action_to_dict(plan), indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
