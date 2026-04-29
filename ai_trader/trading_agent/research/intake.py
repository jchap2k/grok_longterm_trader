"""Idea intake helpers for long-term research packets."""

from __future__ import annotations

from typing import Any, Mapping

from portfolio.portfolio_profile import PortfolioProfile
from research.research_packet import CompanyCategory, ResearchPacket


def _normalize_company_category(value: Any) -> CompanyCategory | None:
    """Normalize a raw category value into a CompanyCategory enum."""
    if value is None or value == "":
        return None
    if isinstance(value, CompanyCategory):
        return value

    normalized = str(value).strip().lower()
    for category in CompanyCategory:
        if category.value == normalized:
            return category
    raise ValueError(f"Unknown company category: {value}")


def create_research_packet_from_idea(
    idea: Mapping[str, Any],
    *,
    profile: PortfolioProfile | None = None,
    idea_source: str | None = None,
) -> ResearchPacket:
    """Create a normalized ResearchPacket from a raw idea dictionary."""
    payload = dict(idea)
    payload["company_category"] = _normalize_company_category(
        payload.get("company_category")
    )

    if idea_source:
        payload["idea_source"] = idea_source

    if profile is not None:
        payload.setdefault("account_strategy_mode", profile.account_strategy_mode)
        payload.setdefault("protected_symbols", profile.protected_symbols)
        payload.setdefault("benchmark_symbol", profile.benchmark_symbol)
        payload.setdefault("defensive_parking_symbol", profile.defensive_parking_symbol)

    return ResearchPacket(**payload)
