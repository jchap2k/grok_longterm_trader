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

## Discovery Queue

Discovery builds the stock universe that deserves research. It does not create
orders, recommendations, or account action plans. Good V1 source inputs include
S&P 500 / Russell lists, Nasdaq/NYSE listings filtered for quality, major ETF
holdings, quality/dividend/moat lists, manual watchlists, and Motley Fool
premium captures.

Run discovery from a candidate JSON file:

```powershell
python scripts/run_longterm_discovery.py --candidates path\to\candidates.json
```

Run discovery directly from a local universe source file:

```powershell
python scripts/run_longterm_discovery.py --source-file path\to\sp500.csv --source sp500
python scripts/run_longterm_discovery.py --source-file path\to\qqq_holdings.csv --source qqq
python scripts/run_longterm_discovery.py --source-file path\to\nasdaqlisted.txt --source nasdaq_listed
```

Write research-ready candidates as an idea batch for the existing research
cycle:

```powershell
python scripts/run_longterm_discovery.py --candidates path\to\candidates.json --research-ideas-output path\to\research_ideas.json
python scripts/run_longterm_cycle.py --idea-batch path\to\research_ideas.json --journal-db path\to\journal.db
```

Or let a cycle build the discovery queue directly before research:

```powershell
python scripts/run_longterm_cycle.py --discovery-candidates path\to\candidates.json --journal-db path\to\journal.db
python scripts/run_longterm_cycle.py --discovery-source-file path\to\sp500.csv --discovery-source sp500 --journal-db path\to\journal.db
```

Discovery buckets:
- `research_queue`: candidates that clear the mechanical quality-growth pre-filter.
- `watchlist`: interesting candidates that need more evidence before research.
- `rejected`: candidates failing basic liquidity, growth, leverage, or trend checks.

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

## Capital-Needed Email Payloads

Capital-needed emails are informational only. The long-term trader can build provider-agnostic email payloads and send them through a Brevo-compatible SMTP sender, but email sending should remain off until explicitly enabled in a local ignored config file. A capital request should only be sent when a high-conviction candidate lacks active-sleeve cash and existing non-protected holdings do not already have sell/reduce recommendations that should fund the idea first.

Use this template:

```text
ai_trader/trading_agent/config/email_notifications.example.json
```

Copy it locally to `ai_trader/trading_agent/config/email_notifications.json` and fill in the Brevo SMTP login/key if you want delivery. It is fine to reuse `jchap2k.swingtrader@gmail.com` as both recipient and verified sender.

Dry-run the alert markdown:

```powershell
python scripts/longterm_capital_alert.py --active-sleeve-value 34000 --available-cash 500 --portfolio-state path\to\portfolio.json
```

Send through the local Brevo-compatible config only when explicitly intended:

```powershell
python scripts/longterm_capital_alert.py --active-sleeve-value 34000 --available-cash 500 --portfolio-state path\to\portfolio.json --send
```

## Motley Fool Idea Capture

Motley Fool premium tables are treated as a high-quality idea source, not a buy authority. Captured rows become investigation ideas that still require independent long-term research, valuation review, thesis critique, and portfolio-fit checks.

Capture the full recommendation/ranking sources:

```powershell
python scripts/longterm_motley_fool_capture.py
```

Capture one source:

```powershell
python scripts/longterm_motley_fool_capture.py --source new_recommendations
python scripts/longterm_motley_fool_capture.py --source analyst_rankings
python scripts/longterm_motley_fool_capture.py --source quant_rankings
python scripts/longterm_motley_fool_capture.py --source dashboard
```

The capture uses the logged-in Chrome profile at `~/.grok3api_chrome_profile`.
Use one capture process at a time for that profile. The default full capture runs
the pages sequentially so the profile is not opened by multiple Playwright
sessions at once.

Scheduler-facing Motley Fool settings live at:

```text
ai_trader/trading_agent/config/motley_fool_capture.json
```

Use the committed `.example.json` as the safe template. The local config is
ignored because it records whether this machine has a subscribed/logged-in Fool
profile available. Current scheduler-facing behavior is:

- If `enabled` is `false`, skip Motley Fool intake without warning or failure.
- If `enabled` is `true` and `cookie_ready` is `false`, open the configured
  Chrome profile at `login_url` for interactive login/setup, then set
  `cookie_ready` once verified.
- If `enabled` and `cookie_ready` are both `true`, call the capture API/scripts
  directly and continue treating Fool rows as research ideas, not trade orders.

Run interactive setup when a subscribed profile needs fresh cookies:

```powershell
python scripts/longterm_motley_fool_setup.py
```

This opens Chrome with the configured `profile_dir`, waits for you to complete
login, verifies access by capturing the configured verification source
(`dashboard` by default), then persists `cookie_ready=true` into the local
ignored config file.

## One-Cycle Long-Term Orchestration

The repo now has a first dry-run orchestration entrypoint that can combine:
- manual idea input
- optional Motley Fool capture
- research packet normalization
- CGH decision recording
- markdown recommendation report generation
- optional next-actions report generation when a portfolio snapshot is supplied

Minimal smoke with Motley Fool disabled or config-missing:

```powershell
python scripts/run_longterm_cycle.py --motley-fool-config path\to\missing_or_optional.json --quiet
```

Allow a one-cycle run to launch setup if Fool is enabled but cookies are not
ready:

```powershell
python scripts/run_longterm_cycle.py --launch-login-if-needed --journal-db path\to\journal.db
```

Run one cycle from a single idea file:

```powershell
python scripts/run_longterm_cycle.py --idea-file path\to\idea.json --journal-db path\to\journal.db
```

Run one cycle and also generate next-actions output using a portfolio snapshot:

```powershell
python scripts/run_longterm_cycle.py --idea-batch path\to\ideas.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db
```

Add dry-run capital-needed alert markdown to the same cycle result:

```powershell
python scripts/run_longterm_cycle.py --idea-batch path\to\ideas.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --active-sleeve-value 35000 --available-cash 500
```

Cycle result JSON includes operator artifacts:
- `idea_provenance_summary`
- `packet_completeness_warnings`
- `decision_journal_refs`
- `report_generated`
- `next_actions_generated`
- `capital_alert_markdown` and `capital_alert_generated`
- `rebalance_markdown` and `rebalance_generated`
- `account_action_plan` and `account_action_plan_generated`

`account_action_plan` is the structured dry-run contract for the future
autonomous account manager. It includes a schema version, plan id, dry-run mode,
benchmark gate reason, blocked reasons, and machine-readable intents such as
`BUY`, `REBALANCE`, `REVIEW`, `CAPITAL_NEEDED`, and `BLOCKED`. It is not an
order ticket and does not place broker orders.
When the journal supports it, each generated account action plan is also stored
in `longterm_action_plan_journal` so later paper/live reconciliation can compare
intended actions against outcomes.

Recommendation table ranks are action-aware. The journal emits a `ranking_score`
and `rank_reason` for each row so actionable `BUY` / `ADD` candidates with
meaningful suggested size can outrank passive high-confidence `HOLD` rows. The
markdown report exposes both fields for operator auditability.

Rebalance markdown is an explanatory dry-run artifact. It includes the funding
source, target, proposed sell value, source and target ranks, rank gap, source
current value, source target value, suggested target size, decision IDs when
available, review/thesis status context when supplied, and the benchmark gate
reason. Protected holdings remain excluded as funding sources.
When review context is available, stale, deteriorating, broken, or review-due
source holdings receive a small dry-run rebalance-score adjustment so the
proposal can favor rotating from names with higher thesis-review risk. This is
still advisory only and does not place orders.

Current limitations of this first cycle:
- still dry-run only
- recommendation, next-actions, and capital-alert outputs are returned as markdown strings in the cycle result JSON

## Dry-Run Long-Term Scheduler

The dry-run scheduler repeatedly calls the same one-cycle orchestration path.
It does not place orders. Each cycle reloads profile, Motley Fool settings, idea
input, and portfolio state from disk so cash/holding changes are not frozen in
memory during a longer run.

Run exactly one scheduled cycle:

```powershell
python scripts/run_longterm_scheduler.py --run-once --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --quiet
```

Run one scheduled cycle with a refreshed discovery-candidate file:

```powershell
python scripts/run_longterm_scheduler.py --run-once --discovery-candidates path\to\candidates.json --journal-db path\to\journal.db --quiet
python scripts/run_longterm_scheduler.py --run-once --discovery-source-file path\to\sp500.csv --discovery-source sp500 --journal-db path\to\journal.db --quiet
```

Run a bounded recurring dry-run:

```powershell
python scripts/run_longterm_scheduler.py --max-runs 3 --interval-seconds 3600 --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --quiet
```

Write the structured scheduler summary to disk:

```powershell
python scripts/run_longterm_scheduler.py --run-once --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --summary-output path\to\scheduler_summary.json --quiet
```

Allow setup if Motley Fool is enabled but cookies are stale:

```powershell
python scripts/run_longterm_scheduler.py --run-once --launch-login-if-needed --journal-db path\to\journal.db
```

The scheduler summary JSON includes per-run status, capture/setup status,
decision IDs, idea provenance summary, packet completeness warnings,
recommendation markdown, next-actions markdown, capital-alert markdown,
rebalance markdown, account action plan, and any error message. Use
`--continue-on-error` only for supervised dry-run testing where a later cycle
should still run after a failed cycle.

## Calendar-Flow Concept Research

There is now a research-only tool for evaluating the monthly `TLT` calendar-flow
idea that came from the X thread. This is not wired into long-term decision
rules or live trading.

Run the summary view:

```powershell
python scripts/run_tlt_calendar_flow_research.py --symbol TLT --start 2004-01-01 --end 2024-12-01
```

Add a simple round-trip cost assumption:

```powershell
python scripts/run_tlt_calendar_flow_research.py --symbol TLT --start 2004-01-01 --end 2024-12-01 --round-trip-cost-bps 10
```

Show every trade only when needed:

```powershell
python scripts/run_tlt_calendar_flow_research.py --symbol TLT --start 2004-01-01 --end 2024-12-01 --include-trades
```

Current conclusion from the rebuilt test:
- the social-media backtest claims were not credible as shown
- the cleaned-up test still suggests a plausible `TLT` calendar effect
- this should be treated as optional research context, not as a direct active-sleeve rule for the long-term quality-growth trader

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
