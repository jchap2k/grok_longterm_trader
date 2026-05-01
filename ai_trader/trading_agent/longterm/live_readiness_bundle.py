"""Read-only live-readiness evidence bundle from local advisory artifacts."""

from __future__ import annotations

from typing import Any, Mapping

from longterm.broker_capabilities import evaluate_broker_capability_match
from longterm.live_readiness import LiveReadinessChecklist
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.paper_trading_verification import build_paper_trading_verification_report


def build_live_readiness_bundle(
    *,
    base_observed: Mapping[str, Any] | None = None,
    paper_ledger: PaperTradeLedger | None = None,
    paper_broker: str = "alpaca_paper",
    live_broker: str = "schwab_api",
    required_order_model: str = "notional_fractional",
) -> dict[str, Any]:
    """Merge local advisory evidence and evaluate the live-readiness checklist."""
    broker = evaluate_broker_capability_match(
        paper_broker=paper_broker,
        live_broker=live_broker,
        required_order_model=required_order_model,
    )
    paper = (
        build_paper_trading_verification_report(paper_ledger)
        if paper_ledger is not None
        else {
            "mode": "paper_trading_verification",
            "paper_trading_verified": False,
            "live_readiness_observed": {"paper_trading_verified": False},
            "blockers": ["paper_ledger_missing"],
        }
    )
    observed = dict(base_observed or {})
    observed.update(broker.get("live_readiness_observed") or {})
    observed.update(paper.get("live_readiness_observed") or {})
    result = LiveReadinessChecklist.default().evaluate(observed)
    return {
        "schema_version": 1,
        "mode": "live_readiness_bundle",
        "order_submission_enabled": False,
        "ready": result.ready,
        "unmet_gate_keys": result.unmet_gate_keys,
        "observed": observed,
        "gates": result.gates,
        "broker_capabilities": broker,
        "paper_trading_verification": paper,
        "notes": [
            "Read-only evidence bundle. No broker orders were submitted, canceled, or modified.",
            "A ready checklist does not enable live trading; it only summarizes local evidence.",
        ],
    }


def build_live_readiness_bundle_markdown(bundle: Mapping[str, Any]) -> str:
    lines = [
        "# Live Readiness Evidence Bundle",
        "",
        "Read-only evidence bundle. No orders were submitted, canceled, or modified.",
        "",
        f"- Ready for live trading: {'yes' if bundle.get('ready') else 'no'}",
        f"- Unmet gates: {', '.join(bundle.get('unmet_gate_keys') or []) or 'none'}",
        "",
        "## Advisory Evidence",
        "",
        f"- Broker capability match: {'yes' if (bundle.get('broker_capabilities') or {}).get('compatible') else 'no'}",
        f"- Paper trading verified: {'yes' if (bundle.get('paper_trading_verification') or {}).get('paper_trading_verified') else 'no'}",
        "",
        "## Gates",
        "",
        "| Gate | Status | Observed |",
        "|---|---|---|",
    ]
    for gate in bundle.get("gates") or []:
        lines.append(
            "| {gate} | {status} | {observed} |".format(
                gate=gate.get("label") or gate.get("key") or "",
                status="pass" if gate.get("passed") else "missing",
                observed=gate.get("observed_value"),
            )
        )
    return "\n".join(lines) + "\n"


__all__ = ["build_live_readiness_bundle", "build_live_readiness_bundle_markdown"]
