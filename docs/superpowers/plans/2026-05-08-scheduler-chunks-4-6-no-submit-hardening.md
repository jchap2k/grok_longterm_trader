# Scheduler Chunks 4-6 No-Submit Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic sell/rebalance/news-trigger review visibility, wire it into the no-submit scheduler cadence, and prepare a disabled-by-default paper submit-mode readiness artifact without enabling broker submission.

**Architecture:** Build a new advisory `position_review_queue` artifact that reuses existing portfolio, action-plan, portfolio-news, and review-status facts. The scheduler runs it as a no-submit stage after portfolio news monitoring and before the pipeline. A separate submit-mode plan checker validates readiness gates but never emits a runnable submit command by default.

**Tech Stack:** Python, pytest, JSON artifacts, existing long-term scheduler and paper execution safety modules.

---

### Task 1: Position Review Queue

**Files:**
- Create: `ai_trader/trading_agent/longterm/position_review_queue.py`
- Create: `ai_trader/trading_agent/longterm/position_review_queue_cli.py`
- Create: `ai_trader/trading_agent/scripts/longterm_position_review_queue.py`
- Test: `ai_trader/trading_agent/longterm/test_position_review_queue.py`

- [x] Write failing tests for explicit SELL/REDUCE/REBALANCE action-plan rows becoming advisory review rows.
- [x] Write failing tests for high-impact portfolio-news rows becoming thesis/news review rows.
- [x] Write failing tests that protected symbols such as FXAIX are excluded even when present in news or action-plan inputs.
- [x] Implement builder with `order_submission_enabled=false`, `llm_calls_enabled=false`, and `broker_calls_enabled=false`.
- [x] Include symbol, review_type, trigger_source, severity, actionability, decision_id, latest_recommendation, thesis_state, review_due, portfolio weight/value fields when available.
- [x] Add CLI JSON output and optional file output.

### Task 2: Scheduler Wiring

**Files:**
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler.py`
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_cli.py`
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_verification.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler_verification.py`

- [x] Add `position_review_queue_command_template` to scheduler inputs and run records.
- [x] Run the stage after `portfolio_news_monitor` and before the pipeline.
- [x] Add placeholder `{position_review_queue}` and run-folder artifact path `position_review_queue.json`.
- [x] Update policy-state after successful stage with `last_position_review_at`.
- [x] Add `--position-review-queue` to the safe `ongoing-no-submit` preset; require `--portfolio-news-monitor` when enabled.
- [x] Add verifier support for `last_position_review_at` when the stage is configured.

### Task 3: Disabled Submit-Mode Readiness Plan

**Files:**
- Create: `ai_trader/trading_agent/longterm/paper_submit_mode_plan.py`
- Create: `ai_trader/trading_agent/longterm/paper_submit_mode_plan_cli.py`
- Create: `ai_trader/trading_agent/scripts/longterm_paper_submit_mode_plan.py`
- Test: `ai_trader/trading_agent/longterm/test_paper_submit_mode_plan.py`

- [x] Write failing tests that missing/stale handoff blocks readiness.
- [x] Write failing tests that submit flags in handoff, scheduler summary, or position-review queue block readiness.
- [x] Implement read-only readiness artifact with `order_submission_enabled=false` and `submit_profile_enabled=false`.
- [x] Require ready handoff, successful no-submit scheduler summary, and completed position-review queue before reporting `ready_for_manual_review`.
- [x] Do not emit runnable submit commands; emit only gate status and next safe action.

### Task 4: Docs and Verification

**Files:**
- Modify: `docs/system/REPO_CONTEXT.md`
- Modify: `codex_compatible/memory/RECENT_CHANGES.md` after commit

- [x] Update repo context with the new no-submit review queue and submit-mode plan boundary.
- [x] Run focused tests for new modules and scheduler/verifier wiring.
- [x] Run full `python -m pytest ai_trader\trading_agent\longterm -q`.
- [ ] Commit and push.
