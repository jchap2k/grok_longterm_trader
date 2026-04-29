"""Prompt helpers for long-term stock research."""

from __future__ import annotations

import json
from typing import Iterable, List, Optional

from research.research_packet import ResearchPacket


def build_research_prompt(
    packet: ResearchPacket,
    *,
    source_notes: Optional[Iterable[str]] = None,
) -> str:
    """Build a compact structured prompt for long-term research review."""
    notes: List[str] = list(packet.source_notes)
    if source_notes:
        notes.extend(source_notes)

    payload = packet.to_dict()
    payload["source_notes"] = notes

    return (
        "You are reviewing a long-term stock candidate for a research-first "
        "quality-growth strategy.\n\n"
        "Candidate packet:\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n\n"
        "Evaluate the business, valuation, and thesis quality with discipline.\n"
        "If account_strategy_mode is roth_ira, you may recommend rebalance into "
        "more pullback-resilient holdings during major market stress and then "
        "rebalancing again after the general market is recovering.\n"
        "Never recommend selling, trimming, rotating, or rebalancing protected "
        "symbols listed in protected_symbols unless the user explicitly removes "
        "that protection.\n"
        "Use defensive_parking_symbol for temporary index-fund parking during "
        "defensive rebalancing. Do not use benchmark_symbol for temporary parking "
        "if benchmark_symbol is also protected.\n"
        "Answer these questions:\n"
        "- Why is this business attractive?\n"
        "- What is supposed to improve or persist?\n"
        "- What confirms the thesis is working?\n"
        "- What breaks the thesis?\n\n"
        "Respond with valid JSON containing:\n"
        "- thesis_strength\n"
        "- quality_summary\n"
        "- valuation_summary\n"
        "- balance_sheet_summary\n"
        "- confirming_signals\n"
        "- invalidation_conditions\n"
        "- reviewer_support\n"
        "- reviewer_objections\n"
        "- pullback_resilience_summary\n"
        "- recovery_reentry_signals\n"
        "- recommended_action\n"
    )
