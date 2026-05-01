"""Provider-free paper fill outcome tracking versus FXAIX."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.paper_trade_ledger import PaperTradeLedger


def summarize_paper_outcomes(
    ledger: PaperTradeLedger,
    *,
    price_map: Mapping[str, Any],
    default_benchmark_symbol: str = "FXAIX",
) -> dict[str, Any]:
    """Summarize paper fill outcomes from explicit current-price evidence."""
    items = []
    for event in ledger.list_execution_events(limit=10000):
        if event.get("status") not in {"filled", "partially_filled"}:
            continue
        payload = event.get("event_json") or {}
        item = _outcome_item(
            event,
            payload=payload,
            price_map=price_map,
            default_benchmark_symbol=default_benchmark_symbol,
        )
        items.append(item)
    evaluated = [item for item in items if item["status"] == "evaluated"]
    excess = [float(item["excess_return_pct"]) for item in evaluated]
    return {
        "schema_version": 1,
        "mode": "paper_outcome_summary",
        "benchmark_symbol": default_benchmark_symbol,
        "evaluated_fills": len(evaluated),
        "pending_count": len(items) - len(evaluated),
        "average_excess_return_pct": round(sum(excess) / len(excess), 4) if excess else 0.0,
        "items": items,
        "notes": [
            "Paper outcome tracking only. No broker orders were submitted.",
            "Current prices must be supplied explicitly through a price map.",
        ],
    }


def build_paper_outcome_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Outcome Summary",
        "",
        "Provider-free paper fill outcome tracking. No orders were submitted.",
        "",
        f"- Evaluated fills: {payload.get('evaluated_fills', 0)}",
        f"- Pending prices: {payload.get('pending_count', 0)}",
        f"- Average excess return: {float(payload.get('average_excess_return_pct') or 0.0):.2f}%",
        "",
        "| Symbol | Status | Paper Return | Benchmark Return | Excess |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for item in payload.get("items") or []:
        lines.append(
            "| {symbol} | {status} | {paper:.2f}% | {benchmark:.2f}% | {excess:.2f}% |".format(
                symbol=item.get("symbol") or "",
                status=item.get("status") or "",
                paper=float(item.get("paper_return_pct") or 0.0),
                benchmark=float(item.get("benchmark_return_pct") or 0.0),
                excess=float(item.get("excess_return_pct") or 0.0),
            )
        )
    return "\n".join(lines) + "\n"


def _outcome_item(
    event: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    price_map: Mapping[str, Any],
    default_benchmark_symbol: str,
) -> dict[str, Any]:
    symbol = str(event.get("symbol") or "").upper()
    benchmark_symbol = str(payload.get("benchmark_symbol") or default_benchmark_symbol).upper()
    fill_price = float(payload.get("filled_price") or 0.0)
    benchmark_fill_price = float(payload.get("benchmark_price_at_fill") or 0.0)
    current_price = _current_price(price_map, symbol)
    current_benchmark_price = _current_price(price_map, benchmark_symbol)
    base = {
        "decision_id": event.get("decision_id") or "",
        "broker_order_id": event.get("broker_order_id") or "",
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "fill_price": fill_price,
        "benchmark_price_at_fill": benchmark_fill_price,
        "current_price": current_price,
        "current_benchmark_price": current_benchmark_price,
    }
    if fill_price <= 0 or benchmark_fill_price <= 0 or current_price is None or current_benchmark_price is None:
        return {
            **base,
            "status": "pending_price",
            "paper_return_pct": None,
            "benchmark_return_pct": None,
            "excess_return_pct": None,
        }
    paper_return = ((current_price - fill_price) / fill_price) * 100.0
    benchmark_return = ((current_benchmark_price - benchmark_fill_price) / benchmark_fill_price) * 100.0
    return {
        **base,
        "status": "evaluated",
        "paper_return_pct": round(paper_return, 4),
        "benchmark_return_pct": round(benchmark_return, 4),
        "excess_return_pct": round(paper_return - benchmark_return, 4),
    }


def _current_price(price_map: Mapping[str, Any], symbol: str) -> float | None:
    value = price_map.get(symbol) or price_map.get(symbol.upper()) or price_map.get(symbol.lower())
    if isinstance(value, Mapping):
        raw = value.get("current_price") or value.get("price") or value.get("close")
    else:
        raw = value
    if raw is None:
        return None
    return float(raw)


__all__ = ["build_paper_outcome_markdown", "summarize_paper_outcomes"]
