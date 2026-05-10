"""Automatic dry-run research campaign flow for broad long-term universes."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from longterm.discovery_sources import load_candidate_source_file, load_candidate_source_url
from longterm.evidence_enrichment_campaign_cli import run_cli as run_evidence_campaign_cli
from longterm.extended_universe import prepare_extended_universe
from longterm.extended_universe_scan import fetch_yfinance_fundamental_metrics, run_python_first_pass_scan
from longterm.extended_universe_scan_cli import (
    _allowed_fetch_symbols,
    _load_optional_symbol_cache,
    _remaining_fetch_work,
    _requested_symbols,
    _write_json,
    _write_jsonl,
)
from longterm.kronos_advisory_batch_cli import run_cli as run_kronos_batch_cli
from longterm.kronos_advisory_cli import DEFAULT_KRONOS_PYTHON, DEFAULT_KRONOS_ROOT
from longterm.perplexity_research_enrichment import (
    DEFAULT_PERPLEXITY_API_URL,
    DEFAULT_PERPLEXITY_MAX_TOKENS,
    DEFAULT_PERPLEXITY_MODEL,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Advance a dry-run long-term research campaign through universe, scan, and evidence stages."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-file", default="")
    source_group.add_argument("--source-url", default="")
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--supplemental-source-file",
        action="append",
        default=[],
        help="Optional extra discovery source as source_name=path, e.g. spy_holdings=spy.csv.",
    )
    parser.add_argument(
        "--supplemental-source-url",
        action="append",
        default=[],
        help="Optional extra discovery source as source_name=url.",
    )
    parser.add_argument(
        "--supplemental-ideas-file",
        action="append",
        default=[],
        help="Optional research ideas JSON list as source_name=path, e.g. motley_fool=fool.json.",
    )
    parser.add_argument("--campaign-dir", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--run-until",
        choices=["scan_ready", "evidence_ready", "research_queue_ready"],
        default="scan_ready",
    )
    parser.add_argument("--watchlist-limit", type=int, default=100)
    parser.add_argument("--universe-batch-size", type=int, default=50)
    parser.add_argument("--top-percent", type=float, default=10.0)
    parser.add_argument("--min-pass-count", type=int, default=10)
    parser.add_argument("--max-pass-count", type=int, default=300)
    parser.add_argument("--min-coverage-percent-for-enrichment", type=float, default=80.0)
    parser.add_argument("--max-fundamental-fetches", type=int, default=500)
    parser.add_argument("--fundamental-fetch-chunk-size", type=int, default=500)
    parser.add_argument("--evidence-batch-size", type=int, default=25)
    parser.add_argument("--max-evidence-batches", type=int, default=None)
    parser.add_argument("--rate-limit-batch-size", type=int, default=5)
    parser.add_argument("--rate-limit-pause-seconds", type=float, default=66.0)
    parser.add_argument("--campaign-batch-pause-seconds", type=float, default=0.0)
    parser.add_argument("--polygon-news", action="store_true")
    parser.add_argument("--news-cache-path", default="")
    parser.add_argument("--skip-grok", action="store_true")
    parser.add_argument("--xai-grok", action="store_true")
    parser.add_argument("--perplexity-research", action="store_true")
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
    parser.add_argument("--selection-top-percent", type=float, default=20.0)
    parser.add_argument("--selection-min-count", type=int, default=10)
    parser.add_argument("--selection-max-count", type=int, default=50)
    parser.add_argument("--portfolio-state", default="")
    parser.add_argument("--recent-research-symbols-file", default="")
    parser.add_argument("--as-of-date", default="")
    parser.add_argument("--kronos-advisory", action="store_true")
    parser.add_argument("--kronos-root", default=DEFAULT_KRONOS_ROOT)
    parser.add_argument("--kronos-python", default=DEFAULT_KRONOS_PYTHON)
    parser.add_argument("--kronos-period", default="2y")
    parser.add_argument("--kronos-interval", default="1d")
    parser.add_argument("--kronos-lookback", type=int, default=256)
    parser.add_argument("--kronos-pred-len", type=int, default=5)
    parser.add_argument("--kronos-model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--kronos-tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--kronos-device", default="cpu")
    parser.add_argument("--kronos-timeout-seconds", type=int, default=600)
    parser.add_argument("--kronos-limit", type=int, default=None)
    return parser


def run_cli(args: argparse.Namespace, *, fetch_metrics=fetch_yfinance_fundamental_metrics) -> int:
    _validate_research_provider_mode(args)
    campaign_dir = Path(args.campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(campaign_dir) if args.resume else _initial_state(campaign_dir)
    _record_event(campaign_dir, "campaign_started", {"run_until": args.run_until, "resume": bool(args.resume)})

    if _prepare_stage_needs_refresh(args, campaign_dir):
        _run_prepare_stage(args, campaign_dir, state)
    else:
        state["prepare"] = _load_json(campaign_dir / "extended_universe_summary.json")

    _run_scan_stage(args, campaign_dir, state, fetch_metrics=fetch_metrics)

    if args.run_until in {"evidence_ready", "research_queue_ready"} and state.get("stage") == "scan_ready":
        _run_evidence_stage(args, campaign_dir, state)

    if args.run_until == "research_queue_ready" and state.get("stage") == "evidence_ready":
        _run_selection_stage(args, campaign_dir, state)

    _write_state(campaign_dir, state)
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


def _run_prepare_stage(args: argparse.Namespace, campaign_dir: Path, state: dict[str, Any]) -> None:
    candidates = (
        load_candidate_source_url(args.source_url, source=args.source)
        if args.source_url
        else load_candidate_source_file(args.source_file, source=args.source)
    )
    supplemental_candidates = _load_supplemental_candidates(args)
    prepared = prepare_extended_universe(
        candidates,
        source=args.source,
        supplemental_candidates=supplemental_candidates,
        watchlist_limit=args.watchlist_limit,
        batch_size=args.universe_batch_size,
    )
    _write_json(campaign_dir / "extended_watchlist_ideas.json", prepared.watchlist_ideas)
    _write_batches(campaign_dir / "universe_batches", prepared.batches)
    summary = dict(prepared.summary)
    summary["ideas_output"] = str(campaign_dir / "extended_watchlist_ideas.json")
    summary["batches_output_dir"] = str(campaign_dir / "universe_batches")
    _write_json(campaign_dir / "extended_universe_summary.json", summary)
    state["prepare"] = summary
    state["stage"] = "universe_prepared"
    _write_state(campaign_dir, state)
    _record_event(campaign_dir, "universe_prepared", summary)


def _prepare_stage_needs_refresh(args: argparse.Namespace, campaign_dir: Path) -> bool:
    ideas_path = campaign_dir / "extended_watchlist_ideas.json"
    summary_path = campaign_dir / "extended_universe_summary.json"
    if not ideas_path.exists() or not summary_path.exists():
        return True
    summary = _load_json(summary_path)
    if str(summary.get("source") or "") != str(args.source or ""):
        return True
    supplemental_count = len(args.supplemental_source_file or []) + len(args.supplemental_source_url or []) + len(args.supplemental_ideas_file or [])
    if int(summary.get("supplemental_source_count") or 0) != supplemental_count:
        return True
    if int(summary.get("watchlist_ideas_count") or 0) == 0:
        return int(summary.get("source_candidate_count") or 0) > 0 and int(args.watchlist_limit or 0) > 0
    return False


def _load_supplemental_candidates(args: argparse.Namespace) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for spec in args.supplemental_source_file or []:
        source, path = _split_source_spec(spec, flag_name="--supplemental-source-file")
        groups.append(load_candidate_source_file(path, source=source))
    for spec in args.supplemental_source_url or []:
        source, url = _split_source_spec(spec, flag_name="--supplemental-source-url")
        groups.append(load_candidate_source_url(url, source=source))
    for spec in args.supplemental_ideas_file or []:
        source, path = _split_source_spec(spec, flag_name="--supplemental-ideas-file")
        groups.append(_load_supplemental_ideas_file(path, source=source))
    return groups


def _load_supplemental_ideas_file(path: str, *, source: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("--supplemental-ideas-file must contain a JSON list.")
    ideas = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        idea = dict(item)
        idea.setdefault("source", source)
        idea.setdefault("idea_source", source)
        ideas.append(idea)
    return ideas


def _split_source_spec(spec: str, *, flag_name: str) -> tuple[str, str]:
    if "=" not in str(spec):
        raise ValueError(f"{flag_name} must use source_name=value format.")
    source, value = str(spec).split("=", 1)
    source = source.strip()
    value = value.strip()
    if not source or not value:
        raise ValueError(f"{flag_name} must use source_name=value format.")
    return source, value


def _run_scan_stage(
    args: argparse.Namespace,
    campaign_dir: Path,
    state: dict[str, Any],
    *,
    fetch_metrics,
) -> None:
    ideas = _load_list(campaign_dir / "extended_watchlist_ideas.json")
    cache_path = campaign_dir / "extended_fundamentals_cache.json"
    remaining_budget = max(0, int(args.max_fundamental_fetches))
    chunk_size = max(1, int(args.fundamental_fetch_chunk_size))
    summary: dict[str, Any] = {}

    while True:
        fetch_limit = min(chunk_size, remaining_budget) if remaining_budget > 0 else 0
        snapshots = _load_optional_symbol_cache(cache_path)
        requested_symbols = _requested_symbols(ideas)
        allowed = _allowed_fetch_symbols(requested_symbols, snapshots, fetch_limit)
        stats = {
            "fundamentals_cache": str(cache_path),
            "fundamentals_cache_hits": sum(1 for symbol in requested_symbols if symbol in snapshots),
            "fundamentals_cache_fetches": 0,
            "fundamentals_fetch_error_count": 0,
            "fundamentals_fetch_errors": [],
            "fundamentals_fetch_limit": fetch_limit,
        }

        def cached_fetch(symbol: str) -> Mapping[str, Any]:
            normalized = symbol.upper()
            if normalized not in snapshots:
                if normalized not in allowed:
                    return {}
                try:
                    fetched = dict(fetch_metrics(normalized))
                except Exception as exc:  # pragma: no cover - provider failures vary
                    stats["fundamentals_fetch_errors"].append({"symbol": normalized, "error": str(exc)})
                    stats["fundamentals_fetch_error_count"] = len(stats["fundamentals_fetch_errors"])
                    return {}
                if fetched:
                    snapshots[normalized] = fetched
                    stats["fundamentals_cache_fetches"] += 1
            return snapshots.get(normalized, {})

        result = run_python_first_pass_scan(
            ideas,
            metrics_by_symbol=snapshots,
            fetch_metrics=cached_fetch,
            top_percent=args.top_percent,
            min_pass_count=args.min_pass_count,
            max_pass_count=args.max_pass_count,
            as_of_date=args.as_of_date or None,
            min_coverage_percent_for_enrichment=args.min_coverage_percent_for_enrichment,
        )
        _write_json(cache_path, snapshots)
        _write_scan_outputs(campaign_dir, result)
        summary = dict(result.summary)
        summary.update(stats)
        fetch_skipped = [
            symbol for symbol in requested_symbols if symbol not in snapshots and symbol not in allowed
        ]
        summary["fundamentals_fetch_skipped_count"] = len(fetch_skipped)
        summary["fundamentals_fetch_skipped_symbols"] = fetch_skipped
        summary.update(_remaining_fetch_work(summary, fetch_limit))
        _write_json(campaign_dir / "python_scan_summary.json", summary)
        state["scan"] = summary
        state["stage"] = "scan_ready" if summary.get("ready_for_expensive_enrichment") else "scan_filling"
        _write_state(campaign_dir, state)
        _record_event(campaign_dir, "scan_pass_completed", summary)

        fetched = int(stats["fundamentals_cache_fetches"])
        remaining_budget -= fetched
        if summary.get("ready_for_expensive_enrichment"):
            break
        if fetched <= 0 or remaining_budget <= 0:
            break


def _run_evidence_stage(args: argparse.Namespace, campaign_dir: Path, state: dict[str, Any]) -> None:
    evidence_dir = campaign_dir / "evidence_campaign"
    news_cache = args.news_cache_path or str(evidence_dir / "polygon_news_cache.json")
    kronos_path = ""
    if args.kronos_advisory:
        kronos_path = _run_kronos_stage(args, campaign_dir, state)
    evidence_args = [
        "--idea-batch",
        str(campaign_dir / "python_scan_passed.json"),
        "--fundamentals-snapshot-file",
        str(campaign_dir / "extended_fundamentals_cache.json"),
        "--batch-size",
        str(args.evidence_batch_size),
        "--output-dir",
        str(evidence_dir),
        "--resume",
        "--rate-limit-batch-size",
        str(args.rate_limit_batch_size),
        "--rate-limit-pause-seconds",
        str(args.rate_limit_pause_seconds),
        "--campaign-batch-pause-seconds",
        str(args.campaign_batch_pause_seconds),
    ]
    if args.max_evidence_batches is not None:
        evidence_args.extend(["--max-batches", str(args.max_evidence_batches)])
    if args.polygon_news:
        evidence_args.extend(["--polygon-news", "--news-cache-path", news_cache])
    if args.perplexity_research:
        evidence_args.extend(
            [
                "--perplexity-research",
                "--perplexity-api-key-env",
                args.perplexity_api_key_env,
                "--perplexity-model",
                args.perplexity_model,
                "--perplexity-api-url",
                args.perplexity_api_url,
                "--perplexity-timeout-seconds",
                str(args.perplexity_timeout_seconds),
                "--perplexity-max-tokens",
                str(args.perplexity_max_tokens),
                "--perplexity-search-context-size",
                args.perplexity_search_context_size,
            ]
        )
        if args.perplexity_credits_purchased_to_date is not None:
            evidence_args.extend(
                [
                    "--perplexity-credits-purchased-to-date",
                    str(args.perplexity_credits_purchased_to_date),
                ]
            )
    elif args.xai_grok:
        evidence_args.append("--xai-grok")
    else:
        evidence_args.append("--skip-grok")
    if kronos_path:
        evidence_args.extend(["--kronos-advisory-file", kronos_path])
    if args.as_of_date:
        evidence_args.extend(["--as-of-date", args.as_of_date])

    from longterm.evidence_enrichment_campaign_cli import build_parser as build_evidence_parser

    with contextlib.redirect_stdout(io.StringIO()):
        run_evidence_campaign_cli(build_evidence_parser().parse_args(evidence_args))
    summary = _load_json(evidence_dir / "campaign_summary.json")
    state["evidence"] = summary
    if int(summary.get("enriched_count") or 0) >= int(state.get("scan", {}).get("passed_count") or 0):
        state["stage"] = "evidence_ready"
    else:
        state["stage"] = "evidence_in_progress"
    _write_state(campaign_dir, state)
    _record_event(campaign_dir, "evidence_campaign_advanced", summary)


def _run_kronos_stage(args: argparse.Namespace, campaign_dir: Path, state: dict[str, Any]) -> str:
    output = campaign_dir / "kronos_advisory_batch.json"
    kronos_args = argparse.Namespace(
        symbols="",
        idea_batch=str(campaign_dir / "python_scan_passed.json"),
        output=str(output),
        kronos_root=args.kronos_root,
        kronos_python=args.kronos_python,
        period=args.kronos_period,
        interval=args.kronos_interval,
        lookback=args.kronos_lookback,
        pred_len=args.kronos_pred_len,
        model=args.kronos_model,
        tokenizer=args.kronos_tokenizer,
        device=args.kronos_device,
        timeout_seconds=args.kronos_timeout_seconds,
        limit=args.kronos_limit,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        run_kronos_batch_cli(kronos_args)
    summary = _load_json(output)
    state["kronos"] = {
        "advisory_file": str(output),
        "provider_status": summary.get("provider_status"),
        "symbol_count": summary.get("symbol_count"),
        "ok_count": summary.get("ok_count"),
        "unavailable_count": summary.get("unavailable_count"),
        "policy_boundary": summary.get("policy_boundary"),
    }
    _write_state(campaign_dir, state)
    _record_event(campaign_dir, "kronos_advisory_batch_completed", state["kronos"])
    return str(output)


def _run_selection_stage(args: argparse.Namespace, campaign_dir: Path, state: dict[str, Any]) -> None:
    selection_dir = campaign_dir / "research_selection"
    selection_args = [
        "--evidence-file",
        str(campaign_dir / "evidence_campaign" / "campaign_enriched.json"),
        "--output-dir",
        str(selection_dir),
        "--campaign-id",
        str(campaign_dir.name),
        "--top-percent",
        str(args.selection_top_percent),
        "--min-count",
        str(args.selection_min_count),
        "--max-count",
        str(args.selection_max_count),
    ]
    if args.portfolio_state:
        selection_args.extend(["--portfolio-state", args.portfolio_state])
    if args.recent_research_symbols_file:
        selection_args.extend(["--recent-research-symbols-file", args.recent_research_symbols_file])

    from longterm.research_selection_cli import build_parser as build_selection_parser
    from longterm.research_selection_cli import run_cli as run_selection_cli

    with contextlib.redirect_stdout(io.StringIO()):
        run_selection_cli(build_selection_parser().parse_args(selection_args))
    summary = _load_json(selection_dir / "research_queue_summary.json")
    state["research_selection"] = summary
    if int(summary.get("selected_count") or 0) > 0:
        state["stage"] = "research_queue_ready"
        event_type = "research_queue_selected"
    else:
        state["stage"] = "research_queue_empty"
        event_type = "research_queue_empty"
    _write_state(campaign_dir, state)
    _record_event(campaign_dir, event_type, summary)


def _validate_research_provider_mode(args: argparse.Namespace) -> None:
    selected = [
        name
        for name, enabled in (
            ("skip-grok", bool(getattr(args, "skip_grok", False))),
            ("xai-grok", bool(getattr(args, "xai_grok", False))),
            ("perplexity-research", bool(getattr(args, "perplexity_research", False))),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise ValueError(
            "Choose at most one research provider mode: "
            "--skip-grok, --xai-grok, or --perplexity-research."
        )


def _write_scan_outputs(campaign_dir: Path, result) -> None:
    _write_json(campaign_dir / "python_scan_passed.json", result.passed_ideas)
    _write_json(campaign_dir / "python_scan_deferred.json", result.deferred_ideas)
    _write_json(campaign_dir / "python_scan_scanned.json", result.scanned_ideas)
    _write_jsonl(campaign_dir / "python_scan_passed.jsonl", result.passed_ideas)
    _write_jsonl(campaign_dir / "python_scan_deferred.jsonl", result.deferred_ideas)
    _write_jsonl(campaign_dir / "python_scan_scanned.jsonl", result.scanned_ideas)


def _initial_state(campaign_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "longterm_research_automation_campaign",
        "campaign_dir": str(campaign_dir),
        "stage": "initialized",
        "created_at": datetime.now().isoformat(),
    }


def _load_state(campaign_dir: Path) -> dict[str, Any]:
    state_path = campaign_dir / "campaign_state.json"
    if not state_path.exists():
        return _initial_state(campaign_dir)
    return _load_json(state_path)


def _write_state(campaign_dir: Path, state: Mapping[str, Any]) -> None:
    payload = dict(state)
    payload["updated_at"] = datetime.now().isoformat()
    _write_json(campaign_dir / "campaign_state.json", payload)
    state.clear() if hasattr(state, "clear") else None
    if isinstance(state, dict):
        state.update(payload)


def _record_event(campaign_dir: Path, event_type: str, payload: Mapping[str, Any]) -> None:
    event = {
        "event_type": event_type,
        "created_at": datetime.now().isoformat(),
        "payload": dict(payload),
    }
    path = campaign_dir / "campaign_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _write_batches(output_dir: str | Path, batches: list[dict[str, Any]]) -> None:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        target = target_dir / f"{batch['batch_id']}.json"
        target.write_text(json.dumps(batch["ideas"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return dict(payload)


def _load_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list: {path}")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


__all__ = ["build_parser", "main", "run_cli"]
