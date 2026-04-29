"""
Test script for Performance Attribution Phase 2A components.

This script tests all Phase 2A components with mock/historical data:
1. AttributionAnalyzer - pattern validation queries
2. LessonImpactAnalyzer - before/after analysis
3. WeeklyAttributionReport - report generation
4. EOD integration - pattern context injection

Run this BEFORE the first Friday to ensure everything works.
"""

import os
import sys
import logging
from datetime import date, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_attribution_analyzer():
    """Test AttributionAnalyzer with real database."""
    logger.info("=" * 80)
    logger.info("TEST 1: Attribution Analyzer")
    logger.info("=" * 80)

    try:
        from analytics.attribution_analyzer import AttributionAnalyzer

        analyzer = AttributionAnalyzer()

        # Get validated patterns
        patterns = analyzer.get_validated_patterns(
            period_end=date.today(),
            top_n=10
        )

        logger.info(f"Found {len(patterns)} patterns")

        for i, pattern in enumerate(patterns, 1):
            logger.info(f"\nPattern {i}:")
            logger.info(f"  Key: {pattern.pattern_key}")
            logger.info(f"  Status: {pattern.status}")
            logger.info(f"  Trades: {pattern.total_trades}")
            logger.info(f"  Win Rate: {pattern.win_rate:.1%}")
            logger.info(f"  Profit Factor: {pattern.profit_factor:.2f}")
            logger.info(f"  Expectancy: ${pattern.expectancy:.2f}")

        # Test pattern context for EOD
        context = analyzer.get_pattern_context_for_eod(
            today_symbols=["TEST", "AAPL"],  # Mock symbols
            top_n=5
        )

        if context:
            logger.info("\nPattern Context for EOD:")
            logger.info(context)
        else:
            logger.info("\nNo pattern context available (expected if <30 trades)")

        logger.info("\n✅ Attribution Analyzer test passed")
        return True

    except Exception as e:
        logger.error(f"❌ Attribution Analyzer test failed: {e}", exc_info=True)
        return False


def test_lesson_impact_analyzer():
    """Test LessonImpactAnalyzer with real database."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: Lesson Impact Analyzer")
    logger.info("=" * 80)

    try:
        from analytics.lesson_impact_report import LessonImpactAnalyzer

        analyzer = LessonImpactAnalyzer()

        # Analyze recent lessons (last 90 days)
        results = analyzer.analyze_all_recent_lessons(days_back=90)

        logger.info(f"Analyzed {len(results)} lessons with sufficient data")

        for result in results[:3]:  # Show first 3
            logger.info(f"\nLesson #{result.lesson_id}:")
            logger.info(f"  Date: {result.lesson_date}")
            logger.info(f"  Category: {result.category}")
            logger.info(f"  Before: {result.before_trades} trades, {result.before_win_rate:.1%} WR")
            logger.info(f"  After: {result.after_trades} trades, {result.after_win_rate:.1%} WR")
            logger.info(f"  Assessment: {result.assessment}")
            logger.info(f"  Significant: {result.impact_significant} (p={result.wr_p_value:.4f})")

        # Generate summary report
        if results:
            report = analyzer.generate_summary_report(results)
            logger.info("\nLesson Impact Summary:")
            logger.info(report[:500] + "...")  # First 500 chars
        else:
            logger.info("\nNo lesson impacts to report (expected if <5 trades before/after)")

        logger.info("\n✅ Lesson Impact Analyzer test passed")
        return True

    except Exception as e:
        logger.error(f"❌ Lesson Impact Analyzer test failed: {e}", exc_info=True)
        return False


def test_weekly_report():
    """Test WeeklyAttributionReport with real database."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: Weekly Attribution Report")
    logger.info("=" * 80)

    try:
        from analytics.weekly_attribution_report import WeeklyAttributionReport

        reporter = WeeklyAttributionReport()

        # Find most recent Friday
        today = date.today()
        days_since_friday = (today.weekday() - 4) % 7
        friday = today - timedelta(days=days_since_friday)

        logger.info(f"Generating report for week ending {friday}")

        # Generate report (dry run - don't save)
        report = reporter.generate_weekly_report(
            week_end_date=friday,
            save_to_file=False  # Don't save during test
        )

        logger.info("\nWeekly Report Preview:")
        logger.info(report[:1000] + "...")  # First 1000 chars

        # Generate Discord summary
        summary = reporter.generate_discord_summary(friday)

        logger.info("\nDiscord Summary:")
        logger.info(summary)

        logger.info("\n✅ Weekly Report test passed")
        return True

    except Exception as e:
        logger.error(f"❌ Weekly Report test failed: {e}", exc_info=True)
        return False


def test_eod_pattern_context():
    """Test EOD pattern context injection."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 4: EOD Pattern Context Integration")
    logger.info("=" * 80)

    try:
        # This test just verifies the method exists and doesn't crash
        # Full integration test requires running EOD generator

        from analytics.eod_lesson_generator import EODLessonGenerator

        generator = EODLessonGenerator()

        # Create mock trade context
        from analytics.eod_lesson_generator import TradeContext

        mock_trades = [
            TradeContext(
                symbol="TEST",
                entry_time="2026-02-12 09:30:00",
                exit_time="2026-02-12 10:00:00",
                entry_price=100.0,
                exit_price=102.0,
                quantity=10,
                pnl=20.0,
                pnl_pct=2.0,
                exit_reason="take_profit",
                conviction=8,
                entry_rationale="Test trade",
                stop_price=98.0,
                target_price=102.0
            )
        ]

        # Test pattern context method
        context = generator._get_pattern_context(date.today(), mock_trades)

        if context:
            logger.info("Pattern context generated:")
            logger.info(context[:500] + "...")
        else:
            logger.info("No pattern context (expected if <30 trades in database)")

        logger.info("\n✅ EOD Pattern Context test passed")
        return True

    except Exception as e:
        logger.error(f"❌ EOD Pattern Context test failed: {e}", exc_info=True)
        return False


def main():
    """Run all tests."""
    logger.info("=" * 80)
    logger.info("PERFORMANCE ATTRIBUTION PHASE 2A - TEST SUITE")
    logger.info("=" * 80)
    logger.info("")

    results = {
        'Attribution Analyzer': test_attribution_analyzer(),
        'Lesson Impact Analyzer': test_lesson_impact_analyzer(),
        'Weekly Report': test_weekly_report(),
        'EOD Pattern Context': test_eod_pattern_context()
    }

    logger.info("\n" + "=" * 80)
    logger.info("TEST RESULTS SUMMARY")
    logger.info("=" * 80)

    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"{test_name:30s}: {status}")

    all_passed = all(results.values())
    logger.info("")

    if all_passed:
        logger.info("🎉 ALL TESTS PASSED - Phase 2A is ready!")
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Wait for Friday Feb 14, 2026 (need 3 days of trade data)")
        logger.info("2. Weekly report will auto-generate at 4:15 PM Friday")
        logger.info("3. EOD lesson generator will use pattern context daily")
        logger.info("4. Review first weekly report for insights")
    else:
        logger.info("⚠️  SOME TESTS FAILED - Review errors above")
        logger.info("")
        logger.info("Note: Some failures are expected if database has <30 trades")
        logger.info("Phase 2A components will work once sufficient data is collected")

    logger.info("=" * 80)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
