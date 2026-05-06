"""CLI for Grok catalyst enrichment of long-term research ideas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from longterm.grok_research_enrichment import (
    DEFAULT_GROK_MODEL,
    DEFAULT_XAI_BASE_URL,
    FakeGrokResearchClient,
    XaiGrokResearchClient,
    enrich_ideas_with_grok_research,
)
from longterm.perplexity_research_enrichment import (
    DEFAULT_PERPLEXITY_API_URL,
    DEFAULT_PERPLEXITY_MAX_TOKENS,
    DEFAULT_PERPLEXITY_MODEL,
    PerplexityResearchClient,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich long-term ideas with source-backed Grok catalyst research.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--idea-file", default="")
    source.add_argument("--idea-batch", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--facts-file", default="", help="Optional symbol-keyed free facts JSON, e.g. Finnhub snapshots.")
    source_mode = parser.add_mutually_exclusive_group()
    source_mode.add_argument("--snapshot-file", default="", help="Optional symbol-keyed offline Grok/Perplexity response JSON.")
    source_mode.add_argument("--perplexity-research", action="store_true")
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--allow-unsourced", action="store_true")
    parser.add_argument("--model", default=DEFAULT_GROK_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_XAI_BASE_URL)
    parser.add_argument("--api-key-env", default="XAI_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
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
    return parser


def run_cli(args: argparse.Namespace) -> int:
    ideas = _load_ideas(args.idea_file or args.idea_batch, single=bool(args.idea_file))
    facts = _load_symbol_keyed_json(args.facts_file) if args.facts_file else {}
    if args.snapshot_file:
        client = FakeGrokResearchClient(_load_symbol_keyed_json(args.snapshot_file))
        mode = "snapshot_file"
    elif args.perplexity_research:
        client = PerplexityResearchClient(
            api_key_env=args.perplexity_api_key_env,
            model=args.perplexity_model,
            api_url=args.perplexity_api_url,
            timeout_seconds=args.perplexity_timeout_seconds,
            max_tokens=args.perplexity_max_tokens,
            search_context_size=args.perplexity_search_context_size,
            credits_purchased_to_date_usd=args.perplexity_credits_purchased_to_date,
        )
        mode = "perplexity_api"
    else:
        client = XaiGrokResearchClient(
            api_key_env=args.api_key_env,
            model=args.model,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
        )
        mode = "xai_api"

    enriched = enrich_ideas_with_grok_research(
        ideas,
        client=client,
        free_facts_by_symbol=facts,
        as_of_date=args.as_of_date or None,
        allow_unsourced=bool(args.allow_unsourced),
        limit=args.limit,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "mode": mode,
        "input_count": len(ideas),
        "enriched_count": len(enriched),
        "facts_count": len(facts),
        "output": str(output_path),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


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


__all__ = ["build_parser", "main", "run_cli"]
