"""
FillReconciler - Reconciles Alpaca filled sell orders with trade_journal.

Solves the logging gap where positions closed outside the scheduler's
position monitor (trailing stops at open, watchdog emergency close,
partial profit-takes, pre-market fills) don't get their exit data
written to learning.db.

Flow:
    1. Fetch all filled sell orders from Alpaca (last N days)
    2. Find trade_journal entries with exit_time IS NULL
    3. For each open entry, collect fills chronologically:
       - Partial fill (qty < remaining shares): append to partial_exits JSON
       - Final fill (qty >= remaining shares): write exit_time/exit_price/actual_pnl
    4. Entries with only partial fills stay open (exit_time remains NULL)

Handles:
    - Full closes (trailing stop, market-on-close, watchdog)
    - Partial profit-takes (scale-out sells)
    - Multiple fills closing a single position across several orders
    - Re-runs safely (deduplicates by alpaca_order_id in partial_exits)

Usage (standalone):
    from analytics.fill_reconciler import FillReconciler
    reconciler = FillReconciler(broker=broker, learning_db_path=path)
    result = reconciler.reconcile()
    # result: {checked, updated, partial_updated, skipped, no_match, details:[...]}

Usage (EOD routine):
    Called automatically after _close_all_positions() in market_close_routine().
"""

import json
import sqlite3
import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class FillReconciler:
    """
    Reconciles Alpaca filled sell orders with trade_journal exit data.

    Safe to run multiple times:
    - Already fully reconciled entries (exit_time IS NOT NULL) are skipped
    - Partial exits already in partial_exits JSON are deduplicated by alpaca_order_id
    """

    def __init__(self, broker, learning_db_path: Path, lookback_days: int = 7):
        """
        Args:
            broker: AlpacaBroker instance (or None for dashboard without live broker)
            learning_db_path: Path to learning.db
            lookback_days: How far back to look for fills and open journal entries
        """
        self.broker = broker
        self.learning_db_path = Path(learning_db_path)
        self.lookback_days = lookback_days

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconcile(self) -> Dict[str, Any]:
        """
        Run full reconciliation pass.

        Returns:
            {
                'checked': int,           # open journal entries examined
                'updated': int,           # entries fully closed from Alpaca fills
                'partial_updated': int,   # entries with new partial exits recorded
                'skipped': int,           # entries skipped (bad data / parse error)
                'no_match': int,          # entries with no fill found
                'details': [...]          # one dict per fully closed entry
            }
        """
        result = {
            'checked': 0, 'updated': 0, 'partial_updated': 0,
            'skipped': 0, 'no_match': 0, 'details': []
        }

        if not self.broker:
            logger.info("[FillReconciler] No broker - skipping Alpaca fetch")
            result['error'] = 'no_broker'
            return result

        try:
            fills = self._get_filled_sells()
            if not fills:
                logger.info(f"[FillReconciler] No filled sells found in Alpaca "
                            f"(last {self.lookback_days}d)")
                return result

            logger.info(f"[FillReconciler] {len(fills)} filled sell order(s) from Alpaca")

            open_entries = self._get_open_journal_entries()
            if not open_entries:
                logger.info("[FillReconciler] No open journal entries to reconcile")
                return result

            logger.info(f"[FillReconciler] {len(open_entries)} journal entry/entries "
                        "missing full exit data")

            stats = self._match_and_update(fills, open_entries)
            result.update(stats)

        except Exception as e:
            logger.error(f"[FillReconciler] Reconciliation failed: {e}", exc_info=True)
            result['error'] = str(e)

        return result

    def get_open_entries_count(self) -> int:
        """Quick check: how many journal entries are missing exit data?"""
        try:
            cutoff = (date.today() - timedelta(days=self.lookback_days)).isoformat()
            conn = sqlite3.connect(self.learning_db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM trade_journal "
                "WHERE exit_time IS NULL AND trade_date >= ?",
                (cutoff,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_filled_sells(self) -> List[Dict[str, Any]]:
        """
        Delegate to broker.get_filled_sell_orders().
        Returns list of {symbol, filled_at, filled_avg_price, qty, order_id}.
        """
        try:
            return self.broker.get_filled_sell_orders(since_days=self.lookback_days)
        except AttributeError:
            logger.warning("[FillReconciler] broker.get_filled_sell_orders() not found - "
                           "update AlpacaBroker")
            return []
        except Exception as e:
            logger.error(f"[FillReconciler] Failed to fetch fills from broker: {e}")
            return []

    def _get_open_journal_entries(self) -> List[Dict[str, Any]]:
        """
        Load trade_journal rows where exit_time IS NULL within lookback window.
        Includes partial_exits column for deduplication.
        """
        try:
            cutoff = (date.today() - timedelta(days=self.lookback_days)).isoformat()
            conn = sqlite3.connect(self.learning_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, symbol, entry_time, entry_price, shares,
                       trade_date, partial_exits
                FROM trade_journal
                WHERE exit_time IS NULL
                  AND trade_date >= ?
                ORDER BY trade_date ASC, entry_time ASC
            """, (cutoff,))

            entries = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return entries

        except Exception as e:
            logger.error(f"[FillReconciler] Failed to read open journal entries: {e}")
            return []

    def _parse_entry_time(self, entry_time_str: str, trade_date_str: str = None) -> Optional[datetime]:
        """Parse entry_time string to timezone-aware datetime (UTC)."""
        if not entry_time_str:
            return None
        try:
            if 'T' in entry_time_str:
                dt = datetime.fromisoformat(entry_time_str)
            elif trade_date_str and len(entry_time_str.split(':')) == 3:
                dt = datetime.strptime(
                    f"{trade_date_str} {entry_time_str}",
                    '%Y-%m-%d %H:%M:%S'
                )
            else:
                dt = datetime.strptime(entry_time_str, '%Y-%m-%d %H:%M:%S')
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    def _load_partial_exits(self, partial_exits_json: Optional[str]) -> List[Dict]:
        """Safely parse existing partial_exits JSON from trade_journal."""
        if not partial_exits_json:
            return []
        try:
            data = json.loads(partial_exits_json)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, TypeError):
            return []

    def _match_and_update(self, fills: List[Dict], entries: List[Dict]) -> Dict[str, Any]:
        """
        Match fills to open journal entries and write exit data to DB.

        Matching rules:
        - Symbol must match
        - fill.filled_at must be AFTER entry.entry_time
        - Each fill consumed at most once across all entries (FIFO within symbol)
        - Partial fills (qty < remaining shares) update partial_exits JSON
        - Full closes write exit_time / exit_price / actual_pnl

        P&L accounting for partial exits:
        - Each tranche: (exit_price - entry_price) * tranche_shares
        - Total P&L = sum of all tranches (partials + final close)

        Returns stats dict.
        """
        stats = {
            'checked': 0, 'updated': 0, 'partial_updated': 0,
            'skipped': 0, 'no_match': 0, 'details': []
        }

        # Index fills by symbol, sorted oldest-first (FIFO)
        fills_by_symbol: Dict[str, List[Dict]] = {}
        for f in fills:
            sym = f['symbol']
            fills_by_symbol.setdefault(sym, []).append(f)
        for sym in fills_by_symbol:
            fills_by_symbol[sym].sort(key=lambda x: x['filled_at'])

        # Track order_ids consumed across all entries this run
        consumed_order_ids: set = set()

        conn = sqlite3.connect(self.learning_db_path)
        cursor = conn.cursor()

        try:
            for entry in entries:
                stats['checked'] += 1
                entry_id = entry['id']
                sym = entry['symbol']
                entry_time_str = entry.get('entry_time', '')
                entry_price = entry.get('entry_price') or 0.0
                entry_shares = entry.get('shares') or 0

                entry_dt = self._parse_entry_time(entry_time_str, entry.get('trade_date'))
                if entry_dt is None:
                    logger.warning(
                        f"[FillReconciler] Cannot parse entry_time for "
                        f"journal id={entry_id}: '{entry_time_str}'"
                    )
                    stats['skipped'] += 1
                    continue

                # Load existing partial exits (for deduplication + running P&L)
                existing_partials = self._load_partial_exits(entry.get('partial_exits'))
                already_consumed_ids = {
                    p.get('alpaca_order_id') for p in existing_partials
                    if p.get('alpaca_order_id')
                }

                # Shares already sold in previous partial exits
                already_sold = sum(p.get('shares', 0) for p in existing_partials)
                remaining_shares = entry_shares - already_sold

                if remaining_shares <= 0:
                    logger.warning(
                        f"[FillReconciler] {sym} id={entry_id}: partial_exits cover "
                        f"all {entry_shares} shares but exit_time is NULL - skipping"
                    )
                    stats['skipped'] += 1
                    continue

                # Available fills: same symbol, after entry, not yet used
                available_fills = [
                    f for f in fills_by_symbol.get(sym, [])
                    if f['order_id'] not in consumed_order_ids
                    and f['order_id'] not in already_consumed_ids
                    and f['filled_at'] > entry_dt
                ]

                if not available_fills:
                    logger.info(
                        f"[FillReconciler] No fill match for {sym} "
                        f"(journal id={entry_id}, entered {entry_time_str})"
                    )
                    stats['no_match'] += 1
                    continue

                # Walk fills chronologically until position fully closes or fills exhausted
                new_partials: List[Dict] = []
                exit_data: Optional[Dict] = None

                for f in available_fills:
                    fill_shares = f['qty']
                    fill_price = f['filled_avg_price']
                    fill_time_str = f['filled_at'].strftime('%Y-%m-%d %H:%M:%S')
                    order_id = f['order_id']

                    if fill_shares >= remaining_shares:
                        # Final fill - closes the rest of the position
                        pnl_this_tranche = (fill_price - entry_price) * remaining_shares
                        prior_partial_pnl = sum(
                            p.get('pnl', 0) for p in existing_partials + new_partials
                        )
                        total_pnl = prior_partial_pnl + pnl_this_tranche

                        exit_data = {
                            'exit_time': fill_time_str,
                            'exit_price': fill_price,
                            'actual_pnl': round(total_pnl, 2),
                            'alpaca_order_id': order_id,
                        }
                        consumed_order_ids.add(order_id)
                        remaining_shares = 0
                        break

                    else:
                        # Partial fill - record tranche, position still open
                        pnl_this_tranche = (fill_price - entry_price) * fill_shares
                        new_partials.append({
                            'exit_time': fill_time_str,
                            'exit_price': fill_price,
                            'shares': int(fill_shares),
                            'pnl': round(pnl_this_tranche, 2),
                            'source': 'alpaca_fill_reconciled',
                            'alpaca_order_id': order_id,
                        })
                        consumed_order_ids.add(order_id)
                        remaining_shares -= fill_shares
                        logger.info(
                            f"[FillReconciler] Partial exit for {sym} id={entry_id}: "
                            f"{int(fill_shares)} shares @ ${fill_price:.2f} "
                            f"P&L=${pnl_this_tranche:+.2f}, "
                            f"{int(remaining_shares)} shares remaining"
                        )

                # --- Write results to DB ---

                if exit_data:
                    # Full close: write exit fields + merged partial_exits
                    all_partials = existing_partials + new_partials
                    partial_json = json.dumps(all_partials) if all_partials else None

                    cursor.execute("""
                        UPDATE trade_journal
                        SET exit_time     = ?,
                            exit_price    = ?,
                            exit_reason   = 'alpaca_fill_reconciled',
                            actual_pnl    = ?,
                            partial_exits = ?
                        WHERE id = ?
                    """, (
                        exit_data['exit_time'],
                        exit_data['exit_price'],
                        exit_data['actual_pnl'],
                        partial_json,
                        entry_id,
                    ))

                    stats['updated'] += 1
                    stats['details'].append({
                        'journal_id': entry_id,
                        'symbol': sym,
                        'exit_price': exit_data['exit_price'],
                        'exit_time': exit_data['exit_time'],
                        'actual_pnl': exit_data['actual_pnl'],
                        'partial_exits_count': len(all_partials),
                        'alpaca_order_id': exit_data['alpaca_order_id'],
                    })

                    partial_note = (
                        f" (includes {len(existing_partials + new_partials)} partial exit(s))"
                        if existing_partials or new_partials else ""
                    )
                    logger.info(
                        f"[FillReconciler] Closed {sym} id={entry_id}: "
                        f"exit=${exit_data['exit_price']:.2f} @ {exit_data['exit_time']}, "
                        f"P&L=${exit_data['actual_pnl']:+.2f}{partial_note}"
                    )

                elif new_partials:
                    # Partial-only pass: update partial_exits, leave exit_time NULL
                    all_partials = existing_partials + new_partials
                    cursor.execute("""
                        UPDATE trade_journal
                        SET partial_exits = ?
                        WHERE id = ?
                    """, (json.dumps(all_partials), entry_id))

                    stats['partial_updated'] += 1
                    logger.info(
                        f"[FillReconciler] Partial exit(s) recorded for {sym} id={entry_id}: "
                        f"{len(new_partials)} new fill(s), "
                        f"{int(remaining_shares)} shares still open"
                    )

                else:
                    stats['no_match'] += 1

            conn.commit()

        except Exception as e:
            conn.rollback()
            logger.error(f"[FillReconciler] DB update failed: {e}", exc_info=True)
            stats['error'] = str(e)

        finally:
            conn.close()

        logger.info(
            f"[FillReconciler] Done - checked={stats['checked']}, "
            f"fully_closed={stats['updated']}, "
            f"partial_updated={stats['partial_updated']}, "
            f"no_match={stats['no_match']}, skipped={stats['skipped']}"
        )
        return stats
