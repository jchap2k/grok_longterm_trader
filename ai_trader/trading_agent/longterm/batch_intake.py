"""Batch idea intake for long-term research packets."""

from __future__ import annotations

import json
from pathlib import Path

from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea
from research.research_packet import ResearchPacket


def load_idea_batch(
    path: str | Path,
    *,
    profile: PortfolioProfile | None = None,
    idea_source: str | None = None,
) -> list[ResearchPacket]:
    """Load a JSON list of ideas and normalize them into ResearchPackets."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Idea batch file must contain a JSON list.")
    return [
        create_research_packet_from_idea(
            idea,
            profile=profile,
            idea_source=idea_source or idea.get("idea_source"),
        )
        for idea in payload
    ]
