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

The recommendation table is derived from `DecisionJournal` rows through `RecommendationTableBuilder`. Volatile market/fundamental fields should be enriched at report time and cached daily; do not write transient enrichment directly into the journal unless it becomes part of a durable decision record. Markdown reports include shortened decision IDs so every recommendation row can be traced back to the durable journal entry.

Review status is layered onto the same table with `ReviewStatusBuilder`. It reads stored packet JSON from the journal, applies the configured review cadence through `ThesisMonitor`, and returns `review_due`, `days_since_review`, and `thesis_state` fields for reports and next-action markdown.

## Dry-Run Action Plan

```powershell
python scripts/longterm_action_plan.py --symbol NVDA --portfolio-state path\to\portfolio.json --decision-file path\to\decision.json
```

## Next-Actions Report

```powershell
python scripts/longterm_next_actions.py --portfolio-state path\to\portfolio.json --limit 10
```

The next-actions report is still dry-run only. It evaluates the FXAIX benchmark gate before surfacing new buys:

- If the active sleeve is not clearing the benchmark guard, new buy candidates are marked `paused_buy_candidate`.
- If a buy is attractive but active-sleeve cash is short, it is marked `capital_needed` so an email or dashboard can later notify the user.
- Protected symbols such as `FXAIX` remain excluded from sell, trim, rebalance, and rotation logic.

## Grok Project Review

The repo-safe project config is:

```text
ai_trader/trading_agent/config/grok_project_config.json
```

It points browser-based Grok review tooling at the long-term trader project:

```text
https://grok.com/project/e397a91c-e647-4c3b-868f-ff0d0ed6c175?tab=conversations
```

`GrokPlanReviewer.review(..., trading_mode="auto")` reads that config and uses the `longterm` context. You can still override the project URL for one run with `GROK_PROJECT_URL`.

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
