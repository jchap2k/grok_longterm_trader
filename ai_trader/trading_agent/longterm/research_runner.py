"""Reusable research runner for long-term multi-agent analysis."""

from __future__ import annotations

from pathlib import Path

from agent.utils.cheap_grok_heavy import CheapGrokHeavy
from longterm.active_rules_provider import ActiveRulesProvider
from longterm.book_principles import BookPrinciplesProvider
from longterm.decision_journal import LongTermDecisionJournal
from longterm.decision_parser import parse_decision_response
from longterm.portfolio_state import PortfolioState
from longterm.review_cadence import ReviewCadencePolicy
from longterm.reviewers import (
    BalanceSheetReviewer,
    BusinessStoryReviewer,
    QualityDurabilityReviewer,
    QualityAtReasonablePriceReviewer,
    ReviewResult,
)
from longterm.thesis_challenge import ThesisChallengeReviewer
from research.research_packet import ResearchPacket


class LongTermResearchRunner:
    """Thin wrapper around CheapGrokHeavy for long-term research packets."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str,
        agent_preset: str | None = "decision_4",
        agent_max_tokens: int = 700,
        max_concurrent: int | None = None,
        verbose: bool = True,
        book_principles_provider: BookPrinciplesProvider | None = None,
        active_rules_provider: ActiveRulesProvider | None = None,
        rules_path: str | Path | None = None,
    ):
        self._client = CheapGrokHeavy(
            api_key=api_key,
            agent_specs_path=config_path,
            agent_preset=agent_preset,
            agent_max_tokens=agent_max_tokens,
            max_concurrent=max_concurrent,
            verbose=verbose,
        )
        self.book_principles_provider = book_principles_provider or BookPrinciplesProvider()
        self.active_rules_provider = active_rules_provider or ActiveRulesProvider(rules_path)
        self.review_cadence_policy = ReviewCadencePolicy()
        self.decision_journal: LongTermDecisionJournal | None = None

    def _run_deterministic_reviews(self, packet: ResearchPacket) -> list[ReviewResult]:
        return [
            BusinessStoryReviewer().review(packet),
            BalanceSheetReviewer().review(packet),
            QualityDurabilityReviewer().review(packet),
            QualityAtReasonablePriceReviewer().review(packet),
        ]

    @staticmethod
    def _format_review_results(results: list[ReviewResult]) -> str:
        lines = []
        for result in results:
            lines.append(
                f"{result.reviewer}: score={result.score}, passed={result.passed}; "
                f"support={'; '.join(result.support) or 'none'}; "
                f"objections={'; '.join(result.objections) or 'none'}"
            )
        return "\n".join(lines)

    def _build_context_sections(
        self,
        packet: ResearchPacket,
        *,
        portfolio_state: PortfolioState | None = None,
        financial_metrics: str = "",
        macro_regime: str = "",
        market_risk_context: str = "",
        supporting_evidence: str = "",
        risk_flags: str = "",
    ) -> dict:
        principles_query = " ".join(
            [
                packet.symbol,
                packet.company_category.value if packet.company_category else "",
                packet.thesis_summary,
                "business first quality durability pricing power recurring revenue moat valuation balance sheet thesis classification benchmark",
            ]
        )
        review_results = self._run_deterministic_reviews(packet)
        cadence = self.review_cadence_policy.assign(packet)
        thesis_challenge = ThesisChallengeReviewer().review(
            packet,
            review_results=review_results,
            risk_flags=risk_flags,
        )
        return {
            "company_research_packet": (
                f"Symbol: {packet.symbol}\n"
                f"Company: {packet.company_name or packet.symbol}\n"
                f"Category: {packet.company_category.value if packet.company_category else 'unclassified'}\n"
                f"Idea source: {packet.idea_source or 'unspecified'}\n"
                f"Notes: {'; '.join(packet.source_notes) if packet.source_notes else 'none'}"
            ),
            "financial_metrics": financial_metrics,
            "business_summary": packet.business_summary,
            "macro_regime": macro_regime,
            "portfolio_context": _format_portfolio_context(packet, portfolio_state),
            "market_risk_context": market_risk_context,
            "bull_thesis": packet.thesis_summary,
            "supporting_evidence": supporting_evidence,
            "risk_flags": risk_flags,
            "deterministic_reviews": self._format_review_results(review_results),
            "thesis_challenge": thesis_challenge.to_context_text(),
            "review_cadence": (
                f"review_cadence={cadence.review_cadence}; "
                f"expected_hold_horizon={cadence.expected_hold_horizon}; "
                f"reason={cadence.reason}"
            ),
            "sizing_policy_context": (
                "Start new positions small. Add only when thesis confirmation improves. "
                "Use suggested_size_pct as active-sleeve target percent, capped by conviction. "
                "Recommend BUY/ADD/HOLD/PASS/REDUCE/SELL only within non-protected active capital."
            ),
            "research_principles": self.book_principles_provider.recall(principles_query),
            "active_rules_context": self.active_rules_provider.load(),
            "decision_constraints": (
                "Do not recommend actions that violate protected-symbol constraints. "
                "Respect benchmark-awareness and active-sleeve discipline."
            ),
            "benchmark_context": (
                f"Benchmark symbol: {packet.benchmark_symbol or 'none'}. "
                f"Defensive parking symbol: {packet.defensive_parking_symbol or 'none'}."
            ),
        }

    def run(
        self,
        packet: ResearchPacket,
        *,
        portfolio_state: PortfolioState | None = None,
        financial_metrics: str = "",
        macro_regime: str = "",
        market_risk_context: str = "",
        supporting_evidence: str = "",
        risk_flags: str = "",
    ) -> str:
        """Run the configured long-term research flow for one packet."""
        context_sections = self._build_context_sections(
            packet,
            portfolio_state=portfolio_state,
            financial_metrics=financial_metrics,
            macro_regime=macro_regime,
            market_risk_context=market_risk_context,
            supporting_evidence=supporting_evidence,
            risk_flags=risk_flags,
        )
        task_prompt = (
            f"Evaluate whether {packet.symbol} deserves consideration in a long-term "
            "quality-growth active sleeve. Ignore short-term noise. Focus on "
            "multi-year thesis durability, risks, benchmark-aware edge, and "
            "whether the idea is strong enough to justify active capital. Return the "
            "buy, add, hold, pass, reduce, or sell choice with suggested active-sleeve size."
        )
        return self._client.call_with_context(task_prompt, context_sections)

    def run_and_record(
        self,
        packet: ResearchPacket,
        *,
        journal_db_path: str | Path | None = None,
        candidate_price: float | None = None,
        benchmark_price: float | None = None,
        portfolio_state: PortfolioState | None = None,
        financial_metrics: str = "",
        macro_regime: str = "",
        market_risk_context: str = "",
        supporting_evidence: str = "",
        risk_flags: str = "",
    ) -> str:
        """Run research and record the structured decision in the journal."""
        raw_response = self.run(
            packet,
            portfolio_state=portfolio_state,
            financial_metrics=financial_metrics,
            macro_regime=macro_regime,
            market_risk_context=market_risk_context,
            supporting_evidence=supporting_evidence,
            risk_flags=risk_flags,
        )
        decision = parse_decision_response(raw_response)
        self.decision_journal = LongTermDecisionJournal(journal_db_path)
        return self.decision_journal.record_decision(
            packet,
            decision=decision,
            candidate_price=candidate_price,
            benchmark_price=benchmark_price,
            raw_response=raw_response,
        )


def _format_portfolio_context(
    packet: ResearchPacket,
    portfolio_state: PortfolioState | None,
) -> str:
    """Format read-only account context for the LLM committee."""
    protected = [symbol.upper() for symbol in (packet.protected_symbols or [])]
    parking_symbols = _dedupe_symbols(
        [
            packet.defensive_parking_symbol,
            "SGOV",
            "TLT",
        ]
    )
    lines = [
        f"Account mode: {packet.account_strategy_mode or 'standard'}.",
        f"Protected symbols: {', '.join(protected) if protected else 'none'}.",
        f"Benchmark symbol: {packet.benchmark_symbol or 'none'}.",
        f"Parking symbols: {', '.join(parking_symbols) if parking_symbols else 'none'}.",
    ]
    if portfolio_state is None:
        lines.append("Current portfolio state: not supplied.")
        return "\n".join(lines)

    protected_set = set(protected) | set(portfolio_state.protected_symbols)
    parking_set = set(parking_symbols)
    lines.extend(
        [
            f"Cash: {_money(portfolio_state.cash)}.",
            f"Active market value: {_money(portfolio_state.active_market_value)}.",
            f"Protected/core value: {_money(portfolio_state.protected_market_value)}.",
        ]
    )
    if not portfolio_state.holdings:
        lines.append("Current holdings: none.")
        return "\n".join(lines)

    for holding in portfolio_state.holdings:
        role = "active"
        if holding.symbol in protected_set:
            role = "protected"
        elif holding.symbol == packet.symbol:
            role = "existing_candidate_position"
        elif holding.symbol in parking_set:
            role = "parking"
        lines.append(
            f"Holding {holding.symbol}: {_money(holding.market_value)}, role={role}."
        )
    return "\n".join(lines)


def _money(value: float | int | None) -> str:
    return f"${float(value or 0.0):,.2f}"


def _dedupe_symbols(symbols: list[str | None]) -> list[str]:
    results: list[str] = []
    for symbol in symbols:
        normalized = str(symbol or "").upper()
        if normalized and normalized not in results:
            results.append(normalized)
    return results
