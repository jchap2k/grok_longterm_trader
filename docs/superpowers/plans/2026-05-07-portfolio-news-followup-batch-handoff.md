# Portfolio News Follow-Up Batch Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the no-submit pipeline split packet-validated portfolio-news follow-up ideas into bounded committee batch files without running LLMs, changing account actions, or submitting orders.

**Architecture:** Reuse the existing `longterm_research_universe.py` batch splitter against `portfolio_news_followup_ideas.json`. The portfolio-news monitor still produces the source report, the ingest stage still validates and writes follow-up ideas, and the new optional stage only creates normal research batch files plus rollup metadata for scheduler/dashboard visibility.

**Tech Stack:** Python CLI modules, pytest, existing long-term pipeline/scheduler artifacts, JSON batch files.

---

### Task 1: Add Pipeline Batch Split Stage

**Files:**
- Modify: `ai_trader/trading_agent/longterm/research_to_paper_pipeline.py`
- Test: `ai_trader/trading_agent/longterm/test_research_to_paper_pipeline.py`

- [x] Add a failing test that builds a `portfolio_news_followup_batch_split` stage from a tiny `portfolio_news_followup_ideas.json` file, runs it, and asserts the batch split summary plus `portfolio_news_followup_batch_dir` artifact exist.
- [x] Implement `build_portfolio_news_followup_batch_split_stage(output_dir, followup_ideas=None, batch_size=3)` with positive batch-size validation, command validation, and artifact paths.
- [x] Extend artifact rollup to report `followup_batch_count`, `followup_batch_total_ideas`, and `followup_batch_dir` under `artifact_rollup.portfolio_news_monitor`.
- [x] Run the focused pipeline test.

### Task 2: Add Pipeline CLI Flags

**Files:**
- Modify: `ai_trader/trading_agent/longterm/research_to_paper_pipeline_cli.py`
- Test: `ai_trader/trading_agent/longterm/test_research_to_paper_pipeline.py`

- [x] Add a failing print-plan test for `--portfolio-news-monitor --portfolio-news-followup-batches --portfolio-news-followup-batch-size 2`.
- [x] Add `--portfolio-news-followup-batches`, `--portfolio-news-followup-batch-size`, and `--portfolio-news-followup-ideas` flags.
- [x] Require either `--portfolio-news-monitor` or explicit `--portfolio-news-followup-ideas` when follow-up batching is enabled.
- [x] Append ingest before split when monitor input is supplied.
- [x] Run focused pipeline CLI tests.

### Task 3: Add Scheduler Preset Flags And Verification Timestamp

**Files:**
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_cli.py`
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_policy.py`
- Modify: `ai_trader/trading_agent/longterm/pipeline_scheduler_verification.py`
- Test: `ai_trader/trading_agent/longterm/test_pipeline_scheduler.py`

- [x] Add failing scheduler print-plan test proving the safe preset forwards follow-up batch flags into the pipeline template.
- [x] Add failing validation test proving follow-up batching requires `--portfolio-news-monitor`.
- [x] Add failing policy-state/verifier test proving successful `portfolio_news_followup_batch_split` records and verifies `last_followup_batch_split_at`.
- [x] Add scheduler CLI flags and validation.
- [x] Add policy-state timestamp detection from the pipeline summary stage.
- [x] Include `last_followup_batch_split_at` in verifier timestamp rollup and preset verifier requirements when enabled.
- [x] Run focused scheduler tests.

### Task 4: Docs, Context, And Full Verification

**Files:**
- Modify: `docs/system/OPERATIONS.md`
- Modify: `docs/system/ARCHITECTURE.md`
- Modify: `docs/system/REPO_CONTEXT.md`
- Modify: `codex_compatible/memory/RECENT_CHANGES.md`
- Modify: `codex_compatible/memory/TODO.md`
- Modify if needed: `codex_compatible/memory/DECISIONS.md`

- [x] Document that follow-up batch split is artifact-only and does not run committee/LLM/order paths.
- [x] Update Grok repo context with the new safe handoff.
- [x] Run `python -m pytest ai_trader/trading_agent/longterm -q`.
- [x] Run `git diff --check`.
- [ ] Commit and push.
