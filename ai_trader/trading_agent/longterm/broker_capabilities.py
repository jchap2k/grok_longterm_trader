"""Advisory broker capability compatibility checks for live readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BrokerCapabilityProfile:
    key: str
    label: str
    api_trading_supported: bool
    supports_whole_share_orders: bool
    supports_fractional_shares: bool
    supports_notional_orders: bool
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BROKER_CAPABILITIES: dict[str, BrokerCapabilityProfile] = {
    "alpaca_paper": BrokerCapabilityProfile(
        key="alpaca_paper",
        label="Alpaca Paper",
        api_trading_supported=True,
        supports_whole_share_orders=True,
        supports_fractional_shares=True,
        supports_notional_orders=True,
        notes=[
            "Used as the supervised paper simulator in Stage 6B.",
            "Fractional/notional support is a paper-simulation capability, not a live-readiness guarantee.",
        ],
    ),
    "schwab_api": BrokerCapabilityProfile(
        key="schwab_api",
        label="Schwab API",
        api_trading_supported=True,
        supports_whole_share_orders=True,
        supports_fractional_shares=False,
        supports_notional_orders=False,
        notes=[
            "Schwab offers Stock Slices outside the API, but the API path is treated as whole-share only.",
            "Any future Schwab live path must adapt sizing before order submission.",
        ],
    ),
}


def evaluate_broker_capability_match(
    *,
    paper_broker: str = "alpaca_paper",
    live_broker: str = "schwab_api",
    required_order_model: str = "notional_fractional",
) -> dict[str, Any]:
    """Compare paper and intended live broker capabilities without broker calls."""
    paper = _profile(paper_broker)
    live = _profile(live_broker)
    blockers: list[str] = []
    warnings: list[str] = []

    if not live.api_trading_supported:
        blockers.append("live_broker_api_trading_not_supported")
    if required_order_model == "notional_fractional":
        if not live.supports_fractional_shares:
            blockers.append("live_broker_lacks_fractional_shares")
        if not live.supports_notional_orders:
            blockers.append("live_broker_lacks_notional_orders")
    elif required_order_model == "whole_share":
        if not live.supports_whole_share_orders:
            blockers.append("live_broker_lacks_whole_share_orders")
        if paper.supports_fractional_shares or paper.supports_notional_orders:
            warnings.append("paper_model_may_be_more_flexible_than_live_model")
    else:
        raise ValueError(f"Unsupported required_order_model: {required_order_model}")

    compatible = not blockers
    return {
        "schema_version": 1,
        "mode": "broker_capability_match",
        "paper_broker": paper.to_dict(),
        "live_broker": live.to_dict(),
        "required_order_model": required_order_model,
        "compatible": compatible,
        "blockers": blockers,
        "warnings": warnings,
        "live_readiness_observed": {"broker_capability_match": compatible},
        "notes": [
            "Advisory-only capability check. No broker orders were submitted.",
            "A compatible report does not enable live trading; it only satisfies one live-readiness gate.",
        ],
    }


def build_broker_capability_markdown(report: Mapping[str, Any]) -> str:
    paper = report.get("paper_broker") or {}
    live = report.get("live_broker") or {}
    lines = [
        "# Broker Capability Match",
        "",
        "Advisory-only check. No broker orders were submitted.",
        "",
        f"- Paper broker: {paper.get('label', '')}",
        f"- Intended live broker: {live.get('label', '')}",
        f"- Required order model: {report.get('required_order_model', '')}",
        f"- Compatible: {'yes' if report.get('compatible') else 'no'}",
        "",
        "| Capability | Paper | Live |",
        "|---|---:|---:|",
        _capability_row("API trading", paper, live, "api_trading_supported"),
        _capability_row("Whole-share orders", paper, live, "supports_whole_share_orders"),
        _capability_row("Fractional shares", paper, live, "supports_fractional_shares"),
        _capability_row("Notional orders", paper, live, "supports_notional_orders"),
        "",
    ]
    if report.get("blockers"):
        lines.extend(["## Blockers", ""])
        lines.extend(f"- {blocker}" for blocker in report.get("blockers") or [])
        lines.append("")
    if report.get("warnings"):
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.get("warnings") or [])
        lines.append("")
    return "\n".join(lines) + "\n"


def _profile(key: str) -> BrokerCapabilityProfile:
    try:
        return BROKER_CAPABILITIES[key]
    except KeyError as exc:
        supported = ", ".join(sorted(BROKER_CAPABILITIES))
        raise ValueError(f"Unknown broker capability profile '{key}'. Supported: {supported}") from exc


def _capability_row(label: str, paper: Mapping[str, Any], live: Mapping[str, Any], key: str) -> str:
    return f"| {label} | {_yes_no(paper.get(key))} | {_yes_no(live.get(key))} |"


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


__all__ = [
    "BROKER_CAPABILITIES",
    "BrokerCapabilityProfile",
    "build_broker_capability_markdown",
    "evaluate_broker_capability_match",
]
