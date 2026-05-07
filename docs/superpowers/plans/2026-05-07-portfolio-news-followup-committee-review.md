# Portfolio News Follow-up Committee Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, capped scheduler-safe path that runs portfolio-news follow-up batch files through the long-term committee without submitting orders or silently refreshing account actions.

**Architecture:** The research-to-paper pipeline gets a distinct follow-up committee runner stage that reuses the existing no-submit `longterm_committee_batch_runner.py` but writes separate artifacts and rollups. The scheduler preset forwards explicit cap/resume controls, records bounded resource metadata, and updates policy timestamps only when the follow-up committee stage succeeds without failures.

**Tech Stack:** Python 3, argparse CLIs, JSON artifacts, pytest, existing long-term pipeline scheduler modules.

---

### Task 1: Pipeline Stage And CLI

**Files:**
- Modify: `ai_trader/trading_agent/longterm/research_to_paper_pipeline.py`
- Modify: `ai_trader/trading_agent/longterm/research_to_paper_pipeline_cli.py`
- Test: `ai_trader/trading_agent/longterm/test_research_to_paper_pipeline.py`

- [x] **Step 1: Add failing tests for a capped portfolio-news follow-up committee runner stage.**
- [x] **Step 2: Implement `build_portfolio_news_followup_committee_batch_runner_stage`.**
- [x] **Step 3: Wire pipeline CLI flags and ordering after follow-up batch split and before preflight.**
- [x] **Step 4: Add artifact rollup fields for follow-up committee batch progress.**

### Task 2: Scheduler Resource Controls And Policy State

**Files:**
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler.py`
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_cli.py`
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_policy.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler_policy.py`

- [x] **Step 1: Add failing tests for scheduler preset validation and command forwarding.**
- [x] **Step 2: Require explicit `--portfolio-news-followup-max-batches` when committee follow-up review is enabled.**
- [x] **Step 3: Include follow-up committee cap fields in scheduler resource controls.**
- [x] **Step 4: Mark `last_followup_committee_at` only for successful bounded follow-up committee review runs.**

### Task 3: Verification, Docs, And Handoff

**Files:**
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_verification.py`
- Modify: `docs/system/ARCHITECTURE.md`
- Modify: `docs/system/OPERATIONS.md`
- Modify: `docs/system/REPO_CONTEXT.md`
- Modify: `../codex_compatible/memory/RECENT_CHANGES.md`
- Modify: `../codex_compatible/memory/TODO.md`
- Modify: `../codex_compatible/memory/DECISIONS.md`

- [x] **Step 1: Ensure verifier surfaces `last_followup_committee_at` when required.**
- [x] **Step 2: Document the no-submit safety boundary and separate final-planning gate.**
- [x] **Step 3: Update repo context and shared memory without appending a bottomless log.**
- [ ] **Step 4: Run focused and full longterm tests, then commit and push.**
