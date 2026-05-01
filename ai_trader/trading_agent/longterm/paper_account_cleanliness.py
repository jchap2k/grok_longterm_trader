"""Read-only paper account cleanliness checks before supervised smoke runs."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.portfolio_state import PortfolioState


def evaluate_paper_account_cleanliness(
    portfolio_state: PortfolioState,
    *,
    expected_cash: float | None = None,
    cash_tolerance: float = 1.0,
    protected_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Report whether a paper account appears reset for the next smoke run."""
    protected = {symbol.upper() for symbol in (protected_symbols or portfolio_state.protected_symbols)}
    unexpected_holdings = [
        {
            "symbol": holding.symbol,
            "market_value": holding.market_value,
            "quantity": holding.quantity,
        }
        for holding in portfolio_state.holdings
        if holding.symbol not in protected and holding.market_value > 0
    ]
    cash_delta = None
    cash_within_tolerance = True
    if expected_cash is not None:
        cash_delta = round(float(portfolio_state.cash or 0.0) - float(expected_cash), 2)
        cash_within_tolerance = abs(cash_delta) <= float(cash_tolerance)
    clean = not unexpected_holdings and cash_within_tolerance
    return {
        "schema_version": 1,
        "mode": "paper_account_cleanliness",
        "clean": clean,
        "order_submission_enabled": False,
        "cash": portfolio_state.cash,
        "expected_cash": expected_cash,
        "cash_delta": cash_delta,
        "cash_tolerance": float(cash_tolerance),
        "cash_within_tolerance": cash_within_tolerance,
        "position_count": len(portfolio_state.holdings),
        "unexpected_symbols": [item["symbol"] for item in unexpected_holdings],
        "unexpected_holdings": unexpected_holdings,
        "protected_symbols": sorted(protected),
        "notes": [
            "Read-only cleanliness check. No broker orders were submitted, canceled, or modified.",
            "Use this before a new paper smoke to confirm previous test positions are gone.",
        ],
    }


def build_paper_account_cleanliness_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Account Cleanliness",
        "",
        "Read-only check. No orders were submitted, canceled, or modified.",
        "",
        f"- Clean: {'yes' if report.get('clean') else 'no'}",
        f"- Cash: ${float(report.get('cash') or 0.0):,.2f}",
        f"- Position count: {int(report.get('position_count') or 0)}",
        f"- Unexpected symbols: {', '.join(report.get('unexpected_symbols') or []) or 'none'}",
    ]
    if report.get("expected_cash") is not None:
        lines.append(f"- Expected cash: ${float(report.get('expected_cash') or 0.0):,.2f}")
        lines.append(f"- Cash delta: ${float(report.get('cash_delta') or 0.0):,.2f}")
        lines.append(f"- Cash within tolerance: {'yes' if report.get('cash_within_tolerance') else 'no'}")
    holdings = report.get("unexpected_holdings") or []
    if holdings:
        lines.extend(["", "## Unexpected Holdings", "", "| Symbol | Quantity | Market Value |", "|---|---:|---:|"])
        for item in holdings:
            lines.append(
                "| {symbol} | {quantity:g} | ${market_value:,.2f} |".format(
                    symbol=item.get("symbol") or "",
                    quantity=float(item.get("quantity") or 0.0),
                    market_value=float(item.get("market_value") or 0.0),
                )
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "build_paper_account_cleanliness_markdown",
    "evaluate_paper_account_cleanliness",
]
