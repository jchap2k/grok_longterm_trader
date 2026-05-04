# Long-Term Research Selection Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an autonomous-ready, deterministic research-selection stage that turns evidence-enriched broad-universe survivors into a manageable committee research queue.

**Architecture:** Keep selection pure and dry-run: score enriched idea dictionaries, attach stable selection metadata, write selected/deferred artifacts, and let the automation campaign advance to `research_queue_ready`. The selector is portfolio-aware enough to avoid protected symbols and deprioritize current/recent names, but it does not mutate journals or submit broker orders.

**Tech Stack:** Python 3, existing longterm CLI patterns, JSON/JSONL artifacts, pytest.

---

### Task 1: Pure Research Selector

**Files:**
- Create: `ai_trader/trading_agent/longterm/research_selection.py`
- Test: `ai_trader/trading_agent/longterm/test_research_selection.py`

- [ ] **Step 1: Write failing selector tests**

Add tests proving strong evidence ranks ahead of thin/warning evidence, `FXAIX` is hard-skipped, and repeated runs are deterministic.

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest longterm/test_research_selection.py -q`

Expected: fail because `longterm.research_selection` does not exist.

- [ ] **Step 3: Implement selector**

Implement `select_research_queue(...)`, canonical SHA-256 hashing, rule-aligned scoring, selection metadata, source-note traceability, and selected/deferred summaries.

- [ ] **Step 4: Verify selector tests pass**

Run: `python -m pytest longterm/test_research_selection.py -q`

Expected: pass.

### Task 2: Selection CLI

**Files:**
- Create: `ai_trader/trading_agent/longterm/research_selection_cli.py`
- Create: `ai_trader/trading_agent/scripts/longterm_research_selection.py`
- Test: `ai_trader/trading_agent/longterm/test_research_selection.py`

- [ ] **Step 1: Write failing CLI test**

Add a test that runs the CLI on sample evidence JSON and verifies selected/deferred JSON, JSONL, summary, and markdown report outputs.

- [ ] **Step 2: Run targeted test**

Run: `python -m pytest longterm/test_research_selection.py -q`

Expected: fail because the CLI/script does not exist.

- [ ] **Step 3: Implement CLI and script wrapper**

Follow existing CLI patterns and write output artifacts under the chosen output directory.

- [ ] **Step 4: Verify targeted tests pass**

Run: `python -m pytest longterm/test_research_selection.py -q`

Expected: pass.

### Task 3: Automation Campaign Integration

**Files:**
- Modify: `ai_trader/trading_agent/longterm/research_automation_campaign_cli.py`
- Modify: `ai_trader/trading_agent/longterm/test_research_automation_campaign.py`

- [ ] **Step 1: Write failing automation tests**

Add tests for forwarding `--campaign-batch-pause-seconds` into evidence summaries and reaching `research_queue_ready` with selected queue artifacts.

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest longterm/test_research_automation_campaign.py -q`

Expected: fail because automation does not expose the new run stage or pause forwarding.

- [ ] **Step 3: Implement automation changes**

Extend `--run-until`, add selection options, call the selector after evidence readiness, update campaign state and events.

- [ ] **Step 4: Verify targeted tests pass**

Run: `python -m pytest longterm/test_research_automation_campaign.py -q`

Expected: pass.

### Task 4: Docs and Live Artifact Smoke

**Files:**
- Modify: `docs/system/OPERATIONS.md`

- [ ] **Step 1: Update operations docs**

Document the `research_queue_ready` automation stage and standalone selection CLI.

- [ ] **Step 2: Run selector on repaired 305-name artifact**

Run standalone selection against:
`C:\Users\johnd\AppData\Local\Temp\longterm_warning_retry_skipgrok_20260503_200151\campaign_enriched_merged_with_retry.json`

Expected: selected queue, deferred queue, summary, and markdown report.

- [ ] **Step 3: Run full validation**

Run: `python -m pytest longterm -q`

Expected: pass.
