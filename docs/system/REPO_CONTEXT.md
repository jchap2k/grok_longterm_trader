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

Last updated: 2026-05-08 by Codex after registering the no-submit scheduler
and adding a separate read-only dashboard startup launcher.

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
- Kronos is cloned locally at `S:\LLM_files\other_github\Kronos` and queued
  after scheduler readiness as an optional local
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
- The recurring capture default is `new_recommendations` from
  `https://www.fool.com/premium/new-recs`; broader `all_full`, rankings, and
  Stock Advisor service-list captures are intentional universe/context runs.
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
  The monitor honors a `published_after` watermark for daily cadence and keeps
  undated articles for conservative review.
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
- The recurring no-submit Windows task is `LongTermTraderNoSubmit`. It remains
  separate from dashboard startup and keeps `order_submission_enabled=false`.
- First registered-task verification is complete. The original same-day trigger
  did not backfill because the task was registered after 09:30, so Codex
  manually started `LongTermTraderNoSubmit` once. Windows reported
  `LastTaskResult=0`; the launcher wrote `scheduler_runs\run_001`; the scheduler
  summary completed with `error_count=0`, `success_count=1`, and
  `order_submission_enabled=false`; and the cadence verifier reported
  `status=ready` with no blockers.
- The task action now calls
  `S:\LLM_files\grok_longterm_trader_runtime\no_submit_scheduler\start_longterm_no_submit_scheduler.ps1`
  instead of directly calling Python. That runtime launcher sets the
  trading-agent working directory, writes stdout/stderr/status logs, and runs
  `refresh_latest_operator_surface.ps1` after successful runs.
- The latest operator dashboard surface is
  `S:\LLM_files\grok_longterm_trader_runtime\no_submit_scheduler\scheduler_runs\latest_operator_surface\dashboard_manifest.json`.
  It merges fresh run artifacts with stable scheduler registration/launch
  evidence so the live dashboard can show both current run health and
  no-submit registration readiness. The writer must emit UTF-8 without BOM
  because the Python dashboard loader rejects BOM-prefixed JSON.
- The local dashboard startup path is separate: a per-user Startup shortcut
  launches
  `S:\LLM_files\grok_longterm_trader_runtime\no_submit_scheduler\dashboard_server\start_longterm_dashboard.ps1`
  at logon. That launcher starts only the localhost dashboard server on
  `127.0.0.1:8765`, uses `--auto-manifest-root` pointed at `scheduler_runs`,
  exits without starting a duplicate if the port is already listening, and does
  not run scheduler cycles, broker calls, or LLM calls.
- A true `LongTermTraderDashboard` Windows Scheduled Task was prepared but the
  current non-elevated shell was denied registration by Windows; the elevated
  retry command is saved in
  `S:\LLM_files\grok_longterm_trader_runtime\no_submit_scheduler\registration_review\dashboard_startup_registration_review.json`.
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
- Scheduler run records can now also carry a `scheduler_review_bundle_command`
  and output/log fields. When supplied, the bundle runs only after a completed
  scheduler cycle has written its summary and the post-run verifier exits `0`.
  If the bundle fails, the scheduler marks the run failed with
  `scheduler_review_bundle_command_failed`; if the verifier fails, the bundle is
  not run.
- Scheduler run records can carry a deterministic
  `position_review_queue_command` stage after portfolio-news monitoring and
  before the pipeline. The stage writes `run_00N/position_review_queue.json`,
  remains no-submit/no-LLM/no-broker, excludes protected symbols by default,
  and records `last_position_review_at` on success.
- `scripts/longterm_position_review_queue.py` builds advisory sell/reduce,
  rebalance, and portfolio-news/thesis review rows from saved portfolio state,
  action plans, portfolio-news monitor reports, and journal review status. It
  reuses existing thesis/review facts and linkage fields, adds Mr. Market
  drawdown/rally review rows directly from current holdings, emits
  `staged_entry_graduation_review` rows for starter-sized BUY/ADD holdings that
  may deserve add-toward-target review, and does not create trade authority.
- `scripts/longterm_paper_submit_mode_plan.py` is a disabled-by-default
  readiness checklist for future submit-capable paper profiles. It requires a
  fresh ready scheduler handoff, a successful no-submit scheduler summary, and
  a completed position-review queue; it emits no runnable submit command and
  keeps `order_submission_enabled=false`, `submit_profile_enabled=false`, and
  `broker_calls_enabled=false`.
- `scripts/longterm_scheduler_review_bundle.py` is the post-scheduler
  no-submit review gate bundler. It consumes a dashboard manifest, scheduler
  handoff, scheduler summary, position-review queue, and post-run verifier
  report; writes `paper_submit_mode_plan.json`,
  `scheduler_review_bundle.json`, and
  `dashboard_review_gates_manifest.json`; and returns ready only when the
  verifier, resource controls, scheduler run, position queue, benchmark policy,
  and disabled submit-mode plan are clean. It can optionally block on supplied
  buy-promotion/final-action artifacts. It never emits submit commands,
  enables a submit profile, calls a broker, or calls an LLM.
- The safe `ongoing-no-submit` scheduler preset can render this bundler behind
  `--scheduler-review-bundle`, which requires `--position-review-queue` and a
  saved `--scheduler-handoff`. The preset writes bundle outputs under
  `run_00N/scheduler_review_bundle/` and scans the rendered command for
  submit-capable fragments like every other scheduler stage.
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
  track `last_news_monitor_at`, `last_position_review_at`,
  `last_followup_batch_split_at`, and `last_followup_committee_at`. The
  scheduler updates account/preflight timestamps after each completed cycle,
  writes `last_news_monitor_at` and `last_position_review_at` after successful
  no-submit stages even if a later pipeline stage fails, and marks final
  planning complete only when the saved pipeline summary completed both
  `final_planning_refresh` and `extract_final_action_plan` with zero blockers.
- `longterm_pipeline_scheduler_verify.py` is the saved-artifact verifier for
  no-submit cadence runs. It checks scheduler/pipeline status, no-submit command
  fragments, bounded resource controls, final-planning timeout, workflow-smoke
  submitted count, optional position-review stage exit code, and required
  policy-state timestamps.
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
  Scheduler `rules_path` inputs are resolved to absolute paths before command
  rendering so child commands do not inherit repo-root-relative active-rules
  paths while running from `ai_trader/trading_agent`.
- `%TEMP%\longterm_scheduler_review_bundle_printplan_20260508_124852`:
  print-plan proof of the full no-submit artifact chain. It rendered
  portfolio-news monitor, position-review queue, post-run verifier, and
  scheduler-review bundle stages together, confirmed
  `order_submission_enabled=false`, rendered an absolute active-rules path, and
  included no submit flags.
- `%TEMP%\longterm_scheduler_review_bundle_smoke_20260508_125905`: real
  one-cycle no-submit scheduler smoke with the same full chain enabled. Alpaca
  paper snapshot, portfolio-news monitor, position-review queue, pipeline,
  scheduler policy, account/dashboard refresh, post-run verifier, and
  scheduler-review bundle all exited `0`. The review bundle reported
  `status=ready_for_manual_review`, no blockers,
  `runnable_submit_command_emitted=false`, `order_submission_enabled=false`,
  `submit_profile_enabled=false`, `broker_calls_enabled=false`, and
  `llm_calls_enabled=false`.
- Stage 6B submit prechecks can now consume the scheduler-review bundle before
  any broker refresh. A supplied bundle must be fresh, `ready_for_manual_review`,
  policy-clean, position-review-clean, no-broker/no-LLM/no-submit-command, and
  free of submit-capable command fragments. The Monday paper runbook can store
  and, when explicitly revealed, pass `--scheduler-review-bundle` into the
  supervised submit command.
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
- Config validation now includes `recurring_no_submit_ready` plus an
  `operating_mode_summary`. For the safe preset, a scheduler profile is ready
  for unattended no-submit operation only when required stages are present,
  resource controls are bounded, and the submit boundary remains
  `blocked_by_no_submit_scheduler`. The summary exposes active stage flags for
  paper snapshot, portfolio-news monitor, position-review queue, research
  pipeline, scheduler policy, account/dashboard refresh, post-run verifier,
  scheduler-review bundle, generated committee batches, final planning, and
  portfolio-news follow-up committee work.
- Dashboard and handoff consumers normalize scheduler validation through
  `longterm/scheduler_config_validation.py`. Missing or legacy status-only
  validation artifacts are treated as `recurring_no_submit_ready=false`.
  `longterm_scheduler_handoff.py` now blocks Windows Task Scheduler handoff
  unless the validation artifact explicitly confirms unattended no-submit
  readiness and the no-submit broker boundary.
- `longterm_scheduler_profile.py --run-mode no-submit --validate-after-write`
  now writes validation to the profile's `scheduler_config_validation` path,
  not to `summary_output` (which belongs to the future scheduler run summary).
  Use that run-profile validation artifact for task-plan handoff.
- `longterm_scheduler_task_register.py` is the guarded bridge from a ready
  handoff to Windows Task Scheduler. It is dry-run by default and writes a
  registration review artifact. Actual registration requires both `--register`
  and `--confirm-register NO_SUBMIT_SCHEDULER_REGISTER`; even then it only
  registers the no-submit scheduler task and does not enable broker submission.
- `longterm_scheduler_launch_packet.py` validates the reviewed no-submit chain
  as one operator artifact: scheduler profile validation, task plan, handoff,
  registration review, dashboard manifest, optional Stage 6B filtered plan,
  parking intent context, sell/rebalance exclusion, market-regime snapshot,
  portfolio-news monitor, position-review queue, API/provider usage, research
  queue status, and one-cycle soak preview. It is review-only and never runs the
  scheduler, registers a task, calls an LLM, or submits orders. It now surfaces
  `provider_usage_review`, `research_queue_review`, `scheduler_soak_review`,
  and `registration_readiness`; guarded Windows registration remains optional
  and blocked whenever the launch packet has blockers.
- `longterm_scheduler_no_submit_smoke.py` packages that launch packet and
  markdown into a named smoke folder. This is the preferred pre-registration
  sanity artifact when the operator wants a one-command readiness bundle from
  already generated scheduler artifacts.
- `longterm_scheduler_soak_plan.py` writes a one-cycle no-submit soak preview
  from a reviewed run profile. It requires `preset=ongoing-no-submit`,
  scheduler `max_runs=1` (legacy `max_cycles=1` is still accepted), and no
  submit-capable keys. It prints/writes the preview command and expected
  artifacts but does not execute the scheduler. `interval_seconds` is reported
  but does not block a one-run soak because no second cycle can sleep.
- Dashboard manifests can point at `scheduler_task_registration`,
  `scheduler_launch_packet`, `scheduler_no_submit_smoke`,
  `research_queue_summary`, and `scheduler_soak_plan`; the localhost server
  exposes `/api/scheduler-task-registration.json` and
  `/api/scheduler-chain.json`, includes them in `/api/summary.json`, and renders
  read-only task-registration plus chain-readiness cards. The dashboard loader
  forces `order_submission_enabled=false` even if a saved artifact is malformed
  or unsafe.
- Stable no-submit runtime artifacts now live outside `%TEMP%` at
  `S:\LLM_files\grok_longterm_trader_runtime\no_submit_scheduler`.
  `inputs/` contains the saved action plan, candidate plan, scheduler policy,
  portfolio/operator snapshots, and latest successful scheduler summaries;
  `data/` contains copied watch journal/ledger DBs; `registration_review/`
  contains the reviewed no-submit profile, validation, task plan, handoff,
  dry-run task-registration review, soak preview, launch packet, smoke packet,
  and dashboard manifest. The latest stable launch packet is
  `ready_for_no_submit_launch_review`, has no blockers, has
  `registration_readiness.status=ready_for_guarded_no_submit_registration`,
  and keeps `order_submission_enabled=false`.

Book-principle context:
- `knowledge_agent/sources/longterm_trader/books/The Intelligent Investor Third
  Edition.pdf` has been scanned into the knowledge-agent extracted source store
  with 49 sections.
- Curated notes now live at
  `knowledge_agent/docs/the_intelligent_investor_third_edition_notes.md` and are
  included by `longterm/book_principles.py`. The long-term trader should use
  this as a margin-of-safety, Mr. Market, normalized-earnings, and permanent
  capital-loss guardrail layer on top of Lynch, Quality Investing, and
  Greenblatt/QARP context.
- `longterm/reviewers.py` now includes `MarginOfSafetyReviewer` in the
  deterministic review stack passed to the CGH committee. It is advisory and
  scores valuation support, normalized earnings/cash-flow evidence, overpayment
  risk, and permanent capital-loss risk.
- `longterm/buy_promotion.py` now records `margin_of_safety_score` and routes a
  first-pass BUY/ADD with weak margin-of-safety support to
  `WATCHLIST_PENDING_CONFIRMATION` via `margin_of_safety_review`, rather than
  turning it into an actionable BUY intent.
- `longterm/graham_risk.py` now carries the reusable Graham layer: permanent
  capital-loss flags, defensive/enterprising/speculative labels, staged-entry
  sizing hints, normalized-earnings quality labels, and Mr. Market drawdown or
  rally review triggers for existing holdings.
- `longterm/buy_promotion.py` also records permanent-loss score/flags,
  normalized-earnings quality, staged-entry label/size, and the
  defensive/enterprising mode. High permanent-loss risk routes to confirmation
  follow-up without becoming a broker blocker.
- `longterm/next_actions.py` now surfaces `mr_market_drawdown_review` and
  `mr_market_rally_review` categories for held positions with large quote moves;
  these are review prompts only and never automatic sell/trim/add instructions.
- `longterm/operator_dashboard.py` ticker pages now surface Graham discipline
  fields from promotion reviews alongside scorecards, financials, earnings, and
  article evidence.
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
  and renders a Scheduler Handoff card in Safety / Preflight.
- Dashboard manifests can point at `scheduler_task_registration`; the localhost
  server exposes `/api/scheduler-task-registration.json`, includes it in
  `/api/summary.json`, and renders the guarded registration-review artifact in
  Safety / Preflight. Missing artifacts display as unavailable and do not imply
  scheduler registration or broker authorization.
- Dashboard manifests can point at `scheduler_launch_packet` and
  `scheduler_no_submit_smoke`; the localhost server exposes
  `/api/scheduler-chain.json`, includes the chain in `/api/summary.json`, and
  renders a single Scheduler Chain timeline alongside the individual preflight
  cards.
- Dashboard manifests can point at `position_review_queue`; the localhost
  server exposes `/api/position-review-queue.json`, includes it in
  `/api/summary.json`, and renders a Position Review Queue card with advisory
  sell/rebalance/news review rows. The card is read-only and does not imply
  sell/rebalance authorization.
- Dashboard manifests can point at `paper_submit_mode_plan`; the localhost
  server exposes `/api/paper-submit-mode-plan.json`, includes it in
  `/api/summary.json`, and renders a disabled submit-profile readiness card.
  It remains a checklist only and never emits runnable submit commands.
- `dashboard_review_gates_manifest.json` is now the preferred manifest after a
  completed no-submit scheduler run when `longterm_scheduler_review_bundle.py`
  has been run. It preserves existing dashboard inputs and adds/replaces the
  latest scheduler handoff, pipeline scheduler summary, position review queue,
  and generated paper submit-mode plan paths for live localhost review.
- Read-only paper-account refresh and the `ongoing-no-submit` scheduler preset
  can pass scheduler review artifacts, position-review queues, and optional
  paper-submit readiness plans through to refreshed dashboard manifests/sites,
  so reviewed local profiles, handoff evidence, and review gates remain visible
  after recurring account/dashboard refreshes.
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
- Use `paper_submit_mode_plan` as a read-only gate checklist before any future
  submit-capable scheduler profile is drafted. It is not itself authorization
  and intentionally does not print or save broker-submit commands.
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
- Actionable BUY rows can be resized to the Graham staged-entry starter
  percentage in dry-run account plans when margin-of-safety support is
  moderate. Missing margin detail alone does not shrink otherwise clean legacy
  BUY rows.

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
