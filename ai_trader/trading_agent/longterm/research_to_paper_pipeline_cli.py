"""CLI for the dry-run research-to-paper pipeline command planner."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.perplexity_research_enrichment import (
    DEFAULT_PERPLEXITY_API_URL,
    DEFAULT_PERPLEXITY_MAX_TOKENS,
    DEFAULT_PERPLEXITY_MODEL,
)
from longterm.portfolio_state import PortfolioState
from longterm.research_to_paper_pipeline import (
    build_committee_batch_stages,
    build_final_planning_action_plan_extract_stage,
    build_final_planning_refresh_stage,
    build_generated_committee_batch_runner_stage,
    build_paper_preflight_stages,
    build_research_campaign_stages,
    run_pipeline_stages,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or print a no-submit research-to-paper preflight pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--rules-path", default=str(Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"))
    parser.add_argument("--action-plan", required=True)
    parser.add_argument("--portfolio-state", required=True)
    parser.add_argument("--journal-db", required=True)
    parser.add_argument("--ledger-db", required=True)
    research_source = parser.add_mutually_exclusive_group()
    research_source.add_argument("--research-source-file", default="")
    research_source.add_argument("--research-source-url", default="")
    parser.add_argument("--research-source", default="")
    parser.add_argument("--research-campaign-dir", default="")
    parser.add_argument("--research-resume", action="store_true")
    parser.add_argument(
        "--research-run-until",
        choices=["scan_ready", "evidence_ready", "research_queue_ready"],
        default="research_queue_ready",
    )
    parser.add_argument("--research-watchlist-limit", type=int, default=100)
    parser.add_argument("--research-universe-batch-size", type=int, default=50)
    parser.add_argument("--research-top-percent", type=float, default=10.0)
    parser.add_argument("--research-min-pass-count", type=int, default=10)
    parser.add_argument("--research-max-pass-count", type=int, default=300)
    parser.add_argument("--research-min-coverage-percent-for-enrichment", type=float, default=80.0)
    parser.add_argument("--research-max-fundamental-fetches", type=int, default=500)
    parser.add_argument("--research-fundamental-fetch-chunk-size", type=int, default=500)
    parser.add_argument("--research-evidence-batch-size", type=int, default=25)
    parser.add_argument("--research-max-evidence-batches", type=int, default=None)
    parser.add_argument("--research-rate-limit-batch-size", type=int, default=5)
    parser.add_argument("--research-rate-limit-pause-seconds", type=float, default=66.0)
    parser.add_argument("--research-campaign-batch-pause-seconds", type=float, default=0.0)
    parser.add_argument("--polygon-news", action="store_true")
    parser.add_argument("--research-news-cache-path", default="")
    grok = parser.add_mutually_exclusive_group()
    grok.add_argument("--xai-grok", action="store_true")
    grok.add_argument("--skip-grok", action="store_true")
    grok.add_argument("--perplexity-research", action="store_true")
    parser.add_argument("--perplexity-api-key-env", default="PERPLEXITY_API_KEY")
    parser.add_argument("--perplexity-model", default=DEFAULT_PERPLEXITY_MODEL)
    parser.add_argument("--perplexity-api-url", default=DEFAULT_PERPLEXITY_API_URL)
    parser.add_argument("--perplexity-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--perplexity-max-tokens", type=int, default=DEFAULT_PERPLEXITY_MAX_TOKENS)
    parser.add_argument("--perplexity-search-context-size", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--perplexity-credits-purchased-to-date", type=float, default=None)
    parser.add_argument("--selection-top-percent", type=float, default=20.0)
    parser.add_argument("--selection-min-count", type=int, default=10)
    parser.add_argument("--selection-max-count", type=int, default=50)
    parser.add_argument("--recent-research-symbols-file", default="")
    parser.add_argument("--research-as-of-date", default="")
    parser.add_argument("--research-batch-size", type=int, default=5)
    parser.add_argument("--run-generated-committee-batches", action="store_true")
    parser.add_argument(
        "--no-generated-committee-resume",
        action="store_true",
        help="Disable artifact-based resume for generated committee batch runs.",
    )
    parser.add_argument(
        "--generated-committee-max-batches",
        type=int,
        default=None,
        help="Run at most this many pending generated committee batches in this pipeline invocation.",
    )
    parser.add_argument("--committee-batch-dir", default="")
    parser.add_argument("--final-planning-refresh", action="store_true")
    parser.add_argument("--market-regime-file", default="")
    parser.add_argument("--motley-fool-config", default="")
    parser.add_argument("--agent-preset", default="decision_4")
    parser.add_argument("--active-sleeve-value", type=float, default=None)
    parser.add_argument("--available-cash", type=float, default=None)
    parser.add_argument(
        "--planning-capital-from-portfolio-state",
        action="store_true",
        help="Resolve final-planning active sleeve value and available cash from --portfolio-state.",
    )
    parser.add_argument("--price-map", default="")
    parser.add_argument("--expected-cash", type=float, default=None)
    parser.add_argument(
        "--expected-cash-from-portfolio-state",
        action="store_true",
        help="Resolve the paper-account cash cleanliness expectation from --portfolio-state cash.",
    )
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--skip-price-map", action="store_true")
    parser.add_argument(
        "--allow-existing-paper-positions",
        action="store_true",
        help="Allow current paper holdings in readiness checks for ongoing paper portfolio runs.",
    )
    parser.add_argument("--print-plan-only", action="store_true")
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve_expected_cash(args: argparse.Namespace) -> float | None:
    if args.expected_cash is not None and args.expected_cash_from_portfolio_state:
        raise ValueError("Choose either --expected-cash or --expected-cash-from-portfolio-state, not both.")
    if not args.expected_cash_from_portfolio_state:
        return args.expected_cash

    payload = _load_portfolio_payload(args.portfolio_state)
    return _numeric_cash(payload, flag_name="--expected-cash-from-portfolio-state")


def _resolve_planning_capital(args: argparse.Namespace) -> tuple[float | None, float | None]:
    if not args.planning_capital_from_portfolio_state:
        return args.active_sleeve_value, args.available_cash
    if args.active_sleeve_value is not None or args.available_cash is not None:
        raise ValueError(
            "Choose explicit planning capital or --planning-capital-from-portfolio-state, not both."
        )

    payload = _load_portfolio_payload(args.portfolio_state)
    available_cash = _numeric_cash(payload, flag_name="--planning-capital-from-portfolio-state")
    portfolio_state = PortfolioState(**payload)
    active_sleeve_value = round(available_cash + float(portfolio_state.active_market_value or 0.0), 2)
    return active_sleeve_value, available_cash


def _load_portfolio_payload(portfolio_state_path: str | Path) -> dict:
    portfolio_path = Path(portfolio_state_path)
    try:
        payload = json.loads(portfolio_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Portfolio state file not found for portfolio-derived planning inputs: {portfolio_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Portfolio state file is not valid JSON for portfolio-derived planning inputs: {portfolio_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Portfolio state file must contain a JSON object: {portfolio_path}")
    return payload


def _numeric_cash(payload: dict, *, flag_name: str) -> float:
    cash = payload.get("cash") if isinstance(payload, dict) else None
    if isinstance(cash, bool) or cash is None:
        raise ValueError(f"{flag_name} requires a numeric cash field in --portfolio-state.")
    try:
        return float(cash)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{flag_name} requires a numeric cash field in --portfolio-state.") from exc


def run_cli(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    summary_output = Path(args.summary_output) if args.summary_output else output_dir / "pipeline_summary.json"
    expected_cash = _resolve_expected_cash(args)
    active_sleeve_value, available_cash = _resolve_planning_capital(args)
    stages = []
    research_requested = bool(args.research_source_file or args.research_source_url or args.research_campaign_dir)
    if research_requested:
        stages.extend(
            build_research_campaign_stages(
                output_dir=output_dir,
                source_file=args.research_source_file or None,
                source_url=args.research_source_url,
                source=args.research_source,
                campaign_dir=args.research_campaign_dir,
                resume=args.research_resume,
                run_until=args.research_run_until,
                watchlist_limit=args.research_watchlist_limit,
                universe_batch_size=args.research_universe_batch_size,
                top_percent=args.research_top_percent,
                min_pass_count=args.research_min_pass_count,
                max_pass_count=args.research_max_pass_count,
                min_coverage_percent_for_enrichment=args.research_min_coverage_percent_for_enrichment,
                max_fundamental_fetches=args.research_max_fundamental_fetches,
                fundamental_fetch_chunk_size=args.research_fundamental_fetch_chunk_size,
                evidence_batch_size=args.research_evidence_batch_size,
                max_evidence_batches=args.research_max_evidence_batches,
                rate_limit_batch_size=args.research_rate_limit_batch_size,
                rate_limit_pause_seconds=args.research_rate_limit_pause_seconds,
                campaign_batch_pause_seconds=args.research_campaign_batch_pause_seconds,
                polygon_news=args.polygon_news,
                news_cache_path=args.research_news_cache_path or None,
                xai_grok=args.xai_grok,
                skip_grok=(not args.xai_grok and not args.perplexity_research),
                perplexity_research=args.perplexity_research,
                perplexity_api_key_env=args.perplexity_api_key_env,
                perplexity_model=args.perplexity_model,
                perplexity_api_url=args.perplexity_api_url,
                perplexity_timeout_seconds=args.perplexity_timeout_seconds,
                perplexity_max_tokens=args.perplexity_max_tokens,
                perplexity_search_context_size=args.perplexity_search_context_size,
                perplexity_credits_purchased_to_date=args.perplexity_credits_purchased_to_date,
                selection_top_percent=args.selection_top_percent,
                selection_min_count=args.selection_min_count,
                selection_max_count=args.selection_max_count,
                portfolio_state=args.portfolio_state,
                recent_research_symbols_file=args.recent_research_symbols_file or None,
                as_of_date=args.research_as_of_date,
                research_batch_size=args.research_batch_size,
            )
        )
    if args.run_generated_committee_batches:
        if not args.research_campaign_dir and not args.committee_batch_dir:
            raise ValueError("--run-generated-committee-batches requires --research-campaign-dir or --committee-batch-dir.")
        stages.append(
            build_generated_committee_batch_runner_stage(
                output_dir=output_dir,
                campaign_dir=args.research_campaign_dir or None,
                committee_batch_dir=args.committee_batch_dir or None,
                journal_db=args.journal_db,
                portfolio_state=args.portfolio_state,
                market_regime_file=args.market_regime_file or None,
                motley_fool_config=args.motley_fool_config or None,
                agent_preset=args.agent_preset,
                profile_config=args.profile_config,
                resume=not args.no_generated_committee_resume,
                max_batches=args.generated_committee_max_batches,
            )
        )
    if args.committee_batch_dir and not args.run_generated_committee_batches:
        stages.extend(
            build_committee_batch_stages(
                committee_batch_dir=args.committee_batch_dir,
                output_dir=output_dir,
                journal_db=args.journal_db,
                portfolio_state=args.portfolio_state,
                market_regime_file=args.market_regime_file or None,
                motley_fool_config=args.motley_fool_config or None,
                agent_preset=args.agent_preset,
                profile_config=args.profile_config,
            )
        )
    if args.final_planning_refresh:
        stages.append(
            build_final_planning_refresh_stage(
                output_dir=output_dir,
                journal_db=args.journal_db,
                portfolio_state=args.portfolio_state,
                market_regime_file=args.market_regime_file or None,
                motley_fool_config=args.motley_fool_config or None,
                agent_preset=args.agent_preset,
                profile_config=args.profile_config,
                active_sleeve_value=active_sleeve_value,
                available_cash=available_cash,
            )
        )
        stages.append(
            build_final_planning_action_plan_extract_stage(
                output_dir=output_dir,
                action_plan=args.action_plan,
            )
        )
    stages.extend(build_paper_preflight_stages(
        output_dir=output_dir,
        rules_path=args.rules_path,
        action_plan=args.action_plan,
        portfolio_state=args.portfolio_state,
        journal_db=args.journal_db,
        ledger_db=args.ledger_db,
        price_map=args.price_map or None,
        expected_cash=expected_cash,
        profile_config=args.profile_config,
        skip_price_map=args.skip_price_map,
        allow_existing_paper_positions=args.allow_existing_paper_positions,
    ))
    result = run_pipeline_stages(
        stages,
        output_dir=output_dir,
        summary_output=summary_output,
        print_plan_only=args.print_plan_only,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Pipeline {result.status}: {len(result.stages)} / {result.stage_count} stages recorded.")
        print(f"Summary: {summary_output}")
    return 0 if result.status in {"completed", "planned"} else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
