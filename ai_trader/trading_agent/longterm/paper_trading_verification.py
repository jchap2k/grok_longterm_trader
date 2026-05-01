"""Read-only paper trading verification evidence for live-readiness gates."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.paper_execution_status import PaperExecutionStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger


def build_paper_trading_verification_report(ledger: PaperTradeLedger) -> dict[str, Any]:
    """Build a conservative observed fragment for the paper-trading gate."""
    status = PaperExecutionStatusBuilder(ledger).build()
    filled_symbols = sorted(
        symbol
        for symbol, item in status.by_symbol.items()
        if int(item.get("paper_execution_filled_count") or 0) > 0
    )
    current_error_symbols = sorted(
        symbol
        for symbol, item in status.by_symbol.items()
        if bool(item.get("paper_execution_current_status_is_error"))
    )
    blockers: list[str] = []
    if not filled_symbols:
        blockers.append("no_filled_paper_execution")
    if current_error_symbols:
        blockers.append("current_status_error_present")
    verified = not blockers
    return {
        "schema_version": 1,
        "mode": "paper_trading_verification",
        "order_submission_enabled": False,
        "paper_trading_verified": verified,
        "live_readiness_observed": {"paper_trading_verified": verified},
        "filled_symbol_count": len(filled_symbols),
        "filled_symbols": filled_symbols,
        "current_error_symbols": current_error_symbols,
        "blockers": blockers,
        "notes": [
            "Read-only verification report. No broker orders were submitted, canceled, or modified.",
            "This only verifies that a paper execution reached a filled status in the ledger.",
        ],
    }


def build_paper_trading_verification_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Trading Verification",
        "",
        "Read-only verification report. No orders were submitted, canceled, or modified.",
        "",
        f"- Paper trading verified: {'yes' if report.get('paper_trading_verified') else 'no'}",
        f"- Filled symbols: {', '.join(report.get('filled_symbols') or []) or 'none'}",
        f"- Current error symbols: {', '.join(report.get('current_error_symbols') or []) or 'none'}",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


__all__ = [
    "build_paper_trading_verification_markdown",
    "build_paper_trading_verification_report",
]
