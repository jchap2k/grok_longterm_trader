# Scheduler End-To-End Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and verify a no-submit recurring scheduler handoff from real artifacts, surface it on the dashboard, and harden sell/rebalance simulation visibility before any Windows task registration.

**Architecture:** Use existing scheduler JSON profile, validation, task-plan, dashboard manifest, and handoff CLIs as the artifact chain. Add only small TDD seams where artifacts cannot yet be displayed or verified. Preserve the existing Stage 6B boundary: simple BUY paper submission only, no scheduler broker submission.

**Tech Stack:** Python CLI modules under `ai_trader/trading_agent/longterm`, pytest, JSON artifacts, static/localhost dashboard, Windows Task Scheduler command artifacts.

---

### Task 1: Real Scheduler Handoff Packet

**Files:**
- Read/run only: `ai_trader/trading_agent/scripts/longterm_scheduler_profile.py`
- Read/run only: `ai_trader/trading_agent/scripts/longterm_scheduler_task_plan.py`
- Read/run only: `ai_trader/trading_agent/scripts/longterm_scheduler_handoff.py`

- [x] Find the latest usable action plan, journal, ledger, dashboard manifest, and profile artifact under the current temp artifact roots.
- [x] Render a validation profile with `longterm_scheduler_profile.py --validate-after-write --json`.
- [x] Render a no-submit run profile with `--run-mode no-submit`.
- [x] Generate a task-plan artifact.
- [x] Generate a handoff-check artifact and require `status=ready`.

### Task 2: Short No-Submit Scheduler Execution

**Files:**
- Read/run only: `ai_trader/trading_agent/scripts/longterm_pipeline_scheduler.py`

- [x] Run the generated no-submit profile for one short cycle only.
- [x] Verify scheduler summary has `order_submission_enabled=false`, no submit flags, and completed/planned artifacts.
- [x] Do not register Windows tasks or submit orders.

### Task 3: Dashboard Scheduler Handoff Seam

**Files:**
- Modify: `ai_trader/trading_agent/longterm/operator_dashboard_server.py`
- Modify: `ai_trader/trading_agent/longterm/operator_dashboard_server_cli.py`
- Modify: `ai_trader/trading_agent/longterm/operator_dashboard.py`
- Modify: `ai_trader/trading_agent/longterm/paper_account_refresh.py`
- Modify: `ai_trader/trading_agent/longterm/paper_account_refresh_cli.py`
- Test: `ai_trader/trading_agent/longterm/test_operator_dashboard_server.py`
- Test: `ai_trader/trading_agent/longterm/test_longterm_paper_account_refresh.py`

- [x] Write failing tests for `scheduler_handoff` in manifest, `/api/scheduler-handoff.json`, summary JSON, static dashboard, and account refresh pass-through.
- [x] Implement the minimal loader/panel/pass-through.
- [x] Run focused dashboard/refresh tests.

### Task 4: Portfolio-News Monitor TODO Reconciliation

**Files:**
- Modify if needed: `codex_compatible/memory/TODO.md`
- Modify if needed: `docs/system/REPO_CONTEXT.md`

- [x] Inspect scheduler preset/policy-state support for `portfolio_news_monitor`.
- [x] If support exists, mark the stale TODO complete with evidence.
- [x] If a gap exists, add only the missing artifact/check without enabling default LLM or broker work.

### Task 5: Bounded Scheduled-Research No-Submit Smoke

**Files:**
- Read/run only: `ai_trader/trading_agent/scripts/longterm_pipeline_scheduler.py`

- [x] Prefer a print-plan/validate smoke if live paid-resource inputs are not clean.
- [x] If executing, require Perplexity/research caps and generated committee max-batch caps.
- [x] Verify no submit flags and bounded resource controls.

### Task 6: Sell/Rebalance Simulation Hardening

**Files:**
- Inspect/modify if needed: `ai_trader/trading_agent/longterm/paper_execution_boundary.py`
- Inspect/modify if needed: `ai_trader/trading_agent/longterm/action_plan_filter.py`
- Inspect/modify if needed: `ai_trader/trading_agent/longterm/operator_dashboard.py`

- [x] Add failing tests if sell/reduce/rebalance intents are not visibly excluded from V1 paper submission.
- [x] Ensure simulation/review intents are surfaced but cannot become Stage 6B paper orders.
- [x] Run paper boundary and dashboard focused tests.

### Task 7: Windows Task Scheduler Registration Boundary

**Files:**
- Read/run only: generated `scheduler_task_plan.json`

- [x] If all prior handoff checks are ready, leave a reviewed registration command artifact.
- [x] Do not register a recurring Windows task unless the handoff packet is ready and the no-submit profile has been proven by a short execution.
- [x] If registration is deferred, document exact blocker/next action.

### Completion Evidence

- Ready handoff and one-cycle no-submit scheduler run:
  `%TEMP%\longterm_scheduler_handoff_20260508_062101`
- Bounded scheduled-research print-plan only:
  `%TEMP%\longterm_scheduled_research_printplan_20260508_062300`
- Handoff status: `ready`
- Explicit handoff checks: `scheduler_config_validation=ready`, `scheduler_task_plan=ready`,
  `dashboard_manifest=ready`, `order_submission_boundary=ready`
- Scheduler execution: `1` run, `1` success, `0` errors,
  `order_submission_enabled=false`
- Windows task registration: deferred by design; artifact only, no task registered
- Broker submission: disabled throughout; no paper/live orders submitted
- Verification: `python -m pytest ai_trader\trading_agent\longterm -q` ->
  `749 passed`, `1` existing `websockets.legacy` deprecation warning

### Model Optimization

**Overall Recommendation:** Use the current model for implementation because scheduler safety and multi-file integration need high-context reasoning.

**Per-Step Breakdown**
- Task 1, 2, 5, 7 -> fast execution/readback; suitable for a smaller model, but current model is fine.
- Task 3 and 6 -> core integration/safety logic; use strongest available model.
- Task 4 -> documentation/TODO reconciliation; smaller model would be acceptable.
