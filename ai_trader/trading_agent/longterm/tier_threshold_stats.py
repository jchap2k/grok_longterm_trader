"""
Dynamic Tier Threshold Statistics

Manages historical post-reviewer blended scores to calculate an adaptive
floor for Tier 2. This prevents the system from over-enriching weak ideas
during strong periods while still protecting against zero-pick scenarios
via relative ranking.

The floor is calculated as the 25th percentile of historical blended scores
(from ideas that reached Tier 2 or Tier 3). A minimum of 25 samples is
required before the dynamic floor is used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_STATS_FILE = Path(__file__).resolve().parent / "configs" / "tier_threshold_stats.json"
DEFAULT_MAX_SAMPLES = 150
MIN_SAMPLES_FOR_DYNAMIC_FLOOR = 25


@dataclass
class TierThresholdStats:
    """Manages historical blended scores for dynamic Tier 2 flooring."""

    scores: List[float] = field(default_factory=list)
    max_samples: int = DEFAULT_MAX_SAMPLES
    _stats_file: Optional[Path] = None

    def __post_init__(self):
        if self._stats_file is None:
            self._stats_file = DEFAULT_STATS_FILE

    @classmethod
    def load(cls, stats_file: Optional[Path] = None) -> "TierThresholdStats":
        """Load stats from disk. Returns empty stats if file doesn't exist."""
        path = stats_file or DEFAULT_STATS_FILE
        if not path.exists():
            return cls(_stats_file=path)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            scores = data.get("scores", [])
            max_samples = data.get("max_samples", DEFAULT_MAX_SAMPLES)
            return cls(scores=scores, max_samples=max_samples, _stats_file=path)
        except Exception:
            # If file is corrupted, start fresh but keep the path
            return cls(_stats_file=path)

    def save(self) -> None:
        """Persist current stats to disk."""
        if self._stats_file is None:
            return

        self._stats_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "max_samples": self.max_samples,
            "scores": self.scores,
            "last_updated": None,  # could add timestamp later
        }
        self._stats_file.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )

    def add_score(self, blended_score: float) -> None:
        """
        Add a post-reviewer blended score (only call this for Tier 2+ decisions).
        Maintains a rolling window of the most recent scores.
        """
        if blended_score is None:
            return

        self.scores.append(float(blended_score))

        # Keep only the most recent N scores
        if len(self.scores) > self.max_samples:
            self.scores = self.scores[-self.max_samples :]

    def get_tier2_floor(self) -> Optional[float]:
        """
        Returns the current dynamic floor for Tier 2, or None if we don't
        have enough data yet.
        """
        if len(self.scores) < MIN_SAMPLES_FOR_DYNAMIC_FLOOR:
            return None

        # Calculate 25th percentile (simple implementation)
        sorted_scores = sorted(self.scores)
        n = len(sorted_scores)
        index = max(0, int(0.25 * (n - 1)))
        return sorted_scores[index]

    def get_stats_summary(self) -> dict:
        """Return useful stats for logging and debugging."""
        if not self.scores:
            return {
                "sample_count": 0,
                "tier2_floor": None,
                "mean": None,
                "min": None,
            }

        return {
            "sample_count": len(self.scores),
            "tier2_floor": self.get_tier2_floor(),
            "mean": sum(self.scores) / len(self.scores),
            "min": min(self.scores),
            "max": max(self.scores),
        }

    def update_from_enriched_ideas(self, ideas: list[Mapping[str, Any]]) -> int:
        """
        Add blended scores from ideas that reached Tier 2 or higher.
        Uses research_selection.selection_score as the current proxy for the
        post-reviewer blended score (will be improved later when reviewer strength
        is available earlier in the flow).

        Returns the number of scores that were added.
        """
        added = 0
        for idea in ideas:
            tier = idea.get("enrichment_tier")
            if tier is not None and tier >= 2:
                rs = idea.get("research_selection") or {}
                score = rs.get("selection_score")
                if score is not None:
                    self.add_score(float(score))
                    added += 1

        if added > 0:
            self.save()
        return added

    def reset(self) -> None:
        """Clear all stored scores (useful for testing or reset)."""
        self.scores.clear()
