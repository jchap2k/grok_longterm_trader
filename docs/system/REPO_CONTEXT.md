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

Last updated: 2026-05-08 by Codex after the no-submit scheduler handoff packet,
dashboard handoff seam, bounded scheduled-research print-plan, and
sell/rebalance simulation visibility pass.

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
- `longterm/portfolio_news_monitor.py`
- `longterm/portfolio_news_monitor_ingest.py`
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

Future local model candidate:
- Kronos is queued after scheduler readiness as an optional local
  market-language layer, not as a critical-path dependency.
- It needs OHLCV bars from yfinance, Polygon, Alpaca read APIs, or cached
  artifacts; it does not require a paid LLM API.
- First milestone should be an isolated smoke on 2-3 symbols that writes compact
  JSON signals only.
- Intended uses, if validated: pre-deep-enrichment prioritization for broad
  universe candidates, daily current-position regime/divergence sensing, and
  compact context for high-stakes `decision_6` reviews.
- Kronos must remain advisory and must not create trade intents, override
  active rules, or bypass benchmark, buy-promotion, scheduler, protected-symbol,
  paper, or live gates.

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
- `longterm_portfolio_news_monitor.py` now provides a deterministic daily
  portfolio/watchlist news-monitor artifact. It reuses relevant-news scoring,
  excludes protected holdings by default, links queued portfolio symbols to the
  latest journal decision when available, and writes an
  `enrichment_needed_queue` without broker calls, LLM calls, or order intents.
- `longterm_portfolio_news_monitor_ingest.py` validates saved monitor reports
  and creates compact queue summaries plus
  `portfolio_news_followup_ideas.json` for pipeline/scheduler/dashboard
  rollups. Follow-up ideas are validated through
  `research.intake.create_research_packet_from_idea()` before being written,
  but they remain future bounded enrichment/review inputs. The ingest path does
  not fetch news, call LLMs, or create trade intents.
- `longterm_research_to_paper_pipeline.py --portfolio-news-followup-batches`
  can split `portfolio_news_followup_ideas.json` into normal
  `portfolio_news_followup_batches/research-batch-*.json` files. This is only a
  deterministic artifact handoff for later bounded committee review; it does
  not run the committee, call paid providers, or alter account actions.
- `longterm_research_to_paper_pipeline.py
  --run-portfolio-news-followup-committee-batches
  --portfolio-news-followup-max-batches N` can now run a capped number of those
  follow-up batches through the existing no-submit committee runner. This may
  journal review decisions, but it still does not refresh buy-promotion/final
  action planning, mutate account actions, or submit broker orders.
- Pipeline rollups and `/api/pipeline-health.json` now expose reviewed
  portfolio-news follow-up symbols, decision IDs, reviewed count, and the next
  safe action
  `inspect_portfolio_news_followup_reviews_before_final_planning_refresh`.
  This makes the required inspection checkpoint visible before any later
  buy-promotion/final-planning refresh.

## 5. Scheduler And Pipeline State

Scheduler-readiness features now exist:
- Pipeline scheduler can run no-submit research/paper refresh chains.
- `longterm_pipeline_scheduler.py --preset ongoing-no-submit` builds the
  standard safe chain from core paths: fresh Alpaca paper snapshot, no-submit
  research-to-paper pipeline, advisory scheduler policy, and read-only
  account/dashboard refresh. The preset now also appends a post-run
  `longterm_pipeline_scheduler_verify.py` command after the scheduler summary
  and policy-state artifacts are written.
- The safe preset can optionally run the deterministic portfolio-news monitor
  before the pipeline via `--portfolio-news-monitor` and a required
  `--portfolio-news-snapshot-file`. It writes
  `run_00N/portfolio_news_monitor.json`, passes that path into
  `longterm_research_to_paper_pipeline.py --portfolio-news-monitor`, and adds
  `last_news_monitor_at` to verifier timestamp requirements.
- If `--portfolio-news-followup-batches` is enabled, the safe preset forwards
  the follow-up batch flags into the pipeline, records
  `last_followup_batch_split_at` after a successful split stage, and requires
  that timestamp in the post-run verifier.
- If `--run-portfolio-news-followup-committee-batches` is enabled, the safe
  preset requires `--portfolio-news-followup-max-batches`, forwards the cap
  into the pipeline, records `last_followup_committee_at` after a successful
  no-failure capped committee run, and requires that timestamp in the post-run
  verifier. `remaining_count > 0` is acceptable when the run stopped at the
  explicit cap.
- The research-to-paper pipeline now ingests saved monitor reports as
  `ingest_portfolio_news_monitor`. Its artifact rollup exposes
  `portfolio_news_monitor.queue_count`, `high_impact_count`,
  `review_trigger_count`, affected symbols, high-impact journal-linked symbols,
  `followup_idea_count`, follow-up symbols, optional follow-up batch counts,
  follow-up committee progress counts, reviewed follow-up decision IDs/symbols,
  warnings, and top triggers while keeping `order_submission_enabled=false`.
- The safe preset can optionally pass through bounded upstream research
  campaign, Perplexity, and generated committee batch controls; paid Perplexity
  mode requires `--research-max-pass-count`, and generated committee execution
  requires `--generated-committee-max-batches`.
- Scheduler run records include a `resource_controls` summary that surfaces the
  visible provider mode, paid-provider status, research/evidence/committee caps,
  portfolio-news follow-up committee cap, bounded status, and an intentionally
  unknown pre-run cost estimate.
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
- Scheduler run records can carry post-run verification path, command,
  stdout/stderr paths, and exit code. If the verifier exits non-zero after an
  otherwise completed no-submit run, the scheduler marks the run failed with
  `post_run_verification_command_failed`.
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
  - `%TEMP%\longterm_postrun_verifier_plan_20260506_171300`: print-plan smoke
    proved the safe scheduler preset now renders the automatic post-run
    verifier with resource, account-refresh, preflight, and final-planning
    timestamp checks.
  - `%TEMP%\longterm_scheduler_watch_20260507_173916`: two-cycle no-submit
    scheduler watch launched from `ai_trader/trading_agent` against copied
    journal/ledger artifacts and a saved action plan. Both runs completed with
    `error_count=0`, `order_submission_enabled=false`, pre-pipeline Alpaca
    paper snapshot `0`, pipeline `0`, scheduler policy `0`, post-run verifier
    `0`, account/dashboard refresh `0`, bounded resource controls, generated
    dashboard manifests/sites, and current portfolio snapshots showing 10 paper
    holdings plus `$67,641.28` cash. The first attempted launch from the repo
    root failed safely because generated preset commands expect the scheduler
    cwd to be `ai_trader/trading_agent`.
  - `%TEMP%\longterm_scheduler_root_cwd_smoke_20260507_201015`: repo-root
    launch smoke proved the preset now renders absolute script paths,
    normalizes user-supplied paths before child commands run, and executes
    subprocesses from `ai_trader/trading_agent`. The one-cycle no-submit run
    completed with `error_count=0`, `order_submission_enabled=false`,
    pre-pipeline snapshot `0`, pipeline `0`, scheduler policy `0`, post-run
    verifier `0`, and account/dashboard refresh `0`.
  - `%TEMP%\longterm_scheduler_handoff_20260508_062101`: reviewed no-submit
    handoff packet and one-cycle scheduler execution. The config validation,
    task plan, dashboard manifest, and handoff check all aligned with
    `status=ready`, including an explicit `order_submission_boundary=ready`
    handoff check; the one-cycle scheduler run completed with
    `order_submission_enabled=false`, bounded resource controls, no submit
    flags, post-run verification success, and refreshed dashboard manifests
    carrying both scheduler task-plan and scheduler handoff paths.
  - `%TEMP%\longterm_scheduled_research_printplan_20260508_062300`: bounded
    paid-resource scheduler print-plan only. It did not run Perplexity or
    committee work, but proved the command shape for `provider_mode=perplexity`
    with `research_max_pass_count=3`, `research_max_evidence_batches=1`,
    `generated_committee_max_batches=1`, and no submit flags.
- Policy-state artifacts track `last_full_research_at`,
  `last_no_submit_preflight_at`, `last_account_refresh_at`, and
  `last_final_planning_at`; when portfolio-news features are enabled they also
  track `last_news_monitor_at`, `last_followup_batch_split_at`, and
  `last_followup_committee_at`. The scheduler updates account/preflight
  timestamps after each completed cycle, writes `last_news_monitor_at` after a
  successful monitor pass even if a later pipeline stage fails, and marks final
  planning complete only when the saved pipeline summary completed both
  `final_planning_refresh` and `extract_final_action_plan` with zero blockers.
- `longterm_pipeline_scheduler_verify.py` is the saved-artifact verifier for
  no-submit cadence runs. It checks scheduler/pipeline status, no-submit command
  fragments, bounded resource controls, final-planning timeout, workflow-smoke
  submitted count, and required policy-state timestamps.
- Simulator cadence policy: Python refreshes can run often (roughly every
  15-60 minutes during market hours), portfolio/watchlist news checks should be
  daily and deterministic/cache-first, deeper enrichment should be weekly or
  event-triggered, and LLM committee decisions should stay sparse/event-driven
  rather than continuously active.
- Scheduler policy now emits `cadence_recommendations` for account refresh,
  no-submit preflight, full research, and final planning. Final planning becomes
  due when active rules change, when final planning is older than the latest
  full research, or when its cadence expires.
- Committee batch runs are resumable and bounded by `--max-batches`.
- Full research cadence has been proven through smaller chunks and completed
  all generated committee batches in a bounded run.
- A saved-action-plan recurring no-submit watch has now completed two cycles
  with the automatic post-run verifier and dashboard refresh enabled.
- The safe scheduler preset can be launched from the repo root via
  `python ai_trader\trading_agent\scripts\longterm_pipeline_scheduler.py ...`;
  generated child commands use absolute script paths and a trading-agent cwd.
- The scheduler CLI now supports `--config-file` JSON profiles with a strict
  `args` object. Example:
  `longterm/configs/ongoing_no_submit_scheduler.example.json`. This keeps
  repeatable no-submit scheduler launches in one local profile while rejecting
  unknown keys and preserving the existing safety checks.
- `--validate-config-only` validates a resolved scheduler profile/templates and
  prints commands/resource controls without creating run folders, calling
  brokers, or executing scheduler stages. When `summary_output` is supplied,
  validation writes the same JSON payload to that file for dashboard/runbook
  inspection, including the resolved `config_file` path for provenance. Keep
  validation enabled in new local profiles until the operator has reviewed the
  generated command surface.
- `scripts/longterm_scheduler_profile.py` renders a local scheduler JSON
  profile from `ongoing_no_submit_scheduler.example.json` using explicit
  `--set key=value` plus boolean `--enable` / `--disable` overrides. It keeps
  `validate_config_only=true` by default and can validate/write the
  profile-validation summary in the same no-submit pass. After review,
  `--run-mode no-submit` renders a recurring no-submit profile without
  hand-editing JSON; the renderer rejects submit-capable keys such as
  `submit_paper_orders` and `confirm_paper_submit`.
- `scripts/longterm_scheduler_task_plan.py` turns a reviewed no-submit run
  profile into a read-only Windows Task Scheduler plan artifact containing the
  scheduler command plus `schtasks` and PowerShell registration commands. It
  does not register tasks, rejects validation-only profiles, and rejects
  submit-capable profile keys. It also runs scheduler profile validation before
  emitting commands, so unbounded paid-provider profiles fail closed.
- `scripts/longterm_scheduler_handoff.py` validates the final scheduler
  handoff chain across the config-validation artifact, task-plan artifact, and
  dashboard manifest. It exits ready only when the artifacts agree and
  `order_submission_enabled=false` throughout, and now exposes the explicit
  `order_submission_boundary` check for dashboard/operator review.
- Dashboard manifests can point at a saved scheduler config-validation JSON via
  `scheduler_config_validation`; the localhost server exposes it at
  `/api/scheduler-config-validation.json`, includes it in `/api/summary.json`,
  and renders a read-only Scheduler Profile card on the Safety / Preflight
  section.
- Dashboard manifests can also point at `scheduler_task_plan`; the localhost
  server exposes `/api/scheduler-task-plan.json`, includes it in
  `/api/summary.json`, and renders the Windows Task Scheduler registration
  plan as a review artifact.
- Dashboard manifests can point at `scheduler_handoff`; the localhost server
  exposes `/api/scheduler-handoff.json`, includes it in `/api/summary.json`,
  and renders a Scheduler Handoff card in Safety / Preflight. Read-only
  paper-account refresh and the `ongoing-no-submit` scheduler preset can pass
  scheduler config-validation, task-plan, and handoff paths through to refreshed
  dashboard manifests/sites.
- Read-only paper-account refresh and the `ongoing-no-submit` scheduler preset
  can pass scheduler review artifacts through to refreshed dashboard
  manifests/sites, so reviewed local profiles and handoff evidence remain
  visible after recurring account/dashboard refreshes.
- One longer full no-submit execute was intentionally stopped after wrapper
  timeout while the empty-batch final-planning refresh was still running; use
  longer supervised windows, existing saved action plans, or smaller resumable
  chunks rather than assuming final planning is always a quick scheduler step.
- Scheduler can refresh paper account state before downstream planning.

Near-term scheduler target:
- Move from manual supervised scheduler proofs toward a bounded recurring
  no-submit loop using the verifier report as the post-run acceptance check.
- Feed monitor queue rows into a later explicit deeper-enrichment/review queue;
  current scheduler wiring surfaces packet-validated follow-up ideas, can split
  them into bounded research batch files, and timestamps the handoff but does
  not automatically spend LLM calls or change portfolio decisions.
- Keep paid provider flags explicit until cost behavior is comfortable.
- Keep broker submission disabled unless running the supervised paper BUY path.
- Keep Kronos out of scheduler-critical path until recurring no-submit operation
  is stable; then add it as a local advisory sensor for enrichment priority and
  current-position review triggers.

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
- Review / Simulation Intents now keep SELL, REDUCE, REBALANCE, REVIEW, and
  HOLD rows visible for analysis while Stage 6B V1 still submits only simple
  BUY previews.

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
- After scheduler readiness, prototype the Kronos market-language advisory pass
  with saved OHLCV and compare it side-by-side with existing enrichment/committee
  outcomes before feeding it into any decision context.
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
