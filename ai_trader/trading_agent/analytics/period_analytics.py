"""
Period Analytics - Aggregate Daily Summaries into Longer Periods

Aggregates daily portfolio summaries into weekly, monthly, quarterly, and yearly reports.
Provides trend analysis, performance metrics, and comparative statistics.

Author: AI Day Trader Team
Date: January 21, 2026
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
import statistics

AI_TRADER_DATA = Path(__file__).parent.parent.parent / "ai_trader_data"

logger = logging.getLogger(__name__)


class PeriodAnalytics:
    """
    Aggregates daily summaries into longer reporting periods.
    """

    def __init__(self, summaries_dir: str = "ai_trader_data/performance/daily"):
        """
        Initialize period analytics.

        Args:
            summaries_dir: Directory containing daily summary JSON files
        """
        self.summaries_dir = Path(summaries_dir)
        self.summaries_dir.mkdir(parents=True, exist_ok=True)

    def load_daily_summaries(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """
        Load daily summaries for a date range.

        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)

        Returns:
            List of daily summary dictionaries
        """
        summaries = []

        # Generate expected date range
        current_date = start_date
        while current_date <= end_date:
            # Check in month_year subfolders first
            month_year = current_date.strftime('%B_%Y')  # e.g., "January_2026"
            month_dir = self.summaries_dir / month_year
            summary_file = month_dir / f"summary_{current_date.strftime('%Y_%m_%d')}.json"

            # If not found in month subfolder, check main directory (backward compatibility)
            if not summary_file.exists():
                summary_file = self.summaries_dir / f"summary_{current_date.strftime('%Y_%m_%d')}.json"

            if summary_file.exists():
                try:
                    with open(summary_file, 'r') as f:
                        summary = json.load(f)
                        summary['date'] = current_date.date()
                        summaries.append(summary)
                except Exception as e:
                    logger.warning(f"Error loading {summary_file}: {e}")
            else:
                logger.debug(f"No summary file for {current_date.date()}")

            current_date += timedelta(days=1)

        return summaries

    def aggregate_period(self, summaries: List[Dict], period_name: str) -> Dict[str, Any]:
        """
        Aggregate daily summaries into a period report.

        Args:
            summaries: List of daily summary dictionaries
            period_name: Name of the period (e.g., "Weekly", "Monthly")

        Returns:
            Aggregated period statistics
        """
        if not summaries:
            return {
                "period": period_name,
                "message": "No data available for this period",
                "total_days": 0,
                "trading_days": 0,
                "total_pnl": 0.0,
                "avg_daily_pnl": 0.0,
                "best_day": None,
                "worst_day": None,
                "win_days": 0,
                "loss_days": 0,
                "neutral_days": 0,
                "win_rate": 0.0,
                "total_trades": 0,
                "avg_trades_per_day": 0.0,
                "total_wins": 0,
                "total_losses": 0,
                "overall_win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0
            }

        # Basic aggregation
        total_pnl = sum(s.get('total_pnl', 0) for s in summaries)
        total_trades = sum(s.get('num_trades', 0) for s in summaries)
        total_wins = sum(s.get('winning_trades', 0) for s in summaries)
        total_losses = sum(s.get('losing_trades', 0) for s in summaries)

        trading_days = len([s for s in summaries if s.get('num_trades', 0) > 0])
        total_days = len(summaries)

        # Daily P&L analysis
        daily_pnls = [s.get('total_pnl', 0) for s in summaries]
        avg_daily_pnl = statistics.mean(daily_pnls) if daily_pnls else 0

        win_days = len([s for s in summaries if s.get('total_pnl', 0) > 0])
        loss_days = len([s for s in summaries if s.get('total_pnl', 0) < 0])
        neutral_days = len([s for s in summaries if s.get('total_pnl', 0) == 0])

        win_rate = (win_days / trading_days * 100) if trading_days > 0 else 0

        # Best/worst days
        best_day = max(summaries, key=lambda s: s.get('total_pnl', 0)) if summaries else None
        worst_day = min(summaries, key=lambda s: s.get('total_pnl', 0)) if summaries else None

        # Trade analysis
        avg_trades_per_day = total_trades / total_days if total_days > 0 else 0
        overall_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        # Win/loss amounts
        win_amounts = []
        loss_amounts = []

        for s in summaries:
            wins = s.get('winning_trades', 0)
            losses = s.get('losing_trades', 0)
            avg_win = s.get('avg_win', 0)
            avg_loss = s.get('avg_loss', 0)

            if wins > 0:
                win_amounts.extend([avg_win] * wins)
            if losses > 0:
                loss_amounts.extend([avg_loss] * losses)

        avg_win = statistics.mean(win_amounts) if win_amounts else 0
        avg_loss = abs(statistics.mean(loss_amounts)) if loss_amounts else 0

        # Profit factor
        total_win_amount = sum(win_amounts) if win_amounts else 0
        total_loss_amount = abs(sum(loss_amounts)) if loss_amounts else 0
        profit_factor = total_win_amount / total_loss_amount if total_loss_amount > 0 else float('inf')

        # Max drawdown calculation
        max_drawdown = self._calculate_max_drawdown(daily_pnls)

        # Sharpe ratio (simplified, assuming 0% risk-free rate)
        if len(daily_pnls) > 1 and statistics.stdev(daily_pnls) > 0:
            sharpe_ratio = statistics.mean(daily_pnls) / statistics.stdev(daily_pnls) * (252 ** 0.5)  # Annualized
        else:
            sharpe_ratio = 0

        return {
            "period": period_name,
            "total_days": total_days,
            "trading_days": trading_days,
            "total_pnl": round(total_pnl, 2),
            "avg_daily_pnl": round(avg_daily_pnl, 2),
            "best_day": {
                "date": best_day['date'].isoformat() if best_day else None,
                "pnl": round(best_day.get('total_pnl', 0), 2) if best_day else 0
            } if best_day else None,
            "worst_day": {
                "date": worst_day['date'].isoformat() if worst_day else None,
                "pnl": round(worst_day.get('total_pnl', 0), 2) if worst_day else 0
            } if worst_day else None,
            "win_days": win_days,
            "loss_days": loss_days,
            "neutral_days": neutral_days,
            "win_rate": round(win_rate, 1),
            "total_trades": total_trades,
            "avg_trades_per_day": round(avg_trades_per_day, 1),
            "total_wins": total_wins,
            "total_losses": total_losses,
            "overall_win_rate": round(overall_win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "daily_breakdown": [
                {
                    "date": s['date'].isoformat(),
                    "pnl": round(s.get('total_pnl', 0), 2),
                    "trades": s.get('num_trades', 0),
                    "return_pct": round(s.get('daily_return_pct', 0), 2)
                }
                for s in summaries
            ]
        }

    def _calculate_max_drawdown(self, daily_pnls: List[float]) -> float:
        """Calculate maximum drawdown from daily P&L series."""
        if not daily_pnls:
            return 0.0

        # Convert to cumulative returns
        cumulative = [sum(daily_pnls[:i+1]) for i in range(len(daily_pnls))]

        max_drawdown = 0
        peak = cumulative[0]

        for value in cumulative:
            if value > peak:
                peak = value
            drawdown = peak - value
            max_drawdown = max(max_drawdown, drawdown)

        return max_drawdown

    def generate_weekly_report(self, end_date: datetime = None) -> str:
        """Generate weekly performance report."""
        if end_date is None:
            end_date = datetime.now()

        start_date = end_date - timedelta(days=6)  # 7 days total
        summaries = self.load_daily_summaries(start_date, end_date)
        aggregated = self.aggregate_period(summaries, "Weekly")

        return self._format_period_report(aggregated)

    def generate_monthly_report(self, end_date: datetime = None) -> str:
        """Generate monthly performance report."""
        if end_date is None:
            end_date = datetime.now()

        # Start of month to end_date
        start_date = end_date.replace(day=1)
        summaries = self.load_daily_summaries(start_date, end_date)
        aggregated = self.aggregate_period(summaries, "Monthly")

        return self._format_period_report(aggregated)

    def generate_quarterly_report(self, end_date: datetime = None) -> str:
        """Generate quarterly performance report."""
        if end_date is None:
            end_date = datetime.now()

        # Calculate quarter start
        quarter = (end_date.month - 1) // 3 + 1
        quarter_start_month = (quarter - 1) * 3 + 1
        start_date = end_date.replace(month=quarter_start_month, day=1)
        summaries = self.load_daily_summaries(start_date, end_date)
        aggregated = self.aggregate_period(summaries, "Quarterly")

        return self._format_period_report(aggregated)

    def generate_yearly_report(self, end_date: datetime = None) -> str:
        """Generate yearly performance report."""
        if end_date is None:
            end_date = datetime.now()

        # Start of year to end_date
        start_date = end_date.replace(month=1, day=1)
        summaries = self.load_daily_summaries(start_date, end_date)
        aggregated = self.aggregate_period(summaries, "Yearly")

        return self._format_period_report(aggregated)

    def _format_period_report(self, data: Dict[str, Any]) -> str:
        """Format aggregated period data into a readable report."""
        lines = [
            "=" * 80,
            f"{data['period'].upper()} PERFORMANCE REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 80,
            "",
            "PERIOD OVERVIEW:",
            "-" * 80,
            f"Total Days: {data['total_days']}",
            f"Trading Days: {data['trading_days']}",
            f"Total P&L: ${data['total_pnl']:,.2f}",
            f"Avg Daily P&L: ${data['avg_daily_pnl']:,.2f}",
            "",
            "DAILY PERFORMANCE:",
            "-" * 80,
            f"Winning Days: {data['win_days']}",
            f"Losing Days: {data['loss_days']}",
            f"Neutral Days: {data['neutral_days']}",
            f"Win Rate: {data['win_rate']:.1f}%",
        ]

        if data['best_day']:
            lines.append(f"Best Day: {data['best_day']['date']} (${data['best_day']['pnl']:,.2f})")

        if data['worst_day']:
            lines.append(f"Worst Day: {data['worst_day']['date']} (${data['worst_day']['pnl']:,.2f})")

        lines.extend([
            "",
            "TRADING STATISTICS:",
            "-" * 80,
            f"Total Trades: {data['total_trades']}",
            f"Avg Trades/Day: {data['avg_trades_per_day']:.1f}",
            f"Winning Trades: {data['total_wins']}",
            f"Losing Trades: {data['total_losses']}",
            f"Overall Win Rate: {data['overall_win_rate']:.1f}%",
            f"Avg Win Amount: ${data['avg_win']:,.2f}",
            f"Avg Loss Amount: ${data['avg_loss']:,.2f}",
            f"Profit Factor: {data['profit_factor']:.2f}",
            "",
            "RISK METRICS:",
            "-" * 80,
            f"Max Drawdown: ${data['max_drawdown']:,.2f}",
            f"Sharpe Ratio: {data['sharpe_ratio']:.2f}",
            "=" * 80
        ])

        return "\n".join(lines)

    def save_period_report(self, report: str, period_type: str, end_date: datetime = None):
        """Save period report to file."""
        if end_date is None:
            end_date = datetime.now()

        # Create reports directory with subfolders
        base_reports_dir = AI_TRADER_DATA / "performance"

        # Determine subfolder structure based on period type
        if period_type.lower() in ['weekly', 'daily']:
            # Use Month_Year subfolders
            month_year = end_date.strftime('%B_%Y')  # e.g., "January_2026"
            reports_dir = base_reports_dir / period_type.lower() / month_year
        else:
            # Use Year subfolders for monthly/quarterly/yearly
            year = str(end_date.year)
            reports_dir = base_reports_dir / period_type.lower() / year

        reports_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        date_str = end_date.strftime('%Y_%m_%d')
        filename = f"{period_type.lower()}_report_{date_str}.txt"
        filepath = reports_dir / filename

        # Save report
        with open(filepath, 'w') as f:
            f.write(report)

        logger.info(f"{period_type} report saved to: {filepath}")
        return str(filepath)

    def save_period_summary(self, summary_data: Dict[str, Any], period_type: str, end_date: datetime = None):
        """Save period summary as JSON file."""
        if end_date is None:
            end_date = datetime.now()

        # Create summaries directory with subfolders
        base_summaries_dir = AI_TRADER_DATA / "performance"

        # Determine subfolder structure based on period type
        if period_type.lower() in ['weekly', 'daily']:
            # Use Month_Year subfolders
            month_year = end_date.strftime('%B_%Y')  # e.g., "January_2026"
            summaries_dir = base_summaries_dir / period_type.lower() / month_year
        else:
            # Use Year subfolders for monthly/quarterly/yearly
            year = str(end_date.year)
            summaries_dir = base_summaries_dir / period_type.lower() / year

        summaries_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        if period_type.lower() == 'weekly':
            # Weekly summary covers the week ending on end_date
            start_date = end_date - timedelta(days=6)
            filename = f"summary_{start_date.strftime('%Y_%m_%d')}_to_{end_date.strftime('%Y_%m_%d')}.json"
        elif period_type.lower() == 'monthly':
            # Monthly summary for the month of end_date
            filename = f"summary_{end_date.strftime('%Y_%m')}.json"
        elif period_type.lower() == 'quarterly':
            # Quarterly summary
            quarter = (end_date.month - 1) // 3 + 1
            filename = f"summary_{end_date.strftime('%Y')}_Q{quarter}.json"
        elif period_type.lower() == 'yearly':
            # Yearly summary
            filename = f"summary_{end_date.strftime('%Y')}.json"
        else:
            # Fallback
            date_str = end_date.strftime('%Y_%m_%d')
            filename = f"summary_{period_type.lower()}_{date_str}.json"

        filepath = summaries_dir / filename

        # Add metadata
        summary_data['generated_at'] = datetime.now().isoformat()
        summary_data['period_type'] = period_type.lower()
        summary_data['end_date'] = end_date.date().isoformat()

        # Save summary
        with open(filepath, 'w') as f:
            json.dump(summary_data, f, indent=2)

        logger.info(f"{period_type} summary saved to: {filepath}")
        return str(filepath)


# Convenience functions
def generate_weekly_report(end_date: datetime = None) -> str:
    """Generate weekly performance report."""
    analytics = PeriodAnalytics()
    return analytics.generate_weekly_report(end_date)

def generate_monthly_report(end_date: datetime = None) -> str:
    """Generate monthly performance report."""
    analytics = PeriodAnalytics()
    return analytics.generate_monthly_report(end_date)

def generate_quarterly_report(end_date: datetime = None) -> str:
    """Generate quarterly performance report."""
    analytics = PeriodAnalytics()
    return analytics.generate_quarterly_report(end_date)

def generate_yearly_report(end_date: datetime = None) -> str:
    """Generate yearly performance report."""
    analytics = PeriodAnalytics()
    return analytics.generate_yearly_report(end_date)
