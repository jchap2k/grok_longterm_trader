"""Durable operating cadence model for the dry-run long-term scheduler."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SchedulerRoutine:
    key: str
    label: str
    cadence: str
    mode: str
    purpose: str
    command_hint: str
    safety_note: str = "Dry-run artifact only; no broker execution."


@dataclass(frozen=True)
class SchedulerOperatingModel:
    """Document how the long-term system should run without becoming noisy."""

    routines: list[SchedulerRoutine]
    schema_version: int = 1
    operating_note: str = "No broker execution. Scheduler routines produce research and operator artifacts only."

    @classmethod
    def default(cls) -> "SchedulerOperatingModel":
        return cls(
            routines=[
                SchedulerRoutine(
                    key="next_actions_rebalance",
                    label="Next-actions and rebalance refresh",
                    cadence="daily",
                    mode="dry_run",
                    purpose="Refresh benchmark gate, account action plan, urgent reviews, and dry-run rotations.",
                    command_hint="python scripts/longterm_next_actions.py --journal-db path\\to\\journal.db --portfolio-state path\\to\\portfolio.json",
                ),
                SchedulerRoutine(
                    key="discovery_refresh",
                    label="Discovery universe refresh",
                    cadence="weekly",
                    mode="dry_run",
                    purpose="Refresh source universes and rebuild research_queue/watchlist/rejected buckets.",
                    command_hint="python scripts/run_longterm_discovery.py --source-file path\\to\\sp500.csv --source sp500",
                ),
                SchedulerRoutine(
                    key="motley_fool_intake",
                    label="Motley Fool idea intake",
                    cadence="weekly",
                    mode="dry_run",
                    purpose="Capture premium idea feeds when configured cookies are ready.",
                    command_hint="python scripts/run_longterm_cycle.py --launch-login-if-needed --journal-db path\\to\\journal.db",
                ),
                SchedulerRoutine(
                    key="research_batch",
                    label="Research batch",
                    cadence="weekly",
                    mode="dry_run",
                    purpose="Run complete research packets through deterministic reviewers and CGH decision logging.",
                    command_hint="python scripts/run_longterm_cycle.py --idea-batch path\\to\\ideas.json --journal-db path\\to\\journal.db",
                ),
                SchedulerRoutine(
                    key="thesis_review",
                    label="Thesis review refresh",
                    cadence="weekly",
                    mode="dry_run",
                    purpose="Review stale, weakening, or broken held theses and record review events.",
                    command_hint="python scripts/longterm_journal.py thesis-review-list --journal-db path\\to\\journal.db",
                ),
                SchedulerRoutine(
                    key="benchmark_capital_alert",
                    label="Benchmark and capital alert check",
                    cadence="daily",
                    mode="dry_run",
                    purpose="Check FXAIX benchmark accountability and informational capital-needed alerts.",
                    command_hint="python scripts/run_longterm_cycle.py --portfolio-state path\\to\\portfolio.json --active-sleeve-value 35000 --available-cash 500",
                ),
                SchedulerRoutine(
                    key="grok_plan_review",
                    label="Grok plan review",
                    cadence="as_needed",
                    mode="dry_run",
                    purpose="Review complex scheduler, agent, or safety changes before implementation.",
                    command_hint="Use GrokPlanReviewer for multi-file foundation changes.",
                ),
            ]
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "operating_note": self.operating_note,
            "routines": [asdict(routine) for routine in self.routines],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Long-Term Scheduler Operating Model",
            "",
            self.operating_note,
            "",
            "| Cadence | Routine | Mode | Purpose | Command Hint |",
            "|---|---|---|---|---|",
        ]
        for routine in self.routines:
            lines.append(
                "| {cadence} | {label} | {mode} | {purpose} | `{command}` |".format(
                    cadence=routine.cadence,
                    label=routine.label,
                    mode=routine.mode,
                    purpose=_markdown_cell(routine.purpose),
                    command=_markdown_cell(routine.command_hint),
                )
            )
        lines.extend(["", "Safety: No broker execution is performed by this operating model."])
        return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
