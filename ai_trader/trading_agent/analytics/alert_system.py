"""
Alert System - Notifications for Important Events

Sends alerts via email, SMS, or logs for:
- Large wins/losses
- Daily loss limit approaching
- Token expiration warnings
- System errors
- High-confidence opportunities

Author: AI Day Trader Team
Date: January 18, 2026
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from pathlib import Path

AI_TRADER_DATA = Path(__file__).parent.parent.parent / "ai_trader_data"

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertChannel(Enum):
    """Where to send alerts."""
    LOG = "log"           # Just log it
    EMAIL = "email"       # Send email (requires config)
    SMS = "sms"          # Send SMS (requires config)
    FILE = "file"        # Write to alerts file


class AlertSystem:
    """
    Manages alerts and notifications for trading events.
    """

    def __init__(self, alerts_dir: str = str(AI_TRADER_DATA / "alerts")):
        """
        Initialize alert system.

        Args:
            alerts_dir: Directory for alert log files
        """
        self.alerts_dir = Path(alerts_dir)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

        self.alert_history = []
        self.alert_thresholds = {
            'large_win': 500,      # Alert on wins > $500
            'large_loss': 500,     # Alert on losses > $500
            'daily_loss_warning': -4.0,  # Warn at -4% (before -5% limit)
            'token_expiry_days': 1  # Warn when <24 hours left
        }

    def send_alert(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        channels: List[AlertChannel] = None,
        data: Optional[Dict] = None
    ):
        """
        Send an alert through specified channels.

        Args:
            title: Alert title
            message: Alert message
            level: Severity level
            channels: Where to send (defaults to LOG + FILE)
            data: Optional data dictionary
        """
        if channels is None:
            channels = [AlertChannel.LOG, AlertChannel.FILE]

        alert = {
            'timestamp': datetime.now().isoformat(),
            'title': title,
            'message': message,
            'level': level.value,
            'data': data or {}
        }

        self.alert_history.append(alert)

        # Send to each channel
        for channel in channels:
            if channel == AlertChannel.LOG:
                self._send_to_log(alert)
            elif channel == AlertChannel.FILE:
                self._send_to_file(alert)
            elif channel == AlertChannel.EMAIL:
                self._send_to_email(alert)
            elif channel == AlertChannel.SMS:
                self._send_to_sms(alert)

    def check_trade_alert(self, pnl: float, symbol: str, quantity: int):
        """
        Check if trade warrants an alert.

        Args:
            pnl: Profit/loss from trade
            symbol: Stock symbol
            quantity: Number of shares
        """
        if pnl >= self.alert_thresholds['large_win']:
            self.send_alert(
                title=f"🎉 Large Win on {symbol}",
                message=f"Profitable trade: {symbol} {quantity} shares → +${pnl:.2f}",
                level=AlertLevel.INFO,
                data={'pnl': pnl, 'symbol': symbol, 'quantity': quantity}
            )
        elif abs(pnl) >= self.alert_thresholds['large_loss']:
            self.send_alert(
                title=f"⚠️ Large Loss on {symbol}",
                message=f"Loss trade: {symbol} {quantity} shares → -${abs(pnl):.2f}",
                level=AlertLevel.WARNING,
                data={'pnl': pnl, 'symbol': symbol, 'quantity': quantity}
            )

    def check_daily_pnl_alert(self, daily_pnl_percent: float, portfolio_value: float):
        """
        Check if daily P&L warrants an alert.

        Args:
            daily_pnl_percent: Daily P&L as percentage
            portfolio_value: Current portfolio value
        """
        if daily_pnl_percent <= self.alert_thresholds['daily_loss_warning']:
            self.send_alert(
                title="⚠️ Daily Loss Warning",
                message=(
                    f"Daily P&L: {daily_pnl_percent:.2f}% "
                    f"(Portfolio: ${portfolio_value:,.2f}). "
                    f"Approaching -5% limit."
                ),
                level=AlertLevel.WARNING,
                data={'daily_pnl_percent': daily_pnl_percent, 'portfolio_value': portfolio_value}
            )
        elif daily_pnl_percent >= 5.0:
            self.send_alert(
                title="🎉 Strong Daily Performance",
                message=f"Daily P&L: +{daily_pnl_percent:.2f}% (Portfolio: ${portfolio_value:,.2f})",
                level=AlertLevel.INFO,
                data={'daily_pnl_percent': daily_pnl_percent, 'portfolio_value': portfolio_value}
            )

    def alert_system_error(self, error: str, context: str = ""):
        """
        Alert on system errors.

        Args:
            error: Error message
            context: Additional context
        """
        self.send_alert(
            title="🔴 System Error",
            message=f"Error: {error}\nContext: {context}",
            level=AlertLevel.ERROR,
            data={'error': error, 'context': context}
        )

    def alert_opportunity(self, symbol: str, strategy: str, confidence: float, reason: str):
        """
        Alert on high-confidence trading opportunity.

        Args:
            symbol: Stock symbol
            strategy: Trading strategy
            confidence: Confidence score (0-100)
            reason: Why this is an opportunity
        """
        if confidence >= 75:  # Only alert on high-confidence setups
            self.send_alert(
                title=f"🚀 High-Confidence Opportunity: {symbol}",
                message=(
                    f"Strategy: {strategy}\n"
                    f"Confidence: {confidence:.1f}/100\n"
                    f"Reason: {reason}"
                ),
                level=AlertLevel.INFO,
                data={
                    'symbol': symbol,
                    'strategy': strategy,
                    'confidence': confidence,
                    'reason': reason
                }
            )

    def _send_to_log(self, alert: Dict):
        """Send alert to logging system."""
        level_map = {
            'info': logging.INFO,
            'warning': logging.WARNING,
            'error': logging.ERROR,
            'critical': logging.CRITICAL
        }
        log_level = level_map.get(alert['level'], logging.INFO)

        logger.log(
            log_level,
            f"ALERT: {alert['title']} - {alert['message']}"
        )

    def _send_to_file(self, alert: Dict):
        """Write alert to alerts file."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            alert_file = self.alerts_dir / f"alerts_{today}.log"

            with open(alert_file, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"[{alert['timestamp']}] {alert['level'].upper()}\n")
                f.write(f"Title: {alert['title']}\n")
                f.write(f"Message: {alert['message']}\n")
                if alert['data']:
                    f.write(f"Data: {alert['data']}\n")
                f.write(f"{'='*80}\n")
        except Exception as e:
            logger.error(f"Failed to write alert to file: {e}")

    def _send_to_email(self, alert: Dict):
        """
        Send alert via email.

        Note: Requires email configuration (SMTP server, credentials).
        Framework only - implement with actual email service.
        """
        # TODO: Implement email sending
        # Would use smtplib or email service API (SendGrid, Mailgun, etc.)
        logger.info(f"Email alert (not configured): {alert['title']}")

    def _send_to_sms(self, alert: Dict):
        """
        Send alert via SMS.

        Note: Requires SMS service (Twilio, AWS SNS, etc.).
        Framework only - implement with actual SMS service.
        """
        # TODO: Implement SMS sending
        # Would use Twilio API or similar
        logger.info(f"SMS alert (not configured): {alert['title']}")

    def get_daily_summary(self) -> str:
        """
        Get summary of today's alerts.

        Returns:
            Formatted alert summary
        """
        today = datetime.now().date()
        today_alerts = [
            a for a in self.alert_history
            if datetime.fromisoformat(a['timestamp']).date() == today
        ]

        if not today_alerts:
            return "No alerts today."

        summary = [f"Alert Summary ({len(today_alerts)} alerts today):"]

        # Group by level
        by_level = {}
        for alert in today_alerts:
            level = alert['level']
            if level not in by_level:
                by_level[level] = []
            by_level[level].append(alert)

        for level in ['critical', 'error', 'warning', 'info']:
            if level in by_level:
                summary.append(f"\n{level.upper()} ({len(by_level[level])}):")
                for alert in by_level[level][:5]:  # Top 5
                    time = datetime.fromisoformat(alert['timestamp']).strftime('%H:%M')
                    summary.append(f"  [{time}] {alert['title']}")

        return "\n".join(summary)


# Global alert system instance
_alert_system = None


def get_alert_system() -> AlertSystem:
    """Get or create global alert system instance."""
    global _alert_system
    if _alert_system is None:
        _alert_system = AlertSystem()
    return _alert_system


def send_alert(title: str, message: str, level: AlertLevel = AlertLevel.INFO):
    """Convenience function to send an alert."""
    system = get_alert_system()
    system.send_alert(title, message, level)
