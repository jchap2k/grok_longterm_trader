"""
Analytics Module

Provides performance tracking, charts, reporting, and advanced analytics.
"""

from .token_tracker import TokenTracker
from .performance_charts import PerformanceChartGenerator, generate_performance_report
from .strategy_analytics import StrategyAnalytics, generate_weekly_analysis_report
from .period_analytics import PeriodAnalytics, generate_weekly_report, generate_monthly_report, generate_quarterly_report, generate_yearly_report
from .market_regime import MarketRegimeDetector, get_current_market_regime
from .correlation_tracker import CorrelationTracker, analyze_portfolio_correlation
from .dynamic_sizing import DynamicPositionSizer, calculate_optimal_position_size
from .multi_timeframe import MultiTimeframeAnalyzer, analyze_multi_timeframe
from .news_analyzer import NewsAnalyzer, get_symbol_news, check_catalysts
from .alert_system import AlertSystem, get_alert_system, send_alert, AlertLevel

__all__ = [
    'TokenTracker',
    'PerformanceChartGenerator',
    'generate_performance_report',
    'StrategyAnalytics',
    'generate_weekly_analysis_report',
    'PeriodAnalytics',
    'generate_weekly_report',
    'generate_monthly_report',
    'generate_quarterly_report',
    'generate_yearly_report',
    'MarketRegimeDetector',
    'get_current_market_regime',
    'CorrelationTracker',
    'analyze_portfolio_correlation',
    'DynamicPositionSizer',
    'calculate_optimal_position_size',
    'MultiTimeframeAnalyzer',
    'analyze_multi_timeframe',
    'NewsAnalyzer',
    'get_symbol_news',
    'check_catalysts',
    'AlertSystem',
    'get_alert_system',
    'send_alert',
    'AlertLevel'
]
