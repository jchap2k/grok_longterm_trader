# Current Memory - Grok Long-Term Trader

Last updated: 2026-04-30
Repo: `S:\LLM_files\grok_longterm_trader`
Remote: `https://github.com/jchap2k/grok_longterm_trader.git`
Branch: `main`

This file is a temporary handoff document for another Codex instance. It is meant to explain:
- what this project is
- what has already been built
- what the next planned milestones are
- what guardrails matter most
- where another agent should work next

## 1. Project Intent

This repo is a research-first long-term trader built around a quality-growth active sleeve, not a day trader and not a swing-trader clone.

Core idea:
- keep `FXAIX` as the protected benchmark/core holding
- use the active sleeve for higher-conviction quality-growth ideas
- evaluate whether active decisions actually beat simply leaving that money in `FXAIX`
- stay dry-run and safety-gated until live trading is explicitly approved

Current operating mode:
- research
- decision logging
- recommendation ranking
- thesis review tracking
- dry-run next-actions planning
- informational capital-needed alerts

Not yet active:
- autonomous scheduled orchestration
- live broker order execution for the long-term system

## 2. What We Finished So Far

The first substantial long-term foundation was built and pushed on 2026-04-29.

Recent commits in that foundation batch:
- `d10295f` Add Motley Fool capture settings loader
- `4258870` Add Motley Fool premium idea capture
- `07a3fab` Add capital alert CLI and funding guardrail
- `393eeb5` Add capital-needed email sender foundation
- `f3e8ad3` Add recommendation traceability and review status
- `d0aa1d6` Convert long-term rules to XML structure
- `15f2e41` Add quality durability guardrails
- `65c1f15` Add recommendation table builder enrichment

What those changes accomplished:

### Strategy / rules foundation
- `ai_trader/rules/active_rules.txt` is now an XML-style long-term rules file.
- The rules emphasize:
  - understandable businesses
  - quality durability
  - valuation discipline
  - thesis clarity
  - balance-sheet awareness
  - leadership over cheap laggards
  - `FXAIX` as protected benchmark/core holding
  - dry-run only unless live mode is explicitly enabled

### Research and decision pipeline
- Raw ideas can be normalized into canonical `ResearchPacket` objects.
- Deterministic local reviewers ground the decision with business-story, balance-sheet, quality-durability, and quality-at-reasonable-price context.
- The long-term CGH committee then produces the actual structured decision.
- That decision is persisted into the journal for later reporting and benchmarking.

### Recommendation table / reporting layer
- A recommendation-table builder now creates ranked rows with traceability.
- The output is closer to a curated service/research list than a raw log dump.
- Enrichment adds daily/volatile fields like price, change, market cap, revenue growth, estimated return range, and drawdown without polluting the immutable journal.
- Review status can be derived later from stored packets without mutating historical decision records.

### Next-actions / portfolio-aware planning
- A dry-run action planner can translate a structured decision into a proposed `BUY`, `SELL`, or `NONE`.
- Portfolio state can be loaded as read-only context.
- Protected holdings are separated from active holdings.
- The benchmark guard can pause new buys if active results lag `FXAIX`.
- The rebalance planner can propose rotations from weaker active holdings into stronger candidates without touching protected holdings.

### Capital alerting
- Capital-needed alerts exist but are informational only.
- The system checks whether a better idea should be funded by selling/reducing an existing non-protected holding before ever surfacing a capital-needed alert.
- Email sending exists through a Brevo-compatible SMTP sender, but it is disabled unless the ignored local config enables it.

### Motley Fool premium intake
- Motley Fool premium capture was added and live-tested using a logged-in Chrome profile.
- Supported premium source capture includes:
  - dashboard
  - new recommendations
  - analyst rankings
  - AI / quant rankings
- Live smoke result already achieved:
  - `all_full` captured 50 ideas
  - 20 new recommendations
  - 10 analyst rankings
  - 20 AI rankings
- Motley Fool is treated as a high-quality idea source only, never as automatic buy/sell authority.

### Scheduler-facing config contract
- `ai_trader/trading_agent/config/motley_fool_capture.example.json` is the committed template.
- `ai_trader/trading_agent/config/motley_fool_capture.json` is local/ignored runtime config.
- `longterm/motley_fool_settings.py` loads this optional config and exposes safe state for future scheduler work:
  - missing config -> disabled
  - `enabled=true` + `cookie_ready=true` -> capture can run
  - `enabled=true` + `cookie_ready=false` -> scheduler can open login/setup flow

## 3. Validation Already Completed

Validated command baseline:

From `S:\LLM_files\grok_longterm_trader\ai_trader\trading_agent`:

- `python -m pytest longterm -q`
  - passed with `93 passed`
- `python scripts/longterm_motley_fool_capture.py --source dashboard`
  - works
- `python scripts/longterm_motley_fool_capture.py --source new_recommendations`
  - works
- `python scripts/longterm_motley_fool_capture.py --source analyst_rankings`
  - works
- `python scripts/longterm_motley_fool_capture.py --source quant_rankings`
  - works

## 4. Critical Guardrails

These are important enough that another agent should assume them unless explicitly changed by the user:

### Protected symbol
- `FXAIX` is protected.
- Do not recommend selling, trimming, rotating out of, or rebalancing out of `FXAIX`.

### Benchmarking
- The active sleeve must justify itself against `FXAIX`.
- If the active sleeve materially lags `FXAIX` over enough evaluated decisions, new buys should be paused and the system should prefer review/research mode.

### Dry-run only for now
- Current outputs are research, logging, planning, and alerts.
- Do not silently convert this into live broker execution.
- Keep any future execution layer safety-gated until explicit approval.

### Motley Fool is optional
- Missing subscription, cookies, or browser profile must not break the scheduler.
- Paid-source availability should degrade to a clean skip.

### Capital alerts are informational only
- Do not auto-request funds.
- Do not bypass cash constraints.
- Before surfacing a capital-needed alert, check whether a weaker non-protected active holding should fund the better idea first.

### Do not commit local secrets/runtime configs
Keep these local and ignored:
- broker configs
- Schwab credentials/tokens
- `ai_trader/trading_agent/config/email_notifications.json`
- `ai_trader/trading_agent/config/motley_fool_capture.json`
- generated DBs/logs/caches if they are runtime artifacts

## 5. Main Code Areas

### Core architecture and docs
- `README.md`
- `docs/system/ARCHITECTURE.md`
- `docs/system/OPERATIONS.md`
- `docs/system/SAFETY.md`
- `docs/system/README.md`

### Rules
- `ai_trader/rules/active_rules.txt`

### Research packet / intake / portfolio profile
- `ai_trader/trading_agent/research/research_packet.py`
- `ai_trader/trading_agent/research/intake.py`
- `ai_trader/trading_agent/portfolio/portfolio_profile.py`

### Long-term decision pipeline
- `ai_trader/trading_agent/longterm/research_runner.py`
- `ai_trader/trading_agent/longterm/reviewers.py`
- `ai_trader/trading_agent/longterm/review_cadence.py`
- `ai_trader/trading_agent/longterm/decision_parser.py`
- `ai_trader/trading_agent/longterm/decision_journal.py`
- `ai_trader/trading_agent/longterm/prompt_builder.py`

### Report / recommendation / review outputs
- `ai_trader/trading_agent/longterm/report_builder.py`
- `ai_trader/trading_agent/longterm/recommendation_enrichment.py`
- `ai_trader/trading_agent/longterm/review_status.py`
- `ai_trader/trading_agent/longterm/thesis_monitor.py`
- `ai_trader/trading_agent/longterm/next_actions.py`

### Portfolio-aware planning and guards
- `ai_trader/trading_agent/longterm/portfolio_state.py`
- `ai_trader/trading_agent/longterm/action_planner.py`
- `ai_trader/trading_agent/longterm/benchmark_guard.py`
- `ai_trader/trading_agent/longterm/rebalance_planner.py`
- `ai_trader/trading_agent/longterm/capital_alert.py`
- `ai_trader/trading_agent/longterm/email_sender.py`

### Motley Fool intake path
- `ai_trader/trading_agent/longterm/motley_fool_capture.py`
- `ai_trader/trading_agent/longterm/motley_fool_capture_cli.py`
- `ai_trader/trading_agent/longterm/motley_fool_intake.py`
- `ai_trader/trading_agent/longterm/motley_fool_settings.py`
- `ai_trader/trading_agent/config/motley_fool_capture.example.json`

### Multi-agent configs
- `ai_trader/trading_agent/agent/configs/longterm_trading_agent_specs.json`
- `ai_trader/trading_agent/agent/configs/planning_agent_specs.json`
- `ai_trader/trading_agent/longterm/configs/longterm_agent_specs_v1.json`
- `ai_trader/trading_agent/agent/utils/cheap_grok_heavy.py`

## 6. Planned Roadmap

The roadmap below is ordered by importance and dependency, not by effort alone.

### Phase 1 - Build long-term scheduler orchestration
Status: not started
Priority: highest

Goal:
- turn the current foundation into a scheduled research-and-decision pipeline
- still dry-run only
- still safety-gated

Required behavior:
- load optional Motley Fool settings through `longterm/motley_fool_settings.py`
- if Fool is disabled, skip Fool intake quietly
- if Fool is enabled but `cookie_ready=false`, open the configured Chrome profile and login URL for interactive setup rather than hard-failing
- if Fool is enabled and `cookie_ready=true`, run Motley Fool capture automatically
- normalize captured rows into research ideas
- feed them into research packets
- run the long-term CGH decision committee
- persist decisions to the journal
- update recommendation-table/report flows
- run benchmark, protected-holding, rebalance, and capital-needed guardrails
- keep order execution dry-run only

Likely implementation seams:
- add a long-term scheduler entrypoint or orchestration module under `ai_trader/trading_agent/longterm/`
- reuse existing CLI/module seams instead of duplicating logic
- prefer composing:
  - Motley Fool settings loader
  - capture/intake
  - research runner
  - journal/report builder
  - next-actions / rebalance / capital alert

Suggested work chunks:
1. Add orchestration module and pure functions for one full cycle.
2. Add optional Motley Fool intake gate behavior.
3. Add scheduler-safe login/setup behavior for `cookie_ready=false`.
4. Add research-run batching over captured/manual ideas.
5. Add end-of-cycle summary/report output.
6. Add tests around disabled vs enabled vs setup-needed Fool states.

Verification:
- unit tests for disabled / can_capture / should_open_login states
- dry-run orchestration test from idea intake through decision logging
- manual smoke with Fool disabled
- manual smoke with Fool enabled + `cookie_ready=true`
- ensure no live broker code path is invoked

### Phase 2 - Define the durable scheduler operating model
Status: planned
Priority: high

Goal:
- decide how this system runs day to day or week to week without becoming noisy or overactive

Questions to settle in code/design:
- when should idea ingestion run
- how often should full long-term review run
- whether there are separate cadences for:
  - new idea intake
  - thesis review refresh
  - benchmark status refresh
  - next-actions / rebalance report refresh
  - capital alert evaluation

Likely outputs:
- one or more scheduled routines
- a daily/weekly summary artifact
- a repeatable operator command set in `docs/system/OPERATIONS.md`

### Phase 3 - Formalize research packet coverage for external idea sources
Status: partially built
Priority: high

Goal:
- make sure all idea sources, especially Motley Fool captures, become strong research packets rather than thin ticker stubs

Desired improvements:
- enforce minimum packet completeness before CGH
- clearly label source provenance
- preserve source-specific fields that are useful later for attribution
- add better fallback behavior if some enrichment fields are missing

Potential files:
- `research/intake.py`
- `longterm/motley_fool_intake.py`
- `longterm/batch_intake.py`
- `longterm/market_enrichment.py`

### Phase 4 - Recommendation table maturity
Status: foundational version done
Priority: medium-high

Goal:
- turn the recommendation table into the central ranked operating surface for the active sleeve

Planned enhancements:
- stronger previous-rank / movement tracking
- better duplicate/repeat recommendation handling
- clearer separation of:
  - raw decision history
  - current best thesis per symbol
  - current next-action state
- better symbol/source attribution reporting over time
- stronger benchmark-relative reporting for recommendations

Potential files:
- `longterm/report_builder.py`
- `longterm/recommendation_enrichment.py`
- `longterm/decision_journal.py`
- `longterm/review_status.py`

### Phase 5 - Thesis-monitoring and review workflow hardening
Status: foundation done
Priority: medium-high

Goal:
- make the system genuinely useful after initial recommendation, not just at idea entry

Planned enhancements:
- improved review-due generation
- clearer thesis-broken / thesis-weakening / thesis-on-track states
- cadence-specific review templates by company type
- better handling of repeated decisions for the same symbol

Potential files:
- `longterm/thesis_monitor.py`
- `longterm/review_cadence.py`
- `longterm/review_status.py`
- `longterm/decision_journal.py`

### Phase 6 - Next-actions and rebalance planning refinement
Status: initial version done
Priority: medium

Goal:
- improve portfolio-aware prioritization without crossing into unsafe autonomous execution

Planned enhancements:
- stronger scoring when comparing new candidates to current active holdings
- better active-cash accounting
- clearer dry-run rebalance proposals
- stronger explanation when benchmark guard pauses buys
- better distinction between:
  - add-to-winner
  - replace weaker active holding
  - hold and wait
  - no-action due to benchmark / cash / protection constraints

Potential files:
- `longterm/next_actions.py`
- `longterm/rebalance_planner.py`
- `longterm/action_planner.py`
- `longterm/benchmark_guard.py`
- `longterm/portfolio_state.py`

### Phase 7 - Alerting and operator workflow polish
Status: partial foundation done
Priority: medium

Goal:
- make operator-facing outputs easy to trust and act on

Planned enhancements:
- scheduled capital-needed checks
- prettier email/report formatting
- clearer CLI output for daily/weekly summaries
- explicit dry-run versus send behavior in docs and commands
- safer operator visibility around what triggered a capital-needed alert

Potential files:
- `longterm/capital_alert.py`
- `longterm/capital_alert_cli.py`
- `longterm/email_sender.py`
- `docs/system/OPERATIONS.md`

### Phase 8 - Live-readiness design, not implementation
Status: future
Priority: later

Goal:
- define what would have to be true before any live long-term execution exists

This phase should happen only after the scheduler/orchestration, research quality, and benchmark framework prove useful in dry-run mode.

Likely requirements:
- explicit live-mode config gate
- strong broker-state read layer
- explicit protected-holding enforcement in execution layer
- robust audit logs
- manual approval or staged rollout
- clear rollback / disable switch

Important:
- Do not jump here early.
- The current project should earn the right to automate by first being a strong research-and-ranking system.

## 7. Recommended Immediate Next Task

If another Codex instance is asked to continue the project, the best next task is:

Build the long-term scheduler/orchestration layer in dry-run mode.

That work should:
- reuse existing modules rather than rewriting logic
- treat Motley Fool as optional and config-gated
- produce research packets, decisions, and next-actions outputs
- avoid any broker execution path

Best small first deliverable:
- a single orchestration function and/or CLI flow that runs one complete long-term cycle end-to-end using:
  - optional Motley Fool capture
  - packet normalization
  - CGH research decision
  - journal persistence
  - next-actions / recommendation output

## 8. Practical Notes For Another Codex

### Commands that already work
From `ai_trader/trading_agent`:
- `python -m pytest longterm -q`
- `python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --dry-run`
- `python scripts/longterm_journal.py report --limit 10`
- `python scripts/longterm_next_actions.py --portfolio-state path\\to\\portfolio.json --limit 10`
- `python scripts/longterm_motley_fool_capture.py --source dashboard`

### Local machine state worth knowing
- local email config exists in `ai_trader/trading_agent/config/email_notifications.json`
- local Motley Fool runtime config exists in `ai_trader/trading_agent/config/motley_fool_capture.json`
- current local Fool runtime config has:
  - `enabled=true`
  - `cookie_ready=true`
  - profile `~/.grok3api_chrome_profile`
  - sources `new_recommendations`, `analyst_rankings`, `quant_rankings`

### Known design stance
- this repo is intentionally slower, deeper, and more thesis-driven than the swing system
- not every idea should become an action
- the recommendation table should become the operating surface for the active sleeve
- benchmark accountability versus `FXAIX` is central, not decorative

## 9. One Warning About Context

There is older workspace memory for the swing trader and day trader. Do not assume that memory directly applies here.

For this repo, trust these first:
- `README.md`
- `docs/system/ARCHITECTURE.md`
- `docs/system/SAFETY.md`
- `ai_trader/rules/active_rules.txt`
- `CURR_MEMORY.md`

## 10. Suggested Follow-Up After The Next Milestone

Once the scheduler/orchestration layer is built and validated, the next likely best sequence is:

1. Harden packet completeness and source provenance.
2. Improve recommendation-table ranking/reporting maturity.
3. Strengthen thesis-monitor and review workflow.
4. Refine next-actions / rebalance decision quality.
5. Only then start a true live-readiness design review.

