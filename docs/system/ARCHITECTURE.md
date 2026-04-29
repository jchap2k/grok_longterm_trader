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
Deterministic business-story, balance-sheet, and quality-at-reasonable-price reviewers. These do not make final decisions; they ground the CGH context.

`longterm/review_cadence.py`
Assigns review cadence and expected holding horizon by company category and risk language.

`longterm/decision_journal.py`
Stores decisions, structured packets, raw responses, benchmark start prices, outcome updates, recommendation table rows, and review candidates.

`longterm/report_builder.py`
Creates a markdown decision report with benchmark outcomes and a Motley-Fool-style recommendation table.

`longterm/action_planner.py`
Converts a structured decision into a non-executing proposed `BUY`, `SELL`, or `NONE` intent.

`longterm/portfolio_state.py`
Loads read-only portfolio snapshots and separates active versus protected holdings.

`longterm/next_actions.py`
Combines recommendation table, portfolio state, benchmark guard, and dry-run planner into a prioritized next-actions report.

`longterm/benchmark_guard.py`
Pauses new active buys when evaluated results lag `FXAIX` enough to question the active process.

`longterm/rebalance_planner.py`
Proposes dry-run rotations from weaker non-protected holdings into stronger candidates.

`longterm/thesis_monitor.py`
Checks review due dates and whether current evidence matches invalidation conditions.

## Decision Flow

Raw idea -> `ResearchPacket` -> deterministic reviews -> CGH committee -> parsed JSON decision -> journal -> recommendation table -> dry-run action plan -> next-actions report.

## Data Flow Safety

Broker configs, credentials, tokens, local databases, logs, and generated caches should not be committed. All current action outputs are proposed intents, not executable broker orders.
