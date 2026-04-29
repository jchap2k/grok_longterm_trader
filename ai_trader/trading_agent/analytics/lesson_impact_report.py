"""
Lesson Impact Analyzer - Phase 2A Simplified

Analyzes before/after performance when lessons are added to learning.db.
Per Grok's recommendation: script-based, no automated table (Phase 2B deferred).

Key Grok recommendations:
- 60-day lookback/monitoring windows (not 30) to account for regime shifts
- Detect regime changes (SPY volatility shift >30%)
- Statistical significance: two-sample proportion test p<0.05
- Simple before/after comparison (control groups deferred to Phase 2B)
"""

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sqlite3
from scipy import stats
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics.learning_database import LearningDatabase

logger = logging.getLogger(__name__)


@dataclass
class LessonImpactMetrics:
    """Metrics for lesson impact analysis."""
    lesson_id: int
    lesson_text: str
    lesson_date: date
    symbol: Optional[str]
    category: str

    # Before period
    before_start: date
    before_end: date
    before_trades: int
    before_win_rate: float
    before_profit_factor: float
    before_expectancy: float
    before_total_pnl: float

    # After period
    after_start: date
    after_end: date
    after_trades: int
    after_win_rate: float
    after_profit_factor: float
    after_expectancy: float
    after_total_pnl: float

    # Statistical significance
    wr_p_value: float  # Two-sample proportion test for win rate
    impact_significant: bool  # p < 0.05

    # Regime change detection
    regime_changed: bool
    regime_note: str

    # Overall assessment
    assessment: str  # POSITIVE_IMPACT, NEGATIVE_IMPACT, NO_IMPACT, INCONCLUSIVE


class LessonImpactAnalyzer:
    """
    Analyzes lesson impact using before/after comparison.
    Phase 2A simplified approach - script-based, no automated table.
    """

    def __init__(self, db_path: str = None, trade_db_path: str = None):
        """
        Initialize analyzer.

        Args:
            db_path: Path to learning.db
            trade_db_path: Path to trading_performance.db
        """
        if db_path is None:
            # Use default path
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(base_dir, "ai_trader_data", "learning.db")

        if trade_db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            trade_db_path = os.path.join(base_dir, "trading_performance.db")

        self.learning_db = LearningDatabase(db_path)
        self.trade_db_path = trade_db_path

        # Grok's recommended windows (60 days, not 30)
        self.LOOKBACK_DAYS = 60
        self.MONITORING_DAYS = 60

        # Statistical threshold
        self.ALPHA = 0.05

        # Regime change threshold (SPY volatility shift)
        self.REGIME_CHANGE_THRESHOLD = 0.30  # 30% change in volatility

        # Minimum trades for meaningful analysis
        self.MIN_TRADES_BEFORE = 5
        self.MIN_TRADES_AFTER = 5

    def analyze_lesson_impact(
        self,
        lesson_id: int,
        as_of_date: date = None
    ) -> Optional[LessonImpactMetrics]:
        """
        Analyze impact of a specific lesson.

        Args:
            lesson_id: Lesson ID from learning.db
            as_of_date: Date to analyze from (default: today)

        Returns:
            LessonImpactMetrics or None if insufficient data
        """
        if as_of_date is None:
            as_of_date = date.today()

        # Get lesson details
        lesson = self._get_lesson_details(lesson_id)
        if not lesson:
            logger.warning(f"Lesson {lesson_id} not found")
            return None

        lesson_text, lesson_date, symbol, category = lesson

        # Define periods
        before_start = lesson_date - timedelta(days=self.LOOKBACK_DAYS)
        before_end = lesson_date - timedelta(days=1)
        after_start = lesson_date
        after_end = min(lesson_date + timedelta(days=self.MONITORING_DAYS), as_of_date)

        # Get matching trades for before/after periods
        before_trades = self._get_matching_trades(
            symbol, category, before_start, before_end
        )
        after_trades = self._get_matching_trades(
            symbol, category, after_start, after_end
        )

        # Check minimum trade counts
        if len(before_trades) < self.MIN_TRADES_BEFORE or len(after_trades) < self.MIN_TRADES_AFTER:
            logger.info(f"Insufficient trades for lesson {lesson_id}: before={len(before_trades)}, after={len(after_trades)}")
            return None

        # Calculate metrics for both periods
        before_metrics = self._calculate_metrics(before_trades)
        after_metrics = self._calculate_metrics(after_trades)

        # Statistical significance test (two-sample proportion test)
        wr_p_value = self._proportion_test(
            before_metrics['wins'], len(before_trades),
            after_metrics['wins'], len(after_trades)
        )
        impact_significant = wr_p_value < self.ALPHA

        # Regime change detection
        regime_changed, regime_note = self._detect_regime_change(before_start, after_end)

        # Overall assessment
        assessment = self._assess_impact(
            before_metrics, after_metrics, impact_significant, regime_changed
        )

        return LessonImpactMetrics(
            lesson_id=lesson_id,
            lesson_text=lesson_text,
            lesson_date=lesson_date,
            symbol=symbol,
            category=category,

            before_start=before_start,
            before_end=before_end,
            before_trades=len(before_trades),
            before_win_rate=before_metrics['win_rate'],
            before_profit_factor=before_metrics['profit_factor'],
            before_expectancy=before_metrics['expectancy'],
            before_total_pnl=before_metrics['total_pnl'],

            after_start=after_start,
            after_end=after_end,
            after_trades=len(after_trades),
            after_win_rate=after_metrics['win_rate'],
            after_profit_factor=after_metrics['profit_factor'],
            after_expectancy=after_metrics['expectancy'],
            after_total_pnl=after_metrics['total_pnl'],

            wr_p_value=wr_p_value,
            impact_significant=impact_significant,

            regime_changed=regime_changed,
            regime_note=regime_note,

            assessment=assessment
        )

    def analyze_all_recent_lessons(
        self,
        days_back: int = 90,
        as_of_date: date = None
    ) -> List[LessonImpactMetrics]:
        """
        Analyze impact of all lessons added in the last N days.

        Args:
            days_back: How far back to look for lessons
            as_of_date: Date to analyze from (default: today)

        Returns:
            List of LessonImpactMetrics (only lessons with sufficient data)
        """
        if as_of_date is None:
            as_of_date = date.today()

        cutoff_date = as_of_date - timedelta(days=days_back)

        # Get recent lessons
        recent_lessons = self._get_recent_lessons(cutoff_date)

        results = []
        for lesson_id, lesson_date in recent_lessons:
            # Only analyze if enough time has passed to collect after data
            if (as_of_date - lesson_date).days >= 7:  # At least 1 week of after data
                metrics = self.analyze_lesson_impact(lesson_id, as_of_date)
                if metrics:
                    results.append(metrics)

        return results

    def generate_summary_report(
        self,
        metrics_list: List[LessonImpactMetrics]
    ) -> str:
        """
        Generate human-readable summary report.

        Args:
            metrics_list: List of LessonImpactMetrics

        Returns:
            Formatted report string
        """
        if not metrics_list:
            return "No lesson impact data available."

        # Group by assessment
        positive = [m for m in metrics_list if m.assessment == 'POSITIVE_IMPACT']
        negative = [m for m in metrics_list if m.assessment == 'NEGATIVE_IMPACT']
        no_impact = [m for m in metrics_list if m.assessment == 'NO_IMPACT']
        inconclusive = [m for m in metrics_list if m.assessment == 'INCONCLUSIVE']

        report = []
        report.append("=" * 80)
        report.append("LESSON IMPACT ANALYSIS SUMMARY")
        report.append("=" * 80)
        report.append(f"Total lessons analyzed: {len(metrics_list)}")
        report.append(f"  - Positive impact: {len(positive)}")
        report.append(f"  - Negative impact: {len(negative)}")
        report.append(f"  - No significant impact: {len(no_impact)}")
        report.append(f"  - Inconclusive (regime changed): {len(inconclusive)}")
        report.append("")

        # Positive impacts (detailed)
        if positive:
            report.append("POSITIVE IMPACT LESSONS")
            report.append("-" * 80)
            for m in sorted(positive, key=lambda x: x.after_win_rate - x.before_win_rate, reverse=True):
                report.append(f"Lesson #{m.lesson_id} - {m.lesson_date}")
                report.append(f"  Category: {m.category}" + (f" | Symbol: {m.symbol}" if m.symbol else ""))
                report.append(f"  Text: {m.lesson_text[:80]}...")
                report.append(f"  Win Rate: {m.before_win_rate:.1%} -> {m.after_win_rate:.1%} (+{m.after_win_rate - m.before_win_rate:.1%})")
                report.append(f"  Profit Factor: {m.before_profit_factor:.2f} -> {m.after_profit_factor:.2f}")
                report.append(f"  Trades: {m.before_trades} before, {m.after_trades} after")
                report.append(f"  Statistical significance: p={m.wr_p_value:.4f}")
                if m.regime_changed:
                    report.append(f"  Note: {m.regime_note}")
                report.append("")

        # Negative impacts (warning)
        if negative:
            report.append("NEGATIVE IMPACT LESSONS (WARNING)")
            report.append("-" * 80)
            for m in sorted(negative, key=lambda x: x.before_win_rate - x.after_win_rate, reverse=True):
                report.append(f"Lesson #{m.lesson_id} - {m.lesson_date}")
                report.append(f"  Category: {m.category}" + (f" | Symbol: {m.symbol}" if m.symbol else ""))
                report.append(f"  Text: {m.lesson_text[:80]}...")
                report.append(f"  Win Rate: {m.before_win_rate:.1%} -> {m.after_win_rate:.1%} ({m.after_win_rate - m.before_win_rate:.1%})")
                report.append(f"  Profit Factor: {m.before_profit_factor:.2f} -> {m.after_profit_factor:.2f}")
                report.append(f"  Trades: {m.before_trades} before, {m.after_trades} after")
                report.append(f"  Statistical significance: p={m.wr_p_value:.4f}")
                if m.regime_changed:
                    report.append(f"  Note: {m.regime_note}")
                report.append("")

        # Inconclusive (regime changed)
        if inconclusive:
            report.append("INCONCLUSIVE (REGIME CHANGED)")
            report.append("-" * 80)
            for m in inconclusive:
                report.append(f"Lesson #{m.lesson_id} - {m.lesson_date}: {m.regime_note}")
            report.append("")

        return "\n".join(report)

    def _get_lesson_details(self, lesson_id: int) -> Optional[Tuple[str, date, Optional[str], str]]:
        """Get lesson details from learning.db."""
        lessons = self.learning_db.get_lessons(
            limit=None,
            min_confidence=0.0,
            active_only=False,
            category=None,
            symbol=None
        )

        for lesson in lessons:
            if lesson.get('id') == lesson_id:
                # Parse date from created_at
                created_str = lesson.get('created_at', '')
                try:
                    lesson_date = datetime.fromisoformat(created_str).date()
                except:
                    lesson_date = date.today()

                return (
                    lesson.get('lesson', ''),
                    lesson_date,
                    lesson.get('symbol'),
                    lesson.get('category', 'general')
                )

        return None

    def _get_recent_lessons(self, cutoff_date: date) -> List[Tuple[int, date]]:
        """Get lesson IDs and dates for lessons added after cutoff_date."""
        lessons = self.learning_db.get_lessons(
            limit=None,
            min_confidence=0.0,
            active_only=False,
            category=None,
            symbol=None
        )

        recent = []
        for lesson in lessons:
            created_str = lesson.get('created_at', '')
            try:
                lesson_date = datetime.fromisoformat(created_str).date()
                if lesson_date >= cutoff_date:
                    recent.append((lesson.get('id'), lesson_date))
            except:
                continue

        return recent

    def _get_matching_trades(
        self,
        symbol: Optional[str],
        category: str,
        start_date: date,
        end_date: date
    ) -> List[Dict]:
        """
        Get trades matching lesson scope.

        Args:
            symbol: Symbol-specific lesson (or None for general)
            category: Lesson category (entry_signal, exit_timing, etc.)
            start_date: Start of period
            end_date: End of period

        Returns:
            List of trade dicts with net_pnl
        """
        conn = sqlite3.connect(self.trade_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Build query based on lesson scope
        if symbol:
            # Symbol-specific lesson
            query = """
                SELECT entry_time, exit_time, net_pnl, symbol
                FROM trade_attributes
                WHERE symbol = ?
                  AND date(entry_time) >= ?
                  AND date(entry_time) <= ?
                  AND exit_time IS NOT NULL
                ORDER BY entry_time
            """
            params = (symbol, start_date.isoformat(), end_date.isoformat())
        else:
            # Category-based or general lesson - all trades
            query = """
                SELECT entry_time, exit_time, net_pnl, symbol
                FROM trade_attributes
                WHERE date(entry_time) >= ?
                  AND date(entry_time) <= ?
                  AND exit_time IS NOT NULL
                ORDER BY entry_time
            """
            params = (start_date.isoformat(), end_date.isoformat())

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        trades = [dict(row) for row in rows]
        return trades

    def _calculate_metrics(self, trades: List[Dict]) -> Dict:
        """Calculate performance metrics for a set of trades."""
        if not trades:
            return {
                'wins': 0,
                'losses': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'expectancy': 0.0,
                'total_pnl': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0
            }

        wins = [t['net_pnl'] for t in trades if t['net_pnl'] > 0]
        losses = [t['net_pnl'] for t in trades if t['net_pnl'] <= 0]

        num_wins = len(wins)
        num_losses = len(losses)
        total_trades = len(trades)

        win_rate = num_wins / total_trades if total_trades > 0 else 0.0

        total_wins = sum(wins) if wins else 0.0
        total_losses = abs(sum(losses)) if losses else 0.0

        profit_factor = total_wins / total_losses if total_losses > 0 else (float('inf') if total_wins > 0 else 0.0)

        avg_win = total_wins / num_wins if num_wins > 0 else 0.0
        avg_loss = total_losses / num_losses if num_losses > 0 else 0.0

        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        total_pnl = sum(t['net_pnl'] for t in trades)

        return {
            'wins': num_wins,
            'losses': num_losses,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'expectancy': expectancy,
            'total_pnl': total_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss
        }

    def _proportion_test(
        self,
        wins_before: int, total_before: int,
        wins_after: int, total_after: int
    ) -> float:
        """
        Two-sample proportion test for win rates.

        H0: win_rate_before = win_rate_after
        H1: win_rate_before != win_rate_after

        Returns:
            p-value
        """
        from scipy.stats import chi2_contingency

        # Contingency table
        # [[wins_before, losses_before],
        #  [wins_after, losses_after]]

        losses_before = total_before - wins_before
        losses_after = total_after - wins_after

        contingency = [
            [wins_before, losses_before],
            [wins_after, losses_after]
        ]

        try:
            chi2, p_value, dof, expected = chi2_contingency(contingency)
            return p_value
        except:
            # Fallback if sample too small
            return 1.0

    def _detect_regime_change(
        self,
        start_date: date,
        end_date: date
    ) -> Tuple[bool, str]:
        """
        Detect if market regime changed between before/after periods.

        Uses average VIX from daily_market_conditions as volatility proxy.
        Grok's threshold: >30% change in average VIX (self.REGIME_CHANGE_THRESHOLD).

        Splits the analysis window at the midpoint and compares first-half
        vs second-half average VIX. Falls back to (False, reason) if data
        is unavailable rather than silently assuming no regime change.

        Args:
            start_date: Start of analysis period (before_start)
            end_date: End of analysis period (after_end)

        Returns:
            (regime_changed: bool, note: str)
        """
        try:
            conn = sqlite3.connect(self.trade_db_path)
            c = conn.cursor()
            c.execute(
                """
                SELECT trade_date, vix_open
                FROM daily_market_conditions
                WHERE trade_date >= ? AND trade_date <= ? AND vix_open IS NOT NULL
                ORDER BY trade_date
                """,
                (start_date.isoformat(), end_date.isoformat()),
            )
            rows = c.fetchall()
            conn.close()

            if len(rows) < 4:
                return False, f"Insufficient VIX data ({len(rows)} days) - regime detection skipped"

            # Compare first half vs second half of the analysis window
            mid = len(rows) // 2
            before_vix = [r[1] for r in rows[:mid]]
            after_vix = [r[1] for r in rows[mid:]]

            before_avg = sum(before_vix) / len(before_vix)
            after_avg = sum(after_vix) / len(after_vix)

            if before_avg == 0:
                return False, "Invalid VIX data (zero before-period average)"

            change_pct = abs(after_avg - before_avg) / before_avg

            if change_pct >= self.REGIME_CHANGE_THRESHOLD:
                return True, (
                    f"VIX regime shift: {before_avg:.1f} -> {after_avg:.1f} "
                    f"({change_pct * 100:.0f}% change, threshold {self.REGIME_CHANGE_THRESHOLD * 100:.0f}%)"
                )
            else:
                return False, (
                    f"No regime shift: VIX {before_avg:.1f} -> {after_avg:.1f} "
                    f"({change_pct * 100:.0f}% change)"
                )

        except Exception as exc:
            logger.warning(f"[LessonImpact] Regime detection error: {exc}")
            return False, f"Regime detection error: {exc}"

    def _assess_impact(
        self,
        before_metrics: Dict,
        after_metrics: Dict,
        significant: bool,
        regime_changed: bool
    ) -> str:
        """
        Overall assessment of lesson impact.

        Returns:
            'POSITIVE_IMPACT', 'NEGATIVE_IMPACT', 'NO_IMPACT', or 'INCONCLUSIVE'
        """
        if regime_changed:
            return 'INCONCLUSIVE'

        if not significant:
            return 'NO_IMPACT'

        # Check if performance improved
        wr_improved = after_metrics['win_rate'] > before_metrics['win_rate']
        pf_improved = after_metrics['profit_factor'] > before_metrics['profit_factor']

        # Both improved = positive
        if wr_improved and pf_improved:
            return 'POSITIVE_IMPACT'

        # Both declined = negative
        if not wr_improved and not pf_improved:
            return 'NEGATIVE_IMPACT'

        # Mixed = use profit factor as tiebreaker (Grok's primary metric)
        if pf_improved:
            return 'POSITIVE_IMPACT'
        else:
            return 'NEGATIVE_IMPACT'


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    analyzer = LessonImpactAnalyzer()

    # Analyze all recent lessons (last 90 days)
    results = analyzer.analyze_all_recent_lessons(days_back=90)

    # Generate and print report
    report = analyzer.generate_summary_report(results)
    print(report)

    # Save report to file
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "ai_trader_data",
        "performance",
        "lesson_impact"
    )
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, f"lesson_impact_{date.today().isoformat()}.txt")
    with open(output_file, 'w') as f:
        f.write(report)

    print(f"\nReport saved to: {output_file}")
