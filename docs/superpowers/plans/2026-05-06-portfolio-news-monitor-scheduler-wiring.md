# Portfolio News Monitor Scheduler Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the deterministic portfolio news monitor into the research-to-paper pipeline and recurring scheduler without enabling LLM calls or order submission.

**Architecture:** The pipeline becomes the consumer of the monitor artifact through a non-executing ingestion stage and rollup summary. The scheduler runs the monitor before the pipeline, passes the rendered artifact path into the pipeline, records audit stdout/stderr, and timestamps successful monitor completion in scheduler policy state.

**Tech Stack:** Python dataclasses, pytest, existing long-term pipeline/scheduler CLIs.

---

### Task 1: Pipeline Ingestion

**Files:**
- Modify: `ai_trader/trading_agent/longterm/research_to_paper_pipeline.py`
- Modify: `ai_trader/trading_agent/longterm/research_to_paper_pipeline_cli.py`
- Test: `ai_trader/trading_agent/longterm/test_research_to_paper_pipeline.py`

- [ ] Write failing tests for a valid `--portfolio-news-monitor` report and a malformed report.
- [ ] Add a planned stage builder that validates the monitor JSON via a Python one-liner and exposes `artifact_paths["portfolio_news_monitor"]`.
- [ ] Extend artifact rollup with queue counts, symbols, high-impact/review-trigger counts, and top triggers.
- [ ] Add CLI argument `--portfolio-news-monitor` and insert the stage before paper preflight.
- [ ] Run focused pipeline tests.

### Task 2: Scheduler Execution

**Files:**
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler.py`

- [ ] Write failing tests for monitor template validation, monitor execution before pipeline, monitor failure blocking, and timestamp persistence when pipeline later fails.
- [ ] Add `portfolio_news_monitor_command_template` input and run-record fields.
- [ ] Add `{portfolio_news_monitor}` placeholder.
- [ ] Execute monitor after pre-refresh and before pipeline.
- [ ] Update `last_news_monitor_at` when monitor exits 0 even if a later pipeline command fails.
- [ ] Run focused scheduler tests.

### Task 3: Scheduler CLI Preset

**Files:**
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_cli.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler.py`

- [ ] Write failing tests for manual monitor template acceptance and `ongoing-no-submit` preset rendering.
- [ ] Add `--portfolio-news-monitor-command-template`.
- [ ] Add preset flags for snapshot/watchlist/published-after/relevance/max articles.
- [ ] Require snapshot when preset monitor is enabled.
- [ ] Add monitor command, pipeline `--portfolio-news-monitor`, and verifier `last_news_monitor_at` requirement.
- [ ] Run focused CLI scheduler tests.

### Task 4: Docs And Verification

**Files:**
- Modify: `docs/system/OPERATIONS.md`
- Modify: `docs/system/REPO_CONTEXT.md`
- Modify: `S:/LLM_files/codex_compatible/memory/RECENT_CHANGES.md`
- Modify: `S:/LLM_files/codex_compatible/memory/TODO.md`

- [ ] Document the monitor-in-scheduler flow and safety boundary.
- [ ] Update repo context concisely, optimizing rather than appending a dump.
- [ ] Run focused tests and then `python -m pytest ai_trader/trading_agent/longterm -q`.
- [ ] Commit and push.
