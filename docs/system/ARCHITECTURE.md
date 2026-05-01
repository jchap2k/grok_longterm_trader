# Long-Term Trader Architecture

## Main Components

`research/research_packet.py`
Defines the canonical `ResearchPacket` and Lynch-style company categories.

`research/intake.py`
Normalizes raw idea dictionaries into research packets and applies portfolio profile defaults.

`portfolio/portfolio_profile.py`
Defines account-level constraints: protected symbols, benchmark, defensive parking symbol, cash symbol, and tradable capital.

`longterm/discovery.py`
Builds the upstream stock universe for research. It merges candidate rows from sources such as S&P 500/Russell/Nasdaq lists, ETF holdings, manual watchlists, quality-growth screens, and Motley Fool premium captures; scores them with a lightweight quality-growth pre-filter; then buckets them into `research_queue`, `watchlist`, or `rejected`. Discovery is not allowed to read portfolio state or create trade intents.

`longterm/discovery_sources.py`
Normalizes local universe source files into discovery candidate dictionaries. V1 supports CSV-style index/ETF files and NasdaqTrader-style pipe-delimited listings, preserving sector, market-category, and ETF/index weight notes while filtering ETF/test-issue listing rows before discovery scoring.

`longterm/discovery_enrichment.py`
Applies optional local/cacheable metric enrichment to discovery candidates before scoring. It normalizes JSON/CSV rows keyed by symbol into fields such as market cap, revenue growth, earnings growth, gross margin, return on capital, debt/equity, price trend, category leadership, valuation label, source rank, and source score. Enrichment preserves the original discovery source and remains upstream of portfolio, benchmark, account-action, and broker logic.

`longterm/discovery_cli.py`
Reads candidate JSON or local source files, optionally applies local enrichment, and emits the discovery buckets. It can also export the research-ready queue as idea-batch JSON for the existing research cycle.

`longterm/research_universe.py`
Splits research-ready universe ideas into stable batch files so the operator can work through a broad stock universe in small long-term research waves instead of sending hundreds of symbols into one cycle.

`longterm/research_campaign.py`
Tracks multi-batch research campaigns after universe batching. It creates a manifest from `research-batch-*.json` files, records pending/completed/deferred/failed/skipped status, and emits the exact supervised `run_longterm_cycle.py --idea-batch ...` command for the next pending batch. It does not run research automatically.

`longterm/research_packet_enrichment.py`
Merges local/cacheable enrichment rows into research ideas before `ResearchPacket` intake. It scores packet readiness with `completeness_score`, `completeness_bucket`, and `missing_fields`, while keeping provider-specific metrics transient unless a later decision explicitly journals them.

`longterm/research_runner.py`
Builds context sections and runs the CGH decision committee through `CheapGrokHeavy`. It now includes a deterministic thesis challenge section so the final decision sees an explicit bull case, bear case, key risks, and kill criteria before producing a recommendation.

`research/research_packet.py`
Defines the normalized research packet and the minimum completeness rule for deep research. Packets must have a company name, idea source, and at least one research-context field (`business_summary`, `thesis_summary`, or `source_notes`) before the cycle calls the research runner. Incomplete ideas are skipped and reported rather than sent to the LLM committee.

`longterm/orchestration.py`
Builds one dry-run cycle from manual, discovery, and optional Motley Fool ideas. It now emits `skipped_ideas` and a richer `deferred_research_queue` for incomplete packets, including missing fields and a suggested enrichment command, so skipped ticker stubs become explicit enrichment work instead of disappearing. When a journal is configured, deferred research rows are persisted for later enrichment follow-up.

`longterm/next_actions.py`
Creates the operator-facing dry-run next-actions report from the journal, portfolio state, review status, and benchmark guard. It elevates held positions with `broken` or `weakening` thesis state into `urgent_review_holding` rows. It also renders deferred research items from the cycle so incomplete candidate packets become visible enrichment work with missing fields and suggested discovery/enrichment commands.

`longterm/scheduler_operating_model.py`
Defines the dry-run cadence model for daily, weekly, and as-needed operator routines. It covers discovery refresh, Motley Fool intake, research batches, thesis reviews, benchmark/capital checks, next-actions/rebalance refreshes, and Grok plan review touchpoints. It is guidance and artifact generation only, not a cron engine and not broker execution.

`agent/configs/longterm_trading_agent_specs.json`
Defines the long-term CGH domain roles and presets:

- `decision_4`: default V1 committee.
- `decision_6`: expanded valuation and portfolio committee.

`longterm/reviewers.py`
Deterministic business-story, balance-sheet, quality-durability, and quality-at-reasonable-price reviewers. These do not make final decisions; they ground the CGH context. The quality-durability reviewer reflects the `Quality Investing` notes by naming durable quality patterns and common quality traps.

`longterm/thesis_challenge.py`
Builds the deterministic bull/bear thesis challenge from the research packet, reviewer support, reviewer objections, invalidation conditions, and risk flags. This borrows the useful adversarial-review idea from multi-agent trading architectures without adding another LLM call.

`longterm/review_cadence.py`
Assigns review cadence and expected holding horizon by company category and risk language.

`longterm/thesis_monitor.py`
Marks review status as `healthy`, `stale`, `weakening`, or `broken` from review cadence, supplied evidence, thesis-invalidation conditions, and common quality-durability risk language.

`longterm/review_templates.py`
Builds operator thesis-review checklists from `ResearchPacket`, review status, evidence, decision IDs, and a compact excerpt of `active_rules.txt`. This keeps human review prompts aligned with quality durability, valuation discipline, thesis breakers, and FXAIX benchmark accountability.

`longterm/decision_journal.py`
Stores decisions, structured packets, raw responses, benchmark start prices, outcome updates, recommendation table rows, review candidates, durable thesis review events, dry-run account action plans, persisted deferred research items, recommendation rank snapshots, and durable symbol feedback profiles for future paper/live reconciliation.

`longterm/report_builder.py`
Creates a markdown decision report with benchmark outcomes and a Motley-Fool-style recommendation table. `RecommendationTableBuilder` is the preferred seam for report/next-action rows: it starts from `DecisionJournal` rows, optionally hydrates volatile fields, carries shortened decision IDs for traceability, includes previous-rank / rank-movement context when snapshots exist, counts repeat recommendations by symbol, surfaces new-information notes from repeat recommendations for later profile enrichment, and does not write enrichment back into the journal.

Symbol feedback profiles are research memory, not trade authority. The journal can rebuild them deterministically from prior `BUY` / `ADD` / `HOLD` rows, auto-refresh them after new decisions, preserve paper-preview feedback, and enrich future same-symbol research ideas with repeat-count, latest-thesis, new-information notes, and paper-preview blocker context before LLM review.

`longterm/recommendation_enrichment.py`
Provides `CachedRecommendationEnricher`, a daily cache wrapper for recommendation-table enrichment such as current price, daily change, market cap, revenue growth, estimated return range, and max drawdown. This keeps external data calls out of core journal storage and avoids repeated fetches during report generation.

`longterm/capital_alert.py`
Builds informational capital-needed alerts and provider-agnostic email payloads when high-conviction ideas exceed available active-sleeve cash. Alerts can be suppressed with portfolio state when an existing non-protected holding has a sell/reduce recommendation and should fund the better idea first. These payloads are not instructions to deposit funds and do not execute trades.

`longterm/risk_review.py`
Builds deterministic dry-run risk reviews for account-action intents. Reviews check protected symbols, benchmark gate state, thesis/review status, position-size warnings, and active-sleeve cash warnings before actions are surfaced as machine-readable plan intents.

`longterm/live_readiness.py`
Builds a dry-run live-readiness checklist. It reports unmet gates such as benchmark proof, paper trading, broker-capability match, protected-symbol enforcement, manual approval, kill switch, audit logs, broker-read reconciliation, explicit live-mode config, and secrets hygiene. The broker-capability gate prevents Alpaca paper notional/fractional behavior from being treated as proof that a future live broker supports the same sizing model. It does not enable live execution.

`longterm/broker_capabilities.py`
Builds an advisory broker-capability compatibility report between the paper simulator and an intended live API. V1 includes Alpaca paper and Schwab API profiles and can emit a `broker_capability_match` observed JSON fragment for the live-readiness checklist. It is static/read-only and does not call any broker.

`longterm/capital_alert_cli.py`
Provides a dry-run-first command surface for rendering capital-needed markdown or explicitly sending the prepared payload through the configured SMTP sender.

`longterm/email_sender.py`
Provides a Brevo-compatible SMTP sender and config loader. It reads `ai_trader/trading_agent/config/email_notifications.json` by default, is disabled unless the local ignored config enables it, and can reuse the swing-trader alert email address.

`longterm/motley_fool_intake.py`
Normalizes Motley Fool premium table rows into investigation ideas. Motley Fool is treated as a high-quality idea source, not an automatic trading authority.

`longterm/motley_fool_capture.py`
Uses the logged-in Playwright/Chrome profile to capture Motley Fool premium table payloads from full new-recommendation, analyst-ranking, AI-ranking, or dashboard pages.

`longterm/motley_fool_capture_cli.py`
Provides a command surface for exporting captured Motley Fool ideas as JSON. The default source set captures the full new recommendations, analyst rankings, and AI rankings pages; dashboard capture is available as a smoke test.

`config/motley_fool_capture.json`
Local scheduler-facing toggle for optional Motley Fool intake. It records
whether Fool is enabled on this machine, whether the logged-in Chrome profile is
ready, which profile to use, and which premium sources should be captured. The
real local file is ignored; `config/motley_fool_capture.example.json` is the
repo-safe template.

`longterm/motley_fool_settings.py`
Loads the optional Motley Fool config for future scheduler wiring. Missing config
is treated as disabled, `enabled=true` plus `cookie_ready=true` means scheduled
capture may run, and `enabled=true` plus `cookie_ready=false` can trigger an
interactive login/setup flow.

`longterm/review_status.py`
Builds per-symbol thesis review status from journal review candidates and the durable thesis-review event table. Newer CGH decisions take precedence over older reviews; otherwise, recorded thesis reviews supply the last-review date and can preserve a `broken` or `weakening` state until a newer decision or new evidence changes it. The builder returns review-due/thesis-state fields for recommendation tables, markdown reports, and next-action reports without mutating the journal.

`longterm/rebalance_outcome_analysis.py`
Builds read-only evidence for future review-aware rebalance tuning. It groups evaluated decision outcomes by the shared thesis/review-risk buckets (`healthy`, `review_due`, `stale`, `weakening`, `broken`, `unreviewed`), reports excess return versus `FXAIX`, beat rate, confidence-weighted excess return, and pending outcome counts. It does not change planner weights.

`longterm/action_planner.py`
Converts a structured decision into a non-executing proposed `BUY`, `SELL`, or `NONE` intent.

`longterm/account_action_plan.py`
Builds the structured dry-run account action contract that future paper/live execution should consume. It aggregates recommendation-table rows, portfolio state, benchmark gating, capital-shortfall suppression, review status, and rebalance proposals into JSON-compatible intents (`BUY`, `REBALANCE`, `REVIEW`, `CAPITAL_NEEDED`, or `BLOCKED`). It does not place orders.

`longterm/portfolio_state.py`
Loads read-only portfolio snapshots and separates active versus protected holdings.

`longterm/alpaca_paper_account.py`
Reads Alpaca paper-account state through the standard broker API, normalizes positions into a read-only snapshot, and can export the same `PortfolioState` contract used by next-actions, benchmark, rebalance, and capital-alert planning. It is paper-only and does not expose order placement.

`longterm/paper_reconciliation.py`
Compares read-only paper account state against dry-run action-plan targets, expected cash, and optional paper execution ledger events. It reports missing target symbols, extra non-protected symbols, value mismatches, protected-symbol presence, missing filled symbols, and unexpected holdings after rejected orders. It is reconciliation only and never submits orders.

`longterm/paper_account_cleanliness.py`
Checks whether a read-only paper account snapshot is reset enough for the next supervised smoke run. It flags non-protected holdings and optional cash drift from an expected cash baseline. It reads only exported portfolio-state data and never calls a broker.

`longterm/paper_smoke_readiness.py`
Combines paper account cleanliness, broker capability compatibility, and optional scheduler-readiness output into a single read-only pre-flight report for supervised paper smokes. It can block on a dirty paper account, broker capability mismatch, or scheduler-readiness blockers. It does not submit, cancel, or modify orders.

`longterm/paper_order_preview.py`
Converts dry-run account action plan intents into broker-shaped paper order previews without importing Alpaca or submitting orders. Preview rows carry plan/decision traceability, risk/review metadata, cash shortfall, blocked reasons, and paired rebalance transaction IDs. `order_submission_enabled` is always `false`.

`longterm/paper_trade_ledger.py`
Persists non-submitting paper preview rows with plan, preview, decision, transaction, and future trade IDs. The ledger provides durable traceability before any broker submission path exists and reserves execution-event storage for a later Stage 6B paper execution layer.

`longterm/paper_preview_status.py`
Hydrates paper preview ledger rows into read-only status maps by decision ID and symbol. Recommendation reports and next-actions can use this to show whether a candidate already has a ready, blocked, or no-order paper preview without mutating the decision journal.

`longterm/paper_execution_status.py`
Hydrates paper execution ledger events into read-only status maps by decision ID and symbol. Recommendation reports, next-actions, lifecycle, and position intelligence reports can show latest paper execution state, broker order ID, filled quantity/price, and error context without mutating original decision rows. Symbol summaries distinguish historical status-refresh error counts from whether the current/latest status is still an error.

`longterm/paper_trading_verification.py`
Builds a conservative live-readiness observed fragment for the `paper_trading_verified` gate from append-only paper execution ledger events. It requires at least one filled paper execution and no current status-refresh errors. It does not call a broker.

`longterm/paper_execution_eligibility.py`
Builds the pre-6B paper execution eligibility contract from a dry-run account action plan, the paper preview ledger, portfolio state, and protected-symbol profile. It checks decision-id traceability, preview freshness, preview ready/blocked/no-order status, explicit paper-execution gate state, protected symbols, and intent-level blockers. It does not import Alpaca and does not submit orders.

`longterm/paper_execution.py`
Provides the supervised Stage 6B Alpaca paper execution boundary. V1 submits only simple `BUY` paper previews after revalidating protected symbols, benchmark guard, review/thesis state, journal decision quality, preview freshness, cash, duplicate submission state, and the active-rules hash. Rebalance/sell previews are hard-blocked with `rebalance_blocked_v1`. Execution truth is append-only in `PaperTradeLedger`; original decision rows remain immutable. The real CLI submit path refreshes the Alpaca paper account snapshot before broker calls, emits a pre-flight audit, and never enables live trading or scheduler automation.

`longterm/paper_order_status_refresh.py`
Refreshes already-submitted Alpaca paper order statuses by reading broker order IDs from `PaperTradeLedger`, calling a read-only broker status API, and appending status events such as `filled`, `partially_filled`, `rejected`, or `status_refresh_error`. It does not submit, cancel, or modify orders.

`longterm/paper_outcomes.py`
Builds provider-free paper fill outcome summaries from `PaperTradeLedger` fill events and an explicit current-price map. It compares paper fill return against `FXAIX` from the fill baseline and does not mutate journal decisions or call a broker.

`longterm/paper_lifecycle.py`
Builds a read-only symbol lifecycle summary across paper previews, paper execution events, and optional provider-free paper outcomes. It classifies symbols as preview-ready, preview-blocked, submitted, filled with pending outcome, outcome-evaluated, rejected, or status-error without submitting or modifying broker orders.

`longterm/feedback_refresh.py`
Runs explicit dry-run feedback maintenance. It can rebuild symbol profiles, apply paper-preview feedback, apply paper execution feedback, apply reconciliation feedback, refresh active-vs-FXAIX outcomes from explicit price maps, compute ephemeral outcome freshness, summarize review/thesis state, compute benchmark-guard context, persist idempotent eligibility evaluation events, and produce analysis-only tuning inputs. It does not mutate ranking, sizing, planner weights, or broker state.

`longterm/scheduler_readiness.py`
Builds an advisory scheduler-readiness report from existing artifacts such as portfolio state, dry-run action plans, feedback refresh summaries, paper lifecycle summaries, review/thesis state, benchmark guard state, and the active-rules reference. V1 always keeps `scheduler_submission_enabled=false` and `ready_for_scheduler_paper_submit=false`; it is a blocker/warning checklist only, not scheduler automation.

`longterm/operator_status_bundle.py`
Assembles a read-only operator bundle from the paper lifecycle summary, advisory scheduler readiness report, and position intelligence report. It is meant as the manual pre-automation status surface; it does not call a broker or enable scheduler submission.

`longterm/position_report.py`
Builds an on-demand monthly or quarterly position intelligence report from portfolio state, the decision journal, symbol feedback profiles, review status, paper preview status, paper execution status, provider-free paper outcome summaries, outcome freshness, and optional feedback-refresh summaries. It summarizes the portfolio and then shows collected research/feedback context for each held symbol, including knowledge gaps. It can produce a Brevo-compatible email payload, but it is not scheduler-wired and does not submit broker orders.

`longterm/next_actions.py`
Combines recommendation table builder output, portfolio state, automatically-derived review status, benchmark guard, and dry-run planner into a prioritized next-actions report. When the benchmark guard pauses new buys, buy candidates are shown as paused rather than actionable. If a high-conviction idea lacks active-sleeve cash, the report surfaces a `capital_needed` alert instead of pretending the buy can proceed.

`longterm/benchmark_guard.py`
Pauses new active buys when evaluated results lag `FXAIX` enough to question the active process.

`longterm/rebalance_planner.py`
Proposes dry-run rotations from weaker non-protected holdings into stronger candidates. It honors the benchmark guard before suggesting rotations into new buy candidates.

`longterm/thesis_monitor.py`
Checks review due dates and whether current evidence matches invalidation conditions.

## Decision Flow

Universe sources -> discovery queue -> research packet enrichment -> research batches -> research campaign manifest -> `ResearchPacket` completeness gate -> deterministic reviews -> CGH committee -> parsed JSON decision -> journal -> recommendation table builder/enrichment/review status -> rebalance outcome analysis -> Alpaca paper/read-only portfolio snapshot -> paper reconciliation -> benchmark guard -> dry-run account action plan -> paper order preview -> paper preview ledger -> paper execution eligibility -> supervised Stage 6B paper execution boundary -> paper order status refresh -> paper outcomes/lifecycle summaries -> feedback refresh -> scheduler-readiness checklist -> operator status bundle -> next-actions/report artifacts and on-demand position intelligence reports.

## Data Flow Safety

Broker configs, credentials, tokens, local databases, logs, and generated caches should not be committed. Live execution remains unavailable; Stage 6B is limited to explicitly requested Alpaca paper BUY submission.
