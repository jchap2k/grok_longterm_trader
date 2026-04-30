# Long-Term Trader Architecture

## Main Components

`research/research_packet.py`
Defines the canonical `ResearchPacket` and Lynch-style company categories.

`research/intake.py`
Normalizes raw idea dictionaries into research packets and applies portfolio profile defaults.

`portfolio/portfolio_profile.py`
Defines account-level constraints: protected symbols, benchmark, defensive parking symbol, cash symbol, and tradable capital.

`longterm/research_runner.py`
Builds context sections and runs the CGH decision committee through `CheapGrokHeavy`.

`agent/configs/longterm_trading_agent_specs.json`
Defines the long-term CGH domain roles and presets:

- `decision_4`: default V1 committee.
- `decision_6`: expanded valuation and portfolio committee.

`longterm/reviewers.py`
Deterministic business-story, balance-sheet, quality-durability, and quality-at-reasonable-price reviewers. These do not make final decisions; they ground the CGH context. The quality-durability reviewer reflects the `Quality Investing` notes by naming durable quality patterns and common quality traps.

`longterm/review_cadence.py`
Assigns review cadence and expected holding horizon by company category and risk language.

`longterm/decision_journal.py`
Stores decisions, structured packets, raw responses, benchmark start prices, outcome updates, recommendation table rows, and review candidates.

`longterm/report_builder.py`
Creates a markdown decision report with benchmark outcomes and a Motley-Fool-style recommendation table. `RecommendationTableBuilder` is the preferred seam for report/next-action rows: it starts from `DecisionJournal` rows, optionally hydrates volatile fields, carries shortened decision IDs for traceability, and does not write enrichment back into the journal.

`longterm/recommendation_enrichment.py`
Provides `CachedRecommendationEnricher`, a daily cache wrapper for recommendation-table enrichment such as current price, daily change, market cap, revenue growth, estimated return range, and max drawdown. This keeps external data calls out of core journal storage and avoids repeated fetches during report generation.

`longterm/capital_alert.py`
Builds informational capital-needed alerts and provider-agnostic email payloads when high-conviction ideas exceed available active-sleeve cash. Alerts can be suppressed with portfolio state when an existing non-protected holding has a sell/reduce recommendation and should fund the better idea first. These payloads are not instructions to deposit funds and do not execute trades.

`longterm/capital_alert_cli.py`
Provides a dry-run-first command surface for rendering capital-needed markdown or explicitly sending the prepared payload through the configured SMTP sender.

`longterm/email_sender.py`
Provides a Brevo-compatible SMTP sender and config loader. It reads `ai_trader/trading_agent/config/email_notifications.json` by default, is disabled unless the local ignored config enables it, and can reuse the swing-trader alert email address.

`longterm/review_status.py`
Builds per-symbol thesis review status from journal review candidates. It rehydrates the stored research packet, applies `ThesisMonitor`, and returns review-due/thesis-state fields for recommendation tables, markdown reports, and next-action reports without mutating the journal.

`longterm/action_planner.py`
Converts a structured decision into a non-executing proposed `BUY`, `SELL`, or `NONE` intent.

`longterm/portfolio_state.py`
Loads read-only portfolio snapshots and separates active versus protected holdings.

`longterm/next_actions.py`
Combines recommendation table builder output, portfolio state, automatically-derived review status, benchmark guard, and dry-run planner into a prioritized next-actions report. When the benchmark guard pauses new buys, buy candidates are shown as paused rather than actionable. If a high-conviction idea lacks active-sleeve cash, the report surfaces a `capital_needed` alert instead of pretending the buy can proceed.

`longterm/benchmark_guard.py`
Pauses new active buys when evaluated results lag `FXAIX` enough to question the active process.

`longterm/rebalance_planner.py`
Proposes dry-run rotations from weaker non-protected holdings into stronger candidates. It honors the benchmark guard before suggesting rotations into new buy candidates.

`longterm/thesis_monitor.py`
Checks review due dates and whether current evidence matches invalidation conditions.

## Decision Flow

Raw idea -> `ResearchPacket` -> deterministic reviews -> CGH committee -> parsed JSON decision -> journal -> recommendation table builder/enrichment/review status -> benchmark guard -> dry-run action plan -> next-actions report.

## Data Flow Safety

Broker configs, credentials, tokens, local databases, logs, and generated caches should not be committed. All current action outputs are proposed intents, not executable broker orders.
