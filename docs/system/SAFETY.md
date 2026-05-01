# Long-Term Trader Safety Model

## Current Execution State

The project is not live-trading enabled. The current system can research, log, report, and produce dry-run action intents only.

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
