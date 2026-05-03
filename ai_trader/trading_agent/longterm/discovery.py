"""Build the upstream stock universe for long-term research.

Discovery is intentionally upstream of portfolio and execution logic. It builds
the research universe; it does not create trade intents, read portfolio state, or
override the independent research/decision pipeline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping


SOURCE_BOOSTS = {
    "manual_watchlist": 14.0,
    "motley_fool": 12.0,
    "sp500": 10.0,
    "s&p500": 10.0,
    "russell1000": 8.0,
    "russell3000": 6.0,
    "nasdaq100": 8.0,
    "nasdaq_listed": 5.0,
    "nyse_amex_listed": 5.0,
    "qqq": 8.0,
    "etf_holdings": 5.0,
    "quality_growth_screen": 6.0,
    "screen_growth": 3.0,
}


@dataclass
class DiscoveryCandidate:
    symbol: str
    company_name: str = ""
    source: str = "unknown"
    source_rank: int | None = None
    source_score: float | None = None
    market_cap: float | None = None
    revenue_growth_1y_pct: float | None = None
    earnings_growth_1y_pct: float | None = None
    debt_to_equity: float | None = None
    return_on_capital_pct: float | None = None
    gross_margin_pct: float | None = None
    price_trend_6m_pct: float | None = None
    valuation_label: str = ""
    category_leader: bool = False
    existing_watchlist: bool = False
    notes: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    discovery_id: str = ""
    discovery_score: float = 0.0
    decision: str = ""
    decision_reason: str = ""

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()
        self.company_name = self.company_name or self.symbol
        self.source = self.source or "unknown"
        self.notes = _as_note_list(self.notes)
        self.source_metadata = dict(self.source_metadata or {})
        self.source_metadata.setdefault("sources", [self.source])
        if not self.discovery_id:
            self.discovery_id = _discovery_id(self.symbol, self.source_metadata["sources"])

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DiscoveryCandidate":
        data = dict(payload)
        data["symbol"] = str(data.get("symbol") or "").upper()
        data["company_name"] = data.get("company_name") or data.get("company") or data["symbol"]
        data["source"] = data.get("source") or data.get("idea_source") or "unknown"
        return cls(**{key: value for key, value in data.items() if key in _candidate_fields()})


@dataclass(frozen=True)
class DiscoveryResult:
    research_queue: list[DiscoveryCandidate]
    watchlist: list[DiscoveryCandidate]
    rejected: list[DiscoveryCandidate]


class DiscoveryEngine:
    """Score and bucket raw universe candidates into a research queue."""

    def __init__(
        self,
        *,
        research_score_threshold: float = 70.0,
        watchlist_score_threshold: float = 30.0,
    ):
        self.research_score_threshold = float(research_score_threshold)
        self.watchlist_score_threshold = float(watchlist_score_threshold)

    def build_queue(
        self,
        candidates: list[Mapping[str, Any] | DiscoveryCandidate],
        *,
        research_limit: int = 25,
    ) -> DiscoveryResult:
        merged = self._merge_candidates(
            [
                candidate
                if isinstance(candidate, DiscoveryCandidate)
                else DiscoveryCandidate.from_mapping(candidate)
                for candidate in candidates
                if (candidate.symbol if isinstance(candidate, DiscoveryCandidate) else candidate.get("symbol"))
            ]
        )

        scored = []
        for candidate in merged:
            candidate.discovery_score = _score_candidate(candidate)
            candidate.decision, candidate.decision_reason = _bucket_candidate(
                candidate,
                research_score_threshold=self.research_score_threshold,
                watchlist_score_threshold=self.watchlist_score_threshold,
            )
            scored.append(candidate)

        scored.sort(key=lambda item: item.discovery_score, reverse=True)
        research_queue = [item for item in scored if item.decision == "research_ready"][:research_limit]
        watchlist = [item for item in scored if item.decision == "watchlist"]
        rejected = [item for item in scored if item.decision == "rejected"]
        return DiscoveryResult(
            research_queue=research_queue,
            watchlist=watchlist,
            rejected=rejected,
        )

    @staticmethod
    def to_research_ideas(candidates: list[DiscoveryCandidate]) -> list[dict[str, Any]]:
        ideas = []
        for candidate in candidates:
            sources = ", ".join(candidate.source_metadata.get("sources") or [candidate.source])
            source_notes = [
                f"Discovery ID: {candidate.discovery_id}.",
                f"Discovery source(s): {sources}.",
                f"Discovery score: {candidate.discovery_score:.1f}.",
                f"Discovery decision: {candidate.decision}.",
            ]
            source_notes.extend(candidate.notes)
            metric_note = _enrichment_metric_note(candidate)
            if metric_note:
                source_notes.append(metric_note)
            ideas.append(
                {
                    "symbol": candidate.symbol,
                    "company_name": candidate.company_name,
                    "idea_source": f"discovery_{candidate.source}",
                    "source_notes": source_notes,
                    "business_summary": (
                        f"Discovery candidate from {candidate.source}; requires independent research."
                    ),
                    "thesis_summary": (
                        f"Potential quality-growth candidate; discovery score {candidate.discovery_score:.1f}."
                    ),
                    "primary_growth_driver": "Requires research.",
                    "industry_context": "Requires research.",
                    "balance_sheet_assessment": "Requires research.",
                }
            )
        return ideas

    def _merge_candidates(self, candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        by_symbol: dict[str, DiscoveryCandidate] = {}
        for candidate in candidates:
            existing = by_symbol.get(candidate.symbol)
            if existing is None:
                by_symbol[candidate.symbol] = candidate
                continue
            by_symbol[candidate.symbol] = _merge_candidate(existing, candidate)
        return list(by_symbol.values())


def _merge_candidate(first: DiscoveryCandidate, second: DiscoveryCandidate) -> DiscoveryCandidate:
    sources = []
    for source in [
        *(first.source_metadata.get("sources") or [first.source]),
        *(second.source_metadata.get("sources") or [second.source]),
    ]:
        if source not in sources:
            sources.append(source)

    notes = []
    for note in [*first.notes, *second.notes]:
        if note not in notes:
            notes.append(note)

    merged = DiscoveryCandidate(
        symbol=first.symbol,
        company_name=first.company_name if first.company_name != first.symbol else second.company_name,
        source="+".join(sources),
        source_rank=_min_present(first.source_rank, second.source_rank),
        source_score=_max_present(first.source_score, second.source_score),
        market_cap=_max_present(first.market_cap, second.market_cap),
        revenue_growth_1y_pct=_max_present(first.revenue_growth_1y_pct, second.revenue_growth_1y_pct),
        earnings_growth_1y_pct=_max_present(first.earnings_growth_1y_pct, second.earnings_growth_1y_pct),
        debt_to_equity=_min_present(first.debt_to_equity, second.debt_to_equity),
        return_on_capital_pct=_max_present(first.return_on_capital_pct, second.return_on_capital_pct),
        gross_margin_pct=_max_present(first.gross_margin_pct, second.gross_margin_pct),
        price_trend_6m_pct=_max_present(first.price_trend_6m_pct, second.price_trend_6m_pct),
        valuation_label=first.valuation_label or second.valuation_label,
        category_leader=first.category_leader or second.category_leader,
        existing_watchlist=first.existing_watchlist or second.existing_watchlist,
        notes=notes,
        source_metadata={"sources": sources},
    )
    return merged


def _score_candidate(candidate: DiscoveryCandidate) -> float:
    score = 25.0
    score += _source_boost(candidate)
    score += _positive_metric(candidate.revenue_growth_1y_pct, good=15, excellent=30, points=15)
    score += _positive_metric(candidate.earnings_growth_1y_pct, good=10, excellent=25, points=12)
    score += _positive_metric(candidate.return_on_capital_pct, good=12, excellent=25, points=12)
    score += _positive_metric(candidate.gross_margin_pct, good=40, excellent=65, points=8)
    score += _positive_metric(candidate.price_trend_6m_pct, good=5, excellent=25, points=8)
    score += 10.0 if candidate.category_leader else 0.0
    score += 8.0 if candidate.existing_watchlist else 0.0
    score += _market_cap_score(candidate.market_cap)
    score += _valuation_score(candidate.valuation_label)
    score -= _debt_penalty(candidate.debt_to_equity)
    if candidate.source_rank is not None:
        score += max(0.0, 8.0 - min(float(candidate.source_rank), 20.0) * 0.4)
    if candidate.source_score is not None:
        score += min(10.0, max(0.0, float(candidate.source_score)) / 10.0)
    return round(max(0.0, min(score, 100.0)), 1)


def _bucket_candidate(
    candidate: DiscoveryCandidate,
    *,
    research_score_threshold: float,
    watchlist_score_threshold: float,
) -> tuple[str, str]:
    hard_reject = _hard_reject_reason(candidate)
    if hard_reject:
        return "rejected", hard_reject
    if candidate.discovery_score >= research_score_threshold:
        return "research_ready", "Quality-growth discovery score clears research threshold."
    if candidate.discovery_score >= watchlist_score_threshold:
        return "watchlist", "Interesting but not strong enough for immediate research."
    return "rejected", "Discovery score below watchlist threshold."


def _hard_reject_reason(candidate: DiscoveryCandidate) -> str:
    if candidate.market_cap is not None and candidate.market_cap < 500_000_000:
        return "Rejected: tiny market cap below long-term liquidity threshold."
    if candidate.revenue_growth_1y_pct is not None and candidate.revenue_growth_1y_pct < -20:
        return "Rejected: revenue growth is materially negative."
    if candidate.debt_to_equity is not None and candidate.debt_to_equity > 4:
        return "Rejected: debt load is too high for V1 quality-growth discovery."
    if candidate.price_trend_6m_pct is not None and candidate.price_trend_6m_pct < -60:
        return "Rejected: price trend is badly broken."
    return ""


def _source_boost(candidate: DiscoveryCandidate) -> float:
    boost = 0.0
    for source in candidate.source_metadata.get("sources") or [candidate.source]:
        normalized = str(source).lower().replace("_", "")
        if "motleyfool" in normalized:
            boost = max(boost, SOURCE_BOOSTS["motley_fool"])
        elif "sp500" in normalized or "s&p500" in normalized:
            boost = max(boost, SOURCE_BOOSTS["sp500"])
        elif "russell1000" in normalized:
            boost = max(boost, SOURCE_BOOSTS["russell1000"])
        elif "russell3000" in normalized:
            boost = max(boost, SOURCE_BOOSTS["russell3000"])
        elif "nasdaq100" in normalized or "qqq" in normalized:
            boost = max(boost, SOURCE_BOOSTS["qqq"])
        else:
            boost = max(boost, SOURCE_BOOSTS.get(str(source).lower(), 0.0))
    return boost


def _positive_metric(value: float | None, *, good: float, excellent: float, points: float) -> float:
    if value is None:
        return 0.0
    value = float(value)
    if value <= 0:
        return min(0.0, value / 10.0)
    if value >= excellent:
        return points
    if value >= good:
        return points * 0.65
    return points * 0.25


def _market_cap_score(value: float | None) -> float:
    if value is None:
        return 0.0
    value = float(value)
    if value >= 50_000_000_000:
        return 8.0
    if value >= 10_000_000_000:
        return 6.0
    if value >= 2_000_000_000:
        return 3.0
    if value >= 500_000_000:
        return -5.0
    return -20.0


def _valuation_score(label: str) -> float:
    normalized = (label or "").lower()
    if normalized in {"attractive", "cheap", "reasonable"}:
        return 6.0
    if normalized in {"fair", "fairly valued"}:
        return 3.0
    if normalized in {"expensive", "stretched"}:
        return -8.0
    return 0.0


def _debt_penalty(value: float | None) -> float:
    if value is None:
        return 0.0
    value = float(value)
    if value <= 1.0:
        return 0.0
    if value <= 2.0:
        return 4.0
    if value <= 4.0:
        return 12.0
    return 30.0


def _enrichment_metric_note(candidate: DiscoveryCandidate) -> str:
    if not any(str(note).startswith("Enriched from ") for note in candidate.notes):
        return ""
    parts = []
    if candidate.market_cap is not None:
        parts.append(f"market cap {_format_number(candidate.market_cap)}")
    if candidate.revenue_growth_1y_pct is not None:
        parts.append(f"revenue growth {_format_number(candidate.revenue_growth_1y_pct)}%")
    if candidate.earnings_growth_1y_pct is not None:
        parts.append(f"earnings growth {_format_number(candidate.earnings_growth_1y_pct)}%")
    if candidate.return_on_capital_pct is not None:
        parts.append(f"return on capital {_format_number(candidate.return_on_capital_pct)}%")
    if candidate.gross_margin_pct is not None:
        parts.append(f"gross margin {_format_number(candidate.gross_margin_pct)}%")
    if candidate.debt_to_equity is not None:
        parts.append(f"debt/equity {_format_number(candidate.debt_to_equity)}")
    if candidate.price_trend_6m_pct is not None:
        parts.append(f"6m price trend {_format_number(candidate.price_trend_6m_pct)}%")
    if not parts:
        return ""
    return f"Discovery metrics: {'; '.join(parts)}."


def _format_number(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _discovery_id(symbol: str, sources: list[str]) -> str:
    digest = hashlib.sha1("|".join([symbol, *sources]).encode("utf-8")).hexdigest()[:10]
    return f"DISC-{symbol}-{digest}"


def _as_note_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _candidate_fields() -> set[str]:
    return set(DiscoveryCandidate.__dataclass_fields__.keys())


def _max_present(first, second):
    values = [value for value in [first, second] if value is not None]
    return max(values) if values else None


def _min_present(first, second):
    values = [value for value in [first, second] if value is not None]
    return min(values) if values else None
