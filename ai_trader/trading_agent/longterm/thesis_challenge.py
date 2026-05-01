"""Deterministic bull/bear thesis challenge for long-term research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from longterm.reviewers import ReviewResult
from research.research_packet import ResearchPacket


@dataclass(frozen=True)
class ThesisChallenge:
    bull_case: str
    bear_case: str
    key_risks: list[str] = field(default_factory=list)
    kill_criteria: list[str] = field(default_factory=list)
    challenge_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_context_text(self) -> str:
        return "\n".join(
            [
                f"Bull case: {self.bull_case or 'No durable bull case identified.'}",
                f"Bear case: {self.bear_case or 'No explicit bear case identified.'}",
                f"Key risks: {'; '.join(self.key_risks) or 'none'}",
                f"Kill criteria: {'; '.join(self.kill_criteria) or 'none'}",
                f"Synthesis: {self.challenge_summary or 'Require more evidence before sizing up.'}",
            ]
        )


class ThesisChallengeReviewer:
    """Make bull/bear tension explicit before the CGH decision step."""

    def review(
        self,
        packet: ResearchPacket,
        *,
        review_results: list[ReviewResult] | None = None,
        risk_flags: str = "",
    ) -> ThesisChallenge:
        support = [item for result in (review_results or []) for item in result.support]
        objections = [item for result in (review_results or []) for item in result.objections]
        key_risks = _dedupe(
            [
                *packet.reviewer_objections,
                *objections,
                *([risk_flags] if risk_flags else []),
            ]
        )
        kill_criteria = _dedupe([*packet.invalidation_conditions])
        bull_parts = _dedupe(
            [
                packet.thesis_summary,
                packet.business_summary,
                *packet.confirming_signals,
                *packet.reviewer_support,
                *support[:3],
            ]
        )
        bear_parts = _dedupe([*kill_criteria, *key_risks])
        return ThesisChallenge(
            bull_case=" ".join(part for part in bull_parts if part) or packet.symbol,
            bear_case=" ".join(part for part in bear_parts if part),
            key_risks=key_risks,
            kill_criteria=kill_criteria,
            challenge_summary=_challenge_summary(key_risks, kill_criteria),
        )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


def _challenge_summary(key_risks: list[str], kill_criteria: list[str]) -> str:
    if kill_criteria and key_risks:
        return "Bull case must survive explicit kill criteria and current risk objections."
    if kill_criteria:
        return "Bull case has clear kill criteria; monitor before adding aggressively."
    if key_risks:
        return "Risk objections exist; require confirming evidence before sizing up."
    return "No major deterministic bear-case objections surfaced yet."
