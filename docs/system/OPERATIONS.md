# Long-Term Trader Operations

Run commands from `ai_trader/trading_agent`.

## Dry-Run A Ticker

```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --dry-run
```

## Run Research

```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --candidate-price 180 --benchmark-price 165
```

Use the expanded committee when a decision is high-value or borderline:

```powershell
python scripts/run_longterm_research.py --symbol AAPL --agent-preset decision_6
```

## Journal Commands

```powershell
python scripts/longterm_journal.py summary
python scripts/longterm_journal.py list --limit 10
python scripts/longterm_journal.py report --limit 10
python scripts/longterm_journal.py update-outcome --decision-id <id> --candidate-price 190 --benchmark-price 170 --notes "monthly review"
```

## Dry-Run Action Plan

```powershell
python scripts/longterm_action_plan.py --symbol NVDA --portfolio-state path\to\portfolio.json --decision-file path\to\decision.json
```

## Next-Actions Report

```powershell
python scripts/longterm_next_actions.py --portfolio-state path\to\portfolio.json --limit 10
```

## Minimal Portfolio Snapshot

```json
{
  "cash": 5000,
  "holdings": [
    {"symbol": "FXAIX", "market_value": 34000, "quantity": 120.5},
    {"symbol": "AAPL", "market_value": 3000, "quantity": 12}
  ]
}
```

## Minimal Decision File

```json
{
  "recommendation": "BUY",
  "confidence": 86,
  "suggested_size_pct": 6,
  "key_thesis": "Durable long-term compounder."
}
```
