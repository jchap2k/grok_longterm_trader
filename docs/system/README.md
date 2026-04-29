# Long-Term Trader System Overview

## Purpose

`grok_longterm_trader` is a research-first long-term trading system for a quality-growth active sleeve. It is designed to evaluate fewer stocks more deeply, maintain thesis records, rank liked candidates, and compare active decisions against the protected `FXAIX` benchmark.

The system is not live-trading enabled. Current behavior is research, journaling, dry-run action planning, next-actions reporting, and safety validation.

## Strategy Identity

The trader prefers:

- understandable businesses
- durable or improving growth
- category or industry leadership
- balance-sheet resilience
- acceptable valuation relative to quality
- thesis clarity with explicit invalidation conditions

The trader avoids:

- forced activity
- low-quality cheap stocks
- vague story stocks
- excessive leverage
- protected-symbol actions

## Core Flow

1. Ingest an idea or batch of ideas.
2. Normalize each idea into a `ResearchPacket`.
3. Add deterministic reviewer context and book-derived principles.
4. Run the configured long-term CGH decision committee.
5. Record the structured decision in the journal.
6. Build the recommendation table from recent decisions.
7. Use dry-run planners to propose next actions.
8. Track outcomes versus `FXAIX`.

## Current Boundaries

- No broker orders are placed.
- No protected holding can be sold, trimmed, rotated, or rebalanced.
- `FXAIX` is the protected benchmark/core holding.
- `SPY` is the reversible defensive parking symbol.
- Cash mode is allowed only for truly hostile active-sleeve conditions.
- Capital-needed alerts are informational only.

## Future LLM-Collab Context

Use this folder as the starting context for future LLM-collab or project-link setup. The project manifest lists the files that explain the strategy, architecture, safety model, and operational commands.
