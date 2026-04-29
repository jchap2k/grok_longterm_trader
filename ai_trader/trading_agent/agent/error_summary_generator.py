"""
Daily Error Summary Generator

Uses Ollama 30b to analyze full day's logs and create concise error summaries
that Claude/Cline can quickly read and act upon.

Creates human-readable summary reports in:
- ai_trader_data/error_summaries/YYYY-MM-DD_error_summary.md

Usage:
    generator = ErrorSummaryGenerator(ollama_provider)
    summary = generator.generate_daily_summary()
"""

import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class ErrorSummaryGenerator:
    """Generates daily error summaries for AI assistant review."""

    def __init__(self, ollama_provider=None):
        """
        Initialize generator.
        
        Args:
            ollama_provider: OllamaProvider for intelligent analysis
        """
        self.ollama = ollama_provider
        self.log_dir = Path(__file__).parent.parent.parent / "ai_trader_data" / "logs"
        self.summary_dir = Path(__file__).parent.parent.parent / "ai_trader_data" / "error_summaries"
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        
    def generate_daily_summary(self) -> Dict:
        """
        Generate comprehensive error summary for today.
        
        Returns:
            Dict with summary data and file path
        """
        logger.info("Generating daily error summary...")
        
        # Get today's logs
        log_content = self._get_todays_logs()
        if not log_content:
            logger.warning("No logs found for summary generation")
            return {"status": "no_logs", "summary_file": None}
        
        # Extract and categorize errors
        categorized_errors = self._categorize_errors(log_content)
        
        if not any(categorized_errors.values()):
            logger.info("No errors found - clean trading day!")
            return self._generate_clean_day_summary()
        
        # Use Ollama to analyze and create summary
        if self.ollama and self.ollama.is_available():
            summary_text = self._generate_ai_summary(categorized_errors, log_content)
        else:
            summary_text = self._generate_template_summary(categorized_errors)
        
        # Save summary to file
        summary_file = self._save_summary(summary_text, categorized_errors)
        
        logger.info(f"Error summary saved: {summary_file.name}")
        
        return {
            "status": "generated",
            "summary_file": str(summary_file),
            "error_count": sum(len(errors) for errors in categorized_errors.values()),
            "categories": list(categorized_errors.keys())
        }
    
    def _get_todays_logs(self) -> str:
        """Get all of today's log content."""
        try:
            today = datetime.now().strftime("%Y%m%d")
            log_files = []
            
            # Check 2026 directory
            year_dir = self.log_dir / "2026"
            if year_dir.exists():
                log_files.extend(list(year_dir.glob(f"*{today}*.log")))
            
            if not log_files:
                logger.warning(f"No log files found for {today}")
                return ""
            
            # Combine all log files
            full_content = []
            for log_file in sorted(log_files):
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    full_content.append(f.read())
            
            logger.info(f"Loaded {len(log_files)} log file(s) for analysis")
            return "\n\n".join(full_content)
            
        except Exception as e:
            logger.error(f"Error reading logs: {e}")
            return ""
    
    def _categorize_errors(self, log_content: str) -> Dict[str, List[Dict]]:
        """Categorize errors by type with priority-based detection."""
        categories = defaultdict(list)
        
        # Split into lines
        lines = log_content.split('\n')
        
        for i, line in enumerate(lines):
            # Skip non-error lines
            if not any(keyword in line.lower() for keyword in ['error', 'failed', 'exception', 'critical', 'warning']):
                continue
            
            # Extract timestamp if present
            timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            timestamp = timestamp_match.group(1) if timestamp_match else "unknown"
            
            # Categorize by content (priority order - most critical first)
            error_entry = {
                "timestamp": timestamp,
                "line": line.strip(),
                "context": self._get_context(lines, i, window=2)
            }
            
            # CRITICAL: Order Execution Failures (highest priority)
            if any(keyword in line.lower() for keyword in [
                'failed to sell', 'failed to buy', 'failed to close', 'order rejected',
                'order failed', 'could not place order', 'insufficient funds',
                'position closure failed', 'emergency close failed', 'cannot sell'
            ]):
                categories['🔴 CRITICAL: Order Execution Failures'].append(error_entry)
            
            # CRITICAL: WebSocket/Stream Failures
            elif any(keyword in line.lower() for keyword in [
                'websocket', 'stream', 'connection lost', 'reconnect failed',
                'stream error', 'stream failed', 'disconnected', 'connection unhealthy',
                'websocket closed', 'stream disconnected'
            ]):
                categories['🔴 CRITICAL: WebSocket/Stream Failures'].append(error_entry)
            
            # CRITICAL: Position Management Failures
            elif any(keyword in line.lower() for keyword in [
                'bracket order failed', 'stop loss failed', 'take profit failed',
                'moc order failed', 'protection failed', 'shares unavailable',
                'insufficient qty', 'positions still open', 'failed to protect'
            ]):
                categories['🔴 CRITICAL: Position Management Failures'].append(error_entry)
            
            # HIGH: Broker Communication Errors
            elif any(keyword in line.lower() for keyword in [
                'broker', 'alpaca', 'schwab', 'account info failed',
                'get_quote failed', 'broker timeout', 'broker error', 'broker unavailable'
            ]):
                categories['🟠 HIGH: Broker Communication Errors'].append(error_entry)
            
            # HIGH: API Errors (rate limits, timeouts)
            elif any(keyword in line.lower() for keyword in [
                'api', 'rate limit', 'request failed', '429', '500', '503',
                'http error', 'timeout', 'overloaded', 'too many requests'
            ]):
                categories['🟠 HIGH: API Errors'].append(error_entry)
            
            # MEDIUM: Market Data Errors
            elif any(keyword in line.lower() for keyword in [
                'market data', 'price', 'quote', 'candle', 'data unavailable',
                'invalid data', 'data error', 'quote failed'
            ]):
                categories['🟡 MEDIUM: Market Data Errors'].append(error_entry)
            
            # MEDIUM: Worker Pool/Ollama Errors
            elif any(keyword in line.lower() for keyword in [
                'ollama', 'worker', 'pool', 'vram', 'oom', 'cuda',
                'worker failed', 'worker crash', 'model load failed'
            ]):
                categories['🟡 MEDIUM: Worker Pool/Ollama Errors'].append(error_entry)
            
            # MEDIUM: Trading Logic Errors
            elif any(keyword in line.lower() for keyword in [
                'trade', 'signal', 'strategy', 'regime', 'scanner',
                'analysis failed', 'discovery failed'
            ]):
                categories['🟡 MEDIUM: Trading Logic Errors'].append(error_entry)
            
            # LOW: News/Analysis Errors
            elif any(keyword in line.lower() for keyword in [
                'news', 'brave', 'search', 'sentiment', 'catalyst',
                'headlines', 'article'
            ]):
                categories['🟢 LOW: News/Analysis Errors'].append(error_entry)
            
            # MEDIUM: System/Infrastructure
            elif any(keyword in line.lower() for keyword in [
                'connection', 'network', 'crash', 'recovery',
                'restart', 'shutdown', 'hang'
            ]):
                categories['🟡 MEDIUM: System/Infrastructure Errors'].append(error_entry)
            
            # Other
            else:
                categories['ℹ️ Other Errors'].append(error_entry)
        
        # Remove empty categories and limit entries per category
        return {
            cat: errors[:20]  # Max 20 errors per category
            for cat, errors in categories.items()
            if errors
        }
    
    def _get_context(self, lines: List[str], index: int, window: int = 2) -> List[str]:
        """Get surrounding lines for context."""
        start = max(0, index - window)
        end = min(len(lines), index + window + 1)
        return [lines[i].strip() for i in range(start, end) if i != index and lines[i].strip()]
    
    def _generate_ai_summary(self, categorized_errors: Dict, log_content: str) -> str:
        """Use Ollama to generate intelligent error summary."""
        # Prepare error overview for Ollama
        error_overview = []
        for category, errors in categorized_errors.items():
            error_overview.append(f"\n## {category} ({len(errors)} errors)")
            for i, error in enumerate(errors[:5], 1):  # First 5 of each category
                error_overview.append(f"{i}. {error['line'][:200]}")
        
        overview_text = "\n".join(error_overview)
        
        prompt = f"""Analyze these errors from today's automated trading system and create a concise summary for a developer.

Error Categories:
{overview_text}

For each category with errors:
1. Identify the root cause
2. Assess severity (Critical/High/Medium/Low)
3. Suggest specific fixes
4. Indicate if it needs immediate attention

Focus on:
- Order execution failures (can't buy/sell)
- WebSocket/stream disconnections (lost market data)
- Position management issues (can't close positions)
- Broker communication failures

Format as a clear, actionable summary that a developer can quickly scan and understand what needs fixing.
Focus on patterns, not individual errors. Be concise but thorough."""

        system = (
            "You are a senior DevOps engineer reviewing trading system logs. "
            "Create clear, actionable error summaries that help developers quickly understand "
            "and fix issues. Prioritize trading-critical errors like order failures and disconnections. "
            "Focus on root causes and practical solutions."
        )
        
        try:
            ai_summary = self.ollama.complete(prompt, system=system)
            if ai_summary:
                return self._format_summary_with_ai(ai_summary, categorized_errors)
        except Exception as e:
            logger.error(f"AI summary generation failed: {e}")
        
        return self._generate_template_summary(categorized_errors)
    
    def _format_summary_with_ai(self, ai_summary: str, categorized_errors: Dict) -> str:
        """Format the AI summary with additional context."""
        today = datetime.now().strftime("%Y-%m-%d")
        total_errors = sum(len(errors) for errors in categorized_errors.values())
        
        # Count critical errors
        critical_count = sum(
            len(errors) for cat, errors in categorized_errors.items()
            if '🔴 CRITICAL' in cat
        )
        
        output = [
            f"# Daily Error Summary - {today}",
            "",
            f"**Total Errors Found:** {total_errors}",
            f"**Critical Errors:** {critical_count}",
            f"**Categories Affected:** {len(categorized_errors)}",
            ""
        ]
        
        if critical_count > 0:
            output.append("⚠️ **IMMEDIATE ATTENTION REQUIRED** - Critical errors detected!")
            output.append("")
        
        output.extend([
            "---",
            "",
            "## AI Analysis",
            "",
            ai_summary.strip(),
            "",
            "---",
            "",
            "## Detailed Error Breakdown",
            ""
        ])
        
        # Add detailed breakdown
        for category, errors in sorted(categorized_errors.items()):
            output.append(f"### {category} ({len(errors)} occurrences)")
            output.append("")
            
            # Group similar errors
            error_patterns = self._group_similar_errors(errors)
            
            for pattern, count in sorted(error_patterns.items(), key=lambda x: x[1], reverse=True):
                if count > 1:
                    output.append(f"- **{pattern}** (×{count})")
                else:
                    output.append(f"- {pattern}")
            
            output.append("")
        
        return "\n".join(output)
    
    def _generate_template_summary(self, categorized_errors: Dict) -> str:
        """Generate template-based summary (fallback)."""
        today = datetime.now().strftime("%Y-%m-%d")
        total_errors = sum(len(errors) for errors in categorized_errors.values())
        
        # Count critical errors
        critical_count = sum(
            len(errors) for cat, errors in categorized_errors.items()
            if '🔴 CRITICAL' in cat
        )
        
        output = [
            f"# Daily Error Summary - {today}",
            "",
            f"**Total Errors Found:** {total_errors}",
            f"**Critical Errors:** {critical_count}",
            f"**Categories Affected:** {len(categorized_errors)}",
            "",
            "⚠️ *AI analysis unavailable - using template summary*",
            ""
        ]
        
        if critical_count > 0:
            output.append("🚨 **IMMEDIATE ATTENTION REQUIRED** - Critical errors detected!")
            output.append("")
        
        output.append("---")
        output.append("")
        
        for category, errors in sorted(categorized_errors.items()):
            output.append(f"## {category} ({len(errors)} occurrences)")
            output.append("")
            
            # Group similar errors
            error_patterns = self._group_similar_errors(errors)
            
            for pattern, count in sorted(error_patterns.items(), key=lambda x: x[1], reverse=True)[:10]:
                if count > 1:
                    output.append(f"- **{pattern}** (×{count})")
                else:
                    output.append(f"- {pattern}")
            
            output.append("")
            
            # Add first few examples
            output.append("**Recent Examples:**")
            for error in errors[:3]:
                output.append(f"- `{error['timestamp']}`: {error['line'][:150]}")
            output.append("")
        
        output.extend([
            "---",
            "",
            "## Recommended Actions",
            ""
        ])
        
        if critical_count > 0:
            output.append("### URGENT:")
            output.append("1. Check WebSocket connections (if stream failures)")
            output.append("2. Verify broker API status (if order failures)")
            output.append("3. Review position closure logic (if management failures)")
            output.append("")
        
        output.extend([
            "### General:",
            "1. Review error categories above",
            "2. Check corresponding source files",
            "3. Run relevant test scripts",
            "4. Monitor for recurring patterns",
            ""
        ])
        
        return "\n".join(output)
    
    def _group_similar_errors(self, errors: List[Dict]) -> Dict[str, int]:
        """Group similar error messages."""
        patterns = defaultdict(int)
        
        for error in errors:
            # Extract error message (remove timestamps, numbers, variable values)
            line = error['line']
            
            # Normalize: remove timestamps, paths, numbers
            normalized = re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', '<TIME>', line)
            normalized = re.sub(r'[/\\][^\s]+\.(py|log)', '<FILE>', normalized)
            normalized = re.sub(r'\d+(\.\d+)?', '<NUM>', normalized)
            normalized = re.sub(r'[a-f0-9]{8,}', '<HASH>', normalized)
            
            # Take first 100 chars as pattern
            pattern = normalized[:100].strip()
            patterns[pattern] += 1
        
        return dict(patterns)
    
    def _generate_clean_day_summary(self) -> Dict:
        """Generate summary for error-free day."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        summary_text = f"""# Daily Error Summary - {today}

**Status:** ✅ CLEAN DAY - No Errors

**Total Errors Found:** 0

---

## Summary

No errors or warnings detected in today's logs. The trading system operated smoothly throughout the day.

**System Health:** Excellent
**Action Required:** None

---

*Keep up the good work! 🎉*
"""
        
        summary_file = self._save_summary(summary_text, {})
        
        return {
            "status": "clean",
            "summary_file": str(summary_file),
            "error_count": 0,
            "categories": []
        }
    
    def _save_summary(self, summary_text: str, categorized_errors: Dict) -> Path:
        """Save summary to markdown file."""
        today = datetime.now().strftime("%Y-%m-%d")
        summary_file = self.summary_dir / f"{today}_error_summary.md"
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(summary_text)
        
        # Also save JSON version for programmatic access
        json_file = self.summary_dir / f"{today}_error_summary.json"
        import json
        with open(json_file, 'w') as f:
            json.dump({
                "date": today,
                "total_errors": sum(len(errors) for errors in categorized_errors.values()),
                "critical_errors": sum(
                    len(errors) for cat, errors in categorized_errors.items()
                    if '🔴 CRITICAL' in cat
                ),
                "categories": {
                    cat: len(errors) for cat, errors in categorized_errors.items()
                },
                "summary_file": str(summary_file)
            }, f, indent=2)
        
        return summary_file


def generate_daily_error_summary(ollama_provider) -> Dict:
    """
    Generate daily error summary.
    
    Called by automated_scheduler at end of day (4:00 PM).
    Creates markdown summary that Claude/Cline can read.
    
    Args:
        ollama_provider: OllamaProvider instance
        
    Returns:
        Dict with summary results
    """
    logger.info("=" * 70)
    logger.info("DAILY ERROR SUMMARY GENERATION")
    logger.info("=" * 70)
    
    generator = ErrorSummaryGenerator(ollama_provider)
    result = generator.generate_daily_summary()
    
    if result['status'] == 'clean':
        logger.info("Clean day - no errors found!")
    elif result['status'] == 'generated':
        logger.info(f"Error summary generated: {result['error_count']} errors across {len(result['categories'])} categories")
        logger.info(f"   Summary file: {result['summary_file']}")
        
        # Log categories (Windows-friendly - strip emoji prefixes)
        if result.get('categories'):
            critical_cats = [c for c in result['categories'] if 'CRITICAL' in c]
            if critical_cats:
                logger.warning(f"   CRITICAL ERRORS DETECTED: {len(critical_cats)} critical category(ies)")
                for cat in critical_cats:
                    clean_name = cat.split(':', 1)[-1].strip() if ':' in cat else cat
                    logger.warning(f"     - {clean_name}")
    else:
        logger.warning("Could not generate summary - no logs available")
    
    logger.info("=" * 70)
    
    return result