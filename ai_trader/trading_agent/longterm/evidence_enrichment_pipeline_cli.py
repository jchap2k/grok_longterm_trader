"""CLI for the long-term evidence enrichment pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.evidence_enrichment_pipeline import (
    fetch_yfinance_fundamentals_for_pipeline,
    run_evidence_enrichment_pipeline,
)
from longterm.grok_research_enrichment import (
    DEFAULT_GROK_MODEL,
    DEFAULT_XAI_BASE_URL,
    FakeGrokResearchClient,
    XaiGrokResearchClient,
)
from longterm.news_relevance_enrichment import (
    CachedNewsProvider,
    FakeNewsProvider,
    PolygonNewsProvider,
)
from longterm.perplexity_research_enrichment import (
    DEFAULT_PERPLEXITY_API_URL,
    DEFAULT_PERPLEXITY_MAX_TOKENS,
    DEFAULT_PERPLEXITY_MODEL,
    PerplexityResearchClient,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fundamentals, news, earnings, scorecard, Grok, and evidence-brief enrichment."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--idea-file", default="")
    source.add_argument("--idea-batch", default="")

    fundamentals = parser.add_mutually_exclusive_group()
    fundamentals.add_argument("--fundamentals-snapshot-file", default="")
    fundamentals.add_argument("--fundamentals-provider", choices=["yfinance"], default="")

    news = parser.add_mutually_exclusive_group()
    news.add_argument("--news-snapshot-file", default="")
    news.add_argument("--polygon-news", action="store_true")

    grok = parser.add_mutually_exclusive_group()
    grok.add_argument("--grok-snapshot-file", default="")
    grok.add_argument("--xai-grok", action="store_true")
    grok.add_argument("--perplexity-research", action="store_true")
    grok.add_argument("--skip-grok", action="store_true")

    parser.add_argument("--facts-file", default="", help="Optional symbol-keyed free facts JSON for Grok.")
    parser.add_argument("--kronos-advisory-file", default="", help="Optional Kronos advisory batch or symbol-keyed JSON.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-news-items", type=int, default=5)
    parser.add_argument("--rate-limit-batch-size", type=int, default=5)
    parser.add_argument("--rate-limit-pause-seconds", type=float, default=66.0)
    parser.add_argument("--news-cache-path", default="")
    parser.add_argument("--polygon-api-key-env", default="POLYGON_API_KEY")
    parser.add_argument("--xai-api-key-env", default="XAI_API_KEY")
    parser.add_argument("--grok-model", default=DEFAULT_GROK_MODEL)
    parser.add_argument("--grok-base-url", default=DEFAULT_XAI_BASE_URL)
    parser.add_argument("--grok-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--perplexity-api-key-env", default="PERPLEXITY_API_KEY")
    parser.add_argument("--perplexity-model", default=DEFAULT_PERPLEXITY_MODEL)
    parser.add_argument("--perplexity-api-url", default=DEFAULT_PERPLEXITY_API_URL)
    parser.add_argument("--perplexity-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--perplexity-max-tokens", type=int, default=DEFAULT_PERPLEXITY_MAX_TOKENS)
    parser.add_argument("--perplexity-search-context-size", choices=["low", "medium", "high"], default="low")
    parser.add_argument(
        "--perplexity-credits-purchased-to-date",
        type=float,
        default=None,
        help="Optional API-console credit total, e.g. 12 if you have purchased $12 toward Tier 1.",
    )
    parser.add_argument("--allow-unsourced-grok", action="store_true")
    parser.add_argument("--tier-only", "--dry-run-tiers", dest="tier_only", action="store_true",
                        help="Run only up to tier decision (no heavy LLM enrichment). Useful for threshold tuning.")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_ideas(args.idea_file or args.idea_batch, single=bool(args.idea_file))
    result = run_evidence_enrichment_pipeline(
        ideas,
        fundamentals_by_symbol=_fundamentals_snapshot(args),
        fetch_fundamentals=(
            fetch_yfinance_fundamentals_for_pipeline
            if args.fundamentals_provider == "yfinance"
            else None
        ),
        news_provider=_news_provider(args),
        grok_client=_grok_client(args),
        free_facts_by_symbol=_load_symbol_keyed_json(args.facts_file) if args.facts_file else None,
        kronos_advisory_by_symbol=(
            _load_kronos_advisory_json(args.kronos_advisory_file)
            if args.kronos_advisory_file
            else None
        ),
        as_of_date=args.as_of_date or None,
        limit=args.limit,
        max_news_items=args.max_news_items,
        tier_only=args.tier_only,
        published_after=args.published_after or None,
        news_batch_size=args.rate_limit_batch_size,
        news_pause_seconds=(0.0 if args.news_snapshot_file else args.rate_limit_pause_seconds),
        allow_unsourced_grok=bool(args.allow_unsourced_grok),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result["ideas"], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary = dict(result["summary"])
    summary["output"] = str(output_path)
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        summary["summary_output"] = str(summary_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


def _fundamentals_snapshot(args: argparse.Namespace) -> dict[str, dict[str, Any]] | None:
    if not args.fundamentals_snapshot_file:
        return None
    return _load_symbol_keyed_json(args.fundamentals_snapshot_file)


def _news_provider(args: argparse.Namespace):
    if args.news_snapshot_file:
        return FakeNewsProvider(_load_symbol_articles(args.news_snapshot_file))
    if not args.polygon_news:
        return None
    polygon = PolygonNewsProvider(api_key_env=args.polygon_api_key_env)
    if args.news_cache_path:
        return CachedNewsProvider(
            fetch=polygon.fetch_news,
            cache_path=args.news_cache_path,
            today=args.as_of_date or None,
        )
    return polygon


def _grok_client(args: argparse.Namespace):
    if args.skip_grok:
        return None
    if args.grok_snapshot_file:
        return FakeGrokResearchClient(_load_symbol_keyed_json(args.grok_snapshot_file))
    if args.perplexity_research:
        return PerplexityResearchClient(
            api_key_env=args.perplexity_api_key_env,
            model=args.perplexity_model,
            api_url=args.perplexity_api_url,
            timeout_seconds=args.perplexity_timeout_seconds,
            max_tokens=args.perplexity_max_tokens,
            search_context_size=args.perplexity_search_context_size,
            credits_purchased_to_date_usd=args.perplexity_credits_purchased_to_date,
        )
    if args.xai_grok:
        return XaiGrokResearchClient(
            api_key_env=args.xai_api_key_env,
            model=args.grok_model,
            base_url=args.grok_base_url,
            timeout_seconds=args.grok_timeout_seconds,
        )
    return None


def _load_ideas(path: str | Path, *, single: bool) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if single:
        if not isinstance(payload, Mapping):
            raise ValueError("Idea file must contain a JSON object.")
        return [dict(payload)]
    if not isinstance(payload, list):
        raise ValueError("Idea batch file must contain a JSON list.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _load_symbol_keyed_json(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Symbol-keyed JSON file must contain an object.")
    return {str(symbol).upper(): dict(value) for symbol, value in payload.items() if isinstance(value, Mapping)}


def _load_kronos_advisory_json(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        return {
            str(item.get("symbol") or "").upper(): dict(item)
            for item in payload["items"]
            if isinstance(item, Mapping) and str(item.get("symbol") or "").strip()
        }
    if isinstance(payload, Mapping):
        return {str(symbol).upper(): dict(value) for symbol, value in payload.items() if isinstance(value, Mapping)}
    raise ValueError("Kronos advisory file must contain a batch object or symbol-keyed object.")


def _load_symbol_articles(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("News snapshot file must contain a symbol-keyed object.")
    return {
        str(symbol).upper(): [dict(item) for item in rows if isinstance(item, Mapping)]
        for symbol, rows in payload.items()
        if isinstance(rows, list)
    }


__all__ = ["build_parser", "main", "run_cli"]
