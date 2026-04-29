"""
Strategy Analytics - Performance-Based Strategy Scoring

Analyzes historical strategy performance to identify:
- Win rates per strategy
- Average R:R ratios
- Profitability metrics
- Pattern recognition
- Strategy confidence scores

Author: AI Day Trader Team
Date: January 18, 2026
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import statistics

AI_TRADER_DATA = Path(__file__).parent.parent.parent / "ai_trader_data"

logger = logging.getLogger(__name__)


class StrategyAnalytics:
    """
    Analyzes strategy performance and provides confidence scores.
    """

    def __init__(self, lookback_days: int = 30):
        """
        Initialize strategy analytics.

        Args:
            lookback_days: Number of days to analyze for performance metrics
        """
        self.lookback_days = lookback_days
        self.data_dir = AI_TRADER_DATA / "logs"

    def analyze_strategy_performance(self, trade_log: List[Dict]) -> Dict[str, Any]:
        """
        Analyze performance of each strategy.

        Args:
            trade_log: List of all trades with strategy attribution

        Returns:
            Dictionary with performance metrics per strategy
        """
        if not trade_log:
            return {}

        # Group trades by strategy
        strategy_trades = defaultdict(list)
        for trade in trade_log:
            strategy = trade.get('strategy', 'UNKNOWN')
            strategy_trades[strategy].append(trade)

        # Calculate metrics for each strategy
        strategy_metrics = {}
        for strategy, trades in strategy_trades.items():
            metrics = self._calculate_strategy_metrics(trades)
            strategy_metrics[strategy] = metrics

        return strategy_metrics

    def _calculate_strategy_metrics(self, trades: List[Dict]) -> Dict[str, Any]:
        """
        Calculate comprehensive metrics for a strategy.

        Args:
            trades: List of trades using this strategy

        Returns:
            Dictionary of performance metrics
        """
        if not trades:
            return {}

        # Separate buys and sells
        buys = [t for t in trades if t.get('side', '').upper() == 'BUY']
        sells = [t for t in trades if t.get('side', '').upper() == 'SELL']

        # Calculate win rate (need to match buys with sells)
        wins = 0
        losses = 0
        total_pnl = 0.0

        # Simple matching: for each sell, find corresponding buy
        for sell in sells:
            symbol = sell.get('symbol')
            sell_price = sell.get('price', 0)

            # Find most recent buy for this symbol
            corresponding_buy = None
            for buy in reversed(buys):
                if buy.get('symbol') == symbol:
                    corresponding_buy = buy
                    break

            if corresponding_buy:
                buy_price = corresponding_buy.get('price', 0)
                quantity = sell.get('quantity', 0)
                pnl = (sell_price - buy_price) * quantity
                total_pnl += pnl

                if pnl > 0:
                    wins += 1
                else:
                    losses += 1

        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        # Calculate average R:R
        rr_ratios = [t.get('risk_reward', 0) for t in trades if t.get('risk_reward')]
        avg_rr = statistics.mean(rr_ratios) if rr_ratios else 0

        # Calculate confidence score (0-100)
        confidence = self._calculate_confidence_score(
            win_rate=win_rate,
            total_trades=total_trades,
            avg_rr=avg_rr,
            total_pnl=total_pnl
        )

        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': round(win_rate, 1),
            'avg_rr': round(avg_rr, 2),
            'total_pnl': round(total_pnl, 2),
            'confidence_score': round(confidence, 1),
            'symbols_traded': list(set(t.get('symbol') for t in trades if t.get('symbol'))),
            'buys': len(buys),
            'sells': len(sells)
        }

    def _calculate_confidence_score(self, win_rate: float, total_trades: int,
                                    avg_rr: float, total_pnl: float) -> float:
        """
        Calculate confidence score for a strategy (0-100).

        Higher confidence when:
        - High win rate
        - Good sample size (more trades)
        - Positive R:R ratio
        - Positive total P&L

        Args:
            win_rate: Win rate percentage (0-100)
            total_trades: Number of trades
            avg_rr: Average risk/reward ratio
            total_pnl: Total profit/loss

        Returns:
            Confidence score (0-100)
        """
        if total_trades == 0:
            return 0

        # Win rate component (0-40 points)
        win_rate_score = min(40, win_rate * 0.4)

        # Sample size component (0-20 points)
        # More trades = higher confidence, max at 50 trades
        sample_score = min(20, (total_trades / 50) * 20)

        # R:R component (0-20 points)
        # R:R > 1.5 is good, > 2.0 is excellent
        rr_score = 0
        if avg_rr >= 2.0:
            rr_score = 20
        elif avg_rr >= 1.5:
            rr_score = 15
        elif avg_rr >= 1.0:
            rr_score = 10
        elif avg_rr >= 0.5:
            rr_score = 5

        # Profitability component (0-20 points)
        pnl_score = 0
        if total_pnl > 0:
            # Scale based on total P&L
            if total_pnl > 1000:
                pnl_score = 20
            elif total_pnl > 500:
                pnl_score = 15
            elif total_pnl > 100:
                pnl_score = 10
            else:
                pnl_score = 5

        total_score = win_rate_score + sample_score + rr_score + pnl_score
        return min(100, max(0, total_score))

    def get_strategy_recommendations(self, strategy_metrics: Dict[str, Any]) -> List[str]:
        """
        Generate actionable recommendations based on strategy performance.

        Args:
            strategy_metrics: Performance metrics for all strategies

        Returns:
            List of recommendation strings
        """
        recommendations = []

        if not strategy_metrics:
            return ["Insufficient data for recommendations. Need at least 1 completed trade."]

        # Sort strategies by confidence score
        sorted_strategies = sorted(
            strategy_metrics.items(),
            key=lambda x: x[1].get('confidence_score', 0),
            reverse=True
        )

        # Best performing strategy
        if sorted_strategies:
            best_strategy, best_metrics = sorted_strategies[0]
            if best_metrics.get('confidence_score', 0) >= 60:
                recommendations.append(
                    f"✅ PRIORITIZE: '{best_strategy}' shows strong performance "
                    f"({best_metrics.get('win_rate', 0):.1f}% win rate, "
                    f"confidence: {best_metrics.get('confidence_score', 0):.1f}/100)"
                )

        # Worst performing strategy
        if len(sorted_strategies) > 1:
            worst_strategy, worst_metrics = sorted_strategies[-1]
            if worst_metrics.get('confidence_score', 0) < 30:
                recommendations.append(
                    f"⚠️ AVOID: '{worst_strategy}' shows weak performance "
                    f"({worst_metrics.get('win_rate', 0):.1f}% win rate, "
                    f"confidence: {worst_metrics.get('confidence_score', 0):.1f}/100)"
                )

        # Check for strategies with insufficient data
        for strategy, metrics in strategy_metrics.items():
            if metrics.get('total_trades', 0) < 10:
                recommendations.append(
                    f"📊 '{strategy}': Limited data ({metrics.get('total_trades', 0)} trades). "
                    "Need more samples for reliable assessment."
                )

        # Check for strategies with poor R:R
        for strategy, metrics in strategy_metrics.items():
            if metrics.get('total_trades', 0) >= 5 and metrics.get('avg_rr', 0) < 1.0:
                recommendations.append(
                    f"⚠️ '{strategy}': Poor risk/reward ratio ({metrics.get('avg_rr', 0):.2f}). "
                    "Consider tighter stops or larger profit targets."
                )

        return recommendations

    def identify_winning_patterns(self, trade_log: List[Dict]) -> List[Dict[str, Any]]:
        """
        Identify patterns in winning trades.

        Args:
            trade_log: List of all trades

        Returns:
            List of identified patterns
        """
        patterns = []

        # Pattern 1: Time of day analysis
        morning_trades = [t for t in trade_log if self._is_morning_trade(t)]
        afternoon_trades = [t for t in trade_log if not self._is_morning_trade(t)]

        if morning_trades and afternoon_trades:
            morning_wins = self._calculate_win_rate(morning_trades)
            afternoon_wins = self._calculate_win_rate(afternoon_trades)

            if morning_wins > afternoon_wins + 15:  # 15% threshold
                patterns.append({
                    'pattern': 'Morning trades outperform afternoon',
                    'morning_win_rate': round(morning_wins, 1),
                    'afternoon_win_rate': round(afternoon_wins, 1),
                    'recommendation': 'Focus trading activity in morning session (6:30-10:00 AM)'
                })
            elif afternoon_wins > morning_wins + 15:
                patterns.append({
                    'pattern': 'Afternoon trades outperform morning',
                    'morning_win_rate': round(morning_wins, 1),
                    'afternoon_win_rate': round(afternoon_wins, 1),
                    'recommendation': 'Focus trading activity in afternoon session (10:00 AM-1:00 PM)'
                })

        # Pattern 2: Strategy + Symbol combinations
        strategy_symbol_performance = defaultdict(list)
        for trade in trade_log:
            strategy = trade.get('strategy', 'UNKNOWN')
            symbol = trade.get('symbol')
            if strategy and symbol:
                key = f"{strategy}_{symbol}"
                strategy_symbol_performance[key].append(trade)

        for combo, trades in strategy_symbol_performance.items():
            if len(trades) >= 3:  # Minimum 3 trades for pattern
                win_rate = self._calculate_win_rate(trades)
                if win_rate >= 70:  # High win rate
                    strategy, symbol = combo.split('_', 1)
                    patterns.append({
                        'pattern': f'High success with {strategy} on {symbol}',
                        'win_rate': round(win_rate, 1),
                        'trades': len(trades),
                        'recommendation': f'Continue using {strategy} for {symbol} trades'
                    })

        return patterns

    def _is_morning_trade(self, trade: Dict) -> bool:
        """Check if trade occurred in morning session (before 10 AM)."""
        try:
            timestamp = trade.get('timestamp', '')
            if timestamp:
                dt = datetime.fromisoformat(timestamp)
                return dt.hour < 10
        except:
            pass
        return False

    def _calculate_win_rate(self, trades: List[Dict]) -> float:
        """Calculate win rate for a set of trades."""
        if not trades:
            return 0.0

        # This is simplified - in reality you'd match buys/sells
        # For now, assume trades with positive P&L indicators are wins
        wins = sum(1 for t in trades if self._is_winning_trade(t))
        return (wins / len(trades)) * 100

    def _is_winning_trade(self, trade: Dict) -> bool:
        """Heuristic to determine if trade was a win."""
        # Check if it's a sell with "profit" or "target" in reason
        reason = trade.get('reason', '').lower()
        if trade.get('side', '').upper() == 'SELL':
            if any(word in reason for word in ['profit', 'target', 'gain']):
                return True
            if any(word in reason for word in ['stop', 'loss', 'cut']):
                return False
        return False  # Default to not a win if uncertain

    def generate_weekly_analysis(self) -> str:
        """
        Generate comprehensive weekly analysis report.

        Returns:
            Formatted analysis report
        """
        # Load all trade logs from past week
        trade_logs = self._load_recent_trade_logs(days=7)

        if not trade_logs:
            return "No trade data available for weekly analysis."

        # Analyze strategies
        strategy_metrics = self.analyze_strategy_performance(trade_logs)

        # Identify patterns
        patterns = self.identify_winning_patterns(trade_logs)

        # Get recommendations
        recommendations = self.get_strategy_recommendations(strategy_metrics)

        # Format report
        report_lines = [
            "=" * 80,
            "WEEKLY PERFORMANCE ANALYSIS",
            f"Period: Last 7 days",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 80,
            "",
            "STRATEGY PERFORMANCE:",
            "-" * 80
        ]

        for strategy, metrics in sorted(
            strategy_metrics.items(),
            key=lambda x: x[1].get('confidence_score', 0),
            reverse=True
        ):
            report_lines.extend([
                f"\n{strategy.upper()}:",
                f"  Total Trades: {metrics.get('total_trades', 0)}",
                f"  Win Rate: {metrics.get('win_rate', 0):.1f}%",
                f"  Avg R:R: {metrics.get('avg_rr', 0):.2f}",
                f"  Total P&L: ${metrics.get('total_pnl', 0):.2f}",
                f"  Confidence Score: {metrics.get('confidence_score', 0):.1f}/100",
                f"  Symbols: {', '.join(metrics.get('symbols_traded', []))}"
            ])

        if patterns:
            report_lines.extend([
                "",
                "\nIDENTIFIED PATTERNS:",
                "-" * 80
            ])
            for i, pattern in enumerate(patterns, 1):
                report_lines.append(f"\n{i}. {pattern.get('pattern', 'Unknown')}")
                for key, value in pattern.items():
                    if key != 'pattern':
                        report_lines.append(f"   {key}: {value}")

        report_lines.extend([
            "",
            "\nRECOMMENDATIONS:",
            "-" * 80
        ])
        for rec in recommendations:
            report_lines.append(f"• {rec}")

        report_lines.append("\n" + "=" * 80)

        return "\n".join(report_lines)

    def _load_recent_trade_logs(self, days: int = 7) -> List[Dict]:
        """
        Load trade logs from recent days.

        Args:
            days: Number of days to look back

        Returns:
            Combined list of all trades
        """
        all_trades = []
        cutoff_date = datetime.now() - timedelta(days=days)

        # Search for strategy log files in the logs directory
        if not self.data_dir.exists():
            return []

        for year_dir in self.data_dir.iterdir():
            if not year_dir.is_dir():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                for log_file in month_dir.glob('strategy_*.log'):
                    try:
                        # Parse the log file
                        trades = self._parse_strategy_log(log_file, cutoff_date)
                        all_trades.extend(trades)
                    except Exception as e:
                        logger.warning(f"Error reading {log_file}: {e}")

        return all_trades

    def _parse_strategy_log(self, log_file: Path, cutoff_date: datetime) -> List[Dict]:
        """
        Parse a strategy log file and extract trades.

        Args:
            log_file: Path to log file
            cutoff_date: Only include trades after this date

        Returns:
            List of trade dictionaries
        """
        trades = []

        try:
            with open(log_file, 'r') as f:
                content = f.read()

            # Look for trade entries in the log
            # This is a simple parser - adjust based on actual log format
            lines = content.split('\n')
            for line in lines:
                if 'Trade logged:' in line or '"side":' in line:
                    # Try to extract trade info
                    # This would need to match actual log format
                    pass

        except Exception as e:
            logger.error(f"Error parsing {log_file}: {e}")

        return trades


def generate_weekly_analysis_report() -> str:
    """
    Convenience function to generate weekly analysis.

    Returns:
        Formatted weekly analysis report
    """
    analytics = StrategyAnalytics(lookback_days=7)
    return analytics.generate_weekly_analysis()
