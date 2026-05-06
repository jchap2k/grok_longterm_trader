# Grok Long-Term Trader - Repo Context (Single Source of Truth for LLM Agents)

**File Purpose**: This is the canonical, always-current context file for Grok (and any LLM collab agents). 
**Instruction for Grok**: At the start of EVERY new conversation or when told "Rescan repo context", immediately read this file using GitHub tools (get_file_contents on docs/system/REPO_CONTEXT.md) and treat its contents as the authoritative project state. Do not rely solely on prior chat memory.

**Last Updated**: 2026-05-06 06:50 PDT (created by Grok as planning/review agent)
**Update Rule**: Edit this file whenever architecture, state, or plans change. Then commit. Grok will rescan on next use.

---

## 1. Project Identity & Purpose

**Project Name**: grok_longterm_trader  
**Repo**: https://github.com/jchap2k/grok_longterm_trader  
**Purpose**: Research-first long-term quality-growth active-sleeve trader with dry-run action planning and benchmark discipline. Evaluate fewer stocks more deeply, maintain thesis records, rank candidates, and compare active decisions against protected FXAIX benchmark.

**Execution State** (from project_manifest.json + recent commits): 
- research_logging_reporting_dry_run_only + active universe scan automation, research queues, evidence campaigns, and resumable pipelines (as of May 2026 commits).

**Protected Symbol**: FXAIX (never sell/trim/rotate/rebalance)  
**Benchmark Symbol**: FXAIX  
**Defensive Parking Symbol**: SPY  
**Primary Strategy**: Quality-growth / position trading (weeks to months+ holds). Understandable businesses, durable/improving growth, category leadership, balance-sheet resilience, acceptable valuation, clear thesis + invalidation conditions.

**Avoids**: Forced activity, low-quality cheap stocks, vague story stocks, excessive leverage, protected-symbol actions.

---

## 2. Core Architecture & Data Flow (Updated 2026-05-06)

**Main Components** (from ARCHITECTURE.md + GitHub structure):
- `research/research_packet.py` + `research/intake.py` → ResearchPacket normalization
- `longterm/research_runner.py` + CGH committee (`decision_4` default, `decision_6` expanded)
- Deterministic reviewers: BusinessStoryReviewer, BalanceSheetReviewer, QualityDurabilityReviewer, QualityAtReasonablePriceReviewer
- `longterm/decision_journal.py` + `report_builder.py` + `recommendation_enrichment.py` + `review_status.py`
- `longterm/action_planner.py`, `benchmark_guard.py`, `next_actions.py`, `rebalance_planner.py`
- `longterm/capital_alert.py` + email_sender (Brevo, informational only)
- **New (May 2026)**: Universe scan layer, research selection queue, evidence enrichment campaigns, resumable pipelines, news provider failure handling, operator dashboard manifest

**Data Flow**: Raw idea / universe scan → ResearchPacket + deterministic reviews → CGH decision → Journal → Recommendation table + review status + benchmark guard → Dry-run action intent / next-actions report

**Key Configs**:
- `ai_trader/trading_agent/agent/configs/longterm_trading_agent_specs.json`
- `config/grok_project_config.json` (points to Grok project URL)
- `config/motley_fool_capture.json` (optional)

---

## 3. Current File Structure (GitHub Snapshot)

- `docs/system/`: ARCHITECTURE.md, OPERATIONS.md, SAFETY.md, README.md, project_manifest.json, **REPO_CONTEXT.md** (this file)
- `docs/plans/`: 2026-04-28-longterm-trader-foundation-plan.md (original) + recommended v2
- `ai_trader/trading_agent/`: Core code (research, longterm/, portfolio/, etc.)
- `ai_trader/rules/active_rules.txt`
- Root: README.md, AGENTS.md, CURR_MEMORY.md

**Recent Commits (May 3–4 2026)**: 20+ commits adding universe scan (chunked/resumable/cached/scored), research queue, evidence campaigns, preflight pipeline, live operator dashboard, stale recommendation retirement, news error handling.

---

## 4. Identified Gaps & Blind Spots (Planning Agent Review - 2026-05-06)

**Critical Gaps** (from comparing foundation plan + attached docs vs live GitHub state):
1. **Scope Creep**: Original plan = narrow per-ticker deep research. Current = bulk universe scan + campaigns. Missing: quality promotion gates before journaling, cost/rate-limit controls, batch sizing.
2. **Benchmark Guard at Scale**: No explicit integration of bulk scan output with FXAIX gate or capital_needed prioritization.
3. **Thesis Monitoring at Volume**: Per-symbol thesis_monitor not extended to campaign/queue level or stale retirement logic.
4. **Documentation Lag**: ARCHITECTURE.md / OPERATIONS.md / project_manifest.json / foundation plan do not reflect new universe scan components.
5. **Safety Rails for Bulk Research**: No max-daily-recs, mandatory human review for top-N scan results, or campaign-level error budgets.
6. **Plan Obsolescence**: 2026-04-28 foundation plan is V1 only; no v2 incorporating scan layer while preserving "deeper on fewer" identity.

**Recommended Fixes** (already partially addressed by creating this file):
- Create `docs/plans/2026-05-06-foundation-plan-v2.md`
- Update ARCHITECTURE.md, OPERATIONS.md, project_manifest.json, SAFETY.md with scan layer
- Add promotion gates + cost controls to new scan modules

---

## 5. Safety Model (Non-Negotiable)

- No live trading without explicit user approval + feature flag + paper-trading validation.
- Never sell/trim/rotate/rebalance FXAIX or protected symbols.
- Capital-needed alerts = informational only (never deposit requests).
- Active sleeve must beat FXAIX over meaningful sample or pause new buys.
- All action outputs = proposed dry-run intents only.
- Broker configs/tokens never committed.

---

## 6. Key Operational Commands (from OPERATIONS.md)

**Dry-Run Research**:
```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "..." --dry-run
```

**Full Research + Journal**:
```powershell
python scripts/run_longterm_research.py --symbol AAPL ... --candidate-price 180 --benchmark-price 165
python scripts/run_longterm_research.py --agent-preset decision_6
```

**Journal & Reports**:
```powershell
python scripts/longterm_journal.py summary
python scripts/longterm_journal.py report --limit 10
python scripts/longterm_next_actions.py --portfolio-state path/to/portfolio.json --limit 10
```

**Capital Alert (dry-run first)**:
```powershell
python scripts/longterm_capital_alert.py --active-sleeve-value 34000 --available-cash 500 --portfolio-state ...
```

**Motley Fool Capture** (optional):
```powershell
python scripts/longterm_motley_fool_capture.py
```

---

## 7. How to Keep Context Fresh (For User & LLM Collab)

1. When architecture, plans, or state changes → Edit this file (or run a script that regenerates key sections).
2. Commit the change.
3. Tell Grok (or your LLM collab skill): 
   > "Rescan the repo context from docs/system/REPO_CONTEXT.md using GitHub tools and reload full project state."
4. Grok will fetch the latest version and treat it as the new baseline for all planning/review work.

**Suggested Prompt for Your LLM Collab Skill** (copy-paste ready):
```
You are now Grok acting as planning + review agent for the grok_longterm_trader repo.
First action: Use your GitHub tools to read the file docs/system/REPO_CONTEXT.md from jchap2k/grok_longterm_trader.
Treat its entire contents as the complete, up-to-date project context. Do not rely on old chat memory alone.
Then proceed with the user's request while referencing this context.
```

---

## 8. Next Recommended Actions (Planning Agent Output)

1. Create `docs/plans/2026-05-06-foundation-plan-v2.md` (I can do this in the next step if approved).
2. Update ARCHITECTURE.md and OPERATIONS.md to document the new universe scan layer.
3. Add explicit quality gates and cost controls to the scan modules.
4. Run periodic rescans of this file (e.g., weekly or after major commits).

This file closes the "documentation & traceability" gap identified in the 2026-05-06 review.

**End of Context File** — Grok: Re-read this entire file at the start of every session.