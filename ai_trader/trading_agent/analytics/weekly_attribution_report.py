"""
Weekly Attribution Report Generator - Phase 2A

Generates comprehensive weekly deep dive reports every Friday at 4:15 PM.
Per Grok's recommendation: weekly cadence (not daily) to reduce noise.

Report structure:
1. Overall performance (week vs prior week)
2. Attribution breakdown (all 6 dimensions)
3. Symbol performance (top 3 winners/losers)
4. Pattern validation updates
5. Lesson impact summary
6. Execution quality
7. Recommendations (top 3 insights)
"""

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sqlite3
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics.attribution_analyzer import AttributionAnalyzer, PatternMetrics
from analytics.lesson_impact_report import LessonImpactAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class WeeklyPerformance:
    """Overall weekly performance summary."""
    week_start: date
    week_end: date
    total_trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    total_pnl: float
    avg_pnl_per_trade: float

    # Execution quality
    avg_slippage_bps: float
    total_commissions: float

    # Comparison to prior week
    prior_week_trades: int
    prior_week_win_rate: float
    prior_week_pnl: float

    wr_change: float  # percentage points
    pnl_change: float  # dollars


@dataclass
class WeeklyInsight:
    """Actionable insight from weekly analysis."""
    priority: int  # 1-3
    category: str  # 'STRENGTH', 'WEAKNESS', 'OPPORTUNITY', 'RISK'
    title: str
    description: str
    recommendation: str


class WeeklyAttributionReport:
    """
    Generates weekly deep dive reports.
    Phase 2A approach - focused on top insights, minimal noise.
    """

    def __init__(self, db_path: str = None, trade_db_path: str = None):
        """
        Initialize report generator.

        Args:
            db_path: Path to learning.db
            trade_db_path: Path to trading_performance.db
        """
        if trade_db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            trade_db_path = os.path.join(base_dir, "trading_performance.db")

        self.trade_db_path = trade_db_path
        self.attribution_analyzer = AttributionAnalyzer(db_path=trade_db_path)
        self.lesson_analyzer = LessonImpactAnalyzer(db_path=db_path, trade_db_path=trade_db_path)

    def generate_weekly_report(
        self,
        week_end_date: date = None,
        save_to_file: bool = True
    ) -> str:
        """
        Generate comprehensive weekly report.

        Args:
            week_end_date: Friday of the week to report on (default: most recent Friday)
            save_to_file: Whether to save report to file

        Returns:
            Formatted report string
        """
        if week_end_date is None:
            # Default to most recent Friday
            today = date.today()
            days_since_friday = (today.weekday() - 4) % 7
            week_end_date = today - timedelta(days=days_since_friday)

        # Week boundaries
        week_start = week_end_date - timedelta(days=4)  # Monday
        prior_week_end = week_start - timedelta(days=1)  # Prior Friday
        prior_week_start = prior_week_end - timedelta(days=4)  # Prior Monday

        logger.info(f"Generating weekly report for {week_start} to {week_end_date}")

        # 1. Overall performance
        overall = self._get_overall_performance(
            week_start, week_end_date,
            prior_week_start, prior_week_end
        )

        # 2. Attribution breakdown
        attribution_breakdown = self._get_attribution_breakdown(week_start, week_end_date)

        # 3. Symbol performance
        symbol_performance = self._get_symbol_performance(week_start, week_end_date)

        # 4. Pattern validation updates
        pattern_updates = self._get_pattern_updates(week_start, week_end_date)

        # 5. Lesson impact summary
        lesson_impacts = self._get_lesson_impacts(week_start, week_end_date)

        # 6. Execution quality
        execution_quality = self._get_execution_quality(week_start, week_end_date)

        # 7. Generate insights and recommendations
        insights = self._generate_insights(
            overall, attribution_breakdown, symbol_performance,
            pattern_updates, lesson_impacts, execution_quality
        )

        # Build report
        report = self._format_report(
            overall, attribution_breakdown, symbol_performance,
            pattern_updates, lesson_impacts, execution_quality, insights
        )

        # Save to file
        if save_to_file:
            self._save_report(report, week_end_date)

        return report

    def generate_discord_summary(
        self,
        week_end_date: date = None
    ) -> str:
        """
        Generate short executive summary for Discord.
        Top 3 insights + recommendations only.

        Args:
            week_end_date: Friday of the week to report on

        Returns:
            Short summary string
        """
        if week_end_date is None:
            today = date.today()
            days_since_friday = (today.weekday() - 4) % 7
            week_end_date = today - timedelta(days=days_since_friday)

        week_start = week_end_date - timedelta(days=4)

        # Get overall performance
        overall = self._get_overall_performance(
            week_start, week_end_date,
            week_start - timedelta(days=7), week_start - timedelta(days=1)
        )

        # Get attribution and insights
        attribution_breakdown = self._get_attribution_breakdown(week_start, week_end_date)
        pattern_updates = self._get_pattern_updates(week_start, week_end_date)
        insights = self._generate_insights(
            overall, attribution_breakdown, {}, pattern_updates, [], {}
        )

        # Build summary
        summary = []
        summary.append(f"**WEEKLY PERFORMANCE SUMMARY** ({week_start} to {week_end_date})")
        summary.append("")
        summary.append(f"Trades: {overall.total_trades} | WR: {overall.win_rate:.1%} | P&L: ${overall.total_pnl:+.2f}")

        if overall.prior_week_trades > 0:
            summary.append(f"vs Prior Week: WR {overall.wr_change:+.1%} pp | P&L ${overall.pnl_change:+.2f}")

        summary.append("")
        summary.append("**TOP INSIGHTS:**")

        for insight in insights[:3]:  # Top 3 only
            emoji = {
                'STRENGTH': ':white_check_mark:',
                'WEAKNESS': ':warning:',
                'OPPORTUNITY': ':bulb:',
                'RISK': ':red_circle:'
            }.get(insight.category, '')

            summary.append(f"{emoji} **{insight.title}**")
            summary.append(f"  {insight.recommendation}")

        return "\n".join(summary)

    def _get_overall_performance(
        self,
        week_start: date, week_end: date,
        prior_week_start: date, prior_week_end: date
    ) -> WeeklyPerformance:
        """Get overall weekly performance metrics."""
        conn = sqlite3.connect(self.trade_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Current week
        query = """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END) as total_wins,
                SUM(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) ELSE 0 END) as total_losses,
                SUM(net_pnl) as total_pnl,
                AVG(slippage_bps) as avg_slippage,
                SUM(commission) as total_commission
            FROM trade_attributes
            WHERE date(entry_time) >= ? AND date(entry_time) <= ?
              AND exit_time IS NOT NULL
        """
        cursor.execute(query, (week_start.isoformat(), week_end.isoformat()))
        row = cursor.fetchone()

        total_trades = row['total_trades'] or 0
        wins = row['wins'] or 0
        total_wins = row['total_wins'] or 0.0
        total_losses = row['total_losses'] or 0.0
        total_pnl = row['total_pnl'] or 0.0
        avg_slippage = row['avg_slippage'] or 0.0
        total_commission = row['total_commission'] or 0.0

        win_rate = wins / total_trades if total_trades > 0 else 0.0
        profit_factor = total_wins / total_losses if total_losses > 0 else (float('inf') if total_wins > 0 else 0.0)

        losses = total_trades - wins
        avg_win = total_wins / wins if wins > 0 else 0.0
        avg_loss = total_losses / losses if losses > 0 else 0.0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

        # Prior week
        cursor.execute(query, (prior_week_start.isoformat(), prior_week_end.isoformat()))
        prior_row = cursor.fetchone()

        prior_trades = prior_row['total_trades'] or 0
        prior_wins = prior_row['wins'] or 0
        prior_pnl = prior_row['total_pnl'] or 0.0
        prior_win_rate = prior_wins / prior_trades if prior_trades > 0 else 0.0

        conn.close()

        return WeeklyPerformance(
            week_start=week_start,
            week_end=week_end,
            total_trades=total_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            total_pnl=total_pnl,
            avg_pnl_per_trade=avg_pnl,
            avg_slippage_bps=avg_slippage,
            total_commissions=total_commission,
            prior_week_trades=prior_trades,
            prior_week_win_rate=prior_win_rate,
            prior_week_pnl=prior_pnl,
            wr_change=win_rate - prior_win_rate,
            pnl_change=total_pnl - prior_pnl
        )

    def _get_attribution_breakdown(
        self,
        week_start: date,
        week_end: date
    ) -> Dict[str, List[Tuple[str, float, float, int]]]:
        """
        Get attribution breakdown for all 6 dimensions.

        Returns:
            Dict mapping dimension name to list of (value, win_rate, pnl, trades)
        """
        conn = sqlite3.connect(self.trade_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        dimensions = [
            'strategy_type',
            'entry_time_bucket',
            'market_regime',
            'conviction_level',
            'exit_type',
            'symbol'
        ]

        breakdown = {}

        for dim in dimensions:
            query = f"""
                SELECT
                    {dim} as value,
                    COUNT(*) as trades,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(net_pnl) as pnl
                FROM trade_attributes
                WHERE date(entry_time) >= ? AND date(entry_time) <= ?
                  AND exit_time IS NOT NULL
                GROUP BY {dim}
                ORDER BY pnl DESC
            """
            cursor.execute(query, (week_start.isoformat(), week_end.isoformat()))
            rows = cursor.fetchall()

            results = []
            for row in rows:
                value = row['value'] or 'unknown'
                trades = row['trades']
                wins = row['wins']
                pnl = row['pnl'] or 0.0
                win_rate = wins / trades if trades > 0 else 0.0
                results.append((value, win_rate, pnl, trades))

            breakdown[dim] = results

        conn.close()
        return breakdown

    def _get_symbol_performance(
        self,
        week_start: date,
        week_end: date
    ) -> Dict[str, List[Tuple[str, float, float, int]]]:
        """Get top 3 winners and losers by symbol."""
        conn = sqlite3.connect(self.trade_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
            SELECT
                symbol,
                COUNT(*) as trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(net_pnl) as pnl
            FROM trade_attributes
            WHERE date(entry_time) >= ? AND date(entry_time) <= ?
              AND exit_time IS NOT NULL
            GROUP BY symbol
            ORDER BY pnl DESC
        """
        cursor.execute(query, (week_start.isoformat(), week_end.isoformat()))
        rows = cursor.fetchall()

        all_symbols = []
        for row in rows:
            symbol = row['symbol']
            trades = row['trades']
            wins = row['wins']
            pnl = row['pnl'] or 0.0
            win_rate = wins / trades if trades > 0 else 0.0
            all_symbols.append((symbol, win_rate, pnl, trades))

        # Top 3 winners, bottom 3 losers
        winners = sorted(all_symbols, key=lambda x: x[2], reverse=True)[:3]
        losers = sorted(all_symbols, key=lambda x: x[2])[:3]

        conn.close()

        return {
            'winners': winners,
            'losers': losers
        }

    def _get_pattern_updates(
        self,
        week_start: date,
        week_end: date
    ) -> Dict[str, List[PatternMetrics]]:
        """Get pattern validation updates during the week."""
        # Get all validated patterns as of week end
        patterns = self.attribution_analyzer.get_validated_patterns(
            period_end=week_end,
            top_n=50  # Get all patterns, we'll filter later
        )

        # Classify by status
        newly_validated = []
        still_validated = []
        degraded = []

        for pattern in patterns:
            if pattern.status == 'VALIDATED':
                # Check if it was validated during this week
                # (This is simplified - in production, track state changes)
                if pattern.total_trades <= 35:  # Likely new
                    newly_validated.append(pattern)
                else:
                    still_validated.append(pattern)
            elif pattern.status == 'DEGRADED':
                degraded.append(pattern)

        return {
            'newly_validated': newly_validated[:5],  # Top 5
            'degraded': degraded,
            'total_validated': len([p for p in patterns if p.status == 'VALIDATED'])
        }

    def _get_lesson_impacts(
        self,
        week_start: date,
        week_end: date
    ) -> List:
        """Get lesson impact summaries."""
        # Analyze lessons from last 30 days
        impacts = self.lesson_analyzer.analyze_all_recent_lessons(
            days_back=30,
            as_of_date=week_end
        )

        # Filter to significant impacts only
        significant = [
            imp for imp in impacts
            if imp.impact_significant and imp.assessment in ['POSITIVE_IMPACT', 'NEGATIVE_IMPACT']
        ]

        return significant[:5]  # Top 5

    def _get_execution_quality(
        self,
        week_start: date,
        week_end: date
    ) -> Dict:
        """Get execution quality metrics."""
        conn = sqlite3.connect(self.trade_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
            SELECT
                AVG(slippage_bps) as avg_slippage,
                MAX(slippage_bps) as max_slippage,
                SUM(commission) as total_commission,
                COUNT(*) as total_trades
            FROM trade_attributes
            WHERE date(entry_time) >= ? AND date(entry_time) <= ?
              AND exit_time IS NOT NULL
        """
        cursor.execute(query, (week_start.isoformat(), week_end.isoformat()))
        row = cursor.fetchone()
        conn.close()

        return {
            'avg_slippage_bps': row['avg_slippage'] or 0.0,
            'max_slippage_bps': row['max_slippage'] or 0.0,
            'total_commission': row['total_commission'] or 0.0,
            'total_trades': row['total_trades'] or 0
        }

    def _generate_insights(
        self,
        overall: WeeklyPerformance,
        attribution: Dict,
        symbols: Dict,
        patterns: Dict,
        lessons: List,
        execution: Dict
    ) -> List[WeeklyInsight]:
        """
        Generate top 3 actionable insights.
        Uses deterministic rules (not LLM) per Grok's Phase 2A guidance.
        """
        insights = []

        # 1. Check overall performance trend
        if overall.prior_week_trades > 0:
            if overall.wr_change > 0.10:  # Win rate improved >10pp
                insights.append(WeeklyInsight(
                    priority=1,
                    category='STRENGTH',
                    title=f"Win rate improved {overall.wr_change:.1%} pp week-over-week",
                    description=f"WR increased from {overall.prior_week_win_rate:.1%} to {overall.win_rate:.1%}",
                    recommendation="Continue current approach - strategy is working well"
                ))
            elif overall.wr_change < -0.10:  # Win rate declined >10pp
                insights.append(WeeklyInsight(
                    priority=1,
                    category='WEAKNESS',
                    title=f"Win rate declined {abs(overall.wr_change):.1%} pp week-over-week",
                    description=f"WR dropped from {overall.prior_week_win_rate:.1%} to {overall.win_rate:.1%}",
                    recommendation="Review recent trades for pattern changes or regime shift"
                ))

        # 2. Check strategy performance
        if 'strategy_type' in attribution and attribution['strategy_type']:
            best_strategy = max(attribution['strategy_type'], key=lambda x: x[2])  # Max P&L
            worst_strategy = min(attribution['strategy_type'], key=lambda x: x[2])

            if best_strategy[3] >= 3:  # At least 3 trades
                insights.append(WeeklyInsight(
                    priority=2,
                    category='STRENGTH',
                    title=f"{best_strategy[0]} strategy performed best",
                    description=f"{best_strategy[3]} trades, {best_strategy[1]:.1%} WR, ${best_strategy[2]:+.2f} P&L",
                    recommendation=f"Consider increasing allocation to {best_strategy[0]} setups"
                ))

        # 3. Check for newly validated patterns
        if patterns.get('newly_validated'):
            pattern = patterns['newly_validated'][0]
            insights.append(WeeklyInsight(
                priority=1,
                category='OPPORTUNITY',
                title=f"New pattern validated: {pattern.pattern_key}",
                description=f"{pattern.total_trades} trades, {pattern.win_rate:.1%} WR, PF {pattern.profit_factor:.2f}",
                recommendation="Increase confidence in this pattern going forward"
            ))

        # 4. Check for degraded patterns
        if patterns.get('degraded'):
            pattern = patterns['degraded'][0]
            insights.append(WeeklyInsight(
                priority=1,
                category='RISK',
                title=f"Pattern degrading: {pattern.pattern_key}",
                description=f"Win rate: {pattern.win_rate:.1%}, Profit factor: {pattern.profit_factor:.2f}",
                recommendation="Reduce exposure to this pattern until performance recovers"
            ))

        # 5. Check execution quality
        if execution['avg_slippage_bps'] > 10:  # High slippage
            insights.append(WeeklyInsight(
                priority=2,
                category='RISK',
                title=f"High average slippage: {execution['avg_slippage_bps']:.1f} bps",
                description=f"Max slippage: {execution['max_slippage_bps']:.1f} bps",
                recommendation="Review entry/exit timing or use limit orders more aggressively"
            ))

        # 6. Check lesson impacts
        if lessons:
            positive_lessons = [l for l in lessons if l.assessment == 'POSITIVE_IMPACT']
            if positive_lessons:
                lesson = positive_lessons[0]
                insights.append(WeeklyInsight(
                    priority=2,
                    category='STRENGTH',
                    title=f"Lesson #{lesson.lesson_id} showing positive impact",
                    description=f"WR improved from {lesson.before_win_rate:.1%} to {lesson.after_win_rate:.1%}",
                    recommendation="Continue applying this lesson's guidance"
                ))

        # Sort by priority and return top 5
        insights.sort(key=lambda x: x.priority)
        return insights[:5]

    def _format_report(
        self,
        overall: WeeklyPerformance,
        attribution: Dict,
        symbols: Dict,
        patterns: Dict,
        lessons: List,
        execution: Dict,
        insights: List[WeeklyInsight]
    ) -> str:
        """Format comprehensive report."""
        report = []
        report.append("=" * 100)
        report.append("WEEKLY PERFORMANCE ATTRIBUTION REPORT")
        report.append("=" * 100)
        report.append(f"Week: {overall.week_start} to {overall.week_end}")
        report.append("")

        # Section 1: Overall Performance
        report.append("1. OVERALL PERFORMANCE")
        report.append("-" * 100)
        report.append(f"Total Trades: {overall.total_trades}")
        report.append(f"Win Rate: {overall.win_rate:.1%}")
        report.append(f"Profit Factor: {overall.profit_factor:.2f}")
        report.append(f"Expectancy: ${overall.expectancy:.2f} per trade")
        report.append(f"Total P&L: ${overall.total_pnl:+.2f}")
        report.append(f"Avg P&L per Trade: ${overall.avg_pnl_per_trade:+.2f}")

        if overall.prior_week_trades > 0:
            report.append("")
            report.append("Week-over-Week Comparison:")
            report.append(f"  Win Rate: {overall.prior_week_win_rate:.1%} -> {overall.win_rate:.1%} ({overall.wr_change:+.1%} pp)")
            report.append(f"  P&L: ${overall.prior_week_pnl:+.2f} -> ${overall.total_pnl:+.2f} (${overall.pnl_change:+.2f})")

        report.append("")

        # Section 2: Attribution Breakdown
        report.append("2. ATTRIBUTION BREAKDOWN")
        report.append("-" * 100)

        for dim_name, results in attribution.items():
            if results:
                report.append(f"\n{dim_name.replace('_', ' ').title()}:")
                for value, wr, pnl, trades in results[:5]:  # Top 5 per dimension
                    report.append(f"  {value:20s}: {trades:3d} trades | {wr:5.1%} WR | ${pnl:+8.2f}")

        report.append("")

        # Section 3: Symbol Performance
        if symbols:
            report.append("3. SYMBOL PERFORMANCE")
            report.append("-" * 100)

            if symbols.get('winners'):
                report.append("\nTop Winners:")
                for symbol, wr, pnl, trades in symbols['winners']:
                    report.append(f"  {symbol:6s}: {trades:3d} trades | {wr:5.1%} WR | ${pnl:+8.2f}")

            if symbols.get('losers'):
                report.append("\nTop Losers:")
                for symbol, wr, pnl, trades in symbols['losers']:
                    report.append(f"  {symbol:6s}: {trades:3d} trades | {wr:5.1%} WR | ${pnl:+8.2f}")

            report.append("")

        # Section 4: Pattern Updates
        report.append("4. PATTERN VALIDATION UPDATES")
        report.append("-" * 100)
        report.append(f"Total Validated Patterns: {patterns.get('total_validated', 0)}")

        if patterns.get('newly_validated'):
            report.append("\nNewly Validated:")
            for p in patterns['newly_validated']:
                report.append(f"  {p.pattern_key}: {p.total_trades} trades | {p.win_rate:.1%} WR | PF {p.profit_factor:.2f}")

        if patterns.get('degraded'):
            report.append("\nDegraded Patterns (WARNING):")
            for p in patterns['degraded']:
                report.append(f"  {p.pattern_key}: {p.total_trades} trades | {p.win_rate:.1%} WR | PF {p.profit_factor:.2f}")

        report.append("")

        # Section 5: Lesson Impacts
        if lessons:
            report.append("5. LESSON IMPACT SUMMARY")
            report.append("-" * 100)
            for lesson in lessons:
                status = "POSITIVE" if lesson.assessment == 'POSITIVE_IMPACT' else "NEGATIVE"
                report.append(f"Lesson #{lesson.lesson_id} ({lesson.lesson_date}): {status}")
                report.append(f"  WR: {lesson.before_win_rate:.1%} -> {lesson.after_win_rate:.1%}")
                report.append(f"  PF: {lesson.before_profit_factor:.2f} -> {lesson.after_profit_factor:.2f}")
                report.append("")

        # Section 6: Execution Quality
        report.append("6. EXECUTION QUALITY")
        report.append("-" * 100)
        report.append(f"Average Slippage: {execution['avg_slippage_bps']:.2f} bps")
        report.append(f"Max Slippage: {execution['max_slippage_bps']:.2f} bps")
        report.append(f"Total Commissions: ${execution['total_commission']:.2f}")
        report.append("")

        # Section 7: Top Insights & Recommendations
        report.append("7. TOP INSIGHTS & RECOMMENDATIONS")
        report.append("-" * 100)
        for i, insight in enumerate(insights, 1):
            emoji = {
                'STRENGTH': '[+]',
                'WEAKNESS': '[-]',
                'OPPORTUNITY': '[!]',
                'RISK': '[X]'
            }.get(insight.category, '')

            report.append(f"\n{i}. {emoji} {insight.title}")
            report.append(f"   {insight.description}")
            report.append(f"   RECOMMENDATION: {insight.recommendation}")

        report.append("")
        report.append("=" * 100)
        report.append(f"Report generated: {datetime.now().isoformat()}")
        report.append("=" * 100)

        return "\n".join(report)

    def _save_report(self, report: str, week_end_date: date):
        """Save report to file."""
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ai_trader_data",
            "performance",
            "weekly"
        )
        os.makedirs(output_dir, exist_ok=True)

        # File naming: weekly_YYYY_MM_DD.txt
        filename = f"weekly_{week_end_date.isoformat().replace('-', '_')}.txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, 'w') as f:
            f.write(report)

        logger.info(f"Weekly report saved to: {filepath}")


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    reporter = WeeklyAttributionReport()

    # Generate report for most recent Friday
    report = reporter.generate_weekly_report()
    print(report)

    # Generate Discord summary
    print("\n" + "=" * 100)
    print("DISCORD SUMMARY:")
    print("=" * 100)
    summary = reporter.generate_discord_summary()
    print(summary)
