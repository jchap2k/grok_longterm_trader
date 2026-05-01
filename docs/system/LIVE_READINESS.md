# Live Readiness

The long-term trader is not live-enabled. This document defines the gates that
must be satisfied before live execution should even be considered.

## Current Position

- Current mode is research, reporting, dry-run account action plans, and paper/live-readiness design.
- The active sleeve must prove it can add value versus `FXAIX` before live trading.
- Protected holdings such as `FXAIX` must remain impossible to sell, trim, rotate, or rebalance out of.
- Alpaca paper trading can be used as a live simulator before real capital is considered.

## Required Gates

The checklist in `longterm/live_readiness.py` should remain conservative:

- Sufficient dry-run history.
- Active-sleeve benchmark proof versus `FXAIX`.
- Paper trading verified.
- Protected-symbol enforcement.
- Manual approval recorded.
- Kill switch documented.
- Audit logs enabled.
- Broker read reconciliation.
- Explicit live-mode config.
- Secrets not committed.

## Commands

```powershell
python scripts/longterm_live_readiness.py
python scripts/longterm_live_readiness.py --json
python scripts/longterm_live_readiness.py --observed-file path\to\live_readiness_observed.json
```

The command reports readiness only. It does not enable live mode and does not
place orders.

