"""Operator thesis-review checklist templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from research.research_packet import ResearchPacket


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"


@dataclass(frozen=True)
class ReviewChecklist:
    symbol: str
    title: str
    sections: list[tuple[str, list[str]]]
    rules_excerpt: str = ""
    decision_id: str = ""

    def to_markdown(self) -> str:
        lines = [f"# Thesis Review Checklist - {self.symbol}", ""]
        if self.decision_id:
            lines.extend([f"Decision ID: `{self.decision_id}`", ""])
        if self.rules_excerpt:
            lines.extend(["## Rules Rubric", "", self.rules_excerpt, ""])
        for heading, items in self.sections:
            lines.extend([f"## {heading}", ""])
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class ReviewTemplateBuilder:
    rules_path: Path = field(default=DEFAULT_RULES_PATH)

    def build(
        self,
        packet: ResearchPacket,
        *,
        review_status: Mapping[str, object] | None = None,
        decision_id: str = "",
        evidence: list[str] | None = None,
    ) -> ReviewChecklist:
        status = review_status or {}
        evidence = evidence or []
        rules_excerpt = load_conviction_rubric(self.rules_path)
        sections = [
            (
                "Business Momentum",
                [
                    f"Business: {packet.business_summary or 'Summarize the business in plain English.'}",
                    f"Growth driver: {packet.primary_growth_driver or 'Identify the current growth driver.'}",
                    "Check whether revenue, margins, retention, or market share support the thesis.",
                ],
            ),
            (
                "Quality durability",
                [
                    "Re-check moat, recurring demand, customer stickiness, and reinvestment runway.",
                    f"Balance sheet: {packet.balance_sheet_assessment or 'Update balance sheet resilience.'}",
                    "Call out any quality trap: debt stress, share loss, churn, or management turnover.",
                ],
            ),
            (
                "Valuation discipline",
                [
                    "Compare current valuation to growth durability and downside risk.",
                    "Do not let a great business bypass price discipline.",
                    "State whether the expected return still beats leaving capital in FXAIX.",
                ],
            ),
            (
                "Thesis breakers",
                _thesis_breaker_items(packet),
            ),
            (
                "Evidence to collect",
                [*evidence] if evidence else ["Add earnings notes, transcript evidence, source links, or current metrics."],
            ),
            (
                "Operator decision",
                [
                    f"Current thesis state: {status.get('thesis_state') or 'unknown'}",
                    f"Review reason: {status.get('review_reason') or 'No review reason supplied.'}",
                    "Choose one: keep thesis healthy, mark weakening, mark broken, or request more research.",
                ],
            ),
        ]
        return ReviewChecklist(
            symbol=packet.symbol,
            title=f"{packet.symbol} thesis review",
            sections=sections,
            rules_excerpt=rules_excerpt,
            decision_id=decision_id,
        )


def load_conviction_rubric(path: Path = DEFAULT_RULES_PATH) -> str:
    """Load a compact rules excerpt so templates stay anchored to project identity."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "Use long-term quality, valuation, thesis, and FXAIX benchmark discipline."
    interesting_lines = [
        line.strip()
        for line in text.splitlines()
        if any(term in line.lower() for term in ("quality", "valuation", "thesis", "fxaix", "benchmark"))
    ]
    excerpt = " ".join(line for line in interesting_lines if line)
    return excerpt[:1200] or "Use long-term quality, valuation, thesis, and FXAIX benchmark discipline."


def _thesis_breaker_items(packet: ResearchPacket) -> list[str]:
    if packet.invalidation_conditions:
        return [f"Check invalidation condition: {condition}" for condition in packet.invalidation_conditions]
    return ["Write explicit conditions that would weaken or break the thesis."]
