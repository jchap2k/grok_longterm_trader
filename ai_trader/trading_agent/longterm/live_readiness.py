"""Dry-run live-readiness checklist for the long-term trader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class LiveReadinessGate:
    key: str
    label: str
    required_value: object = True
    description: str = ""


@dataclass(frozen=True)
class LiveReadinessResult:
    ready: bool
    unmet_gate_keys: list[str]
    gates: list[dict]

    def to_markdown(self) -> str:
        lines = [
            "# Long-Term Live Readiness Checklist",
            "",
            "No live broker execution is enabled by this checklist.",
            "",
            f"Ready for live trading: {'yes' if self.ready else 'no'}",
            "",
            "| Gate | Status | Required | Observed |",
            "|---|---|---|---|",
        ]
        for gate in self.gates:
            status = "pass" if gate["passed"] else "missing"
            lines.append(
                f"| {gate['label']} | {status} | {gate['required_value']} | {gate['observed_value']} |"
            )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class LiveReadinessChecklist:
    gates: list[LiveReadinessGate]

    @classmethod
    def default(cls) -> "LiveReadinessChecklist":
        return cls(
            gates=[
                LiveReadinessGate("dry_run_history", "Sufficient dry-run history", 30),
                LiveReadinessGate("benchmark_proven", "Active sleeve benchmark proof"),
                LiveReadinessGate("paper_trading_verified", "Paper trading verified"),
                LiveReadinessGate(
                    "broker_capability_match",
                    "Live broker capabilities match paper sizing",
                    description=(
                        "The intended live broker must support the same order style "
                        "used in paper simulation, or sizing must be adapted before live mode."
                    ),
                ),
                LiveReadinessGate("protected_symbol_enforced", "Protected symbol enforcement"),
                LiveReadinessGate("manual_approval", "Manual approval recorded"),
                LiveReadinessGate("kill_switch", "Kill switch documented"),
                LiveReadinessGate("audit_logs", "Audit logs enabled"),
                LiveReadinessGate("broker_read_reconciliation", "Broker read reconciliation"),
                LiveReadinessGate("explicit_live_mode_config", "Explicit live mode config"),
                LiveReadinessGate("secrets_not_committed", "Secrets not committed"),
            ]
        )

    def evaluate(self, observed: Mapping[str, object] | None = None) -> LiveReadinessResult:
        observed = observed or {}
        rows: list[dict] = []
        unmet: list[str] = []
        for gate in self.gates:
            observed_value = _observed_value(gate, observed)
            passed = _passes(gate.required_value, observed_value)
            if not passed:
                unmet.append(gate.key)
            rows.append(
                {
                    "key": gate.key,
                    "label": gate.label,
                    "required_value": gate.required_value,
                    "observed_value": observed_value,
                    "passed": passed,
                    "description": gate.description,
                }
            )
        return LiveReadinessResult(ready=not unmet, unmet_gate_keys=unmet, gates=rows)


def _observed_value(gate: LiveReadinessGate, observed: Mapping[str, object]) -> object:
    if gate.key == "dry_run_history":
        return observed.get("dry_run_history", observed.get("dry_run_cycles", 0))
    return observed.get(gate.key, False)


def _passes(required: object, observed: object) -> bool:
    if isinstance(required, bool):
        return bool(observed) is required
    if isinstance(required, (int, float)):
        try:
            return float(observed) >= float(required)
        except (TypeError, ValueError):
            return False
    return observed == required
