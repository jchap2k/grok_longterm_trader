# Research-To-Paper Pipeline Command Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scheduler-ready dry-run wrapper that composes existing long-term research/planning/paper-preflight scripts into one auditable command chain.

**Architecture:** The wrapper is a command planner and safe executor, not a new research brain. It validates required artifacts, builds ordered stage commands, blocks any submit command, logs every stage, and writes `pipeline_summary.json` with `order_submission_enabled=false`.

**Tech Stack:** Python standard library, argparse, dataclasses, subprocess, pytest.

---

### Task 1: Core Planner And Safety Tests

**Files:**
- Create: `ai_trader/trading_agent/longterm/research_to_paper_pipeline.py`
- Test: `ai_trader/trading_agent/longterm/test_research_to_paper_pipeline.py`

- [ ] Write failing tests for command validation, print-plan-only, failure stop, and paper-preflight stage order.
- [ ] Implement `PipelineStage`, `PipelineStageResult`, `PipelineRunResult`, `build_paper_preflight_stages`, `run_pipeline_stages`, and `write_pipeline_summary`.
- [ ] Verify targeted tests pass.

### Task 2: CLI And Script Wrapper

**Files:**
- Create: `ai_trader/trading_agent/longterm/research_to_paper_pipeline_cli.py`
- Create: `ai_trader/trading_agent/scripts/longterm_research_to_paper_pipeline.py`
- Test: `ai_trader/trading_agent/longterm/test_research_to_paper_pipeline.py`

- [ ] Add parser for required artifact paths, `--print-plan-only`, `--skip-price-map`, and `--json`.
- [ ] Add CLI test that writes `pipeline_summary.json` with submission disabled.
- [ ] Verify targeted tests pass.

### Task 3: Docs And Verification

**Files:**
- Modify: `docs/system/OPERATIONS.md`

- [ ] Document the new wrapper and explicitly state it does not submit orders.
- [ ] Run `py_compile`, targeted tests, and `python -m pytest longterm -q`.
- [ ] Commit and push.
