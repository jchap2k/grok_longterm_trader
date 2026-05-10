# Long-Term Trader Strategy Brief

This brief is compact context for CGH planning and decision experiments. It is
not a trading authorization document.

## Operating Boundary

- The system is research-first and dry-run/no-submit unless explicitly changed.
- Current recurring scheduler posture is `order_submission_enabled=false`.
- The no-submit scheduler may refresh market context, FRED regime snapshots,
  Motley Fool recommendation deltas, portfolio/account snapshots, and research
  artifacts, but it must not submit broker orders.
- Paper or live submission requires a separate supervised enablement step and
  explicit operator approval.

## Portfolio Discipline

- The protected holdings rule: protected holdings are never sold, trimmed, or
  used as funding sources unless
  the operator explicitly changes protected-symbol policy.
- FXAIX is the primary benchmark discipline reference for long-term active
  sleeve decisions.
- Active sleeve recommendations must consider portfolio fit, cash availability,
  existing exposure, rank gap, thesis state, and whether the candidate is likely
  to justify active risk versus FXAIX.
- Capital requests should occur only after checking whether existing
  non-protected active holdings should be reduced first.

## Research Inputs

- FRED macro data is context, not an automatic veto. Use it to label regime
  pressure, review cadence, rates/inflation backdrop, and thesis risk.
- Motley Fool data is a paid-source idea and enrichment input. New
  recommendations should be detected by tracking latest and previous
  recommendation dates, then routed into bounded enrichment and committee
  review.
- S&P 500 / SPY constituents are broad-universe baseline inputs. Motley Fool
  and S&P/SPY names should receive deterministic pre-LLM enrichment, but later
  scoring gates still decide whether they receive paid LLM review.

## Decision Output Expectations

- Prefer explicit recommendation, confidence, suggested size, key thesis,
  invalidation conditions, monitoring triggers, benchmark consideration, and an
  executive_summary.
- Summaries should be concise enough for a human operator to understand the
  decision in under 10 seconds.
- Quiet fallback is not success. If FRED, Motley Fool capture, scheduler state,
  or enrichment fails and falls back, report it as a failure or degraded run.

## Safety Rules

- Never enable live trading or broker submission as a side effect of research,
  dashboard, scheduler, or CGH work.
- Never weaken broker, protected-symbol, or dry-run guards to make tests pass.
- Treat scheduler artifacts and decision journals as audit records. Prefer
  explicit artifact paths, timestamps, exit codes, and no-submit state.
