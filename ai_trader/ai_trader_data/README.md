# ai_trader_data

Runtime data directory - not tracked in git (gitignored).

```
ai_trader_data/
  broker_config.json          # Active config: broker, AI provider, API settings
  learning.db                 # SQLite: learned patterns, backtest results, decision journal
  trading_lessons.json        # Validated lessons fed into Grok prompts
  scheduler_heartbeat.txt     # Last heartbeat timestamp (monitor if scheduler is alive)
  circuit_breaker_state.json  # Circuit breaker state (persists across restarts)
  brave_search_usage.json     # Brave API usage tracking (free tier: 2000/month)

  performance/
    daily/                    # Daily P&L summaries (JSON, one per trading day)
    weekly/                   # Weekly reports
    monthly/                  # Monthly reports

  exports/                    # Portfolio state snapshots at market close (JSON)
  charts/                     # Performance charts (HTML + PNG, organized by month)
  logs/                       # Trading logs (organized by month)
  token_logs/                 # API token usage per day (cost tracking)
```

`broker_config.json` is the main config file - see the root README for full documentation.
