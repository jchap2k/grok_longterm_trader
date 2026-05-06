"""CLI for advisory Grok committee preset routing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longterm.committee_preset_policy import (
    CommitteePresetPolicyConfig,
    build_committee_preset_recommendation,
    load_json_mapping,
    load_research_items,
    write_committee_preset_recommendation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recommend decision_4 or decision_6 for the next committee call.")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--research-items", default="")
    parser.add_argument("--market-regime", default="")
    parser.add_argument("--active-sleeve-value", type=float, default=0.0)
    parser.add_argument("--large-position-pct", type=float, default=0.05)
    parser.add_argument("--elevated-vix", type=float, default=25.0)
    parser.add_argument("--borderline-valuation-min", type=float, default=35.0)
    parser.add_argument("--borderline-valuation-max", type=float, default=55.0)
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    recommendation = build_committee_preset_recommendation(
        action_plan=_load_optional_mapping(args.action_plan),
        research_items=load_research_items(args.research_items),
        market_regime=_load_optional_mapping(args.market_regime),
        active_sleeve_value=args.active_sleeve_value,
        config=CommitteePresetPolicyConfig(
            large_position_pct=args.large_position_pct,
            elevated_vix=args.elevated_vix,
            borderline_valuation_min=args.borderline_valuation_min,
            borderline_valuation_max=args.borderline_valuation_max,
        ),
    )
    if args.report_output:
        write_committee_preset_recommendation(recommendation, args.report_output)
    if args.json:
        print(json.dumps(recommendation, indent=2, sort_keys=True))
    else:
        print(
            "Committee preset: {preset} (escalation_required={escalation})".format(
                preset=recommendation["recommended_preset"],
                escalation=str(recommendation["escalation_required"]).lower(),
            )
        )
        print("No LLM calls or broker orders were made.")
        if args.report_output:
            print(f"Report: {Path(args.report_output)}")
    return 0


def _load_optional_mapping(path: str) -> dict[str, object]:
    if not path:
        return {}
    if not Path(path).exists():
        return {}
    return load_json_mapping(path)


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
