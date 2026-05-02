# Live Readiness

The long-term trader is not live-enabled. This document defines the gates that
must be satisfied before live execution should even be considered.

## Current Position

- Current mode is research, reporting, dry-run account action plans, and paper/live-readiness design.
- The active sleeve must prove it can add value versus `FXAIX` before live trading.
- Protected holdings such as `FXAIX` must remain impossible to sell, trim, rotate, or rebalance out of.
- Alpaca paper trading can be used as a live simulator before real capital is considered.
- The current Alpaca integration is a read-only paper account snapshot path over the standard API. It exports portfolio state for planning; it does not place orders and does not require websockets.
- Alpaca paper can support notional/fractional simulation, but that must not be
  treated as proof that a future live broker supports the same sizing model.
  For example, Schwab's public API is whole-share oriented even though Schwab
  offers fractional "Stock Slices" outside the API.
- The paper preview and supervised paper execution boundary can use an explicit
  whole-share order model so Alpaca paper tests can mirror Schwab-compatible
  sizing assumptions before any live-readiness claim is made.

## Required Gates

The checklist in `longterm/live_readiness.py` should remain conservative:

- Sufficient dry-run history.
- Active-sleeve benchmark proof versus `FXAIX`.
- Paper trading verified.
- Supervised paper-smoke readiness verified from a clean pre-flight artifact.
- Live broker capabilities match the paper sizing/order model, or the live
  execution plan has been adapted and reviewed for whole-share constraints.
- Protected-symbol enforcement.
- Manual approval recorded.
- Kill switch documented.
- Audit logs enabled.
- Broker read reconciliation.
- Explicit live-mode config.
- Secrets not committed.

## Commands

```powershell
python scripts/longterm_broker_capabilities.py
python scripts/longterm_broker_capabilities.py --required-order-model whole_share --observed-output path\to\broker_capability_observed.json
python scripts/longterm_paper_trading_verification.py --ledger-db path\to\paper_ledger.db --observed-output path\to\paper_trading_observed.json
python scripts/longterm_live_readiness.py
python scripts/longterm_live_readiness.py --json
python scripts/longterm_live_readiness.py --observed-file path\to\live_readiness_observed.json
python scripts/longterm_live_readiness.py --observed-file path\to\base_observed.json --observed-fragment path\to\broker_capability_observed.json
python scripts/longterm_live_readiness_bundle.py --observed-file path\to\base_observed.json --paper-ledger-db path\to\paper_ledger.db --paper-smoke-readiness path\to\paper_smoke_readiness.json --required-order-model whole_share
```

`longterm_broker_capabilities.py` is advisory-only. With the default
`notional_fractional` order model, it should block Alpaca-paper-to-Schwab-API
live readiness because Schwab API is treated as whole-share only. If a future
live plan is intentionally adapted to whole shares, run it with
`--required-order-model whole_share` and include the generated observed JSON in
the larger live-readiness evidence file.

Whole-share paper previews require an explicit price map and floor quantities to
whole shares. This intentionally surfaces size variance and cash drag instead of
letting Alpaca fractional/notional behavior hide live-broker constraints.
`longterm_paper_price_map.py` can build that map from a dry-run action plan via
read-only Alpaca paper quotes; the resulting map is still operator evidence, not
authorization to submit orders.

`longterm_live_readiness.py` can merge a base observed file with one or more
`--observed-fragment` files. Later fragments override earlier values, which
lets small advisory tools such as broker capability checks provide one gate at
a time without hand-editing the main observed JSON.

`longterm_paper_trading_verification.py` can generate the
`paper_trading_verified` fragment from the paper execution ledger after a
successful filled paper order. It is read-only and does not call a broker.

`longterm_live_readiness_bundle.py` combines the base observed file, broker
capability evidence, paper-trading verification, and optional paper-smoke
readiness into one checklist result. It is still evidence-only and does not
enable live execution.

The command reports readiness only. It does not enable live mode and does not
place orders.
