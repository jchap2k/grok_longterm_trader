"""Reusable research runner for long-term multi-agent analysis."""

from __future__ import annotations

from pathlib import Path

from agent.utils.cheap_grok_heavy import CheapGrokHeavy
from longterm.book_principles import BookPrinciplesProvider
from longterm.decision_journal import LongTermDecisionJournal
from longterm.decision_parser import parse_decision_response
from research.research_packet import ResearchPacket


class LongTermResearchRunner:
    """Thin wrapper around CheapGrokHeavy for long-term research packets."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str,
        agent_max_tokens: int = 700,
        max_concurrent: int | None = None,
        verbose: bool = True,
        book_principles_provider: BookPrinciplesProvider | None = None,
    ):
        self._client = CheapGrokHeavy(
            api_key=api_key,
            agent_specs_path=config_path,
            agent_max_tokens=agent_max_tokens,
            max_concurrent=max_concurrent,
            verbose=verbose,
        )
        self.book_principles_provider = book_principles_provider or BookPrinciplesProvider()
        self.decision_journal: LongTermDecisionJournal | None = None

    def _build_context_sections(
        self,
        packet: ResearchPacket,
        *,
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
                "business first quality valuation balance sheet thesis classification benchmark",
            ]
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
            "portfolio_context": (
                f"Account mode: {packet.account_strategy_mode or 'standard'}. "
                f"Protected symbols: {', '.join(packet.protected_symbols) if packet.protected_symbols else 'none'}."
            ),
            "market_risk_context": market_risk_context,
            "bull_thesis": packet.thesis_summary,
            "supporting_evidence": supporting_evidence,
            "risk_flags": risk_flags,
            "research_principles": self.book_principles_provider.recall(principles_query),
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
        financial_metrics: str = "",
        macro_regime: str = "",
        market_risk_context: str = "",
        supporting_evidence: str = "",
        risk_flags: str = "",
    ) -> str:
        """Run the configured long-term research flow for one packet."""
        context_sections = self._build_context_sections(
            packet,
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
            "whether the idea is strong enough to justify active capital."
        )
        return self._client.call_with_context(task_prompt, context_sections)

    def run_and_record(
        self,
        packet: ResearchPacket,
        *,
        journal_db_path: str | Path | None = None,
        candidate_price: float | None = None,
        benchmark_price: float | None = None,
        financial_metrics: str = "",
        macro_regime: str = "",
        market_risk_context: str = "",
        supporting_evidence: str = "",
        risk_flags: str = "",
    ) -> str:
        """Run research and record the structured decision in the journal."""
        raw_response = self.run(
            packet,
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
