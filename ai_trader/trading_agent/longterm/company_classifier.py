"""Lynch-style company classification helpers."""

from research.research_packet import CompanyCategory


def classify_company(
    revenue_growth_pct: float,
    earnings_growth_pct: float,
    *,
    is_cyclical: bool = False,
    turnaround_signals: bool = False,
    asset_play_signals: bool = False,
) -> CompanyCategory:
    """Classify a company into a long-term research bucket."""
    if turnaround_signals:
        return CompanyCategory.TURNAROUND
    if asset_play_signals:
        return CompanyCategory.ASSET_PLAY
    if is_cyclical:
        return CompanyCategory.CYCLICAL

    average_growth = (float(revenue_growth_pct) + float(earnings_growth_pct)) / 2.0
    if average_growth >= 20.0:
        return CompanyCategory.FAST_GROWER
    if average_growth >= 7.0:
        return CompanyCategory.STALWART
    return CompanyCategory.SLOW_GROWER
