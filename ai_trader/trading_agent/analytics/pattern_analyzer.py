"""
Pattern Analyzer for Automated Lesson Discovery

Discovers statistical patterns in simulated trades.

Analyzes:
- Catalyst age effect (fresh vs aging vs stale)
- Gap direction and size
- Entry timing within golden window
- VIX regime performance
- Market cap segments
- Earnings surprise quality

Uses statistical tests (t-tests, chi-square) to find significant patterns.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import statistics
from collections import defaultdict

logger = logging.getLogger(__name__)

# Try to import scipy for statistical tests
try:
    from scipy import stats
    import numpy as np
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("scipy not available - using simplified statistics")


@dataclass
class LessonCandidate:
    """A discovered pattern that could become a lesson."""
    title: str
    description: str
    rule: str  # Actionable rule for agent

    # Evidence
    supporting_trades: int
    supporting_win_rate: float
    supporting_avg_pnl: float

    opposing_trades: int
    opposing_win_rate: float
    opposing_avg_pnl: float

    # Statistics
    p_value: float  # Statistical significance (lower = more significant)
    effect_size: float  # How big is the difference? (Cohen's d)
    confidence: float  # 0.0-1.0 confidence score

    # Actionable recommendations
    skip_condition: Optional[str]  # When to skip trade entirely
    conviction_adjustment: Optional[int]  # How much to adjust conviction (-3 to +3)

    # Supporting data
    segment_name: str  # What was segmented (e.g., "catalyst_age")
    segment_value: str  # The specific segment (e.g., ">48h")


class PatternAnalyzer:
    """
    Discovers statistical patterns in simulated trades.

    Process:
    1. Segment trades by attributes (catalyst age, gap, VIX, etc.)
    2. Calculate metrics for each segment (win rate, avg P&L)
    3. Run statistical tests to find significant differences
    4. Generate lesson candidates with confidence scores
    """

    def __init__(self, min_sample_size: int = 10, min_confidence: float = 0.75):
        """
        Initialize pattern analyzer.

        Args:
            min_sample_size: Minimum trades per segment for analysis
            min_confidence: Minimum confidence threshold for lessons
        """
        self.min_sample_size = min_sample_size
        self.min_confidence = min_confidence

        logger.info(f"Pattern Analyzer initialized (min_sample={min_sample_size}, min_conf={min_confidence:.0%})")

    def discover_patterns(self, trades: List[Any]) -> List[LessonCandidate]:
        """
        Discover all patterns across trade segments.

        Args:
            trades: List of SimulatedTrade objects

        Returns:
            List of LessonCandidate objects sorted by confidence
        """
        if len(trades) < self.min_sample_size * 2:
            logger.warning(f"Only {len(trades)} trades - need {self.min_sample_size * 2}+ for pattern discovery")
            return []

        logger.info(f"Analyzing {len(trades)} trades for patterns...")

        candidates = []

        # Pattern 1: Catalyst age effect
        age_patterns = self._analyze_catalyst_age(trades)
        candidates.extend(age_patterns)

        # Pattern 2: Gap direction and size
        gap_patterns = self._analyze_gap_direction(trades)
        candidates.extend(gap_patterns)

        # Pattern 3: Entry timing within window
        timing_patterns = self._analyze_entry_timing(trades)
        candidates.extend(timing_patterns)

        # Pattern 4: VIX regime performance
        vix_patterns = self._analyze_vix_regime(trades)
        candidates.extend(vix_patterns)

        # Pattern 5: Market cap segments
        mcap_patterns = self._analyze_market_cap(trades)
        candidates.extend(mcap_patterns)

        # Pattern 6: Earnings surprise quality
        surprise_patterns = self._analyze_earnings_surprise(trades)
        candidates.extend(surprise_patterns)

        # Sort by confidence (highest first)
        candidates.sort(key=lambda x: x.confidence, reverse=True)

        logger.info(f"Found {len(candidates)} pattern candidates")

        return candidates

    def _analyze_catalyst_age(self, trades: List[Any]) -> List[LessonCandidate]:
        """
        Analyze if catalyst age affects performance.

        Segments:
        - Fresh: <24h
        - Aging: 24-48h
        - Stale: >48h
        """
        # Segment trades by catalyst age
        fresh = [t for t in trades if t.catalyst_age_hours < 24]
        aging = [t for t in trades if 24 <= t.catalyst_age_hours < 48]
        stale = [t for t in trades if t.catalyst_age_hours >= 48]

        candidates = []

        # Compare fresh vs stale
        if len(fresh) >= self.min_sample_size and len(stale) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=fresh,
                segment_b=stale,
                segment_a_name="Fresh catalysts (<24h)",
                segment_b_name="Stale catalysts (>48h)",
                attribute_name="catalyst_age",
                better_direction="lower"  # Lower age is better
            )

            if pattern and pattern.confidence >= self.min_confidence:
                # Add actionable recommendation
                if pattern.supporting_avg_pnl > pattern.opposing_avg_pnl:
                    pattern.skip_condition = "catalyst_age_hours > 48"
                    pattern.conviction_adjustment = -2  # Reduce conviction for stale

                candidates.append(pattern)

        # Compare fresh vs aging
        if len(fresh) >= self.min_sample_size and len(aging) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=fresh,
                segment_b=aging,
                segment_a_name="Fresh catalysts (<24h)",
                segment_b_name="Aging catalysts (24-48h)",
                attribute_name="catalyst_age",
                better_direction="lower"
            )

            if pattern and pattern.confidence >= self.min_confidence:
                if pattern.supporting_avg_pnl > pattern.opposing_avg_pnl:
                    pattern.conviction_adjustment = -1  # Slight reduction for aging

                candidates.append(pattern)

        return candidates

    def _analyze_gap_direction(self, trades: List[Any]) -> List[LessonCandidate]:
        """Analyze if gap direction (up vs down) matters."""

        gap_up = [t for t in trades if t.gap_pct > 0]
        gap_down = [t for t in trades if t.gap_pct < 0]

        candidates = []

        if len(gap_up) >= self.min_sample_size and len(gap_down) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=gap_up,
                segment_b=gap_down,
                segment_a_name="Gap up",
                segment_b_name="Gap down",
                attribute_name="gap_direction",
                better_direction="higher"  # Higher P&L is better
            )

            if pattern and pattern.confidence >= self.min_confidence:
                # If gap down significantly underperforms, recommend avoiding
                if pattern.opposing_avg_pnl < -0.5:  # Loses >0.5% on average
                    pattern.skip_condition = "gap_pct < -2.0"
                    pattern.conviction_adjustment = -3

                candidates.append(pattern)

        return candidates

    def _analyze_entry_timing(self, trades: List[Any]) -> List[LessonCandidate]:
        """Analyze optimal entry timing within golden window."""

        early = [t for t in trades if t.entry_time_pst < "07:20"]
        mid = [t for t in trades if "07:20" <= t.entry_time_pst < "07:40"]
        late = [t for t in trades if t.entry_time_pst >= "07:40"]

        candidates = []

        # Compare early vs mid
        if len(early) >= self.min_sample_size and len(mid) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=mid,
                segment_b=early,
                segment_a_name="Mid-window entry (7:20-7:40)",
                segment_b_name="Early entry (<7:20)",
                attribute_name="entry_timing",
                better_direction="higher"
            )

            if pattern and pattern.confidence >= self.min_confidence:
                candidates.append(pattern)

        return candidates

    def _analyze_vix_regime(self, trades: List[Any]) -> List[LessonCandidate]:
        """Analyze performance across VIX regimes."""

        low_vix = [t for t in trades if t.vix_regime == 'low']
        med_vix = [t for t in trades if t.vix_regime == 'medium']
        high_vix = [t for t in trades if t.vix_regime == 'high']

        candidates = []

        # Compare low vs high VIX
        if len(low_vix) >= self.min_sample_size and len(high_vix) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=high_vix,
                segment_b=low_vix,
                segment_a_name="High VIX (>25)",
                segment_b_name="Low VIX (<15)",
                attribute_name="vix_regime",
                better_direction="higher"
            )

            if pattern and pattern.confidence >= self.min_confidence:
                # If low VIX significantly underperforms
                if pattern.opposing_avg_pnl < pattern.supporting_avg_pnl - 1.0:
                    pattern.skip_condition = "vix_level < 15"
                    pattern.conviction_adjustment = -2

                candidates.append(pattern)

        return candidates

    def _analyze_market_cap(self, trades: List[Any]) -> List[LessonCandidate]:
        """Analyze performance by market cap."""

        small = [t for t in trades if t.market_cap < 2_000_000_000]
        mid = [t for t in trades if 2_000_000_000 <= t.market_cap < 10_000_000_000]
        large = [t for t in trades if t.market_cap >= 10_000_000_000]

        candidates = []

        # Compare small vs mid cap
        if len(small) >= self.min_sample_size and len(mid) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=mid,
                segment_b=small,
                segment_a_name="Mid-cap ($2-10B)",
                segment_b_name="Small-cap (<$2B)",
                attribute_name="market_cap",
                better_direction="higher"
            )

            if pattern and pattern.confidence >= self.min_confidence:
                candidates.append(pattern)

        return candidates

    def _analyze_earnings_surprise(self, trades: List[Any]) -> List[LessonCandidate]:
        """Analyze if earnings surprise quality matters."""

        # Filter to trades with surprise data
        with_surprise = [t for t in trades if t.surprise_pct is not None]

        if len(with_surprise) < self.min_sample_size * 2:
            return []

        strong_beat = [t for t in with_surprise if t.surprise_pct > 10]
        weak_or_miss = [t for t in with_surprise if t.surprise_pct <= 10]

        candidates = []

        if len(strong_beat) >= self.min_sample_size and len(weak_or_miss) >= self.min_sample_size:
            pattern = self._compare_segments(
                segment_a=strong_beat,
                segment_b=weak_or_miss,
                segment_a_name="Strong earnings beat (>10%)",
                segment_b_name="Weak beat or miss (<=10%)",
                attribute_name="earnings_surprise",
                better_direction="higher"
            )

            if pattern and pattern.confidence >= self.min_confidence:
                if pattern.supporting_avg_pnl > pattern.opposing_avg_pnl:
                    pattern.conviction_adjustment = +1  # Boost for strong beats

                candidates.append(pattern)

        return candidates

    def _compare_segments(self,
                         segment_a: List[Any],
                         segment_b: List[Any],
                         segment_a_name: str,
                         segment_b_name: str,
                         attribute_name: str,
                         better_direction: str) -> Optional[LessonCandidate]:
        """
        Compare two segments statistically.

        Args:
            segment_a: List of trades in segment A
            segment_b: List of trades in segment B
            segment_a_name: Human-readable name for A
            segment_b_name: Human-readable name for B
            attribute_name: What attribute is being segmented
            better_direction: 'higher' or 'lower' (which segment should be better?)

        Returns:
            LessonCandidate if significant difference found, None otherwise
        """
        # Calculate metrics for each segment
        a_wins = sum(1 for t in segment_a if t.pnl_pct > 0)
        a_win_rate = a_wins / len(segment_a) if segment_a else 0
        a_avg_pnl = statistics.mean([t.pnl_pct for t in segment_a]) if segment_a else 0

        b_wins = sum(1 for t in segment_b if t.pnl_pct > 0)
        b_win_rate = b_wins / len(segment_b) if segment_b else 0
        b_avg_pnl = statistics.mean([t.pnl_pct for t in segment_b]) if segment_b else 0

        # Run statistical test
        p_value, effect_size = self._statistical_test(
            [t.pnl_pct for t in segment_a],
            [t.pnl_pct for t in segment_b]
        )

        # Calculate confidence score
        confidence = self._calculate_confidence(
            p_value=p_value,
            effect_size=effect_size,
            sample_a=len(segment_a),
            sample_b=len(segment_b)
        )

        if confidence < self.min_confidence:
            return None

        # Determine which segment is "supporting" (better)
        if better_direction == "higher":
            if a_avg_pnl > b_avg_pnl:
                supporting, opposing = segment_a, segment_b
                supporting_name, opposing_name = segment_a_name, segment_b_name
                supporting_wr, opposing_wr = a_win_rate, b_win_rate
                supporting_pnl, opposing_pnl = a_avg_pnl, b_avg_pnl
            else:
                supporting, opposing = segment_b, segment_a
                supporting_name, opposing_name = segment_b_name, segment_a_name
                supporting_wr, opposing_wr = b_win_rate, a_win_rate
                supporting_pnl, opposing_pnl = b_avg_pnl, a_avg_pnl
        else:  # lower is better
            if a_avg_pnl > b_avg_pnl:
                supporting, opposing = segment_b, segment_a
                supporting_name, opposing_name = segment_b_name, segment_a_name
                supporting_wr, opposing_wr = b_win_rate, a_win_rate
                supporting_pnl, opposing_pnl = b_avg_pnl, a_avg_pnl
            else:
                supporting, opposing = segment_a, segment_b
                supporting_name, opposing_name = segment_a_name, segment_b_name
                supporting_wr, opposing_wr = a_win_rate, b_win_rate
                supporting_pnl, opposing_pnl = a_avg_pnl, b_avg_pnl

        # Generate lesson
        title = f"{supporting_name} outperform {opposing_name}"
        description = (
            f"{supporting_name} show {supporting_pnl:+.2f}% avg P&L and {supporting_wr:.1%} win rate, "
            f"vs {opposing_pnl:+.2f}% avg P&L and {opposing_wr:.1%} win rate for {opposing_name}. "
            f"Difference is statistically significant (p={p_value:.3f}, effect size={effect_size:.2f})."
        )
        rule = f"Prefer {supporting_name.lower()}"

        return LessonCandidate(
            title=title,
            description=description,
            rule=rule,
            supporting_trades=len(supporting),
            supporting_win_rate=supporting_wr,
            supporting_avg_pnl=supporting_pnl,
            opposing_trades=len(opposing),
            opposing_win_rate=opposing_wr,
            opposing_avg_pnl=opposing_pnl,
            p_value=p_value,
            effect_size=effect_size,
            confidence=confidence,
            skip_condition=None,  # Set by caller if appropriate
            conviction_adjustment=None,  # Set by caller if appropriate
            segment_name=attribute_name,
            segment_value=supporting_name
        )

    def _statistical_test(self, sample_a: List[float], sample_b: List[float]) -> Tuple[float, float]:
        """
        Run t-test to check if means are significantly different.

        Returns:
            (p_value, effect_size)
            - p_value: Probability difference is due to chance (lower = more significant)
            - effect_size: Cohen's d (magnitude of difference)
        """
        if SCIPY_AVAILABLE:
            # Use scipy for proper t-test
            t_stat, p_value = stats.ttest_ind(sample_a, sample_b)

            # Calculate Cohen's d (effect size)
            mean_a = np.mean(sample_a)
            mean_b = np.mean(sample_b)
            std_a = np.std(sample_a, ddof=1)
            std_b = np.std(sample_b, ddof=1)
            pooled_std = np.sqrt(((len(sample_a)-1)*std_a**2 + (len(sample_b)-1)*std_b**2) / (len(sample_a) + len(sample_b) - 2))

            effect_size = abs((mean_a - mean_b) / pooled_std) if pooled_std > 0 else 0

        else:
            # Simplified version without scipy
            mean_a = statistics.mean(sample_a)
            mean_b = statistics.mean(sample_b)
            std_a = statistics.stdev(sample_a) if len(sample_a) > 1 else 0
            std_b = statistics.stdev(sample_b) if len(sample_b) > 1 else 0

            # Rough p-value estimation
            diff = abs(mean_a - mean_b)
            pooled_std = (std_a + std_b) / 2
            effect_size = diff / pooled_std if pooled_std > 0 else 0

            # Rough p-value (not accurate, but directional)
            if effect_size > 0.8:
                p_value = 0.01  # Large effect
            elif effect_size > 0.5:
                p_value = 0.05  # Medium effect
            elif effect_size > 0.2:
                p_value = 0.10  # Small effect
            else:
                p_value = 0.50  # No effect

        return p_value, effect_size

    def _calculate_confidence(self,
                             p_value: float,
                             effect_size: float,
                             sample_a: int,
                             sample_b: int) -> float:
        """
        Calculate overall confidence in the pattern.

        Factors:
        - Statistical significance (p-value)
        - Effect size (magnitude of difference)
        - Sample size (larger = more confident)

        Returns:
            Confidence score 0.0-1.0
        """
        # Base confidence from p-value
        if p_value < 0.01:
            p_conf = 0.95
        elif p_value < 0.05:
            p_conf = 0.85
        elif p_value < 0.10:
            p_conf = 0.75
        else:
            p_conf = 0.50

        # Adjustment for effect size
        if effect_size > 0.8:
            effect_conf = 1.0  # Large effect
        elif effect_size > 0.5:
            effect_conf = 0.9  # Medium effect
        elif effect_size > 0.2:
            effect_conf = 0.7  # Small effect
        else:
            effect_conf = 0.5  # Tiny effect

        # Adjustment for sample size
        total_samples = sample_a + sample_b
        if total_samples >= 100:
            sample_conf = 1.0
        elif total_samples >= 50:
            sample_conf = 0.9
        elif total_samples >= 30:
            sample_conf = 0.8
        else:
            sample_conf = 0.7

        # Combined confidence (weighted average)
        confidence = (p_conf * 0.5) + (effect_conf * 0.3) + (sample_conf * 0.2)

        return min(1.0, max(0.0, confidence))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print("Pattern Analyzer module loaded successfully")
    print(f"Scipy available: {SCIPY_AVAILABLE}")
