# Grok Long-Term Trader

Research-first long-term trading project built around a quality-growth active sleeve, with `FXAIX` as the protected benchmark/core holding.

This repo is not a swing-trader clone. It is meant to evaluate fewer ideas more deeply, track each thesis, and measure whether active decisions beat simply leaving the active sleeve in the benchmark.

## Current Shape

- Protected benchmark/core holding: `FXAIX`
- Defensive parking symbol: `SPY`
- Defensive cash mode: allowed only for hostile market conditions, not ordinary pullbacks
- Account mode: Roth IRA aware
- Default research style: quality-growth / position trading
- Current execution state: research and decision logging foundation, not live trading

## Core Flow

1. Create or ingest an idea.
2. Normalize it into a `ResearchPacket`.
3. Add book-shaped research principles from the knowledge-agent notes.
4. Run the long-term multi-agent research config.
5. Record the structured decision.
6. Later compare active result vs `FXAIX`.

## Important Files

- `ai_trader/rules/active_rules.txt` - active long-term strategy rules
- `ai_trader/trading_agent/research/research_packet.py` - canonical research packet
- `ai_trader/trading_agent/research/intake.py` - raw idea intake
- `ai_trader/trading_agent/portfolio/portfolio_profile.py` - account/profile controls
- `ai_trader/trading_agent/longterm/research_runner.py` - multi-agent research runner
- `ai_trader/trading_agent/longterm/decision_journal.py` - SQLite decision journal
- `ai_trader/trading_agent/longterm/action_planner.py` - non-executing buy/sell intent planner
- `ai_trader/trading_agent/longterm/portfolio_state.py` - read-only portfolio snapshot model
- `ai_trader/trading_agent/longterm/thesis_monitor.py` - thesis review due/broken checks
- `ai_trader/trading_agent/longterm/next_actions.py` - prioritized next-actions report
- `ai_trader/trading_agent/longterm/benchmark_guard.py` - FXAIX active-sleeve benchmark gate
- `ai_trader/trading_agent/longterm/rebalance_planner.py` - dry-run rebalance proposal helper
- `ai_trader/trading_agent/longterm/capital_alert.py` - capital-needed alert payloads
- `ai_trader/trading_agent/longterm/report_builder.py` - markdown reports and recommendation table
- `ai_trader/trading_agent/longterm/recommendation_enrichment.py` - daily cached recommendation-table enrichment
- `ai_trader/trading_agent/longterm/configs/roth_ira_profile.json` - default Roth IRA profile
- `ai_trader/trading_agent/agent/configs/longterm_trading_agent_specs.json` - long-term CGH agent domains
- `ai_trader/trading_agent/agent/utils/cheap_grok_heavy.py` - config-driven multi-agent Grok helper
- `docs/plans/2026-04-28-longterm-trader-foundation-plan.md` - foundation plan
- `docs/system/README.md` - system overview for future project-link / LLM-collab context
- `docs/system/ARCHITECTURE.md` - code map and data flow
- `docs/system/OPERATIONS.md` - command reference and sample JSON payloads
- `docs/system/SAFETY.md` - dry-run and live-readiness safety model
- `docs/system/project_manifest.json` - machine-readable project context manifest
- `ai_trader/trading_agent/config/grok_project_config.json` - Grok project URL/default long-term review mode

## Domain Configs

`cheap_grok_heavy.py` supports separate domain-set JSON files:

- `ai_trader/trading_agent/longterm/configs/longterm_agent_specs_v1.json`
- `ai_trader/trading_agent/agent/configs/longterm_trading_agent_specs.json`
- `ai_trader/trading_agent/agent/configs/default_agent_specs_general.json`
- `ai_trader/trading_agent/agent/configs/planning_agent_specs.json`
- `ai_trader/trading_agent/agent/configs/code_review_agent_specs.json`

Use named presets when a config provides them, rather than assuming the first N roles are the right team.

For long-term trading decisions:

- `decision_4` is the default and uses FundamentalAnalyst, MacroRiskAnalyst, ThesisCritic, and DecisionIntegrator.
- `decision_6` adds ValuationEdgeAnalyst and PortfolioManager when we want deeper but more expensive decision review.

## Dry Run A Ticker

From `ai_trader/trading_agent`:

```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --dry-run
```

This prints the normalized packet without calling Grok.

## Use An Idea File

You can also pass a JSON idea file. Command-line fields override matching values from the file.

```json
{
  "symbol": "AAPL",
  "company_name": "Apple",
  "business_summary": "Consumer technology platform.",
  "thesis_summary": "Services and ecosystem durability.",
  "source_notes": ["Manual watchlist"]
}
```

```powershell
python scripts/run_longterm_research.py --idea-file path\to\idea.json --dry-run
```

## Use An Idea Batch

Pass a JSON list of ideas to create multiple research packets from one file:

```powershell
python scripts/run_longterm_research.py --idea-batch path\to\ideas.json --dry-run
```

## Run Research

Set `XAI_API_KEY`, then run without `--dry-run`:

```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --candidate-price 180 --benchmark-price 165
```

The command prints the recorded `decision_id`.

Use the 6-agent long-term committee when needed:

```powershell
python scripts/run_longterm_research.py --symbol AAPL --agent-preset decision_6
```

## Journal Tools

Summarize active decisions versus `FXAIX`:

```powershell
python scripts/longterm_journal.py summary
```

List recent decisions:

```powershell
python scripts/longterm_journal.py list --limit 10
```

Render a markdown report with the ranked recommendation table:

```powershell
python scripts/longterm_journal.py report --limit 10
```

The recommendation table is modeled after curated stock-ranking services: rank, symbol, company, action, service/source, price, daily change, previous rank, market cap, risk type, 1Y revenue growth, return since recommendation, recommendation date, estimated return range, estimated max drawdown, times recommended, notes/discussion count, thesis reason, and supporting link when those fields are available from research.

Update an outcome review:

```powershell
python scripts/longterm_journal.py update-outcome --decision-id <id> --candidate-price 190 --benchmark-price 170 --notes "monthly review"
```

## Dry-Run Action Planning

Given a portfolio snapshot and a structured decision JSON, produce a proposed intent without touching broker code:

```powershell
python scripts/longterm_action_plan.py --symbol NVDA --portfolio-state path\to\portfolio.json --decision-file path\to\decision.json
```

This outputs `BUY`, `SELL`, or `NONE` intent plus target value, trade value, cash shortfall, and whether the action is allowed under protected-symbol and cash rules.

## Next Actions Report

Use the recommendation table, benchmark guard, and portfolio snapshot to render the next research/trade-review priorities:

```powershell
python scripts/longterm_next_actions.py --portfolio-state path\to\portfolio.json --limit 10
```

The report is still dry-run only. It may pause new buys when evaluated decisions are lagging `FXAIX`, and it may propose reviewing or rebalancing non-protected active holdings.

## Tests

From the repo root:

```powershell
python -m pytest ai_trader/trading_agent/longterm/test_longterm_foundation.py ai_trader/trading_agent/longterm/test_longterm_intake_runner.py ai_trader/trading_agent/longterm/test_longterm_decision_journal.py ai_trader/trading_agent/longterm/test_longterm_book_principles.py ai_trader/trading_agent/longterm/test_longterm_next_steps.py ai_trader/trading_agent/longterm/test_longterm_journal_cli.py ai_trader/trading_agent/agent/utils/test_cheap_grok_heavy_config.py -q
```

## Safety Notes

- Do not commit local broker configs, tokens, API keys, generated DBs, or logs.
- `FXAIX` is a protected symbol and should not be sold, trimmed, rotated, or rebalanced by this agent.
- Temporary defensive index exposure should use `SPY`, not `FXAIX`.
- Active results should be judged against `FXAIX`; if the active sleeve cannot beat it over a meaningful period, the benchmark is the better default.
- Capital-needed alerts are informational only; they should not request deposits or bypass risk rules.
