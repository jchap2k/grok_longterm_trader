# Long-Term Trader Safety Model

## Current Execution State

The project is not live-trading enabled. The current system can research, log,
report, produce dry-run action intents, and submit explicitly approved simple
BUY orders to Alpaca paper through the supervised Stage 6B boundary only.

## Protected Holdings

`FXAIX` is the protected benchmark/core holding. The system must not recommend or execute:

- selling `FXAIX`
- trimming `FXAIX`
- rotating out of `FXAIX`
- using `FXAIX` as temporary defensive parking

Protected holdings can be used for context and benchmark comparison only.

## Active Sleeve

The active sleeve is the only area where future buy, add, reduce, sell, or rebalance actions may be considered. Current planners still only produce dry-run JSON.

## Benchmark Discipline

If the active sleeve cannot beat `FXAIX` over a meaningful sample, the system should pause new buys and prefer review/research until the active process proves itself again.

## Capital Alerts

Capital-needed alerts are informational only. They should never be phrased as a deposit instruction, and they must not bypass sizing, cash, or protected-holding rules.

## Future Live Readiness Checklist

Before live execution exists:

- confirm explicit user approval for live mode
- keep broker execution behind a separate feature flag
- require paper-trading validation first
- require protected-symbol checks at the final execution boundary
- require cash and buying-power checks at the final execution boundary
- require audit logs for every proposed and executed action
- require benchmark guard visibility before new buys
- require a manual kill switch
- keep rebalance outcome analysis read-only; outcome reports may justify future
  scoring changes, but they must not auto-mutate planner weights or execution
  behavior
- keep paper reconciliation read-only; reconciliation reports may compare
  actual paper state to dry-run plans, but they must not submit paper or live
  orders
- keep paper order previews non-submitting; preview rows may look
  broker-shaped, but `order_submission_enabled` must remain false until a
  separate approved paper-execution layer exists
- persist preview rows before paper execution exists; the paper preview ledger
  is audit evidence, not permission to submit orders
- require paper execution eligibility checks before any future paper submission;
  eligibility rows are non-submitting and must be revalidated at the broker
  boundary
- require decision-id traceability for every future paper execution event
- treat eligibility events and feedback refresh outputs as audit/review
  artifacts only; they must include revalidation requirements and must not be
  interpreted as durable authorization to submit orders
- keep feedback tuning inputs analysis-only; they must not auto-mutate ranking,
  sizing, rebalance scoring, or action-planner behavior
- keep monthly/quarterly position intelligence emails informational and
  on-demand until scheduler policy is explicitly implemented; reports may
  summarize collected position research and feedback, but they must not
  authorize orders or trigger broker calls
- keep Stage 6B limited to explicitly requested Alpaca paper `BUY` submission;
  the boundary must revalidate active rules, protected symbols, benchmark
  state, review/thesis state, preview freshness, cash, and duplicate
  submission state immediately before broker calls
- keep rebalances and sells blocked in the first paper execution slice; do not
  submit sell-to-fund-buy flows until settlement/cash sequencing is separately
  designed and reviewed
- require deterministic `client_order_id`, `submission_attempt_id`, active
  rules hash, `paper_mode=true`, and `live_mode=false` in every paper execution
  event
- keep paper order status refresh broker-read-only; it may append status events
  to the paper ledger, but it must not submit, cancel, replace, or modify orders
- keep scheduler-readiness reports advisory-only; readiness checks may surface
  blockers and warnings, but they must not enable scheduler submission or call
  broker execution paths
