"""Batch available universe tickers into manageable long-term research inputs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_research_universe_batches(
    ideas: list[Mapping[str, Any]],
    *,
    batch_size: int = 10,
) -> list[dict[str, Any]]:
    """Split research ideas into stable JSON batches for the research cycle."""
    normalized_batch_size = max(1, int(batch_size or 1))
    batches: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(ideas), normalized_batch_size), start=1):
        batch_ideas = [dict(idea) for idea in ideas[start : start + normalized_batch_size]]
        batches.append(
            {
                "batch_id": f"research-batch-{index:03d}",
                "ideas": batch_ideas,
            }
        )
    return batches


__all__ = ["build_research_universe_batches"]
