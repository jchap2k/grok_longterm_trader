"""Read-only lifecycle summaries across paper preview, execution, and outcome state."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.paper_execution_status import PaperExecutionStatusBuilder
from longterm.paper_outcomes import summarize_paper_outcomes
from longterm.paper_preview_status import PaperPreviewStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger


def build_paper_lifecycle_summary(
    ledger: PaperTradeLedger,
    *,
    price_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a read-only symbol lifecycle summary from existing ledger evidence."""
    preview_by_symbol = PaperPreviewStatusBuilder(ledger).build().by_symbol
    execution_by_symbol = PaperExecutionStatusBuilder(ledger).build().by_symbol
    outcomes_by_symbol = _outcomes_by_symbol(ledger, price_map=price_map)
    symbols = sorted(set(preview_by_symbol) | set(execution_by_symbol) | set(outcomes_by_symbol))

    items = []
    counts: dict[str, int] = {}
    for symbol in symbols:
        item = {
            "symbol": symbol,
            **preview_by_symbol.get(symbol, {}),
            **execution_by_symbol.get(symbol, {}),
            **outcomes_by_symbol.get(symbol, {}),
        }
        item["paper_outcome_status"] = item.pop("status", "") if "status" in item else ""
        item["lifecycle_state"] = _lifecycle_state(item)
        counts[item["lifecycle_state"]] = counts.get(item["lifecycle_state"], 0) + 1
        items.append(item)

    return {
        "schema_version": 1,
        "mode": "paper_lifecycle_summary",
        "state_counts": counts,
        "items": items,
        "notes": [
            "Read-only lifecycle summary. No broker orders were submitted or modified.",
            "Paper outcomes require an explicit price map and are omitted when prices are not supplied.",
        ],
    }


def build_paper_lifecycle_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Lifecycle Summary",
        "",
        "Read-only preview/execution/outcome lifecycle. No broker orders were submitted or modified.",
        "",
        "## State Counts",
        "",
        "| State | Count |",
        "| --- | ---: |",
    ]
    for state, count in sorted((payload.get("state_counts") or {}).items()):
        lines.append(f"| {state} | {count} |")
    lines.extend(
        [
            "",
            "## Symbols",
            "",
            "| Symbol | Lifecycle | Preview | Execution | Outcome | Excess vs FXAIX |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for item in payload.get("items") or []:
        lines.append(
            "| {symbol} | {lifecycle} | {preview} | {execution} | {outcome} | {excess} |".format(
                symbol=item.get("symbol") or "",
                lifecycle=item.get("lifecycle_state") or "",
                preview=item.get("paper_preview_status") or "",
                execution=item.get("paper_execution_latest_status") or "",
                outcome=item.get("paper_outcome_status") or "",
                excess=_pct(item.get("excess_return_pct")),
            )
        )
    return "\n".join(lines) + "\n"


def _outcomes_by_symbol(
    ledger: PaperTradeLedger,
    *,
    price_map: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if price_map is None:
        return {}
    payload = summarize_paper_outcomes(ledger, price_map=price_map)
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("items") or []:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and symbol not in result:
            result[symbol] = dict(item)
    return result


def _lifecycle_state(item: Mapping[str, Any]) -> str:
    outcome_status = str(item.get("paper_outcome_status") or "")
    execution_status = str(item.get("paper_execution_latest_status") or item.get("paper_execution_status") or "")
    preview_status = str(item.get("paper_preview_status") or "")
    if outcome_status == "evaluated":
        return "outcome_evaluated"
    if outcome_status == "pending_price":
        return "filled_outcome_pending"
    if execution_status in {"filled", "partially_filled"}:
        return "filled_outcome_pending"
    if execution_status == "rejected":
        return "execution_rejected"
    if execution_status in {"canceled", "cancelled", "expired"}:
        return "execution_canceled"
    if execution_status == "status_refresh_error":
        return "execution_status_error"
    if execution_status:
        return "submitted_pending_fill"
    if preview_status == "blocked":
        return "preview_blocked"
    if preview_status == "ready":
        return "preview_ready"
    if preview_status == "no_order":
        return "preview_no_order"
    return "unknown"


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


__all__ = ["build_paper_lifecycle_markdown", "build_paper_lifecycle_summary"]
