"""Command helpers for long-term ticker research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.research_runner import LongTermResearchRunner
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


LONGTERM_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE_PATH = LONGTERM_DIR / "configs" / "roth_ira_profile.json"
DEFAULT_AGENT_CONFIG_PATH = LONGTERM_DIR / "configs" / "longterm_agent_specs_v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run long-term research for one ticker.")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--idea-file", default="")
    parser.add_argument("--company-name", default="")
    parser.add_argument("--company-category", default="")
    parser.add_argument("--business-summary", default="")
    parser.add_argument("--thesis", default="")
    parser.add_argument("--growth-driver", default="")
    parser.add_argument("--industry-context", default="")
    parser.add_argument("--idea-source", default="manual_cli")
    parser.add_argument("--financial-metrics", default="")
    parser.add_argument("--macro-regime", default="")
    parser.add_argument("--market-risk-context", default="")
    parser.add_argument("--supporting-evidence", default="")
    parser.add_argument("--risk-flags", default="")
    parser.add_argument("--candidate-price", type=float, default=None)
    parser.add_argument("--benchmark-price", type=float, default=None)
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--agent-config", default=str(DEFAULT_AGENT_CONFIG_PATH))
    parser.add_argument("--journal-db", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def load_idea_file(path: str) -> dict:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Idea file must contain a JSON object.")
    return payload


def _choose_arg_value(value, fallback):
    if value in (None, ""):
        return fallback
    return value


def create_packet_from_args(args: argparse.Namespace):
    profile = PortfolioProfile.from_file(args.profile_config)
    idea = load_idea_file(args.idea_file)
    symbol = _choose_arg_value(args.symbol, idea.get("symbol", ""))
    if not symbol:
        raise ValueError("A symbol is required via --symbol or --idea-file.")

    return create_research_packet_from_idea(
        {
            "symbol": symbol,
            "company_name": _choose_arg_value(args.company_name, idea.get("company_name", "")),
            "company_category": _choose_arg_value(args.company_category, idea.get("company_category", "")),
            "business_summary": _choose_arg_value(args.business_summary, idea.get("business_summary", "")),
            "thesis_summary": _choose_arg_value(args.thesis, idea.get("thesis_summary", "")),
            "primary_growth_driver": _choose_arg_value(args.growth_driver, idea.get("primary_growth_driver", "")),
            "industry_context": _choose_arg_value(args.industry_context, idea.get("industry_context", "")),
            "source_notes": idea.get("source_notes", []),
        },
        profile=profile,
        idea_source=_choose_arg_value(args.idea_source, idea.get("idea_source", "manual_cli")),
    )


def run_cli(args: argparse.Namespace) -> int:
    packet = create_packet_from_args(args)
    if args.dry_run:
        print(json.dumps(packet.to_dict(), indent=2, sort_keys=True))
        return 0

    runner = LongTermResearchRunner(
        config_path=args.agent_config,
        verbose=not args.quiet,
    )
    decision_id = runner.run_and_record(
        packet,
        journal_db_path=args.journal_db,
        candidate_price=args.candidate_price,
        benchmark_price=args.benchmark_price,
        financial_metrics=args.financial_metrics,
        macro_regime=args.macro_regime,
        market_risk_context=args.market_risk_context,
        supporting_evidence=args.supporting_evidence,
        risk_flags=args.risk_flags,
    )
    print(decision_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
