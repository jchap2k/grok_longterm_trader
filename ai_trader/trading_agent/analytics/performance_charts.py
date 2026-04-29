"""
Performance Charts - Visual trade analysis with price charts

Generates charts showing:
- Price movement throughout the day
- Entry/exit points with annotations
- Partial sells, stop losses, take profits
- Strategy used for each trade
- R:R ratio visualization
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import matplotlib, but make it optional
try:
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import Rectangle
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logger.warning("matplotlib not installed - performance charts will not be generated. Install with: pip install matplotlib")

# Try to import pandas, but make it optional
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    logger.warning("pandas not installed - some chart features may be limited")


class PerformanceChartGenerator:
    """
    Generate visual performance reports for trading activity.

    Creates charts showing:
    - Intraday price movement
    - Buy/sell markers
    - Partial profit taking
    - Stop loss and take profit levels
    - Strategy annotations
    """

    def __init__(self, data_provider=None):
        """
        Initialize chart generator.

        Args:
            data_provider: Market data provider for fetching price data
        """
        self.data_provider = data_provider
        # Get path relative to this script file location
        script_dir = Path(__file__).parent  # analytics/
        ai_trader_dir = script_dir.parent.parent  # trading_agent/ -> ai_trader/
        self.charts_base_dir = ai_trader_dir / "ai_trader_data" / "charts"
        
    def _get_charts_dir(self, report_date: date) -> Path:
        """
        Get organized charts directory for a specific date.
        
        Creates directory structure: charts/YYYY/Month_YYYY/
        HTML files go directly in month folder, PNGs go in pngs/ subfolder.
        
        Args:
            report_date: Date for the charts
            
        Returns:
            Path to the charts directory for this date
        """
        year = report_date.year
        month_name = report_date.strftime('%B')  # Full month name
        
        # Create organized directory structure like logs
        charts_dir = self.charts_base_dir / str(year) / f"{month_name}_{year}"
        charts_dir.mkdir(parents=True, exist_ok=True)
        
        return charts_dir
        
    def _get_pngs_dir(self, report_date: date) -> Path:
        """
        Get PNG subfolder within the month directory.
        
        Creates: charts/YYYY/Month_YYYY/pngs/
        
        Args:
            report_date: Date for the charts
            
        Returns:
            Path to the PNG directory for this date
        """
        charts_dir = self._get_charts_dir(report_date)
        pngs_dir = charts_dir / "pngs"
        pngs_dir.mkdir(parents=True, exist_ok=True)
        
        return pngs_dir

    def generate_daily_report(self, trade_log: List[Dict], strategy_log: List[Dict],
                             report_date: Optional[date] = None,
                             price_snapshots: Optional[Dict] = None) -> str:
        """
        Generate comprehensive daily performance report with charts.

        Args:
            trade_log: List of all trades from the day
            strategy_log: List of strategy changes
            report_date: Date of report (defaults to today)
            price_snapshots: Dict of price snapshots {symbol: [{timestamp, price, ...}]}

        Returns:
            Path to generated HTML report
        """
        if not HAS_MATPLOTLIB:
            logger.error("matplotlib not installed - cannot generate charts. Install with: pip install matplotlib")
            raise ImportError("matplotlib is required for chart generation. Install with: pip install matplotlib")

        self.price_snapshots = price_snapshots or {}
        if report_date is None:
            report_date = datetime.now().date()

        logger.info(f"Generating performance charts for {report_date}")

        # Group trades by symbol
        trades_by_symbol = self._group_trades_by_symbol(trade_log)

        # Generate chart for each symbol
        chart_files = []
        for symbol, trades in trades_by_symbol.items():
            try:
                chart_path = self._generate_symbol_chart(symbol, trades, report_date)
                if chart_path:
                    chart_files.append(chart_path)
            except Exception as e:
                logger.error(f"Failed to generate chart for {symbol}: {e}", exc_info=True)

        # Generate HTML report combining all charts
        report_path = self._generate_html_report(
            chart_files,
            trades_by_symbol,
            strategy_log,
            report_date
        )

        logger.info(f"Performance report generated: {report_path}")
        return str(report_path)

    def _group_trades_by_symbol(self, trade_log: List[Dict]) -> Dict[str, List[Dict]]:
        """Group trades by symbol."""
        trades_by_symbol = {}

        for trade in trade_log:
            symbol = trade.get('symbol')
            if symbol:
                if symbol not in trades_by_symbol:
                    trades_by_symbol[symbol] = []
                trades_by_symbol[symbol].append(trade)

        return trades_by_symbol

    def _resample_to_minute_bars(self, price_data: List[Dict]) -> List[Dict]:
        """
        Resample tick-level data to 1-minute OHLC bars for cleaner charts.

        Args:
            price_data: List of {time: datetime, price: float}

        Returns:
            Resampled list with 1-minute bar close prices
        """
        if not HAS_PANDAS or len(price_data) < 10:
            return price_data

        try:
            # Convert to DataFrame
            df = pd.DataFrame(price_data)
            df['time'] = pd.to_datetime(df['time'])
            df = df.set_index('time')

            # Resample to 1-minute bars, use last price (close)
            resampled = df['price'].resample('1min').ohlc().dropna()

            # Convert back to list format using close prices
            result = []
            for idx, row in resampled.iterrows():
                result.append({
                    'time': idx.to_pydatetime(),
                    'price': row['close']
                })

            logger.info(f"Resampled {len(price_data)} ticks to {len(result)} minute bars")
            return result if len(result) >= 2 else price_data

        except Exception as e:
            logger.warning(f"Failed to resample data: {e}, using raw data")
            return price_data

    def _generate_symbol_chart(self, symbol: str, trades: List[Dict],
                               report_date: date) -> Optional[str]:
        """
        Generate price chart for a single symbol with trade annotations.

        Args:
            symbol: Stock symbol
            trades: List of trades for this symbol
            report_date: Date of trading

        Returns:
            Path to generated chart image
        """
        # Get intraday price data
        price_data = self._get_intraday_data(symbol, report_date)

        if not price_data or len(price_data) == 0:
            logger.warning(f"No price data available for {symbol}")
            return None

        # Resample to minute bars if we have too many data points (tick data)
        if len(price_data) > 100:
            price_data = self._resample_to_minute_bars(price_data)

        # Set up dark theme styling to match dashboard
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(14, 8), facecolor='#1a1a2e')
        ax.set_facecolor('#1a1a2e')

        # Plot price data - filter out any invalid entries
        times = []
        prices = []
        for p in price_data:
            t = p.get('time')
            price = p.get('price')
            # Skip entries with invalid timestamps or prices
            if t is None or price is None or price <= 0:
                continue
            if not isinstance(t, datetime):
                continue
            times.append(t)
            prices.append(price)

        if len(times) < 2:
            logger.warning(f"Not enough valid data points for {symbol} chart")
            return None

        # Plot price line with gradient effect
        ax.plot(times, prices, linewidth=2.5, color='#00d4ff', label='Price', zorder=3)

        # Add subtle fill under the line
        ax.fill_between(times, min(prices) * 0.995, prices, alpha=0.15, color='#00d4ff')

        # Track labels already added to legend to avoid duplicates
        self._legend_labels_added = set()

        # Add trade annotations
        self._add_trade_markers(ax, trades, times, prices)

        # Add support/resistance levels (entry, stop, target)
        self._add_support_resistance_levels(ax, trades, times)

        # Formatting with improved styling
        ax.set_xlabel('Time (ET)', fontsize=12, fontweight='bold', color='#e0e0e0')
        ax.set_ylabel('Price ($)', fontsize=12, fontweight='bold', color='#e0e0e0')
        ax.set_title(f'{symbol} - Trade Performance Chart ({report_date})',
                    fontsize=16, fontweight='bold', pad=20, color='#ffffff')

        # Smart x-axis formatting - limit number of ticks based on data range
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        # Use AutoDateLocator for smart tick placement, limit to ~8 ticks max
        locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
        ax.xaxis.set_major_locator(locator)

        # Rotate labels and add padding
        plt.xticks(rotation=45, ha='right', fontsize=10, color='#b0b0b0')
        plt.yticks(fontsize=10, color='#b0b0b0')

        # Grid with subtle styling
        ax.grid(True, alpha=0.2, linestyle='--', color='#404060')

        # Style the spines
        for spine in ax.spines.values():
            spine.set_color('#404060')
            spine.set_linewidth(0.5)

        # Legend - positioned outside to avoid overlap, remove duplicates
        handles, labels = ax.get_legend_handles_labels()
        # Remove duplicates while preserving order
        unique = []
        unique_labels = []
        for h, l in zip(handles, labels):
            if l not in unique_labels:
                unique.append(h)
                unique_labels.append(l)

        if unique:
            legend = ax.legend(unique, unique_labels, loc='upper left',
                             bbox_to_anchor=(1.02, 1), framealpha=0.9,
                             facecolor='#252540', edgecolor='#404060',
                             fontsize=9, labelcolor='#e0e0e0')

        # Add strategy info box
        self._add_strategy_info_box(ax, trades)

        # Tight layout with room for legend
        plt.tight_layout()
        fig.subplots_adjust(right=0.82)  # Make room for external legend

        # Save chart to PNG subfolder
        chart_filename = f"{symbol}_{report_date.strftime('%Y%m%d')}.png"
        pngs_dir = self._get_pngs_dir(report_date)
        chart_path = pngs_dir / chart_filename
        plt.savefig(chart_path, dpi=150, bbox_inches='tight',
                   facecolor='#1a1a2e', edgecolor='none')
        plt.close()

        # Reset style for future plots
        plt.style.use('default')

        return str(chart_path)

    def _get_intraday_data(self, symbol: str, report_date: date) -> List[Dict]:
        """
        Get intraday price data for a symbol.

        Returns list of {time: datetime, price: float}
        """
        # First, check if we have price snapshots from the agent
        if self.price_snapshots and symbol in self.price_snapshots:
            snapshots = self.price_snapshots[symbol]
            if snapshots:
                logger.info(f"Using {len(snapshots)} price snapshots for {symbol}")
                price_data = []
                for snap in snapshots:
                    try:
                        price_data.append({
                            'time': datetime.fromisoformat(snap['timestamp']),
                            'price': snap['price']
                        })
                    except:
                        pass
                if price_data:
                    return price_data

        # Fall back to data provider
        if not self.data_provider:
            # Return mock data for testing
            logger.warning(f"No data provider - using mock data for {symbol}")
            return self._generate_mock_intraday_data(symbol)

        try:
            # Get 1-day intraday data with 5-minute bars
            bars = self.data_provider.get_historical_data(
                symbol=symbol,
                days_back=1,
                timeframe="5Min"
            )

            if not bars:
                return []

            # Convert to our format
            price_data = []
            for bar in bars:
                timestamp = bar.get('timestamp') or bar.get('time')
                # Ensure timestamp is a datetime object
                if timestamp is None:
                    continue
                if isinstance(timestamp, (int, float)):
                    # Assume epoch timestamp
                    timestamp = datetime.fromtimestamp(timestamp)
                elif isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    except:
                        continue
                price_data.append({
                    'time': timestamp,
                    'price': bar.get('close', 0)
                })

            return price_data

        except Exception as e:
            logger.error(f"Failed to fetch intraday data for {symbol}: {e}")
            return []

    def _generate_mock_intraday_data(self, symbol: str) -> List[Dict]:
        """Generate mock intraday data for testing."""
        from datetime import datetime, timedelta

        # Generate 78 data points (6.5 hours * 12 per hour = 78 5-min bars)
        start_time = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
        base_price = 100.0  # Base price

        data = []
        for i in range(78):
            time = start_time + timedelta(minutes=i * 5)
            # Simulate some price movement
            price = base_price + np.random.randn() * 2.0 + (i * 0.1)  # Slight uptrend
            data.append({
                'time': time,
                'price': max(price, 50.0)  # Ensure positive
            })

        return data

    def _add_trade_markers(self, ax, trades: List[Dict], times: List, prices: List):
        """Add markers for buy/sell actions."""
        # Track which marker types have been added to legend
        legend_added = set()
        annotation_positions = []  # Track positions to avoid overlap

        for trade in trades:
            timestamp_str = trade.get('timestamp', '')
            if not timestamp_str:
                continue

            # Parse timestamp
            try:
                trade_time = datetime.fromisoformat(timestamp_str)
                # Convert to matplotlib date number if needed
                trade_time_num = mdates.date2num(trade_time)
            except:
                continue

            # Find closest price point
            price = trade.get('price', 0)
            if price <= 0:
                continue

            side = trade.get('side', '').lower()
            quantity = trade.get('quantity', 0)
            reason = trade.get('reason', '')

            # Determine marker style based on action - use generic labels for legend
            if side == 'buy':
                marker = '^'
                color = '#00ff88'  # Bright green for dark theme
                size = 250
                marker_type = 'BUY'
                display_label = f'BUY {quantity}'
            else:  # sell
                if 'partial' in reason.lower():
                    marker = 'v'
                    color = '#ffd700'  # Gold for dark theme
                    size = 200
                    marker_type = 'PARTIAL SELL'
                    display_label = f'PARTIAL {quantity}'
                elif 'stop' in reason.lower():
                    marker = 'X'
                    color = '#ff4757'  # Bright red for dark theme
                    size = 250
                    marker_type = 'STOP LOSS'
                    display_label = f'STOP {quantity}'
                elif 'target' in reason.lower() or 'profit' in reason.lower():
                    marker = 'v'
                    color = '#00ff88'  # Bright green
                    size = 250
                    marker_type = 'TAKE PROFIT'
                    display_label = f'TP {quantity}'
                else:
                    marker = 'v'
                    color = '#00d4ff'  # Cyan for dark theme
                    size = 200
                    marker_type = 'SELL'
                    display_label = f'SELL {quantity}'

            # Only add label to legend once per marker type
            legend_label = marker_type if marker_type not in legend_added else None
            if legend_label:
                legend_added.add(marker_type)

            # Plot marker using datetime number for x-coordinate
            ax.scatter(trade_time_num, price, marker=marker, s=size,
                      color=color, edgecolors='white', linewidths=2,
                      zorder=5, label=legend_label)

            # Calculate annotation offset to avoid overlap
            base_offset_y = 25 if side == 'buy' else -35
            offset_x = 15

            # Check for nearby annotations and adjust
            for prev_time, prev_price, prev_offset in annotation_positions:
                time_diff = abs(trade_time_num - prev_time)
                price_diff = abs(price - prev_price)
                # If annotations would overlap, offset more
                if time_diff < 0.01 and price_diff < (max(prices) - min(prices)) * 0.05:
                    base_offset_y += 30 if side == 'buy' else -30

            annotation_positions.append((trade_time_num, price, base_offset_y))

            # Simplified annotation - just price and quantity
            annotation = f"${price:.2f}\n{display_label}"

            ax.annotate(annotation, xy=(trade_time_num, price),
                       xytext=(offset_x, base_offset_y),
                       textcoords='offset points',
                       fontsize=9,
                       fontweight='bold',
                       color='white',
                       bbox=dict(boxstyle='round,pad=0.4', fc=color, alpha=0.85,
                               edgecolor='white', linewidth=1),
                       arrowprops=dict(arrowstyle='->', color='white', lw=1.5),
                       zorder=6)

    def _add_support_resistance_levels(self, ax, trades: List[Dict], times: List):
        """Add horizontal lines for entry, stop loss, and take profit levels."""
        # Find the first BUY trade to get entry/stop/target
        entry_price = None
        stop_loss = None
        take_profit = None

        for trade in trades:
            if trade.get('side', '').lower() == 'buy':
                entry_price = trade.get('entry_price') or trade.get('price')
                stop_loss = trade.get('stop_loss')
                take_profit = trade.get('take_profit')
                break

        if not times:
            return

        x_min, x_max = times[0], times[-1]

        # Entry level
        if entry_price:
            ax.axhline(y=entry_price, color='#00d4ff', linestyle='--',
                      linewidth=2, alpha=0.7, label=f'Entry: ${entry_price:.2f}')

        # Stop loss level
        if stop_loss:
            ax.axhline(y=stop_loss, color='#ff4757', linestyle='--',
                      linewidth=2, alpha=0.7, label=f'Stop: ${stop_loss:.2f}')

            # Add subtle shaded region below stop
            ax.fill_between([x_min, x_max], stop_loss - 10, stop_loss,
                           color='#ff4757', alpha=0.08)

        # Take profit level
        if take_profit:
            ax.axhline(y=take_profit, color='#00ff88', linestyle='--',
                      linewidth=2, alpha=0.7, label=f'Target: ${take_profit:.2f}')

            # Add subtle shaded region above target
            ax.fill_between([x_min, x_max], take_profit, take_profit + 10,
                           color='#00ff88', alpha=0.08)

        # Calculate and display R:R if available
        if entry_price and stop_loss and take_profit:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            rr_ratio = reward / risk if risk > 0 else 0

            # Add R:R annotation with dark theme styling
            ax.text(0.98, 0.02, f'R:R = {rr_ratio:.2f}',
                   transform=ax.transAxes,
                   fontsize=14, fontweight='bold',
                   color='white',
                   verticalalignment='bottom', horizontalalignment='right',
                   bbox=dict(boxstyle='round,pad=0.5', facecolor='#6c5ce7',
                           alpha=0.9, edgecolor='white', linewidth=1))

    def _add_strategy_info_box(self, ax, trades: List[Dict]):
        """Add info box showing strategy and trade details."""
        # Get strategy from first trade
        strategy = trades[0].get('strategy') if trades else None
        if not strategy or strategy is None:
            strategy = 'Unknown'

        # Safely convert to string and uppercase
        strategy_str = str(strategy).upper()

        # Count trades
        buys = sum(1 for t in trades if t.get('side', '').lower() == 'buy')
        sells = sum(1 for t in trades if t.get('side', '').lower() == 'sell')

        # Calculate P/L if we have entry and exit
        total_pnl = 0
        for trade in trades:
            if trade.get('pnl'):
                total_pnl += trade.get('pnl', 0)

        # Build info text
        info_text = f"Strategy: {strategy_str}\n"
        info_text += f"Buys: {buys}  |  Sells: {sells}"
        if total_pnl != 0:
            pnl_color = '#00ff88' if total_pnl > 0 else '#ff4757'
            info_text += f"\nP/L: ${total_pnl:+.2f}"

        # Add text box with dark theme styling
        ax.text(0.02, 0.98, info_text,
               transform=ax.transAxes,
               fontsize=11,
               fontweight='bold',
               color='white',
               verticalalignment='top',
               bbox=dict(boxstyle='round,pad=0.6', facecolor='#252540',
                       alpha=0.95, edgecolor='#6c5ce7', linewidth=2))

    def _generate_html_report(self, chart_files: List[str],
                             trades_by_symbol: Dict[str, List[Dict]],
                             strategy_log: List[Dict],
                             report_date: date) -> str:
        """
        Generate HTML report combining all charts and trade details.

        Args:
            chart_files: List of paths to chart images
            trades_by_symbol: Dict of trades grouped by symbol
            strategy_log: List of strategy changes
            report_date: Report date

        Returns:
            Path to HTML report
        """
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Trading Performance Report - {report_date}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #2E86AB;
            text-align: center;
            border-bottom: 3px solid #2E86AB;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #118AB2;
            margin-top: 30px;
            border-left: 5px solid #118AB2;
            padding-left: 10px;
        }}
        .summary {{
            background-color: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }}
        .chart-container {{
            background-color: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }}
        .chart-container img {{
            width: 100%;
            max-width: 1200px;
            height: auto;
            display: block;
            margin: 0 auto;
        }}
        .trade-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        .trade-table th {{
            background-color: #2E86AB;
            color: white;
            padding: 12px;
            text-align: left;
        }}
        .trade-table td {{
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }}
        .trade-table tr:hover {{
            background-color: #f0f0f0;
        }}
        .buy {{
            color: #06D6A0;
            font-weight: bold;
        }}
        .sell {{
            color: #118AB2;
            font-weight: bold;
        }}
        .stop-loss {{
            color: #EF476F;
            font-weight: bold;
        }}
        .strategy-badge {{
            display: inline-block;
            padding: 5px 10px;
            border-radius: 5px;
            background-color: #FFD23F;
            color: #333;
            font-weight: bold;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <h1>📊 Trading Performance Report</h1>
    <p style="text-align: center; font-size: 1.2em; color: #666;">
        {report_date.strftime('%B %d, %Y')}
    </p>

    <div class="summary">
        <h2>Strategy Timeline</h2>
"""

        # Add strategy changes
        if strategy_log:
            html_content += "<ul>"
            for entry in strategy_log:
                timestamp = entry.get('timestamp', '')
                time_str = timestamp.split('T')[1][:8] if 'T' in timestamp else timestamp
                strategy = entry.get('strategy', 'Unknown')
                reason = entry.get('reason', '')

                html_content += f"""
                <li>
                    <strong>{time_str}</strong> -
                    <span class="strategy-badge">{strategy.upper()}</span><br>
                    <em>{reason}</em>
                </li>
"""
            html_content += "</ul>"
        else:
            html_content += "<p>No strategy changes recorded.</p>"

        html_content += """
    </div>
"""

        # Add charts and trade details for each symbol
        for symbol, trades in sorted(trades_by_symbol.items()):
            html_content += f"""
    <div class="chart-container">
        <h2>{symbol}</h2>
"""

            # Find corresponding chart
            chart_file = None
            for cf in chart_files:
                if symbol in cf:
                    chart_file = cf
                    break

            if chart_file:
                # Make path relative for HTML - PNGs are now in pngs/ subfolder
                relative_path = f"pngs/{Path(chart_file).name}"
                html_content += f'<img src="{relative_path}" alt="{symbol} Chart">\n'

            # Trade table
            html_content += """
        <h3>Trade Details</h3>
        <table class="trade-table">
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Action</th>
                    <th>Quantity</th>
                    <th>Price</th>
                    <th>Strategy</th>
                    <th>R:R</th>
                    <th>Reason</th>
                </tr>
            </thead>
            <tbody>
"""

            for trade in trades:
                timestamp = trade.get('timestamp', '')
                time_str = timestamp.split('T')[1][:8] if 'T' in timestamp else ''
                side = trade.get('side', '').upper()
                quantity = trade.get('quantity', 0)
                price = trade.get('price', 0)
                strategy = trade.get('strategy', 'Unknown')
                rr = trade.get('risk_reward')
                rr_str = f"{rr:.2f}" if rr else "N/A"
                reason = trade.get('reason', '')

                # Determine CSS class
                if 'stop' in reason.lower():
                    side_class = 'stop-loss'
                elif side == 'BUY':
                    side_class = 'buy'
                else:
                    side_class = 'sell'

                html_content += f"""
                <tr>
                    <td>{time_str}</td>
                    <td class="{side_class}">{side}</td>
                    <td>{quantity}</td>
                    <td>${price:.2f}</td>
                    <td>{strategy}</td>
                    <td>{rr_str}</td>
                    <td>{reason}</td>
                </tr>
"""

            html_content += """
            </tbody>
        </table>
    </div>
"""

        html_content += """
</body>
</html>
"""

        # Save HTML report to organized directory
        report_filename = f"performance_report_{report_date.strftime('%Y%m%d')}.html"
        charts_dir = self._get_charts_dir(report_date)
        report_path = charts_dir / report_filename

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        return str(report_path)


# Convenience function
def generate_performance_report(trade_log: List[Dict], strategy_log: List[Dict],
                               data_provider=None, report_date: Optional[date] = None,
                               price_snapshots: Optional[Dict] = None) -> str:
    """
    Generate performance report with charts.

    Args:
        trade_log: List of trades from the day
        strategy_log: List of strategy changes
        data_provider: Market data provider (optional)
        report_date: Date of report (defaults to today)
        price_snapshots: Dict of price snapshots from agent (optional)

    Returns:
        Path to HTML report
    """
    generator = PerformanceChartGenerator(data_provider)
    return generator.generate_daily_report(trade_log, strategy_log, report_date, price_snapshots)
