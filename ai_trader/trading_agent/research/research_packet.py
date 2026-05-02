"""Structured research packet models for long-term candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CompanyCategory(str, Enum):
    """Lynch-style company buckets for long-term research."""

    SLOW_GROWER = "slow_grower"
    STALWART = "stalwart"
    FAST_GROWER = "fast_grower"
    CYCLICAL = "cyclical"
    TURNAROUND = "turnaround"
    ASSET_PLAY = "asset_play"


@dataclass
class ResearchPacket:
    """Normalized research packet for deeper long-term stock review."""

    symbol: str
    company_name: str = ""
    company_category: Optional[CompanyCategory] = None
    business_summary: str = ""
    thesis_summary: str = ""
    primary_growth_driver: str = ""
    industry_context: str = ""
    account_strategy_mode: str = ""
    protected_symbols: List[str] = field(default_factory=list)
    benchmark_symbol: str = ""
    defensive_parking_symbol: str = ""
    balance_sheet_assessment: str = ""
    quality_score: Optional[float] = None
    valuation_score: Optional[float] = None
    combined_attractiveness_score: Optional[float] = None
    expected_hold_horizon: str = ""
    review_cadence: str = ""
    idea_source: str = ""
    confirming_signals: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    reviewer_support: List[str] = field(default_factory=list)
    reviewer_objections: List[str] = field(default_factory=list)
    source_notes: List[str] = field(default_factory=list)
    evidence_brief: str = ""

    def __post_init__(self) -> None:
        self.symbol = (self.symbol or "").upper()
        self.company_name = self.company_name or ""
        self.business_summary = self.business_summary or ""
        self.thesis_summary = self.thesis_summary or ""
        self.primary_growth_driver = self.primary_growth_driver or ""
        self.industry_context = self.industry_context or ""
        self.account_strategy_mode = self.account_strategy_mode or ""
        self.protected_symbols = [str(symbol).upper() for symbol in (self.protected_symbols or [])]
        self.benchmark_symbol = (self.benchmark_symbol or "").upper()
        self.defensive_parking_symbol = (self.defensive_parking_symbol or "").upper()
        self.balance_sheet_assessment = self.balance_sheet_assessment or ""
        self.expected_hold_horizon = self.expected_hold_horizon or ""
        self.review_cadence = self.review_cadence or ""
        self.idea_source = self.idea_source or ""
        self.confirming_signals = list(self.confirming_signals or [])
        self.invalidation_conditions = list(self.invalidation_conditions or [])
        self.reviewer_support = list(self.reviewer_support or [])
        self.reviewer_objections = list(self.reviewer_objections or [])
        self.source_notes = list(self.source_notes or [])
        self.evidence_brief = self.evidence_brief or ""

        if self.combined_attractiveness_score is None:
            self.combined_attractiveness_score = self._compute_combined_score()

    def _compute_combined_score(self) -> Optional[float]:
        scores = [
            score for score in (self.quality_score, self.valuation_score)
            if score is not None
        ]
        if not scores:
            return None
        return sum(scores) / len(scores)

    def completeness_warnings(self) -> List[str]:
        """Return blocking warnings for research packets that are too thin."""
        warnings: List[str] = []
        symbol = self.symbol or "UNKNOWN"
        if not self.company_name:
            warnings.append(f"{symbol}: missing company_name")
        if not self.idea_source:
            warnings.append(f"{symbol}: missing idea_source")
        if not self.business_summary and not self.thesis_summary and not self.source_notes and not self.evidence_brief:
            warnings.append(f"{symbol}: missing research context")
        return warnings

    def is_minimally_complete_for_research(self) -> bool:
        """Return whether this packet has enough context for paid/deep research."""
        return not self.completeness_warnings()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize packet to a JSON-friendly dictionary."""
        payload = asdict(self)
        category = payload.get("company_category")
        if isinstance(category, Enum):
            payload["company_category"] = category.value
        return payload
