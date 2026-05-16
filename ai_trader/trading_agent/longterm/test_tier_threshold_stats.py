"""Tests for TierThresholdStats."""

import tempfile
from pathlib import Path

import pytest

from longterm.tier_threshold_stats import (
    TierThresholdStats,
    MIN_SAMPLES_FOR_DYNAMIC_FLOOR,
)


def test_returns_none_with_insufficient_samples():
    stats = TierThresholdStats()
    for i in range(10):
        stats.add_score(50.0 + i)

    assert stats.get_tier2_floor() is None
    assert stats.get_stats_summary()["sample_count"] == 10


def test_returns_25th_percentile_after_enough_samples():
    stats = TierThresholdStats()
    # Add 30 scores from 40 to 69
    for i in range(30):
        stats.add_score(40.0 + i)

    floor = stats.get_tier2_floor()
    assert floor is not None
    # 25th percentile of 40-69 should be around 47-48
    assert 46.0 <= floor <= 49.0


def test_respects_max_samples():
    stats = TierThresholdStats(max_samples=20)
    for i in range(50):
        stats.add_score(float(i))

    assert len(stats.scores) == 20
    assert min(stats.scores) == 30.0  # kept the most recent 20


def test_persistence(tmp_path: Path):
    stats_file = tmp_path / "test_stats.json"

    stats1 = TierThresholdStats(_stats_file=stats_file)
    for i in range(30):
        stats1.add_score(50.0 + i)
    stats1.save()

    stats2 = TierThresholdStats.load(stats_file)
    assert len(stats2.scores) == 30
    assert stats2.get_tier2_floor() is not None
