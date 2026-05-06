"""
Token Usage Tracker - Track LLM API token usage and costs for multiple providers

Monitors token consumption per trading session and provides cost analysis.
Supports multiple LLM providers: Claude, Grok, etc.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List


class TokenTracker:
    """
    Track LLM API token usage and calculate costs across multiple providers.

    Pricing per million tokens (updated 2026-05-05):

    Anthropic Claude (Sonnet):
    - Input: $3.00 per million
    - Output: $15.00 per million

    xAI Grok:
    - grok-4.3: $1.25 input / $2.50 output
    - grok-4-1-fast-* models retire on 2026-05-15 and should not be defaults

    Perplexity Sonar:
    - sonar: $1 input / $1 output plus request/search-context fees
    """

    # Pricing per million tokens by provider
    PRICING = {
        'claude': {
            'input': 3.00,
            'output': 15.00
        },
        'grok': {
            'input': 1.25,
            'output': 2.50
        },
        'perplexity': {
            'input': 1.00,
            'output': 1.00
        },
        'anthropic': {  # Alias for claude
            'input': 3.00,
            'output': 15.00
        },
        'xai': {  # Alias for grok
            'input': 1.25,
            'output': 2.50
        },
        # Per-model pricing (xAI)
        'grok-4.3': {'input': 1.25, 'output': 2.50},
        'grok-4.20-reasoning': {'input': 1.25, 'output': 2.50},
        'grok-4.20-non-reasoning': {'input': 1.25, 'output': 2.50},
        'grok-4.20-multi-agent': {'input': 1.25, 'output': 2.50},
        # Deprecated on 2026-05-15; retained only for historical log accounting.
        'grok-4-fast-reasoning': {'input': 0.20, 'output': 0.50},
        'grok-4-1-fast-reasoning': {'input': 0.20, 'output': 0.50},
        'grok-4-fast-non-reasoning': {'input': 0.20, 'output': 0.50},
        'grok-4-1-fast-non-reasoning': {'input': 0.20, 'output': 0.50},
        'grok-code-fast-1': {'input': 0.20, 'output': 1.50},
        'grok-4.20-beta-0309-reasoning': {'input': 2.00, 'output': 6.00},
        'grok-4.20-multi-agent-beta-0309': {'input': 2.00, 'output': 6.00},
        # Perplexity direct Sonar API token pricing; request fees are tracked separately.
        'sonar': {'input': 1.00, 'output': 1.00},
        'sonar-pro': {'input': 3.00, 'output': 15.00},
        'sonar-reasoning-pro': {'input': 2.00, 'output': 8.00},
        'sonar-deep-research': {'input': 2.00, 'output': 8.00}
    }

    # Legacy constants for backward compatibility
    INPUT_COST_PER_MILLION = 3.00
    OUTPUT_COST_PER_MILLION = 15.00

    def __init__(self, log_dir: Path = None):
        """
        Initialize token tracker.

        Args:
            log_dir: Directory to save token logs (default: ai_trader_data/token_logs)
        """
        if log_dir is None:
            log_dir = Path(__file__).parent.parent.parent / "ai_trader_data" / "token_logs"

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Session tracking
        self.session_start = datetime.now()
        self.session_calls: List[Dict[str, Any]] = []

        # Cumulative totals for current session
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0

        # Per-provider tracking
        self.usage_by_provider: Dict[str, Dict[str, int]] = {}

        # Load today's existing token data if available
        self._load_today_tokens()

    def _load_today_tokens(self):
        """
        Load today's existing token data from log file and add to current session.

        This ensures token tracking persists across scheduler restarts.
        """
        try:
            today = datetime.now()
            year = today.strftime("%Y")
            month = today.strftime("%B_%Y")
            day = today.strftime("%d")

            # Check for today's log file
            session_log_dir = self.log_dir / year / month
            log_file = session_log_dir / f"tokens_{day}.json"

            if log_file.exists():
                with open(log_file, 'r') as f:
                    existing_data = json.load(f)

                # Load existing calls and add to current session
                if "calls" in existing_data:
                    self.session_calls.extend(existing_data["calls"])

                    # Recalculate totals from all calls (existing + new)
                    self.total_input_tokens = sum(call["input_tokens"] for call in self.session_calls)
                    self.total_output_tokens = sum(call["output_tokens"] for call in self.session_calls)
                    self.total_calls = len(self.session_calls)

                print(f"Loaded {len(existing_data.get('calls', []))} existing token calls from today")

        except Exception as e:
            # Don't fail if we can't load existing data - just start fresh
            print(f"Could not load existing token data: {e}")

    def _get_provider_from_model(self, model: str) -> str:
        """
        Determine provider from model name.

        Args:
            model: Model name (e.g., "claude-sonnet-4", "grok-beta")

        Returns:
            Provider name (e.g., "claude", "grok")
        """
        model_lower = model.lower()

        if 'claude' in model_lower or 'anthropic' in model_lower or 'sonnet' in model_lower:
            return 'claude'
        elif 'grok' in model_lower or 'xai' in model_lower:
            return 'grok'
        else:
            # Default to claude for backward compatibility
            return 'claude'

    def record_api_call(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        context: str = "",
        provider: str = None
    ):
        """
        Record a single LLM API call.

        Args:
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
            model: Model name used
            context: Optional context (e.g., "market_open", "trade_update")
            provider: Provider name (e.g., "claude", "grok"). Auto-detected if not provided.
        """
        timestamp = datetime.now()

        # Auto-detect provider if not specified
        if provider is None:
            provider = self._get_provider_from_model(model)

        # Get pricing for this provider
        pricing = self.PRICING.get(provider, self.PRICING['claude'])  # Default to Claude pricing

        # Calculate costs
        input_cost = (input_tokens / 1_000_000) * pricing['input']
        output_cost = (output_tokens / 1_000_000) * pricing['output']
        total_cost = input_cost + output_cost

        # Record call
        call_data = {
            "timestamp": timestamp.isoformat(),
            "provider": provider,
            "context": context,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "total_cost_usd": round(total_cost, 6)
        }

        self.session_calls.append(call_data)

        # Update global totals
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1

        # Update per-provider totals
        if provider not in self.usage_by_provider:
            self.usage_by_provider[provider] = {
                'calls': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'total_cost': 0.0
            }

        self.usage_by_provider[provider]['calls'] += 1
        self.usage_by_provider[provider]['input_tokens'] += input_tokens
        self.usage_by_provider[provider]['output_tokens'] += output_tokens
        self.usage_by_provider[provider]['total_cost'] += total_cost

    def get_session_summary(self) -> Dict[str, Any]:
        """
        Get summary of current session's token usage.

        Returns:
            Dictionary with session statistics
        """
        total_tokens = self.total_input_tokens + self.total_output_tokens
        input_cost = (self.total_input_tokens / 1_000_000) * self.INPUT_COST_PER_MILLION
        output_cost = (self.total_output_tokens / 1_000_000) * self.OUTPUT_COST_PER_MILLION
        total_cost = input_cost + output_cost

        return {
            "session_start": self.session_start.isoformat(),
            "session_duration_minutes": (datetime.now() - self.session_start).total_seconds() / 60,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": total_tokens,
            "input_cost_usd": round(input_cost, 4),
            "output_cost_usd": round(output_cost, 4),
            "total_cost_usd": round(total_cost, 4),
            "avg_tokens_per_call": round(total_tokens / self.total_calls) if self.total_calls > 0 else 0,
            "avg_cost_per_call": round(total_cost / self.total_calls, 4) if self.total_calls > 0 else 0
        }

    def save_session_log(self):
        """Save session token log to file."""
        today = datetime.now()
        year = today.strftime("%Y")
        month = today.strftime("%B_%Y")
        day = today.strftime("%d")

        # Create directory structure
        session_log_dir = self.log_dir / year / month
        session_log_dir.mkdir(parents=True, exist_ok=True)

        # Create log file
        log_file = session_log_dir / f"tokens_{day}.json"

        # Prepare log data
        log_data = {
            "summary": self.get_session_summary(),
            "calls": self.session_calls
        }

        # Save to file
        with open(log_file, 'w') as f:
            json.dump(log_data, f, indent=2)

        return log_file

    def get_breakdown_by_context(self) -> Dict[str, Dict[str, Any]]:
        """
        Get token usage breakdown by context.

        Returns:
            Dictionary mapping context to usage stats
        """
        breakdown = {}

        for call in self.session_calls:
            context = call["context"] or "unknown"

            if context not in breakdown:
                breakdown[context] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0
                }

            breakdown[context]["calls"] += 1
            breakdown[context]["input_tokens"] += call["input_tokens"]
            breakdown[context]["output_tokens"] += call["output_tokens"]
            breakdown[context]["total_tokens"] += call["total_tokens"]
            breakdown[context]["total_cost_usd"] += call["total_cost_usd"]

        # Round costs
        for context in breakdown:
            breakdown[context]["total_cost_usd"] = round(breakdown[context]["total_cost_usd"], 4)

        return breakdown

    def get_breakdown_by_provider(self) -> Dict[str, Dict[str, Any]]:
        """
        Get token usage breakdown by provider.

        Returns:
            Dictionary mapping provider to usage stats
        """
        breakdown = {}

        for call in self.session_calls:
            provider = call.get("provider", "claude")  # Default to claude for old logs

            if provider not in breakdown:
                breakdown[provider] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0
                }

            breakdown[provider]["calls"] += 1
            breakdown[provider]["input_tokens"] += call["input_tokens"]
            breakdown[provider]["output_tokens"] += call["output_tokens"]
            breakdown[provider]["total_tokens"] += call["total_tokens"]
            breakdown[provider]["total_cost_usd"] += call["total_cost_usd"]

        # Round costs
        for provider in breakdown:
            breakdown[provider]["total_cost_usd"] = round(breakdown[provider]["total_cost_usd"], 4)

        return breakdown

    def print_summary(self):
        """Print formatted summary of session token usage."""
        summary = self.get_session_summary()
        breakdown_context = self.get_breakdown_by_context()
        breakdown_provider = self.get_breakdown_by_provider()

        print("\n" + "=" * 70)
        print("TOKEN USAGE SUMMARY")
        print("=" * 70)
        print(f"Session Duration:     {summary['session_duration_minutes']:.1f} minutes")
        print(f"Total API Calls:      {summary['total_calls']}")
        print(f"Total Tokens:         {summary['total_tokens']:,}")
        print(f"  - Input Tokens:     {summary['total_input_tokens']:,}")
        print(f"  - Output Tokens:    {summary['total_output_tokens']:,}")
        print(f"Total Cost:           ${summary['total_cost_usd']:.4f}")
        print(f"  - Input Cost:       ${summary['input_cost_usd']:.4f}")
        print(f"  - Output Cost:      ${summary['output_cost_usd']:.4f}")
        print(f"Avg Tokens/Call:      {summary['avg_tokens_per_call']:,}")
        print(f"Avg Cost/Call:        ${summary['avg_cost_per_call']:.4f}")

        if breakdown_provider:
            print("\nBREAKDOWN BY PROVIDER:")
            print("-" * 70)
            for provider, stats in breakdown_provider.items():
                print(f"{provider:15s}  {stats['calls']:3d} calls  "
                      f"{stats['total_tokens']:8,} tokens  ${stats['total_cost_usd']:.4f}")

        if breakdown_context:
            print("\nBREAKDOWN BY CONTEXT:")
            print("-" * 70)
            for context, stats in breakdown_context.items():
                print(f"{context:20s}  {stats['calls']:3d} calls  "
                      f"{stats['total_tokens']:8,} tokens  ${stats['total_cost_usd']:.4f}")

        print("=" * 70)
