"""Provider-free paper fill outcome tracking versus FXAIX."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_trade_ledger import PaperTradeLedger


def summarize_paper_outcomes(
    ledger: PaperTradeLedger,
    *,
    price_map: Mapping[str, Any],
    default_benchmark_symbol: str = "FXAIX",
    journal: LongTermDecisionJournal | None = None,
) -> dict[str, Any]:
    """Summarize paper fill outcomes from explicit current-price evidence."""
    items = []
    for event in ledger.list_execution_events(limit=10000):
        if event.get("status") not in {"filled", "partially_filled"}:
            continue
        payload = event.get("event_json") or {}
        decision = _decision_for_event(journal, event.get("decision_id"))
        item = _outcome_item(
            event,
            payload=payload,
            price_map=price_map,
            default_benchmark_symbol=default_benchmark_symbol,
            decision=decision,
        )
        items.append(item)
    evaluated = [item for item in items if item["status"] == "evaluated"]
    excess = [float(item["excess_return_pct"]) for item in evaluated]
    benchmark_source_counts = _benchmark_source_counts(items)
    return {
        "schema_version": 1,
        "mode": "paper_outcome_summary",
        "benchmark_symbol": default_benchmark_symbol,
        "evaluated_fills": len(evaluated),
        "pending_count": len(items) - len(evaluated),
        "average_excess_return_pct": round(sum(excess) / len(excess), 4) if excess else 0.0,
        "benchmark_source_counts": benchmark_source_counts,
        "proxy_benchmark_count": int(benchmark_source_counts.get("decision_journal_proxy", 0)),
        "unlinked_count": int(benchmark_source_counts.get("missing", 0)),
        "items": items,
        "notes": [
            "Paper outcome tracking only. No broker orders were submitted.",
            "Current prices must be supplied explicitly through a price map.",
            "Decision-journal benchmark prices may be used as a proxy when the fill event did not capture benchmark_price_at_fill.",
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
        "| Symbol | Decision | Status | Benchmark Source | Paper Return | Benchmark Return | Excess |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for item in payload.get("items") or []:
        lines.append(
            "| {symbol} | {decision} | {status} | {source} | {paper:.2f}% | {benchmark:.2f}% | {excess:.2f}% |".format(
                symbol=item.get("symbol") or "",
                decision=str(item.get("decision_id") or "")[:8],
                status=item.get("status") or "",
                source=item.get("benchmark_price_source") or "",
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
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    symbol = str(event.get("symbol") or "").upper()
    benchmark_symbol = str(
        payload.get("benchmark_symbol")
        or decision.get("benchmark_symbol")
        or default_benchmark_symbol
    ).upper()
    fill_price = float(payload.get("filled_price") or 0.0)
    benchmark_fill_price, benchmark_price_source = _benchmark_fill_price(payload=payload, decision=decision)
    current_price = _current_price(price_map, symbol)
    current_benchmark_price = _current_price(price_map, benchmark_symbol)
    missing_reasons = _missing_reasons(
        fill_price=fill_price,
        benchmark_fill_price=benchmark_fill_price,
        current_price=current_price,
        current_benchmark_price=current_benchmark_price,
    )
    base = {
        "decision_id": event.get("decision_id") or "",
        "broker_order_id": event.get("broker_order_id") or "",
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "fill_price": fill_price,
        "benchmark_price_at_fill": benchmark_fill_price,
        "benchmark_price_source": benchmark_price_source,
        "current_price": current_price,
        "current_benchmark_price": current_benchmark_price,
        "missing_reasons": missing_reasons,
    }
    if missing_reasons:
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


def _decision_for_event(
    journal: LongTermDecisionJournal | None,
    decision_id: Any,
) -> dict[str, Any]:
    if not journal or not decision_id:
        return {}
    try:
        return journal.get_decision(str(decision_id))
    except (KeyError, OSError, ValueError):
        return {}


def _benchmark_fill_price(
    *,
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> tuple[float, str]:
    event_price = float(payload.get("benchmark_price_at_fill") or 0.0)
    if event_price > 0:
        return event_price, "event_payload"
    proxy_price = float(decision.get("benchmark_price_at_decision") or 0.0)
    if proxy_price > 0:
        return proxy_price, "decision_journal_proxy"
    return 0.0, "missing"


def _missing_reasons(
    *,
    fill_price: float,
    benchmark_fill_price: float,
    current_price: float | None,
    current_benchmark_price: float | None,
) -> list[str]:
    reasons = []
    if fill_price <= 0:
        reasons.append("missing_fill_price")
    if benchmark_fill_price <= 0:
        reasons.append("missing_benchmark_price_at_fill")
    if current_price is None:
        reasons.append("missing_current_symbol_price")
    if current_benchmark_price is None:
        reasons.append("missing_current_benchmark_price")
    return reasons


def _benchmark_source_counts(items: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        source = str(item.get("benchmark_price_source") or "missing")
        counts[source] = counts.get(source, 0) + 1
    return counts


__all__ = ["build_paper_outcome_markdown", "summarize_paper_outcomes"]
