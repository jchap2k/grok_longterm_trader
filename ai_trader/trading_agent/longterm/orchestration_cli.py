"""CLI helpers for one long-term research cycle."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from longterm.discovery_enrichment import apply_discovery_enrichment, load_discovery_enrichment_file
from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_url
from longterm.idle_cash_policy import load_market_regime_snapshot
from longterm.motley_fool_settings import load_motley_fool_capture_settings
from longterm.orchestration import run_longterm_cycle
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


LONGTERM_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE_PATH = LONGTERM_DIR / "configs" / "roth_ira_profile.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one long-term research cycle.")
    parser.add_argument("--idea-file", default="")
    parser.add_argument("--idea-batch", default="")
    parser.add_argument("--discovery-candidates", default="")
    parser.add_argument("--discovery-source-file", default="")
    parser.add_argument("--discovery-source-url", default="")
    parser.add_argument("--discovery-source", default="")
    parser.add_argument("--discovery-enrichment-file", default="")
    parser.add_argument("--discovery-enrichment-source", default="local_enrichment")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--motley-fool-config", default=None)
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--market-regime-file", default="")
    parser.add_argument("--agent-config", default=None)
    parser.add_argument("--agent-preset", default="decision_4")
    parser.add_argument("--launch-login-if-needed", action="store_true")
    parser.add_argument("--active-sleeve-value", type=float, default=None)
    parser.add_argument("--available-cash", type=float, default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    cycle_func=run_longterm_cycle,
) -> int:
    profile = PortfolioProfile.from_file(args.profile_config)
    manual_ideas = _load_manual_ideas(args.idea_file, args.idea_batch)
    discovery_candidates = _load_discovery_candidates(
        args.discovery_candidates,
        source_file=args.discovery_source_file,
        source_url=args.discovery_source_url,
        source=args.discovery_source,
        enrichment_file=args.discovery_enrichment_file,
        enrichment_source=args.discovery_enrichment_source,
    )
    settings = load_motley_fool_capture_settings(args.motley_fool_config)
    portfolio_state = (
        PortfolioState.from_file(args.portfolio_state, profile=profile)
        if args.portfolio_state
        else None
    )

    kwargs: dict[str, Any] = {
        "profile": profile,
        "manual_ideas": manual_ideas,
        "discovery_candidates": discovery_candidates,
        "motley_fool_settings": settings,
        "journal_db_path": args.journal_db,
        "portfolio_state": portfolio_state,
        "market_regime": (
            load_market_regime_snapshot(args.market_regime_file)
            if args.market_regime_file
            else None
        ),
        "agent_preset": args.agent_preset,
        "launch_login_if_needed": args.launch_login_if_needed,
        "active_sleeve_value": args.active_sleeve_value,
        "available_cash": args.available_cash,
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


def _load_discovery_candidates(
    path: str,
    *,
    source_file: str = "",
    source_url: str = "",
    source: str = "",
    enrichment_file: str = "",
    enrichment_source: str = "local_enrichment",
) -> list[dict[str, Any]]:
    source_count = sum(1 for value in (path, source_file, source_url) if value)
    if source_count > 1:
        raise ValueError("Use only one of --discovery-candidates, --discovery-source-file, or --discovery-source-url.")
    if source_file:
        if not source:
            raise ValueError("--discovery-source is required when using --discovery-source-file.")
        candidates = load_candidate_source_file(source_file, source=source)
    elif source_url:
        if not source:
            raise ValueError("--discovery-source is required when using --discovery-source-url.")
        candidates = load_candidate_source_url(source_url, source=source)
    elif path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Discovery candidates file must contain a JSON list.")
        candidates = [dict(item) for item in payload]
    else:
        candidates = []
    if enrichment_file:
        candidates = apply_discovery_enrichment(
            candidates,
            load_discovery_enrichment_file(enrichment_file),
            source=enrichment_source,
        )
    return candidates
