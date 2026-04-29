"""
Discord bot notifier for trading agent events.

Sends real-time push notifications to a monthly thread in Discord.
Uses a bot token (not just a webhook) so threads can be auto-created.

Thread behavior:
  - Creates "February 2026" thread automatically on first message of each month
  - Caches thread ID in config/discord_thread_id.txt (rotates monthly)
  - Falls back to webhook if bot token not configured

Events notified:
  - Trade opened (symbol, entry, conviction, catalyst preview)
  - Trade closed (symbol, P&L, exit reason)
  - Morning scan summary (candidates found, top setups)
  - Circuit breaker fired (reason, trading halted)
  - System warnings
  - Daily summary (EOD P&L, win rate)

Config files (all gitignored):
  config/discord_bot_token.txt  - Bot token from Discord Developer Portal
  config/discord_channel_id.txt - Channel ID (right-click channel > Copy ID)
  config/discord_webhook.txt    - Fallback webhook URL (optional)
  config/discord_thread_id.txt  - Auto-managed, do not edit manually
"""

import json
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger('trading_agent')

DISCORD_API = 'https://discord.com/api/v10'
TIMEOUT = 5  # seconds - never block trading for Discord


class DiscordNotifier:
    """
    Discord notifier using bot token for automatic monthly thread management.
    All methods are fire-and-forget - failures are logged but never raise.
    """

    def __init__(self, bot_token: str, channel_id: str,
                 webhook_url: str = None, enabled: bool = True,
                 config_dir: str = None):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.webhook_url = webhook_url
        self.enabled = enabled
        self.config_dir = config_dir
        self._thread_cache = {}  # month_key -> thread_id
        self._load_thread_cache()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config_dir: str, broker_config: dict = None):
        """
        Build a DiscordNotifier from config files. Returns None if not configured.
        Prefers bot token + channel ID. Falls back to webhook-only if no bot token.
        """
        enabled = True
        if broker_config:
            enabled = broker_config.get('discord', {}).get('enabled', True)
        if not enabled:
            return None

        bot_token = _read_config_file(config_dir, 'discord_bot_token.txt')
        channel_id = _read_config_file(config_dir, 'discord_channel_id.txt')
        webhook_url = _read_config_file(config_dir, 'discord_webhook.txt')

        if not bot_token and not webhook_url:
            return None

        if bot_token and channel_id:
            logger.info("Discord: bot token mode (auto monthly threads)")
        elif webhook_url:
            logger.info("Discord: webhook-only mode (no thread auto-creation)")

        return cls(
            bot_token=bot_token or '',
            channel_id=channel_id or '',
            webhook_url=webhook_url or '',
            enabled=enabled,
            config_dir=config_dir,
        )

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def _month_key(self) -> str:
        """e.g. '2026-02'"""
        return datetime.now().strftime('%Y-%m')

    def _month_label(self) -> str:
        """e.g. 'February 2026'"""
        return datetime.now().strftime('%B %Y')

    def _load_thread_cache(self):
        """Load cached thread IDs from disk."""
        if not self.config_dir:
            return
        path = os.path.join(self.config_dir, 'discord_thread_id.txt')
        try:
            if os.path.exists(path):
                self._thread_cache = json.loads(open(path).read().strip())
        except Exception:
            self._thread_cache = {}

    def _save_thread_cache(self):
        """Persist thread ID cache to disk."""
        if not self.config_dir:
            return
        path = os.path.join(self.config_dir, 'discord_thread_id.txt')
        try:
            with open(path, 'w') as f:
                f.write(json.dumps(self._thread_cache))
        except Exception as e:
            logger.warning(f"Discord: could not save thread cache: {e}")

    def _get_or_create_thread(self) -> str:
        """
        Return the thread ID for the current month, creating it if needed.
        Returns empty string on failure.
        """
        if not self.bot_token or not self.channel_id:
            return ''

        month_key = self._month_key()
        if month_key in self._thread_cache:
            return self._thread_cache[month_key]

        # Create new thread for this month
        thread_name = self._month_label()
        url = f'{DISCORD_API}/channels/{self.channel_id}/threads'
        headers = {
            'Authorization': f'Bot {self.bot_token}',
            'Content-Type': 'application/json',
        }
        payload = {
            'name': thread_name,
            'type': 11,  # GUILD_PUBLIC_THREAD
            'auto_archive_duration': 10080,  # 7 days (max)
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            if r.status_code in (200, 201):
                thread_id = r.json().get('id', '')
                if thread_id:
                    self._thread_cache[month_key] = thread_id
                    self._save_thread_cache()
                    logger.info(f"Discord: created thread '{thread_name}' (id={thread_id})")
                    return thread_id
            else:
                logger.warning(f"Discord: thread creation returned {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.warning(f"Discord: thread creation failed: {e}")
        return ''

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def _send(self, content: str = None, embeds: list = None):
        """
        Send a message. Tries bot API -> thread first, falls back to webhook.
        """
        if not self.enabled:
            return

        # Prefer bot token -> post into monthly thread
        if self.bot_token and self.channel_id:
            thread_id = self._get_or_create_thread()
            if thread_id:
                self._send_bot(thread_id, content=content, embeds=embeds)
                return

        # Fallback: plain webhook (no thread)
        if self.webhook_url:
            self._send_webhook(content=content, embeds=embeds)

    def _send_bot(self, channel_id: str, content: str = None, embeds: list = None):
        """POST a message via bot token to a channel or thread ID."""
        url = f'{DISCORD_API}/channels/{channel_id}/messages'
        headers = {
            'Authorization': f'Bot {self.bot_token}',
            'Content-Type': 'application/json',
        }
        payload = {}
        if content:
            payload['content'] = content
        if embeds:
            payload['embeds'] = embeds
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            if r.status_code not in (200, 201):
                logger.warning(f"Discord bot message returned {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.warning(f"Discord bot send failed (non-fatal): {e}")

    def _send_webhook(self, content: str = None, embeds: list = None):
        """POST a message via webhook URL (fallback, no thread support)."""
        payload = {}
        if content:
            payload['content'] = content
        if embeds:
            payload['embeds'] = embeds
        try:
            r = requests.post(self.webhook_url, json=payload, timeout=TIMEOUT)
            if r.status_code not in (200, 204):
                logger.warning(f"Discord webhook returned {r.status_code}")
        except Exception as e:
            logger.warning(f"Discord webhook send failed (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Public event methods
    # ------------------------------------------------------------------

    def trade_opened(self, symbol: str, entry_price: float, conviction: float,
                     catalyst: str = '', side: str = 'long',
                     shares: int = None, target: float = None, stop: float = None):
        """Notify when a new position is opened."""
        time_str = datetime.now().strftime('%H:%M PST')
        direction = 'LONG' if side == 'long' else 'SHORT'
        color = 0x00b300 if side == 'long' else 0xff4444

        fields = [
            {'name': 'Entry', 'value': f'${entry_price:.2f}', 'inline': True},
            {'name': 'Conviction', 'value': f'{conviction}/10', 'inline': True},
        ]
        if shares:
            fields.append({'name': 'Shares', 'value': str(shares), 'inline': True})
        if target:
            fields.append({'name': 'Target', 'value': f'${target:.2f}', 'inline': True})
        if stop:
            fields.append({'name': 'Stop', 'value': f'${stop:.2f}', 'inline': True})
        if catalyst:
            first_line = catalyst.split('\n')[0][:120]
            fields.append({'name': 'Catalyst', 'value': first_line, 'inline': False})

        embed = {
            'title': f'TRADE OPENED - {direction} {symbol}',
            'color': color,
            'fields': fields,
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])

    def order_filled(self, symbol: str, fill_price: float, shares: int,
                     side: str = 'buy', order_type: str = 'entry',
                     limit_price: float = None):
        """
        Notify when a broker order actually fills.
        order_type: 'entry', 'take_profit', or 'stop_loss'
        """
        time_str = datetime.now().strftime('%H:%M PST')
        if order_type == 'entry':
            title = f'ENTRY FILLED - {symbol}'
            color = 0x00b300
        elif order_type == 'take_profit':
            title = f'TAKE PROFIT HIT - {symbol}'
            color = 0x00ff88
        elif order_type == 'stop_loss':
            title = f'STOP LOSS HIT - {symbol}'
            color = 0xff4444
        elif order_type == 'partial_profit':
            title = f'PARTIAL PROFIT - {symbol}'
            color = 0x00cc66
        else:
            title = f'ORDER FILLED - {symbol}'
            color = 0x5865f2

        fields = [
            {'name': 'Fill Price', 'value': f'${fill_price:.2f}', 'inline': True},
            {'name': 'Shares', 'value': str(shares), 'inline': True},
        ]
        if limit_price and order_type == 'entry':
            slippage = fill_price - limit_price
            slippage_str = f'{slippage:+.2f}' if slippage != 0 else 'exact'
            fields.append({'name': 'Slippage', 'value': slippage_str, 'inline': True})

        embed = {
            'title': title,
            'color': color,
            'fields': fields,
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])

    def trade_closed(self, symbol: str, pnl: float, pnl_pct: float,
                     reason: str = '', entry_price: float = None,
                     exit_price: float = None):
        """Notify when a position is closed."""
        time_str = datetime.now().strftime('%H:%M PST')
        color = 0x00b300 if pnl >= 0 else 0xff4444
        pnl_sign = '+' if pnl >= 0 else ''
        result = 'WIN' if pnl >= 0 else 'LOSS'

        fields = [
            {'name': 'P&L', 'value': f'{pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)', 'inline': True},
        ]
        if entry_price:
            fields.append({'name': 'Entry', 'value': f'${entry_price:.2f}', 'inline': True})
        if exit_price:
            fields.append({'name': 'Exit', 'value': f'${exit_price:.2f}', 'inline': True})
        if reason:
            fields.append({'name': 'Reason', 'value': reason[:100], 'inline': False})

        embed = {
            'title': f'TRADE CLOSED - {result} {symbol}',
            'color': color,
            'fields': fields,
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])

    def scan_summary(self, candidates: list, window: str = ''):
        """Notify morning/opportunity scan results - top candidates found."""
        if not candidates:
            return
        time_str = datetime.now().strftime('%H:%M PST')

        top = sorted(candidates, key=lambda x: x.get('score', 0), reverse=True)[:3]
        lines = []
        for c in top:
            sym = c.get('symbol', '?')
            score = c.get('score', 0)
            gap = c.get('change_pct', c.get('gap_pct', 0)) or 0
            rvol = c.get('rel_volume', 0) or 0
            has_cat = bool((c.get('catalyst_summary') or '').strip())
            cat_flag = 'catalyst' if has_cat else 'no catalyst'
            lines.append(f'**{sym}** score:{score:.0f} gap:{gap:+.1f}% rvol:{rvol:.1f}x {cat_flag}')

        label = f' ({window})' if window else ''
        embed = {
            'title': f'SCAN COMPLETE{label} - {len(candidates)} candidates',
            'color': 0x5865f2,
            'description': '\n'.join(lines),
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])

    def circuit_breaker(self, reason: str, daily_pnl_pct: float = None):
        """Notify when circuit breaker halts trading."""
        time_str = datetime.now().strftime('%H:%M PST')
        desc = reason
        if daily_pnl_pct is not None:
            desc += f'\nDaily P&L: {daily_pnl_pct:+.2f}%'

        embed = {
            'title': 'CIRCUIT BREAKER FIRED - Trading Halted',
            'color': 0xff6600,
            'description': desc,
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])

    def daily_summary(self, pnl: float, pnl_pct: float, trades: int,
                      wins: int, win_rate: float):
        """EOD daily summary notification."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        color = 0x00b300 if pnl >= 0 else 0xff4444
        pnl_sign = '+' if pnl >= 0 else ''

        embed = {
            'title': f'DAILY SUMMARY - {date_str}',
            'color': color,
            'fields': [
                {'name': 'P&L', 'value': f'{pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)', 'inline': True},
                {'name': 'Trades', 'value': str(trades), 'inline': True},
                {'name': 'Win Rate', 'value': f'{win_rate:.0f}% ({wins}/{trades})', 'inline': True},
            ],
            'footer': {'text': 'End of day'},
        }
        self._send(embeds=[embed])

    def send_message(self, message: str):
        """Send a plain informational message (EOD summaries, attribution reports, etc.)."""
        time_str = datetime.now().strftime('%H:%M PST')
        embed = {
            'color': 0x5865f2,
            'description': message[:2000],
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])

    def warning(self, message: str):
        """Send a system warning (Perplexity failure, timeout, etc.)."""
        time_str = datetime.now().strftime('%H:%M PST')
        embed = {
            'title': 'SYSTEM WARNING',
            'color': 0xffcc00,
            'description': message[:500],
            'footer': {'text': time_str},
        }
        self._send(embeds=[embed])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _read_config_file(config_dir: str, filename: str) -> str:
    """Read a single-line config file. Returns empty string if missing."""
    try:
        path = os.path.join(config_dir, filename)
        if os.path.exists(path):
            return open(path).read().strip()
    except Exception:
        pass
    return ''
