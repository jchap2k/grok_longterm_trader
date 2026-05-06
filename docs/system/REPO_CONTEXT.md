# Grok Long-Term Trader - Repo Context

This file is the canonical first-read context for Grok and other LLM review
partners that can access the GitHub repo.

Instruction for Grok: at the start of every long-term trader review/debug
conversation, first use GitHub tools, preferably `get_file_contents`, to read
`docs/system/REPO_CONTEXT.md` from `jchap2k/grok_longterm_trader`. Treat this
file as the current project map and single source of truth for repo context. Do
not rely on stale chat memory or day/swing trader context. Inspect source files
only after this file has been loaded and only when the specific review needs
deeper verification.

Last updated: 2026-05-06 by Codex after scheduler no-submit cadence
verification, timeout/cadence-state, enrichment, dashboard, paper-boundary,
and Grok-collab updates.

## 1. Project Identity

Repo: `https://github.com/jchap2k/grok_longterm_trader`

Purpose: research-first long-term quality-growth trader for an active sleeve,
with dry-run/paper safety, thesis records, recommendation ranking, benchmark
discipline, and eventual supervised/autonomous paper execution.

Protected core benchmark: `FXAIX`

Current mode:
- Research, enrichment, ranking, journal persistence, operator dashboard, and
  supervised paper BUY testing are active.
- Live trading is not enabled.
- Broker/order code must fail closed unless paper/non-taxable mode is explicit.
- Sell/rebalance intents can be planned and previewed for operator review, but
  actual Stage 6B broker submission remains simple BUY-only in V1.

Strategy identity:
- Keep `FXAIX` protected as the core benchmark.
- Use active sleeve for fewer, higher-conviction quality-growth ideas.
- Compare active decisions against simply keeping capital in `FXAIX`.
- Avoid forced activity, vague story stocks, weak balance sheets, excessive
  leverage, and protected-symbol actions.

## 2. Current Architecture

Primary flow:
- Universe sources and Motley Fool intake produce raw candidates.
- Deterministic first-pass scoring ranks the wider universe and selects a top
  percent for paid/deeper enrichment rather than hard-filtering everything out.
- Enrichment gathers deterministic financial metrics, quant-style scorecards,
  relevant articles/catalysts, and provider-backed summaries.
- Research packets are normalized and completeness-gated before committee work.
- Deterministic reviewers plus the CGH committee produce structured decisions.
- Decisions are journaled and surfaced through recommendation reports,
  next-actions, paper preflight artifacts, and the local dashboard.
- Paper-boundary logic can submit simple BUYs only when every safety gate is
  revalidated; no live broker path is enabled.

Important modules:
- `research/research_packet.py`
- `research/intake.py`
- `longterm/discovery.py`
- `longterm/discovery_enrichment.py`
- `longterm/research_automation_campaign.py`
- `longterm/research_evidence_brief.py`
- `longterm/research_runner.py`
- `longterm/committee_batch_runner.py`
- `longterm/committee_preset_policy.py`
- `longterm/decision_journal.py`
- `longterm/report_builder.py`
- `longterm/next_actions.py`
- `longterm/action_planner.py`
- `longterm/rebalance_planner.py`
- `longterm/benchmark_guard.py`
- `longterm/account_tax_policy.py`
- `longterm/paper_execution_boundary.py`
- `longterm/pipeline_scheduler.py`
- `longterm/operator_dashboard.py`

Primary docs:
- `docs/system/ARCHITECTURE.md`
- `docs/system/OPERATIONS.md`
- `docs/system/SAFETY.md`
- `docs/system/project_manifest.json`
- `ai_trader/rules/active_rules.txt`

## 3. Model And Provider Policy

Grok 4.1 fast models are being deprecated on 2026-05-15. Current policy:
- Use Grok `grok-4.3` for decision-grade committee reasoning.
- Default committee preset is `decision_4`.
- Escalate to `decision_6` for larger sizing, new/unproven theses, borderline
  valuation, choppy macro, complex rebalance/sell context, or high uncertainty.
- Use Python, cached data, Polygon/Finnhub/yfinance where possible before LLMs.
- Use Perplexity Sonar as explicit opt-in broad enrichment when deterministic
  data and free sources are insufficient.
- Keep paid enrichment capped, resumable, and tracked for cost/API usage.

## 4. Universe And Enrichment State

Broad workflow:
- Load available universe tickers from local/source files.
- Run deterministic first-pass scorecards across the universe.
- Select top percent, commonly 5-10 percent, for deeper enrichment.
- Preserve overlap with Motley Fool rather than excluding it; overlap is useful
  source confirmation.
- Motley Fool fresh recommendations and repeated recommendation counts are
  source-priority signals, not automatic buy authority.

Motley Fool state:
- Optional/config-gated premium intake exists.
- `new_recommendations` rows carry fresh-rec priority metadata.
- Repeat counts from `Times Rec'd` are stored as source recommendation counts.
- Per-ticker Fool summary/financial pages can enrich covered names.
- Optional Stock Advisor service-list capture exists for universe expansion;
  duplicate service-list rows become repeat-count context and the supplied
  service performance snapshot is display-only attribution, not execution logic.

Perplexity state:
- Perplexity enrichment is explicit/opt-in.
- Compact prompt plus malformed-JSON fallback is reliable enough for supervised
  broad enrichment.
- Recent 25-name smoke produced full Perplexity research objects with article
  evidence and no malformed fallback rows.

## 5. Scheduler And Pipeline State

Scheduler-readiness features now exist:
- Pipeline scheduler can run no-submit research/paper refresh chains.
- `longterm_pipeline_scheduler.py --preset ongoing-no-submit` builds the
  standard safe chain from core paths: fresh Alpaca paper snapshot, no-submit
  research-to-paper pipeline, advisory scheduler policy, and read-only
  account/dashboard refresh.
- The safe preset can optionally pass through bounded upstream research
  campaign, Perplexity, and generated committee batch controls; paid Perplexity
  mode requires `--research-max-pass-count`, and generated committee execution
  requires `--generated-committee-max-batches`.
- Scheduler run records include a `resource_controls` summary that surfaces the
  visible provider mode, paid-provider status, research/evidence/committee caps,
  bounded status, and an intentionally unknown pre-run cost estimate.
- Final-planning refresh can be subprocess-timeout bounded. The pipeline stage
  records `timeout_seconds`; on timeout it fails closed with
  `stage_timeout:final_planning_refresh`, stops downstream stages, writes the
  stage log, and leaves `order_submission_enabled=false`.
- The ongoing no-submit scheduler preset forwards
  `--final-planning-timeout-seconds` whenever `--final-planning-refresh` is
  enabled. If omitted by the operator, the preset uses a 900-second default.
  Resource controls expose `final_planning_refresh` and
  `final_planning_timeout_seconds`; enabled final planning with no timeout is
  considered unbounded.
- Dashboard manifests can carry the top-level pipeline scheduler summary path,
  allowing `/api/pipeline-health.json` and the Safety / Preflight card to show
  scheduler resource-control state once the scheduler summary is finalized.
- Scheduler policy treats unbounded paid/provider resource controls as a
  high-urgency `resource_control_review` blocker, while bounded paid runs carry
  a warning for operator awareness. Operator status bundles and markdown surface
  the same controls.
- Latest scheduler smokes:
  - `%TEMP%\longterm_scheduler_full_plan_smoke_20260506_130540`: print-plan
    with bounded Perplexity plus generated committee controls proved
    `resource_controls.bounded=true`, `provider_mode=perplexity`,
    `research_max_pass_count=25`, `research_max_evidence_batches=2`, and
    `generated_committee_max_batches=1` while
    `order_submission_enabled=false`.
  - `%TEMP%\longterm_scheduler_chunks1_4_20260506_160511`: two-cycle real
    no-submit operational scheduler run from the current paper account snapshot
    and saved action plan. Both runs completed with pipeline blockers `0`,
    artifact health `ready`, 10 paper holdings reflected, dashboard pages
    generated, account refresh success, and no submitted orders.
  - `%TEMP%\longterm_scheduler_final_planning_plan_20260506_160649`: print-plan
    proved final planning renders `--final-planning-refresh` plus
    `--final-planning-timeout-seconds 45` and resource controls mark it bounded.
  - `%TEMP%\longterm_scheduler_chunks5_7_20260506_163023`: full no-submit
    research cadence proof using copied/resumable completed committee artifacts.
    The scheduler completed one run with `error_count=0`,
    `order_submission_enabled=false`, generated-committee stage completed by
    skipping the 10 already-handled batches, final planning passed with a
    900-second timeout bound, pipeline artifact health was `ready`, and the new
    scheduler cadence verifier reported `status=ready` with no blockers.
- Policy-state artifacts track `last_full_research_at`,
  `last_no_submit_preflight_at`, `last_account_refresh_at`, and
  `last_final_planning_at`. The scheduler updates account/preflight timestamps
  after each completed cycle, and marks final planning complete only when the
  saved pipeline summary completed both `final_planning_refresh` and
  `extract_final_action_plan` with zero blockers.
- `longterm_pipeline_scheduler_verify.py` is the saved-artifact verifier for
  no-submit cadence runs. It checks scheduler/pipeline status, no-submit command
  fragments, bounded resource controls, final-planning timeout, workflow-smoke
  submitted count, and required policy-state timestamps.
- Scheduler policy now emits `cadence_recommendations` for account refresh,
  no-submit preflight, full research, and final planning. Final planning becomes
  due when active rules change, when final planning is older than the latest
  full research, or when its cadence expires.
- Committee batch runs are resumable and bounded by `--max-batches`.
- Full research cadence has been proven through smaller chunks and completed
  all generated committee batches in a bounded run.
- One longer full no-submit execute was intentionally stopped after wrapper
  timeout while the empty-batch final-planning refresh was still running; use
  longer supervised windows, existing saved action plans, or smaller resumable
  chunks rather than assuming final planning is always a quick scheduler step.
- Scheduler can refresh paper account state before downstream planning.

Near-term scheduler target:
- Move from manual supervised scheduler proofs toward a bounded recurring
  no-submit loop using the verifier report as the post-run acceptance check.
- Keep paid provider flags explicit until cost behavior is comfortable.
- Keep broker submission disabled unless running the supervised paper BUY path.

## 6. Paper Execution Boundary

Stage 6B V1 constraints:
- Only simple BUY previews can submit to Alpaca paper.
- Any preview with a sell leg, explicit sell/reduce intent, or rebalance
  structure is hard-blocked at submit time.
- Paper mode must be provable by constructor flag and paper API URL.
- `client_order_id` is deterministic for idempotency.
- Execution events include attempt grouping and paper/live safety flags.
- Preflight audit logs are written before any broker call.
- FXAIX and protected symbols are never submit-eligible.

Current account-mode stance:
- Alpaca paper is treated as non-taxable simulation.
- Future live mode is expected to target Roth IRA behavior.
- Taxable profiles suppress broad parking and broad rebalance churn, but do not
  block symbol-specific sells when a thesis is broken.
- Account action planning now surfaces explicit non-protected `SELL` / `REDUCE`
  decisions before the default held-position review path, so sell-worthy active
  holdings are visible in preview artifacts without enabling broker sells.

## 7. Portfolio, Parking, And Risk Regime

Idle/parking policy:
- Normal or constructive regime: default idle parking can be `SPY` when account
  mode allows it.
- Elevated uncertainty: blend approved equities/`SPY` with short-duration
  parking such as `SGOV`/`BIL`.
- Classic equity panic with falling yields: optional, capped duration hedge via
  `TLT` or `IEF`.
- Inflation/rate-shock volatility: prefer `SGOV`/`BIL` or cash-like parking.
- Do not blindly buy `TLT` on VIX spikes alone.

Open point:
- Scheduler should refresh market regime and parking prices, not just `SPY`.
- Panic indicators should trigger decision/review logic, not direct broad sells.
- If a downturn exit is made, redeployment should preserve the prior high-quality
  buy list and consider re-buying stronger versions of exited names after risk
  normalizes.

## 8. Dashboard And Operator Surface

The local dashboard now:
- Reads manifests/artifacts rather than requiring manual regeneration after
  every scan.
- Shows rankings, scorecards, ticker tear sheets, portfolio bars/gain state,
  paper candidates, parking intents, safety/preflight, provider/API usage, and
  placeholders for future chat/command surfaces.
- Uses a custom long-term trader logo and left-nav icons.
- Supports pagination-ready ranked/scorecard tables for growing universes.

Dashboard should remain an operator/review surface first:
- Future chat commands can be added later.
- Command chat must route through explicit safety parsing and approval logic.
- The dashboard should not bypass paper/live execution gates.

## 9. Safety Rules

Non-negotiable:
- No live trading without explicit user approval, config flag, paper validation,
  and safety review.
- Never sell, trim, rotate, rebalance, or submit orders for `FXAIX`.
- Capital-needed alerts are informational only.
- Broker credentials/tokens are never committed.
- Broad universe scans do not create automatic buys.
- Source recommendations do not override research, benchmark, promotion, or
  paper-boundary gates.
- Keep user ideas as inputs to evaluate, not automatic commands.

## 10. Current Open Work

Useful next work:
- Run the longer no-submit scheduler cadence in a supervised window.
- Continue hardening the scheduler path toward automatic daily/weekly operation.
- Keep `REPO_CONTEXT.md`, `ARCHITECTURE.md`, `OPERATIONS.md`, and
  `project_manifest.json` synchronized after major changes.
- Expand dashboard data freshness and portfolio state refresh.
- Add/update dashboards for API spend and Perplexity tier progress.
- Keep broad enrichment provider selection cost-aware and explicit.
- Later: design live-readiness separately, after paper/autonomous research has
  proven stable.

## 11. Context Maintenance Rule

Keep this file useful as a map, not a raw changelog.

Maintenance cadence:
- After meaningful architecture/workflow changes, update the relevant section.
- After roughly five meaningful additions, do a compression pass.
- During compression, summarize stale detail into short state bullets, preserve
  current decisions and safety constraints in detail, and move active blockers
  into the right section instead of appending them at the bottom.
- Do not duplicate `RECENT_CHANGES.md`; this file should describe current state
  and operating rules, not every implementation step.
- If a detail is only useful for local continuity, keep it in
  `codex_compatible/memory/`. If Grok needs it before reviewing GitHub state,
  keep it here.

Suggested LLM-collab first prompt:

```text
You are Grok acting as planning and review agent for the
jchap2k/grok_longterm_trader GitHub repo.

First action: use GitHub tools, preferably get_file_contents, to read
docs/system/REPO_CONTEXT.md from jchap2k/grok_longterm_trader. Treat that file
as the authoritative current project context. Do not begin source-file scanning
before reading it. After loading it, proceed with the user's review/debug
request and inspect additional files only if needed.
```
