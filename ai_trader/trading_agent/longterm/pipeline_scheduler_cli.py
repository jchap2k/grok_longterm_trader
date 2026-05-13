"""CLI for the recurring no-submit research-to-paper pipeline scheduler."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from longterm.orchestration_cli import DEFAULT_PROFILE_PATH
from longterm.perplexity_research_enrichment import (
    DEFAULT_PERPLEXITY_API_URL,
    DEFAULT_PERPLEXITY_MAX_TOKENS,
    DEFAULT_PERPLEXITY_MODEL,
)
from longterm.pipeline_scheduler import (
    PipelineSchedulerConfig,
    PipelineSchedulerInputs,
    derive_scheduler_resource_controls,
    run_pipeline_scheduler,
    validate_scheduler_command_template,
)


DEFAULT_FINAL_PLANNING_TIMEOUT_SECONDS = 900.0
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a recurring no-submit research-to-paper pipeline scheduler."
    )
    parser.add_argument(
        "--config-file",
        default="",
        help=(
            "Optional JSON profile with an 'args' object using argparse dest names. "
            "Explicit CLI args override scalar profile values."
        ),
    )
    parser.add_argument(
        "--preset",
        choices=["", "ongoing-no-submit"],
        default="",
        help="Use a safe built-in scheduler command set instead of manual command templates.",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pre-pipeline-refresh-command-template", default="")
    parser.add_argument("--pipeline-command-template", default="")
    parser.add_argument("--market-regime-command-template", default="")
    parser.add_argument("--committee-preset-policy-command-template", default="")
    parser.add_argument("--scheduler-policy-command-template", default="")
    parser.add_argument("--full-research-command-template", default="")
    parser.add_argument("--account-refresh-command-template", default="")
    parser.add_argument("--post-run-verification-command-template", default="")
    parser.add_argument("--scheduler-review-bundle-command-template", default="")
    parser.add_argument("--portfolio-news-monitor-command-template", default="")
    parser.add_argument("--motley-fool-new-recs-capture-command-template", default="")
    parser.add_argument("--motley-fool-new-recs-delta-command-template", default="")
    parser.add_argument("--journal-db", default="")
    parser.add_argument("--ledger-db", default="")
    parser.add_argument("--action-plan", default="")
    parser.add_argument("--profile-config", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--market-regime-file", default="")
    parser.add_argument("--auto-market-regime-snapshot", action="store_true")
    parser.add_argument("--market-regime-provider", choices=["yfinance", "fredapi"], default="yfinance")
    parser.add_argument("--fred-api-key-env", default="FRED_API_KEY")
    parser.add_argument("--fred-provider-attempts", type=int, default=3)
    parser.add_argument("--fred-provider-retry-delay-seconds", type=float, default=2.0)
    parser.add_argument("--price-map", default="")
    parser.add_argument("--scheduler-config-validation", default="")
    parser.add_argument("--scheduler-task-plan", default="")
    parser.add_argument("--scheduler-handoff", default="")
    parser.add_argument("--paper-submit-mode-plan", default="")
    parser.add_argument("--skip-price-map", action="store_true")
    parser.add_argument("--allow-existing-paper-positions", action="store_true")
    parser.add_argument("--final-planning-refresh", action="store_true")
    parser.add_argument(
        "--final-planning-timeout-seconds",
        type=float,
        default=None,
        help=(
            "Timeout forwarded to the final-planning refresh stage. "
            "The ongoing-no-submit preset defaults to 900 seconds when final planning is enabled."
        ),
    )
    parser.add_argument("--planning-capital-from-portfolio-state", action="store_true")
    parser.add_argument("--expected-cash-from-portfolio-state", action="store_true")
    research_source = parser.add_mutually_exclusive_group()
    research_source.add_argument("--research-source-file", default="")
    research_source.add_argument("--research-source-url", default="")
    parser.add_argument("--research-source", default="")
    parser.add_argument("--research-campaign-dir", default="")
    parser.add_argument("--research-resume", action="store_true")
    parser.add_argument(
        "--research-run-until",
        choices=["scan_ready", "evidence_ready", "research_queue_ready"],
        default="",
    )
    parser.add_argument("--research-watchlist-limit", type=int, default=None)
    parser.add_argument("--research-universe-batch-size", type=int, default=None)
    parser.add_argument("--research-top-percent", type=float, default=None)
    parser.add_argument("--research-min-pass-count", type=int, default=None)
    parser.add_argument("--research-max-pass-count", type=int, default=None)
    parser.add_argument("--research-min-coverage-percent-for-enrichment", type=float, default=None)
    parser.add_argument("--research-max-fundamental-fetches", type=int, default=None)
    parser.add_argument("--research-fundamental-fetch-chunk-size", type=int, default=None)
    parser.add_argument("--research-evidence-batch-size", type=int, default=None)
    parser.add_argument("--research-max-evidence-batches", type=int, default=None)
    parser.add_argument("--research-rate-limit-batch-size", type=int, default=None)
    parser.add_argument("--research-rate-limit-pause-seconds", type=float, default=None)
    parser.add_argument("--research-campaign-batch-pause-seconds", type=float, default=None)
    parser.add_argument("--polygon-news", action="store_true")
    parser.add_argument("--research-news-cache-path", default="")
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--xai-grok", action="store_true")
    provider.add_argument("--skip-grok", action="store_true")
    provider.add_argument("--perplexity-research", action="store_true")
    parser.add_argument("--perplexity-api-key-env", default="PERPLEXITY_API_KEY")
    parser.add_argument("--perplexity-model", default=DEFAULT_PERPLEXITY_MODEL)
    parser.add_argument("--perplexity-api-url", default=DEFAULT_PERPLEXITY_API_URL)
    parser.add_argument("--perplexity-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--perplexity-max-tokens", type=int, default=DEFAULT_PERPLEXITY_MAX_TOKENS)
    parser.add_argument("--perplexity-search-context-size", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--perplexity-credits-purchased-to-date", type=float, default=None)
    parser.add_argument("--kronos-advisory", action="store_true")
    parser.add_argument("--kronos-root", default="")
    parser.add_argument("--kronos-python", default="")
    parser.add_argument("--kronos-limit", type=int, default=None)
    parser.add_argument("--selection-top-percent", type=float, default=None)
    parser.add_argument("--selection-min-count", type=int, default=None)
    parser.add_argument("--selection-max-count", type=int, default=None)
    parser.add_argument("--recent-research-symbols-file", default="")
    parser.add_argument("--research-as-of-date", default="")
    parser.add_argument("--research-batch-size", type=int, default=None)
    parser.add_argument(
        "--policy-gated-full-research",
        action="store_true",
        help=(
            "For the ongoing-no-submit preset, keep broad-universe research out of the daily "
            "pipeline command and run it only when the scheduler policy says full research is due."
        ),
    )
    parser.add_argument(
        "--portfolio-news-monitor",
        action="store_true",
        help="Run the deterministic portfolio news monitor before each pipeline cycle.",
    )
    parser.add_argument("--portfolio-news-snapshot-file", default="")
    parser.add_argument("--portfolio-news-watchlist-ideas", default="")
    parser.add_argument("--portfolio-news-published-after", default="")
    parser.add_argument("--portfolio-news-relevance-threshold", type=float, default=0.55)
    parser.add_argument("--portfolio-news-max-articles-per-symbol", type=int, default=5)
    parser.add_argument(
        "--position-review-queue",
        action="store_true",
        help="Build a deterministic no-submit sell/rebalance/news review queue after portfolio-news monitoring.",
    )
    parser.add_argument(
        "--scheduler-review-bundle",
        action="store_true",
        help=(
            "After post-run verification passes, assemble the no-submit operator scheduler review bundle "
            "from the dashboard manifest, handoff, position queue, and verifier report."
        ),
    )
    parser.add_argument("--portfolio-news-followup-batches", action="store_true")
    parser.add_argument("--portfolio-news-followup-batch-size", type=int, default=3)
    parser.add_argument("--run-portfolio-news-followup-committee-batches", action="store_true")
    parser.add_argument("--portfolio-news-followup-max-batches", type=int, default=None)
    parser.add_argument("--portfolio-news-followup-agent-preset", default="decision_4")
    parser.add_argument("--no-portfolio-news-followup-committee-resume", action="store_true")
    parser.add_argument("--motley-fool-new-recs-monitor", action="store_true")
    parser.add_argument("--motley-fool-profile-dir", default="")
    parser.add_argument("--motley-fool-new-recs-batches", action="store_true")
    parser.add_argument("--motley-fool-new-recs-batch-size", type=int, default=3)
    parser.add_argument("--run-motley-fool-new-recs-committee-batches", action="store_true")
    parser.add_argument("--motley-fool-new-recs-max-batches", type=int, default=None)
    parser.add_argument("--motley-fool-new-recs-agent-preset", default="decision_4")
    parser.add_argument("--no-motley-fool-new-recs-committee-resume", action="store_true")
    parser.add_argument("--research-supplemental-source-file", action="append", default=[])
    parser.add_argument("--research-supplemental-source-url", action="append", default=[])
    parser.add_argument("--research-supplemental-ideas-file", action="append", default=[])
    parser.add_argument("--run-generated-committee-batches", action="store_true")
    parser.add_argument("--no-generated-committee-resume", action="store_true")
    parser.add_argument("--generated-committee-max-batches", type=int, default=None)
    parser.add_argument("--committee-batch-dir", default="")
    parser.add_argument(
        "--rules-path",
        default=str(Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"),
    )
    parser.add_argument(
        "--research-rules-path",
        default=str(Path(__file__).resolve().parents[2] / "rules" / "weekly_full_scan_rules.txt"),
    )
    parser.add_argument("--max-runs", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=3600.0)
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--print-plan-only", action="store_true")
    parser.add_argument(
        "--validate-config-only",
        action="store_true",
        help="Validate the resolved scheduler profile/templates without creating run folders or executing commands.",
    )
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def _quote(value: str | Path) -> str:
    return subprocess.list2cmdline([str(value)])


def _normalize_path_arg(value: str | Path) -> str:
    text = str(value)
    if text.startswith("{") or "://" in text:
        return text
    return str(Path(text).expanduser().resolve())


def _quote_path_arg(value: str | Path) -> str:
    return _quote(_normalize_path_arg(value))


def _script_path(script_name: str) -> str:
    return _quote(SCRIPT_DIR / script_name)


def _require_preset_path(args: argparse.Namespace, attr: str, flag: str) -> str:
    value = getattr(args, attr, "")
    if not value:
        raise ValueError(f"{flag} is required when --preset ongoing-no-submit is used.")
    return _normalize_path_arg(value)


def _append_optional_path(parts: list[str], flag: str, value: str) -> None:
    if value:
        parts.extend([flag, _quote_path_arg(value)])


def _append_optional_value(parts: list[str], flag: str, value: object | None) -> None:
    if value is not None and value != "":
        parts.extend([flag, _quote(str(value))])


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _append_optional_flag(parts: list[str], flag: str, enabled: bool) -> None:
    if enabled:
        parts.append(flag)


def _validate_ongoing_no_submit_research_bounds(args: argparse.Namespace) -> None:
    if args.final_planning_timeout_seconds is not None and args.final_planning_timeout_seconds <= 0:
        raise ValueError("--final-planning-timeout-seconds must be positive when supplied.")
    if (args.perplexity_research or args.xai_grok) and args.research_max_pass_count is None:
        raise ValueError(
            "Paid research provider mode with --preset ongoing-no-submit requires "
            "--research-max-pass-count to bound paid enrichment."
        )
    if args.policy_gated_full_research:
        if not (args.research_source_file or args.research_source_url):
            raise ValueError("--policy-gated-full-research requires --research-source-file or --research-source-url.")
        if not args.research_campaign_dir:
            raise ValueError("--policy-gated-full-research requires --research-campaign-dir.")
    if args.run_generated_committee_batches and args.generated_committee_max_batches is None:
        raise ValueError(
            "--run-generated-committee-batches with --preset ongoing-no-submit requires "
            "--generated-committee-max-batches to bound LLM committee work."
        )
    if args.portfolio_news_monitor and not args.portfolio_news_snapshot_file:
        raise ValueError("--portfolio-news-monitor with --preset ongoing-no-submit requires --portfolio-news-snapshot-file.")
    if args.position_review_queue and not args.portfolio_news_monitor:
        raise ValueError("--position-review-queue with --preset ongoing-no-submit requires --portfolio-news-monitor.")
    if args.scheduler_review_bundle:
        if not args.position_review_queue:
            raise ValueError("--scheduler-review-bundle with --preset ongoing-no-submit requires --position-review-queue.")
        if not args.scheduler_handoff:
            raise ValueError("--scheduler-review-bundle with --preset ongoing-no-submit requires --scheduler-handoff.")
    if args.portfolio_news_followup_batches and not args.portfolio_news_monitor:
        raise ValueError("--portfolio-news-followup-batches with --preset ongoing-no-submit requires --portfolio-news-monitor.")
    if args.portfolio_news_followup_batch_size < 1:
        raise ValueError("--portfolio-news-followup-batch-size must be positive.")
    if args.motley_fool_new_recs_batches and not args.motley_fool_new_recs_monitor:
        raise ValueError("--motley-fool-new-recs-batches with --preset ongoing-no-submit requires --motley-fool-new-recs-monitor.")
    if args.motley_fool_new_recs_batch_size < 1:
        raise ValueError("--motley-fool-new-recs-batch-size must be positive.")
    if args.kronos_limit is not None and args.kronos_limit < 1:
        raise ValueError("--kronos-limit must be positive when supplied.")
    if args.run_portfolio_news_followup_committee_batches:
        if not args.portfolio_news_monitor or not args.portfolio_news_followup_batches:
            raise ValueError(
                "--run-portfolio-news-followup-committee-batches with --preset ongoing-no-submit requires "
                "--portfolio-news-monitor and --portfolio-news-followup-batches."
            )
        if args.portfolio_news_followup_max_batches is None or args.portfolio_news_followup_max_batches < 1:
            raise ValueError(
                "--run-portfolio-news-followup-committee-batches with --preset ongoing-no-submit requires "
                "--portfolio-news-followup-max-batches to bound LLM committee work."
            )
    if args.run_motley_fool_new_recs_committee_batches:
        if not args.motley_fool_new_recs_monitor or not args.motley_fool_new_recs_batches:
            raise ValueError(
                "--run-motley-fool-new-recs-committee-batches with --preset ongoing-no-submit requires "
                "--motley-fool-new-recs-monitor and --motley-fool-new-recs-batches."
            )
        if args.motley_fool_new_recs_max_batches is None or args.motley_fool_new_recs_max_batches < 1:
            raise ValueError(
                "--run-motley-fool-new-recs-committee-batches with --preset ongoing-no-submit requires "
                "--motley-fool-new-recs-max-batches to bound LLM committee work."
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args, optionally expanding a JSON scheduler profile first."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config-file", default="")
    known, remaining = pre_parser.parse_known_args(argv)
    if not known.config_file:
        return build_parser().parse_args(argv)
    profile_args = _load_config_file_args(known.config_file)
    expanded = ["--config-file", known.config_file, *profile_args, *remaining]
    return build_parser().parse_args(expanded)


def _load_config_file_args(config_file: str | Path) -> list[str]:
    path = Path(config_file).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--config-file must contain a JSON object.")
    raw_args = payload.get("args", payload)
    if not isinstance(raw_args, dict):
        raise ValueError("--config-file 'args' must be a JSON object.")
    ignored = {"schema_version", "description", "notes"}
    allowed = _config_arg_specs()
    expanded: list[str] = []
    for key, value in raw_args.items():
        if key in ignored:
            continue
        if key == "config_file":
            continue
        spec = allowed.get(key)
        if spec is None:
            raise ValueError(f"Unknown scheduler config arg: {key}")
        option, action = spec
        if action in {"store_true", "store_false"}:
            enabled = bool(value)
            if action == "store_false":
                enabled = not enabled
            if enabled:
                expanded.append(option)
            continue
        if value is None:
            continue
        if action == "append":
            values = value if isinstance(value, list) else [value]
            for item in values:
                expanded.extend([option, str(item)])
            continue
        expanded.extend([option, str(value)])
    return expanded


def _config_arg_specs() -> dict[str, tuple[str, str]]:
    specs: dict[str, tuple[str, str]] = {}
    for action in build_parser()._actions:
        if not action.option_strings or action.dest == "help":
            continue
        option = max(action.option_strings, key=len)
        if isinstance(action, argparse._StoreTrueAction):
            specs[action.dest] = (option, "store_true")
        elif isinstance(action, argparse._StoreFalseAction):
            specs[action.dest] = (option, "store_false")
        elif isinstance(action, argparse._AppendAction):
            specs[action.dest] = (option, "append")
        else:
            specs[action.dest] = (option, "store")
    return specs


def _build_ongoing_no_submit_templates(args: argparse.Namespace) -> dict[str, str]:
    """Build safe no-submit scheduler command templates from stable core paths."""
    journal_db = _require_preset_path(args, "journal_db", "--journal-db")
    ledger_db = _require_preset_path(args, "ledger_db", "--ledger-db")
    action_plan = _require_preset_path(args, "action_plan", "--action-plan")
    profile_config = _normalize_path_arg(args.profile_config or str(DEFAULT_PROFILE_PATH))
    _validate_ongoing_no_submit_research_bounds(args)
    if args.market_regime_file and args.auto_market_regime_snapshot:
        raise ValueError("Use either --market-regime-file or --auto-market-regime-snapshot, not both.")

    pre_refresh = " ".join(
        [
            "python",
            _script_path("longterm_alpaca_paper_snapshot.py"),
            "--profile-config",
            _quote_path_arg(profile_config),
            "--portfolio-state-output",
            "{portfolio_state}",
        ]
    )
    market_regime_path = "{market_regime}" if args.auto_market_regime_snapshot else args.market_regime_file
    market_regime_command_parts: list[str] = []
    if args.auto_market_regime_snapshot:
        market_regime_command_parts = [
            "python",
            _script_path("longterm_market_regime_snapshot.py"),
            "--provider",
            args.market_regime_provider,
            "--output",
            "{market_regime}",
        ]
        if args.market_regime_provider == "fredapi":
            _append_optional_value(market_regime_command_parts, "--fred-api-key-env", args.fred_api_key_env)
            _append_optional_value(
                market_regime_command_parts,
                "--fred-provider-attempts",
                args.fred_provider_attempts,
            )
            _append_optional_value(
                market_regime_command_parts,
                "--fred-provider-retry-delay-seconds",
                _format_number(float(args.fred_provider_retry_delay_seconds)),
            )

    pipeline_parts = [
        "python",
        _script_path("longterm_research_to_paper_pipeline.py"),
        "--output-dir",
        "{pipeline_output_dir}",
        "--action-plan",
        _quote_path_arg(action_plan),
        "--portfolio-state",
        "{portfolio_state}",
        "--journal-db",
        _quote_path_arg(journal_db),
        "--ledger-db",
        _quote_path_arg(ledger_db),
        "--profile-config",
        _quote_path_arg(profile_config),
        "--json",
    ]
    full_research_parts: list[str] = []
    research_parts = pipeline_parts
    if args.policy_gated_full_research:
        full_research_parts = [
            "python",
            _script_path("longterm_research_to_paper_pipeline.py"),
            "--output-dir",
            "{full_research_output_dir}",
            "--action-plan",
            _quote_path_arg(action_plan),
            "--portfolio-state",
            "{portfolio_state}",
            "--journal-db",
            _quote_path_arg(journal_db),
            "--ledger-db",
            _quote_path_arg(ledger_db),
            "--profile-config",
            _quote_path_arg(profile_config),
            "--skip-paper-preflight",
            "--json",
        ]
        research_parts = full_research_parts

    _append_optional_path(research_parts, "--research-source-file", args.research_source_file)
    _append_optional_path(research_parts, "--research-source-url", args.research_source_url)
    _append_optional_value(research_parts, "--research-source", args.research_source)
    for spec in args.research_supplemental_source_file or []:
        _append_optional_value(research_parts, "--supplemental-source-file", spec)
    for spec in args.research_supplemental_source_url or []:
        _append_optional_value(research_parts, "--supplemental-source-url", spec)
    for spec in args.research_supplemental_ideas_file or []:
        _append_optional_value(research_parts, "--supplemental-ideas-file", spec)
    _append_optional_path(research_parts, "--research-campaign-dir", args.research_campaign_dir)
    _append_optional_flag(research_parts, "--research-resume", args.research_resume)
    _append_optional_value(research_parts, "--research-run-until", args.research_run_until)
    _append_optional_value(research_parts, "--research-watchlist-limit", args.research_watchlist_limit)
    _append_optional_value(research_parts, "--research-universe-batch-size", args.research_universe_batch_size)
    _append_optional_value(research_parts, "--research-top-percent", args.research_top_percent)
    _append_optional_value(research_parts, "--research-min-pass-count", args.research_min_pass_count)
    _append_optional_value(research_parts, "--research-max-pass-count", args.research_max_pass_count)
    _append_optional_value(
        research_parts,
        "--research-min-coverage-percent-for-enrichment",
        args.research_min_coverage_percent_for_enrichment,
    )
    _append_optional_value(
        research_parts,
        "--research-max-fundamental-fetches",
        args.research_max_fundamental_fetches,
    )
    _append_optional_value(
        research_parts,
        "--research-fundamental-fetch-chunk-size",
        args.research_fundamental_fetch_chunk_size,
    )
    _append_optional_value(research_parts, "--research-evidence-batch-size", args.research_evidence_batch_size)
    _append_optional_value(research_parts, "--research-max-evidence-batches", args.research_max_evidence_batches)
    _append_optional_value(research_parts, "--research-rate-limit-batch-size", args.research_rate_limit_batch_size)
    _append_optional_value(
        research_parts,
        "--research-rate-limit-pause-seconds",
        args.research_rate_limit_pause_seconds,
    )
    _append_optional_value(
        research_parts,
        "--research-campaign-batch-pause-seconds",
        args.research_campaign_batch_pause_seconds,
    )
    _append_optional_flag(research_parts, "--polygon-news", args.polygon_news)
    _append_optional_path(research_parts, "--research-news-cache-path", args.research_news_cache_path)
    _append_optional_flag(research_parts, "--xai-grok", args.xai_grok)
    _append_optional_flag(research_parts, "--skip-grok", args.skip_grok)
    _append_optional_flag(research_parts, "--perplexity-research", args.perplexity_research)
    if args.perplexity_research:
        _append_optional_value(research_parts, "--perplexity-api-key-env", args.perplexity_api_key_env)
        _append_optional_value(research_parts, "--perplexity-model", args.perplexity_model)
        _append_optional_value(research_parts, "--perplexity-api-url", args.perplexity_api_url)
        _append_optional_value(research_parts, "--perplexity-timeout-seconds", args.perplexity_timeout_seconds)
        _append_optional_value(research_parts, "--perplexity-max-tokens", args.perplexity_max_tokens)
        _append_optional_value(
            research_parts,
            "--perplexity-search-context-size",
            args.perplexity_search_context_size,
        )
        _append_optional_value(
            research_parts,
            "--perplexity-credits-purchased-to-date",
            args.perplexity_credits_purchased_to_date,
        )
    _append_optional_flag(research_parts, "--kronos-advisory", args.kronos_advisory)
    if args.kronos_advisory:
        _append_optional_path(research_parts, "--kronos-root", args.kronos_root)
        _append_optional_path(research_parts, "--kronos-python", args.kronos_python)
        _append_optional_value(research_parts, "--kronos-limit", args.kronos_limit)
    _append_optional_value(research_parts, "--selection-top-percent", args.selection_top_percent)
    _append_optional_value(research_parts, "--selection-min-count", args.selection_min_count)
    _append_optional_value(research_parts, "--selection-max-count", args.selection_max_count)
    _append_optional_path(research_parts, "--recent-research-symbols-file", args.recent_research_symbols_file)
    _append_optional_value(research_parts, "--research-as-of-date", args.research_as_of_date)
    _append_optional_value(research_parts, "--research-batch-size", args.research_batch_size)
    _append_optional_flag(
        research_parts,
        "--run-generated-committee-batches",
        args.run_generated_committee_batches,
    )
    _append_optional_flag(
        research_parts,
        "--no-generated-committee-resume",
        args.no_generated_committee_resume,
    )
    _append_optional_value(
        research_parts,
        "--generated-committee-max-batches",
        args.generated_committee_max_batches,
    )
    _append_optional_path(research_parts, "--committee-batch-dir", args.committee_batch_dir)
    _append_optional_path(pipeline_parts, "--market-regime-file", market_regime_path)
    _append_optional_path(pipeline_parts, "--price-map", args.price_map)
    _append_optional_flag(pipeline_parts, "--skip-price-map", args.skip_price_map)
    _append_optional_flag(
        pipeline_parts,
        "--allow-existing-paper-positions",
        args.allow_existing_paper_positions,
    )
    _append_optional_flag(pipeline_parts, "--final-planning-refresh", args.final_planning_refresh)
    if args.final_planning_refresh:
        timeout_seconds = args.final_planning_timeout_seconds or DEFAULT_FINAL_PLANNING_TIMEOUT_SECONDS
        _append_optional_value(pipeline_parts, "--final-planning-timeout-seconds", _format_number(timeout_seconds))
    _append_optional_flag(
        pipeline_parts,
        "--planning-capital-from-portfolio-state",
        args.planning_capital_from_portfolio_state,
    )
    _append_optional_flag(
        pipeline_parts,
        "--expected-cash-from-portfolio-state",
        args.expected_cash_from_portfolio_state,
    )
    if args.portfolio_news_monitor:
        _append_optional_path(pipeline_parts, "--portfolio-news-monitor", "{portfolio_news_monitor}")
    _append_optional_flag(pipeline_parts, "--portfolio-news-followup-batches", args.portfolio_news_followup_batches)
    if args.portfolio_news_followup_batches:
        _append_optional_value(
            pipeline_parts,
            "--portfolio-news-followup-batch-size",
            args.portfolio_news_followup_batch_size,
        )
    _append_optional_flag(
        pipeline_parts,
        "--run-portfolio-news-followup-committee-batches",
        args.run_portfolio_news_followup_committee_batches,
    )
    if args.run_portfolio_news_followup_committee_batches:
        _append_optional_value(
            pipeline_parts,
            "--portfolio-news-followup-max-batches",
            args.portfolio_news_followup_max_batches,
        )
        _append_optional_value(
            pipeline_parts,
            "--portfolio-news-followup-agent-preset",
            args.portfolio_news_followup_agent_preset,
        )
        _append_optional_flag(
            pipeline_parts,
            "--no-portfolio-news-followup-committee-resume",
            args.no_portfolio_news_followup_committee_resume,
        )
    if args.motley_fool_new_recs_monitor:
        _append_optional_path(pipeline_parts, "--motley-fool-new-recs-ideas", "{motley_fool_new_recs_ideas}")
    _append_optional_flag(pipeline_parts, "--motley-fool-new-recs-batches", args.motley_fool_new_recs_batches)
    if args.motley_fool_new_recs_batches:
        _append_optional_value(
            pipeline_parts,
            "--motley-fool-new-recs-batch-size",
            args.motley_fool_new_recs_batch_size,
        )
    _append_optional_flag(
        pipeline_parts,
        "--run-motley-fool-new-recs-committee-batches",
        args.run_motley_fool_new_recs_committee_batches,
    )
    if args.run_motley_fool_new_recs_committee_batches:
        _append_optional_value(
            pipeline_parts,
            "--motley-fool-new-recs-max-batches",
            args.motley_fool_new_recs_max_batches,
        )
        _append_optional_value(
            pipeline_parts,
            "--motley-fool-new-recs-agent-preset",
            args.motley_fool_new_recs_agent_preset,
        )
        _append_optional_flag(
            pipeline_parts,
            "--no-motley-fool-new-recs-committee-resume",
            args.no_motley_fool_new_recs_committee_resume,
        )

    portfolio_news_monitor_parts: list[str] = []
    if args.portfolio_news_monitor:
        portfolio_news_monitor_parts = [
            "python",
            _script_path("longterm_portfolio_news_monitor.py"),
            "--portfolio-state",
            "{portfolio_state}",
            "--snapshot-file",
            _quote_path_arg(args.portfolio_news_snapshot_file),
            "--journal-db",
            _quote_path_arg(journal_db),
            "--output",
            "{portfolio_news_monitor}",
            "--relevance-threshold",
            _format_number(float(args.portfolio_news_relevance_threshold)),
            "--max-articles-per-symbol",
            str(int(args.portfolio_news_max_articles_per_symbol)),
            "--json",
        ]
        _append_optional_path(portfolio_news_monitor_parts, "--watchlist-ideas", args.portfolio_news_watchlist_ideas)
        _append_optional_value(
            portfolio_news_monitor_parts,
            "--published-after",
            args.portfolio_news_published_after,
        )

    motley_fool_new_recs_capture_parts: list[str] = []
    motley_fool_new_recs_delta_parts: list[str] = []
    if args.motley_fool_new_recs_monitor:
        motley_fool_new_recs_capture_parts = [
            "python",
            _script_path("longterm_motley_fool_capture.py"),
            "--source",
            "new_recommendations",
            "--output",
            "{motley_fool_new_recs_capture}",
        ]
        _append_optional_path(
            motley_fool_new_recs_capture_parts,
            "--profile-dir",
            args.motley_fool_profile_dir,
        )
        motley_fool_new_recs_delta_parts = [
            "python",
            _script_path("longterm_motley_fool_new_recs_state.py"),
            "--ideas-file",
            "{motley_fool_new_recs_capture}",
            "--state-file",
            "{motley_fool_new_recs_state}",
            "--output",
            "{motley_fool_new_recs_delta}",
            "--new-ideas-output",
            "{motley_fool_new_recs_ideas}",
            "--bootstrap-if-empty",
            "--json",
        ]

    position_review_queue_parts: list[str] = []
    if args.position_review_queue:
        position_review_queue_parts = [
            "python",
            _script_path("longterm_position_review_queue.py"),
            "--portfolio-state",
            "{portfolio_state}",
            "--action-plan",
            _quote_path_arg(action_plan),
            "--portfolio-news-monitor",
            "{portfolio_news_monitor}",
            "--journal-db",
            _quote_path_arg(journal_db),
            "--output",
            "{position_review_queue}",
            "--json",
        ]

    scheduler_policy_parts = [
        "python",
        _script_path("longterm_pipeline_scheduler_policy.py"),
        "--rules-path",
        "{rules_path}",
        "--research-rules-path",
        "{research_rules_path}",
        "--journal-db",
        _quote_path_arg(journal_db),
        "--policy-state",
        "{scheduler_policy_state}",
        "--state-output",
        "{scheduler_policy_state}",
        "--pipeline-scheduler-summary",
        "{scheduler_summary}",
        "--pipeline-summary",
        "{pipeline_summary}",
        "--report-output",
        "{scheduler_policy}",
        "--json",
    ]
    _append_optional_path(scheduler_policy_parts, "--market-regime", market_regime_path)

    account_refresh_parts = [
        "python",
        _script_path("longterm_paper_account_refresh.py"),
        "--profile-config",
        _quote_path_arg(profile_config),
        "--journal-db",
        _quote_path_arg(journal_db),
        "--action-plan",
        _quote_path_arg(action_plan),
        "--paper-ledger-db",
        _quote_path_arg(ledger_db),
        "--pipeline-summary",
        "{pipeline_summary}",
        "--pipeline-scheduler-summary",
        "{scheduler_summary}",
        "--output-dir",
        "{account_refresh_output_dir}",
        "--dashboard-manifest-output",
        "{dashboard_manifest}",
        "--dashboard-site-output-dir",
        "{dashboard_site_output_dir}",
        "--json",
    ]
    _append_optional_path(account_refresh_parts, "--market-regime", market_regime_path)
    _append_optional_path(account_refresh_parts, "--scheduler-config-validation", args.scheduler_config_validation)
    _append_optional_path(account_refresh_parts, "--scheduler-task-plan", args.scheduler_task_plan)
    _append_optional_path(account_refresh_parts, "--scheduler-handoff", args.scheduler_handoff)
    if args.position_review_queue:
        account_refresh_parts.extend(["--position-review-queue", "{position_review_queue}"])
    _append_optional_path(account_refresh_parts, "--paper-submit-mode-plan", args.paper_submit_mode_plan)

    post_run_verification_parts = [
        "python",
        _script_path("longterm_pipeline_scheduler_verify.py"),
        "--pipeline-scheduler-summary",
        "{scheduler_summary}",
        "--policy-state",
        "{scheduler_policy_state}",
        "--require-resource-bounded",
        "--require-policy-timestamp",
        "last_no_submit_preflight_at",
        "--require-policy-timestamp",
        "last_account_refresh_at",
        "--report-output",
        "{post_run_verification}",
        "--json",
    ]
    if args.auto_market_regime_snapshot and args.market_regime_provider == "fredapi":
        post_run_verification_parts.append("--require-fred-provider")
    if args.final_planning_refresh:
        post_run_verification_parts.extend(
            [
                "--require-final-planning-bound",
                "--require-policy-timestamp",
                "last_final_planning_at",
            ]
        )
    if args.run_generated_committee_batches and not args.policy_gated_full_research:
        post_run_verification_parts.extend(
            [
                "--require-policy-timestamp",
                "last_full_research_at",
            ]
        )
    if args.portfolio_news_monitor:
        post_run_verification_parts.extend(
            [
                "--require-policy-timestamp",
                "last_news_monitor_at",
            ]
        )
    if args.motley_fool_new_recs_monitor:
        post_run_verification_parts.extend(
            [
                "--require-policy-timestamp",
                "last_motley_fool_new_recs_at",
            ]
        )
    if args.position_review_queue:
        post_run_verification_parts.extend(
            [
                "--require-policy-timestamp",
                "last_position_review_at",
            ]
        )
    if args.portfolio_news_followup_batches:
        post_run_verification_parts.extend(
            [
                "--require-policy-timestamp",
                "last_followup_batch_split_at",
            ]
        )
    if args.run_portfolio_news_followup_committee_batches:
        post_run_verification_parts.extend(
            [
                "--require-policy-timestamp",
                "last_followup_committee_at",
            ]
        )

    scheduler_review_bundle_parts: list[str] = []
    if args.scheduler_review_bundle:
        scheduler_review_bundle_parts = [
            "python",
            _script_path("longterm_scheduler_review_bundle.py"),
            "--dashboard-manifest",
            "{dashboard_manifest}",
            "--scheduler-handoff",
            _quote_path_arg(args.scheduler_handoff),
            "--pipeline-scheduler-summary",
            "{scheduler_summary}",
            "--position-review-queue",
            "{position_review_queue}",
            "--post-run-verification",
            "{post_run_verification}",
            "--output-dir",
            "{scheduler_review_bundle_output_dir}",
            "--allow-blocked-review-exit-zero",
            "--json",
        ]

    return {
        "pre_pipeline_refresh": pre_refresh,
        "market_regime": " ".join(market_regime_command_parts),
        "portfolio_news_monitor": " ".join(portfolio_news_monitor_parts),
        "motley_fool_new_recs_capture": " ".join(motley_fool_new_recs_capture_parts),
        "motley_fool_new_recs_delta": " ".join(motley_fool_new_recs_delta_parts),
        "position_review_queue": " ".join(position_review_queue_parts),
        "pipeline": " ".join(pipeline_parts),
        "scheduler_policy": " ".join(scheduler_policy_parts),
        "full_research": " ".join(full_research_parts),
        "account_refresh": " ".join(account_refresh_parts),
        "post_run_verification": " ".join(post_run_verification_parts),
        "scheduler_review_bundle": " ".join(scheduler_review_bundle_parts),
    }


def _resolve_command_templates(args: argparse.Namespace) -> dict[str, str]:
    explicit_templates = [
        args.pre_pipeline_refresh_command_template,
        args.market_regime_command_template,
        args.pipeline_command_template,
        args.committee_preset_policy_command_template,
        args.scheduler_policy_command_template,
        args.full_research_command_template,
        args.account_refresh_command_template,
        args.post_run_verification_command_template,
        args.scheduler_review_bundle_command_template,
        args.portfolio_news_monitor_command_template,
        args.motley_fool_new_recs_capture_command_template,
        args.motley_fool_new_recs_delta_command_template,
    ]
    if args.preset == "ongoing-no-submit":
        if any(explicit_templates):
            raise ValueError("Choose --preset ongoing-no-submit or explicit command templates, not both.")
        return _build_ongoing_no_submit_templates(args)
    if not args.pipeline_command_template:
        raise ValueError("--pipeline-command-template is required unless --preset ongoing-no-submit is used.")
    return {
        "pre_pipeline_refresh": args.pre_pipeline_refresh_command_template,
        "market_regime": args.market_regime_command_template,
        "portfolio_news_monitor": args.portfolio_news_monitor_command_template,
        "motley_fool_new_recs_capture": args.motley_fool_new_recs_capture_command_template,
        "motley_fool_new_recs_delta": args.motley_fool_new_recs_delta_command_template,
        "pipeline": args.pipeline_command_template,
        "committee_preset_policy": args.committee_preset_policy_command_template,
        "scheduler_policy": args.scheduler_policy_command_template,
        "full_research": args.full_research_command_template,
        "account_refresh": args.account_refresh_command_template,
        "post_run_verification": args.post_run_verification_command_template,
        "scheduler_review_bundle": args.scheduler_review_bundle_command_template,
    }


def validate_resolved_scheduler_config(args: argparse.Namespace) -> dict[str, object]:
    """Validate resolved scheduler templates without creating run artifacts."""
    templates = _resolve_command_templates(args)
    rules_path = Path(args.rules_path)
    research_rules_path = Path(args.research_rules_path) if args.research_rules_path else None
    validate_scheduler_command_template(
        templates["pipeline"],
        command_kind="pipeline",
        rules_path=rules_path,
    )
    command_kinds = [
        ("pre_pipeline_refresh", "pre_pipeline_refresh"),
        ("market_regime", "market_regime"),
        ("portfolio_news_monitor", "portfolio_news_monitor"),
        ("motley_fool_new_recs_capture", "motley_fool_new_recs_capture"),
        ("motley_fool_new_recs_delta", "motley_fool_new_recs_delta"),
        ("position_review_queue", "position_review_queue"),
        ("committee_preset_policy", "committee_preset_policy"),
        ("scheduler_policy", "scheduler_policy"),
        ("full_research", "full_research"),
        ("account_refresh", "account_refresh"),
        ("post_run_verification", "post_run_verification"),
        ("scheduler_review_bundle", "scheduler_review_bundle"),
    ]
    for template_key, command_kind in command_kinds:
        command = templates.get(template_key, "")
        if command:
            validate_scheduler_command_template(command, command_kind=command_kind, rules_path=rules_path)
    commands = {key: value for key, value in templates.items() if value}
    resource_controls = derive_scheduler_resource_controls(
        f"{templates['pipeline']} {templates.get('full_research', '')}".strip()
    )
    operating_mode_summary = _build_operating_mode_summary(
        args=args,
        commands=commands,
        resource_controls=resource_controls,
    )
    return {
        "schema_version": 1,
        "mode": "pipeline_scheduler_config_validation",
        "status": "ready",
        "order_submission_enabled": False,
        "recurring_no_submit_ready": operating_mode_summary["ready_for_unattended_no_submit"],
        "operating_mode_summary": operating_mode_summary,
        "config_file": str(Path(args.config_file).expanduser().resolve()) if args.config_file else "",
        "preset": args.preset,
        "output_dir": str(Path(args.output_dir).expanduser().resolve()),
        "rules_path": str(rules_path.expanduser().resolve()),
        "research_rules_path": str(research_rules_path.expanduser().resolve()) if research_rules_path else "",
        "commands": commands,
        "resource_controls": resource_controls,
        "next_safe_action": "run_scheduler_profile_when_operator_window_is_approved",
    }


def _build_operating_mode_summary(
    *,
    args: argparse.Namespace,
    commands: dict[str, str],
    resource_controls: dict[str, object],
) -> dict[str, object]:
    """Summarize whether a resolved profile is ready for recurring no-submit operation."""
    required_no_submit_stages = (
        "pre_pipeline_refresh",
        "pipeline",
        "scheduler_policy",
        "account_refresh",
        "post_run_verification",
    )
    stage_flags = {
        "pre_pipeline_refresh": bool(commands.get("pre_pipeline_refresh")),
        "market_regime": bool(commands.get("market_regime")),
        "portfolio_news_monitor": bool(commands.get("portfolio_news_monitor")),
        "motley_fool_new_recs_monitor": bool(commands.get("motley_fool_new_recs_capture"))
        and bool(commands.get("motley_fool_new_recs_delta")),
        "position_review_queue": bool(commands.get("position_review_queue")),
        "research_pipeline": bool(commands.get("pipeline")),
        "scheduler_policy": bool(commands.get("scheduler_policy")),
        "account_refresh": bool(commands.get("account_refresh")),
        "post_run_verification": bool(commands.get("post_run_verification")),
        "scheduler_review_bundle": bool(commands.get("scheduler_review_bundle")),
        "portfolio_news_followup_batches": bool(args.portfolio_news_followup_batches),
        "portfolio_news_followup_committee_batches": bool(args.run_portfolio_news_followup_committee_batches),
        "motley_fool_new_recs_batches": bool(args.motley_fool_new_recs_batches),
        "motley_fool_new_recs_committee_batches": bool(args.run_motley_fool_new_recs_committee_batches),
        "kronos_advisory": bool(args.kronos_advisory),
        "generated_committee_batches": bool(args.run_generated_committee_batches),
        "final_planning_refresh": bool(args.final_planning_refresh),
    }
    if commands.get("full_research") or args.policy_gated_full_research:
        stage_flags["policy_gated_full_research"] = bool(commands.get("full_research"))
    missing_required_stages = [
        stage_name for stage_name in required_no_submit_stages if not commands.get(stage_name)
    ]
    readiness_blockers: list[str] = []
    if args.preset != "ongoing-no-submit":
        readiness_blockers.append("preset_is_not_ongoing_no_submit")
    if missing_required_stages:
        readiness_blockers.append("missing_" + "_and_".join(missing_required_stages))
    if not resource_controls.get("bounded", False):
        readiness_blockers.append(str(resource_controls.get("bounded_reason") or "resource_controls_unbounded"))
    ready = not readiness_blockers
    return {
        "schema_version": 1,
        "name": "recurring_no_submit" if args.preset == "ongoing-no-submit" else "custom_no_submit",
        "ready_for_unattended_no_submit": ready,
        "readiness_blockers": readiness_blockers,
        "broker_submit_boundary": "blocked_by_no_submit_scheduler",
        "scheduler_controls": {
            "max_runs": int(args.max_runs),
            "interval_seconds": float(args.interval_seconds),
            "run_once": bool(args.run_once),
            "continue_on_error": bool(args.continue_on_error),
            "print_plan_only": bool(args.print_plan_only),
        },
        "stage_flags": stage_flags,
        "operator_next_step": (
            "schedule_or_run_no_submit_profile_after_operator_window_approval"
            if ready
            else "resolve_readiness_blockers_before_scheduling"
        ),
    }


def run_cli(args: argparse.Namespace) -> int:
    if args.validate_config_only:
        payload = validate_resolved_scheduler_config(args)
        if args.summary_output:
            summary_path = Path(args.summary_output).expanduser().resolve()
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("Pipeline scheduler config ready.")
            print(f"Output: {payload['output_dir']}")
            print("No scheduler run folders were created and no commands were executed.")
        return 0
    max_runs = 1 if args.run_once else args.max_runs
    templates = _resolve_command_templates(args)
    summary = run_pipeline_scheduler(
        PipelineSchedulerInputs(
            output_dir=args.output_dir,
            pre_pipeline_refresh_command_template=templates.get("pre_pipeline_refresh", ""),
            market_regime_command_template=templates.get("market_regime", ""),
            portfolio_news_monitor_command_template=templates.get("portfolio_news_monitor", ""),
            motley_fool_new_recs_capture_command_template=templates.get("motley_fool_new_recs_capture", ""),
            motley_fool_new_recs_delta_command_template=templates.get("motley_fool_new_recs_delta", ""),
            position_review_queue_command_template=templates.get("position_review_queue", ""),
            pipeline_command_template=templates["pipeline"],
            committee_preset_policy_command_template=templates.get("committee_preset_policy", ""),
            scheduler_policy_command_template=templates.get("scheduler_policy", ""),
            full_research_command_template=templates.get("full_research", ""),
            account_refresh_command_template=templates.get("account_refresh", ""),
            post_run_verification_command_template=templates.get("post_run_verification", ""),
            scheduler_review_bundle_command_template=templates.get("scheduler_review_bundle", ""),
            rules_path=args.rules_path,
            research_rules_path=args.research_rules_path or None,
            summary_output=args.summary_output or None,
        ),
        PipelineSchedulerConfig(
            max_runs=max_runs,
            interval_seconds=args.interval_seconds,
            stop_on_error=not args.continue_on_error,
            print_plan_only=args.print_plan_only,
        ),
    )
    payload = asdict(summary)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Pipeline scheduler {summary.status}: {summary.run_count} run(s) recorded.")
        print(f"Output: {summary.output_dir}")
        print("No paper or live orders were submitted.")
    return 0 if summary.status in {"completed", "planned"} else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(parse_args(argv))


__all__ = [
    "build_parser",
    "main",
    "parse_args",
    "run_cli",
    "validate_resolved_scheduler_config",
]
