"""CLI helpers for one long-term research cycle."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from longterm.motley_fool_settings import load_motley_fool_capture_settings
from longterm.orchestration import run_longterm_cycle
from portfolio.portfolio_profile import PortfolioProfile


LONGTERM_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE_PATH = LONGTERM_DIR / "configs" / "roth_ira_profile.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one long-term research cycle.")
    parser.add_argument("--idea-file", default="")
    parser.add_argument("--idea-batch", default="")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--motley-fool-config", default=None)
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--agent-config", default=None)
    parser.add_argument("--agent-preset", default="decision_4")
    parser.add_argument("--quiet", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    cycle_func=run_longterm_cycle,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    manual_ideas = _load_manual_ideas(args.idea_file, args.idea_batch)
    settings = load_motley_fool_capture_settings(args.motley_fool_config)

    kwargs: dict[str, Any] = {
        "profile": profile,
        "manual_ideas": manual_ideas,
        "motley_fool_settings": settings,
        "journal_db_path": args.journal_db,
        "agent_preset": args.agent_preset,
        "verbose": not args.quiet,
    }
    if args.agent_config:
        kwargs["agent_config_path"] = args.agent_config

    result = cycle_func(**kwargs)
    payload = asdict(result) if is_dataclass(result) else result
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _load_manual_ideas(idea_file: str, idea_batch: str) -> list[dict[str, Any]]:
    if idea_file and idea_batch:
        raise ValueError("Use either --idea-file or --idea-batch, not both.")
    if idea_file:
        payload = json.loads(Path(idea_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Idea file must contain a JSON object.")
        return [payload]
    if idea_batch:
        payload = json.loads(Path(idea_batch).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Idea batch file must contain a JSON list.")
        return [dict(item) for item in payload]
    return []
