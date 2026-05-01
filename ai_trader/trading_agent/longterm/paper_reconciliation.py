"""Dry-run reconciliation between paper account state and action plans."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState


def reconcile_paper_account(
    portfolio_state: PortfolioState,
    *,
    action_plan: Mapping[str, Any] | None = None,
    expected_cash: float | None = None,
    protected_symbols: list[str] | None = None,
    paper_ledger: PaperTradeLedger | None = None,
    value_tolerance_pct: float = 5.0,
) -> dict[str, Any]:
    """Compare paper account holdings against dry-run target/action context."""
    protected = {symbol.upper() for symbol in (protected_symbols or portfolio_state.protected_symbols)}
    targets = _targets_from_action_plan(action_plan or {})
    actual_by_symbol = {holding.symbol: holding for holding in portfolio_state.holdings}
    fill_reconciliation = _fill_reconciliation(actual_by_symbol, paper_ledger)

    missing = sorted(symbol for symbol in targets if symbol not in actual_by_symbol)
    extra = sorted(
        symbol
        for symbol in actual_by_symbol
        if symbol not in targets and symbol not in protected
    )
    mismatched = []
    for symbol, target_value in targets.items():
        holding = actual_by_symbol.get(symbol)
        if not holding or target_value <= 0:
            continue
        delta = round(holding.market_value - target_value, 2)
        tolerance = abs(target_value) * (value_tolerance_pct / 100.0)
        if abs(delta) > tolerance:
            mismatched.append(
                {
                    "symbol": symbol,
                    "actual_value": holding.market_value,
                    "target_value": target_value,
                    "delta": delta,
                }
            )

    cash_delta = None
    if expected_cash is not None:
        cash_delta = round(portfolio_state.cash - float(expected_cash), 2)

    return {
        "mode": "dry_run_reconciliation",
        "order_submission_enabled": False,
        "cash": portfolio_state.cash,
        "expected_cash": expected_cash,
        "cash_delta": cash_delta,
        "protected_symbol_status": [
            {
                "symbol": symbol,
                "status": "present" if portfolio_state.holding_value(symbol) > 0 else "missing",
                "market_value": portfolio_state.holding_value(symbol),
            }
            for symbol in sorted(protected)
        ],
        "missing_target_symbols": missing,
        "extra_symbols": extra,
        "mismatched_holdings": mismatched,
        "filled_execution_count": sum(1 for item in fill_reconciliation if item["execution_status"] in {"filled", "partially_filled"}),
        "missing_filled_symbols": sorted(
            item["symbol"]
            for item in fill_reconciliation
            if item["status"] == "missing_after_fill"
        ),
        "unexpected_rejected_fill_symbols": sorted(
            item["symbol"]
            for item in fill_reconciliation
            if item["status"] == "unexpected_holding_after_rejected_order"
        ),
        "paper_fill_reconciliation": fill_reconciliation,
        "notes": [
            "Read-only reconciliation only; no paper or live orders were submitted.",
            "Protected symbols are checked for presence and excluded from extra-symbol warnings.",
        ],
    }


def build_paper_reconciliation_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Account Reconciliation",
        "",
        "Read-only comparison. No orders were submitted.",
        "",
        f"- Cash: ${float(report.get('cash') or 0.0):,.2f}",
    ]
    if report.get("expected_cash") is not None:
        lines.append(f"- Cash delta: ${float(report.get('cash_delta') or 0.0):,.2f}")
    lines.extend(
        [
            f"- Missing target symbols: {', '.join(report.get('missing_target_symbols') or []) or 'none'}",
            f"- Extra symbols: {', '.join(report.get('extra_symbols') or []) or 'none'}",
            "",
            "## Protected Symbols",
            "",
            "| Symbol | Status | Market Value |",
            "| --- | --- | ---: |",
        ]
    )
    for item in report.get("protected_symbol_status") or []:
        lines.append(
            f"| {item.get('symbol')} | {item.get('status')} | ${float(item.get('market_value') or 0.0):,.2f} |"
        )
    lines.extend(["", "## Mismatched Holdings", ""])
    mismatches = report.get("mismatched_holdings") or []
    if not mismatches:
        lines.append("No value mismatches outside tolerance.")
    else:
        lines.extend(["| Symbol | Actual | Target | Delta |", "| --- | ---: | ---: | ---: |"])
        for item in mismatches:
            lines.append(
                "| {symbol} | ${actual:,.2f} | ${target:,.2f} | ${delta:,.2f} |".format(
                    symbol=item.get("symbol"),
                    actual=float(item.get("actual_value") or 0.0),
                    target=float(item.get("target_value") or 0.0),
                    delta=float(item.get("delta") or 0.0),
                )
            )
    fill_rows = report.get("paper_fill_reconciliation") or []
    lines.extend(["", "## Paper Fill Reconciliation", ""])
    if not fill_rows:
        lines.append("No paper fill events to reconcile.")
    else:
        lines.extend(["| Symbol | Execution | Status | Broker Order | Actual Value | Expected Value |", "| --- | --- | --- | --- | ---: | ---: |"])
        for item in fill_rows:
            lines.append(
                "| {symbol} | {execution} | {status} | {order} | ${actual:,.2f} | ${expected:,.2f} |".format(
                    symbol=item.get("symbol") or "",
                    execution=item.get("execution_status") or "",
                    status=item.get("status") or "",
                    order=item.get("broker_order_id") or "",
                    actual=float(item.get("actual_value") or 0.0),
                    expected=float(item.get("expected_value") or 0.0),
                )
            )
    return "\n".join(lines) + "\n"


def _targets_from_action_plan(action_plan: Mapping[str, Any]) -> dict[str, float]:
    targets: dict[str, float] = {}
    for intent in action_plan.get("intents") or []:
        symbol = str(intent.get("symbol") or "").upper()
        if not symbol or intent.get("allowed") is False:
            continue
        target_value = float(intent.get("target_value") or intent.get("trade_value") or 0.0)
        if target_value > 0:
            targets[symbol] = target_value
    return targets


def _fill_reconciliation(
    actual_by_symbol: Mapping[str, Any],
    paper_ledger: PaperTradeLedger | None,
) -> list[dict[str, Any]]:
    if paper_ledger is None:
        return []
    result = []
    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for event in paper_ledger.list_execution_events(limit=10000):
        symbol = str(event.get("symbol") or "").upper()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = event
    for symbol, event in sorted(latest_by_symbol.items()):
        status = str(event.get("status") or "")
        actual = actual_by_symbol.get(symbol)
        expected_value = float(event.get("notional") or 0.0) if status in {"filled", "partially_filled"} else 0.0
        if status in {"filled", "partially_filled"}:
            reconciliation_status = "present" if actual else "missing_after_fill"
        elif status in {"rejected", "status_refresh_error"} and actual:
            reconciliation_status = "unexpected_holding_after_rejected_order"
        else:
            reconciliation_status = "not_expected"
        result.append(
            {
                "symbol": symbol,
                "execution_status": status,
                "status": reconciliation_status,
                "broker_order_id": event.get("broker_order_id") or "",
                "decision_id": event.get("decision_id") or "",
                "actual_value": float(getattr(actual, "market_value", 0.0) or 0.0),
                "actual_quantity": float(getattr(actual, "quantity", 0.0) or 0.0),
                "expected_value": expected_value,
            }
        )
    order = {
        "present": 0,
        "missing_after_fill": 1,
        "unexpected_holding_after_rejected_order": 2,
        "not_expected": 3,
    }
    return sorted(result, key=lambda item: (order.get(str(item.get("status")), 99), item.get("symbol") or ""))


__all__ = ["build_paper_reconciliation_markdown", "reconcile_paper_account"]
