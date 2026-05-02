import json

from longterm.motley_fool_company_enrichment import (
    CompanyPageSnapshot,
    _wait_for_company_content,
    enrich_idea_from_company_snapshot,
)
from longterm.motley_fool_company_enrichment_cli import build_parser, run_cli


def _tsla_snapshot() -> CompanyPageSnapshot:
    return CompanyPageSnapshot(
        requested_url="https://www.fool.com/premium/company/NASDAQ/TSLA/financials/summary",
        resolved_url="https://www.fool.com/premium/company/NASDAQ/TSLA/financials/summary",
        title="TSLA - Summary - Fool IQ",
        text="""
Tesla, Inc.(NASDAQ:TSLA)
Hidden Gems - Moneyball
Superscore
71
Finance 1Y
58
Finance 5Y
92
Product 1Y
49
Product 5Y
99
Leaders
78
AI
69
Investing Type
Aggressive
Est. Annualized Return
-18% to 9%
Est. Max Drawdown
-55%
Analysis
Quality
38%
Growth
47%
Valuation
3%
Safety
76%
Market Buzz
29%
TSLA Recent Earnings - Q1 Fiscal Year 2026
Q1 Earnings
Operating Income
941.0M
Free Cash Flow
1.4B
Upcoming Earnings
EPS Estimate
0.12
Revenue Estimate
2.3B
Announce Date
3/31/2026
View Full Earnings Report
Q2 Earnings
Synthetic Earnings Headline For Parser Testing
Tesla
[@portabletext/react] Unknown block type "inlineTicker", specify a component for it in the `components.types` prop
makes synthetic vehicle and energy products for parser testing.
Key Financial Takeaways
Revenue: $22.4 billion (up 16% YoY)
Diluted EPS (non-GAAP): $0.41 (up 52% YoY)
Latest Developments
Synthetic software revenue expanded.
Synthetic inventory risk increased.
""",
        headings=[
            "Tesla, Inc.(NASDAQ:TSLA)",
            "TSLA Recent Earnings - Q1 Fiscal Year 2026",
            "Synthetic Earnings Headline For Parser Testing",
        ],
        tables=[
            {
                "headers": ["Metric", "Value"],
                "rows": [
                    ["3-Yr Revenue Growth", "5.19%"],
                    ["3-Yr Operating Income Growth", "-31.68%"],
                    ["3-Yr EPS Growth", "-33.18%"],
                    ["3-Yr EBITDA Growth", "-12.66%"],
                    ["3-Yr FCF per Share Growth", "-6.74%"],
                ],
            },
            {
                "headers": ["Metric", "Value"],
                "rows": [
                    ["Price/Earnings", "329.7x"],
                    ["EV/EBITDA", "140.9x"],
                    ["Price/Free Cash Flow", "212.0x"],
                    ["Price/Book Value", "15.2x"],
                    ["Price/Earnings Growth (5-Yr)", "-8.4x"],
                ],
            },
            {
                "headers": ["Metric", "Value"],
                "rows": [
                    ["Gross Margin", "19.07%"],
                    ["Operating Margin", "5.00%"],
                    ["Free Cash Flow Margin", "7.15%"],
                    ["Return on Invested Capital", "3.21%"],
                    ["Debt/Equity", "0.1x"],
                ],
            },
            {
                "headers": ["Metric", "Value"],
                "rows": [
                    ["Revenue", "$97.88B (+2.25%)"],
                    ["EBITDA", "$10.48B (-24.95%)"],
                    ["Net Income", "$3.88B (-38.38%)"],
                    ["Free Cash Flow", "$7.00B (+3.24%)"],
                    ["Total Debt", "$9.23B (+4.95%)"],
                    ["Total Cash", "$44.74B (+19.73%)"],
                ],
            },
        ],
        links=[
            {
                "text": "View Full Earnings Report",
                "href": "https://www.fool.com/premium/4056/coverage/2026/04/22/tesla-posts-52-profit-jump-in-fiscal-q1",
            }
        ],
    )


def test_enrich_idea_from_company_snapshot_maps_fool_iq_sections_to_packet_fields():
    idea = {
        "symbol": "TSLA",
        "company_name": "Tesla",
        "idea_source": "motley_fool_new_recommendations",
        "source_notes": ["Motley Fool candidate."],
        "motley_fool_company_url": _tsla_snapshot().requested_url,
    }

    enriched = enrich_idea_from_company_snapshot(idea, _tsla_snapshot())

    assert enriched["business_summary"].startswith("Tesla makes synthetic vehicle and energy")
    assert "Synthetic software revenue expanded." in enriched["confirming_signals"]
    assert "Synthetic inventory risk increased." in enriched["invalidation_conditions"]
    assert enriched["quality_score"] == 38.0
    assert enriched["valuation_score"] == 3.0
    assert enriched["balance_sheet_assessment"] == "Total Debt: $9.23B (+4.95%); Total Cash: $44.74B (+19.73%); Debt/Equity: 0.1x"
    enrichment = enriched["motley_fool_company_enrichment"]
    assert enrichment["moneyball_scores"]["superscore"] == 71.0
    assert enrichment["moneyball_scores"]["investing_type"] == "Aggressive"
    assert enrichment["growth_metrics"]["3_yr_revenue_growth"] == "5.19%"
    assert enrichment["valuation_metrics"]["price_earnings"] == "329.7x"
    assert enrichment["recent_earnings"]["present"] is True
    assert enrichment["recent_earnings"]["article_title"] == "Synthetic Earnings Headline For Parser Testing"
    assert enrichment["recent_earnings"]["summary"].startswith("Tesla makes synthetic vehicle and energy")
    assert "recent_earnings" in enrichment["sections_found"]


def test_enrich_idea_from_footer_ended_snapshot_does_not_promote_legal_text():
    snapshot = CompanyPageSnapshot(
        requested_url="https://www.fool.com/premium/company/NASDAQ/AMZN/financials/summary",
        resolved_url="https://www.fool.com/premium/company/NASDAQ/AMZN/financials/summary",
        title="AMZN - Summary - Fool IQ",
        text="""
Amazon.com, Inc.
(NASDAQ:AMZN)
Market Cap
$
2.85
T
52 Week Range
$183.85 - $273.88
Volume
2.5M
Avg. Volume
53.2M
Beta
1.51
Next Earnings Date
May 07, 2026
Revenue Growth (CAGR)
Metric
Value
AMZN Recent Earnings - Q4 Fiscal Year 2025
Q4 Earnings
EPS
1.95
Revenue
213.4B
Operating Income
25.0B
Free Cash Flow
14.9B
Upcoming Earnings
EPS Estimate
1.62
Revenue Estimate
177.1B
Announce Date
3/31/2026
View Full Earnings Report
Fool Disclosure
Privacy Policy
Terms and Conditions
Copyright, Trademark and Patent information
""",
        headings=[
            "Amazon.com, Inc.",
            "AMZN Recent Earnings - Q4 Fiscal Year 2025",
            "Q4 Earnings",
            "Upcoming Earnings",
        ],
        tables=[
            {
                "headers": ["Metric", "Value"],
                "rows": [
                    ["Revenue", "$742.78B\n(+14.22%)"],
                    ["EBITDA", "$171.66B\n(+27.17%)"],
                    ["Net Income", "$90.80B\n(+37.69%)"],
                    ["Free Cash Flow", "-$2.47B\n(-111.88%)"],
                    ["Total Debt", "$209.89B\n(+0.73%)"],
                    ["Total Cash", "$143.09B\n(+4.59%)"],
                ],
            }
        ],
        links=[
            {
                "text": "View Full Earnings Report",
                "href": "https://www.fool.com/premium/company/NASDAQ/AMZN/financials/earnings",
            }
        ],
    )

    enriched = enrich_idea_from_company_snapshot(
        {
            "symbol": "AMZN",
            "company_name": "Amazon",
            "idea_source": "motley_fool_company_page",
            "motley_fool_company_url": snapshot.requested_url,
        },
        snapshot,
    )

    earnings = enriched["motley_fool_company_enrichment"]["recent_earnings"]
    assert earnings["metrics"]["eps"] == "1.95"
    assert earnings["metrics"]["revenue"] == "213.4B"
    assert earnings["article_title"] == ""
    assert earnings["summary"] == ""
    assert enriched["business_summary"] == "Amazon"
    assert "Terms and Conditions" not in enriched.get("thesis_summary", "")
    assert "Copyright" not in " ".join(enriched["confirming_signals"])
    assert enriched["motley_fool_company_enrichment"]["market_snapshot"]["market_cap"] == "$2.85T"


def test_company_enrichment_cli_can_enrich_batch_from_snapshot_dir(tmp_path, capsys):
    batch = tmp_path / "batch.json"
    output = tmp_path / "enriched.json"
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    snapshot = _tsla_snapshot()
    batch.write_text(
        json.dumps(
            [
                {
                    "symbol": "TSLA",
                    "company_name": "Tesla",
                    "idea_source": "motley_fool_new_recommendations",
                    "motley_fool_company_url": snapshot.requested_url,
                }
            ]
        ),
        encoding="utf-8",
    )
    (snapshot_dir / "TSLA.json").write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        [
            "--idea-batch",
            str(batch),
            "--snapshot-dir",
            str(snapshot_dir),
            "--output",
            str(output),
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["symbol"] == "TSLA"
    assert payload[0]["motley_fool_company_enrichment"]["resolved_url"] == snapshot.resolved_url
    assert summary["enriched_count"] == 1
    assert summary["skipped_count"] == 0


def test_wait_for_company_content_scrolls_to_lazy_sections():
    class FakeMouse:
        def __init__(self) -> None:
            self.wheels = []

        def wheel(self, x: int, y: int) -> None:
            self.wheels.append((x, y))

    class FakePage:
        def __init__(self) -> None:
            self.mouse = FakeMouse()
            self.waited_for_function = False
            self.timeouts = []

        def wait_for_function(self, script: str, timeout: int) -> None:
            assert "Hidden Gems - Moneyball" in script
            assert timeout == 60000
            self.waited_for_function = True

        def wait_for_timeout(self, timeout: int) -> None:
            self.timeouts.append(timeout)

    page = FakePage()

    _wait_for_company_content(page, timeout_ms=90000)

    assert page.waited_for_function is True
    assert page.mouse.wheels == [(0, 1800)] * 6
    assert page.timeouts == [500] * 6
