"""Run a live OpenRouter two-summary-plus-synthesis evaluation.

This is an opt-in diagnostics script for model selection. It does not read
broker state, does not run scheduler stages, and does not submit orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping


REPO_AGENT_ROOT = Path(__file__).resolve().parents[1] / "ai_trader" / "trading_agent"
if str(REPO_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_AGENT_ROOT))

from longterm.openrouter_text_summary import (  # noqa: E402
    OpenRouterTextSummaryClient,
    summarize_with_dual_openrouter_models,
)


DEFAULT_CASES = [
    {
        "id": "earnings_balanced",
        "symbol": "MSFT",
        "title": "Microsoft cloud and AI demand update",
        "url": "https://example.com/msft-cloud-ai",
        "text": (
            "Microsoft said Azure and AI demand remained durable in the latest quarter. "
            "Management also said capital spending will stay elevated as it expands "
            "data-center capacity. Operating margin held up, but free cash flow "
            "conversion could be pressured while the buildout continues."
        ),
    },
    {
        "id": "thin_noisy_source",
        "symbol": "TSLA",
        "title": "Tesla chatter spikes online",
        "url": "https://example.com/tsla-chatter",
        "text": (
            "Social-media posts claimed a new Tesla robotaxi launch date, but the "
            "article did not cite a company filing, executive statement, or official "
            "event page. The post said traders were discussing the rumor heavily."
        ),
    },
    {
        "id": "source_contradiction",
        "symbol": "AMZN",
        "title": "Amazon logistics margin report",
        "url": "https://example.com/amzn-logistics",
        "text": (
            "The headline said Amazon logistics margins surged. The article body said "
            "management did not disclose a logistics margin figure. It only reported "
            "that delivery speed improved and fuel costs were lower year over year."
        ),
    },
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live-test OpenRouter primary+comparison+synthesis summary models.",
    )
    parser.add_argument("--input", type=Path, help="JSON file containing a list of source text cases.")
    parser.add_argument("--output", type=Path, help="Write full JSON eval report to this path.")
    parser.add_argument("--pricing-file", type=Path, help="Optional model pricing override JSON.")
    parser.add_argument("--as-of-date", default=date.today().isoformat())
    parser.add_argument("--primary-model", default="xiaomi/mimo-v2-flash")
    parser.add_argument("--comparison-model", default="google/gemini-2.5-flash-lite")
    parser.add_argument("--synth-model", default="xiaomi/mimo-v2-flash")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--summary-max-tokens", type=int, default=900)
    parser.add_argument("--synth-max-tokens", type=int, default=700)
    parser.add_argument("--limit", type=int, default=0, help="Limit number of cases; 0 means all.")
    return parser


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.input)
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    pricing = _load_pricing(args.pricing_file)
    results = []
    for case in cases:
        primary_client = OpenRouterTextSummaryClient(
            model=args.primary_model,
            timeout_seconds=args.timeout_seconds,
            max_tokens=args.summary_max_tokens,
        )
        comparison_client = OpenRouterTextSummaryClient(
            model=args.comparison_model,
            timeout_seconds=args.timeout_seconds,
            max_tokens=args.summary_max_tokens,
        )
        synth_client = OpenRouterTextSummaryClient(
            model=args.synth_model,
            timeout_seconds=args.timeout_seconds,
            max_tokens=args.synth_max_tokens,
        )
        result = summarize_with_dual_openrouter_models(
            case,
            primary_client=primary_client,
            comparison_client=comparison_client,
            synth_client=synth_client,
            as_of_date=args.as_of_date,
        )
        result["case_id"] = str(case.get("id") or case.get("symbol") or len(results) + 1)
        _apply_pricing_estimates(result, pricing)
        results.append(result)
    return {
        "source_type": "openrouter_dual_summary_synthesis_eval_report",
        "order_submission_enabled": False,
        "as_of_date": args.as_of_date,
        "models": {
            "primary": args.primary_model,
            "comparison": args.comparison_model,
            "synth": args.synth_model,
        },
        "case_count": len(results),
        "results": results,
        "totals": _report_totals(results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = run_eval(args)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _load_cases(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return [dict(item) for item in DEFAULT_CASES]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Input JSON must be a list of source text cases.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _load_pricing(path: Path | None) -> dict[str, dict[str, float]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Pricing JSON must be an object keyed by model.")
    pricing = {}
    for model, row in payload.items():
        if not isinstance(row, Mapping):
            continue
        pricing[str(model)] = {
            "prompt_per_1m": _float_value(
                row.get("prompt_per_1m")
                or row.get("input_per_1m")
                or row.get("prompt")
                or row.get("input")
            ),
            "completion_per_1m": _float_value(
                row.get("completion_per_1m")
                or row.get("output_per_1m")
                or row.get("completion")
                or row.get("output")
            ),
        }
    return pricing


def _apply_pricing_estimates(
    result: dict[str, Any],
    pricing: Mapping[str, Mapping[str, float]],
) -> None:
    if not pricing:
        return
    for stage in result.get("stages") or []:
        usage = dict(stage.get("usage") or {})
        model = str(stage.get("model") or usage.get("model") or "")
        model_pricing = pricing.get(model)
        if not model_pricing:
            continue
        prompt_tokens = _int_value(usage.get("prompt_tokens"))
        completion_tokens = _int_value(usage.get("completion_tokens"))
        estimated = (
            prompt_tokens / 1_000_000 * float(model_pricing.get("prompt_per_1m") or 0.0)
            + completion_tokens / 1_000_000 * float(model_pricing.get("completion_per_1m") or 0.0)
        )
        usage["pricing_table_estimated_cost_usd"] = round(estimated, 6)
        stage["usage"] = usage
    result["pricing_table_totals"] = {
        "estimated_total_cost_usd": round(
            sum(
                float((stage.get("usage") or {}).get("pricing_table_estimated_cost_usd") or 0.0)
                for stage in result.get("stages") or []
            ),
            6,
        )
    }


def _report_totals(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    totals = [dict(result.get("totals") or {}) for result in results]
    pricing_totals = [dict(result.get("pricing_table_totals") or {}) for result in results]
    return {
        "elapsed_seconds": round(sum(float(total.get("elapsed_seconds") or 0.0) for total in totals), 3),
        "estimated_total_cost_usd": round(
            sum(float(total.get("estimated_total_cost_usd") or 0.0) for total in totals),
            6,
        ),
        "pricing_table_estimated_total_cost_usd": round(
            sum(float(total.get("estimated_total_cost_usd") or 0.0) for total in pricing_totals),
            6,
        ),
        "prompt_tokens": sum(_int_value(total.get("prompt_tokens")) for total in totals),
        "completion_tokens": sum(_int_value(total.get("completion_tokens")) for total in totals),
        "total_tokens": sum(_int_value(total.get("total_tokens")) for total in totals),
    }


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
