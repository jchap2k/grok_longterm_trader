import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_selection import select_research_queue
from longterm.research_selection_cli import build_parser, run_cli


def _idea(
    symbol: str,
    *,
    superscore: float,
    quality: float = 80,
    growth: float = 80,
    valuation: float = 60,
    safety: float = 70,
    news_count: int = 2,
    earnings_confidence: float = 0.8,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Corp",
        "business_summary": f"{symbol} makes useful long-term products.",
        "quality_growth_scorecard": {
            "superscore": superscore,
            "quality_score": quality,
            "growth_score": growth,
            "valuation_score": valuation,
            "safety_score": safety,
            "score_reasons": ["quality growth", "valuation discipline"],
        },
        "relevant_news": [
            {
                "title": f"{symbol} wins contract {index}",
                "url": f"https://example.test/{symbol.lower()}/{index}",
                "impact_category": "Major Contract - High" if index == 0 else "Product/Tech - Medium",
                "relevance_score": 0.9,
            }
            for index in range(news_count)
        ],
        "latest_earnings": {
            "quarter": "Q1 2026",
            "confidence": earnings_confidence,
            "summary": "Execution remains healthy.",
        },
        "evidence_brief": (
            f"research_evidence_brief_v1 | {symbol}\n"
            "Scorecard: quality growth with valuation discipline.\n"
            "Article evidence: contract win and earnings context."
        ),
        "enrichment_warnings": warnings or [],
    }


def test_select_research_queue_ranks_evidence_and_skips_protected_symbol():
    result = select_research_queue(
        [
            _idea("WEAK", superscore=42, quality=40, growth=45, news_count=0, warnings=["thin evidence"]),
            _idea("STRONG", superscore=88, quality=92, growth=86, news_count=3),
            _idea("FXAIX", superscore=95, quality=95, growth=95, news_count=3),
        ],
        campaign_id="campaign-a",
        protected_symbols={"FXAIX"},
        top_percent=50,
        min_count=1,
        max_count=2,
    )

    assert [row["symbol"] for row in result.selected] == ["STRONG"]
    assert result.summary["skipped_protected_symbols"] == ["FXAIX"]
    assert result.summary["formula_version"] == "research_selection_v1"
    assert result.selected[0]["research_selection"]["selected_rank"] == 1
    assert result.selected[0]["research_selection"]["selected_for_committee"] is True
    assert result.selected[0]["research_selection"]["research_selection_id"]
    assert "research_selection_id=" in "\n".join(result.selected[0]["source_notes"])
    assert result.deferred[0]["symbol"] == "WEAK"
    assert "evidence warning penalty" in " ".join(result.deferred[0]["research_selection"]["defer_reasons"])


def test_select_research_queue_is_deterministic_and_deprioritizes_current_or_recent_names():
    ideas = [
        _idea("CURR", superscore=80),
        _idea("NEW", superscore=80),
        _idea("RECENT", superscore=80),
    ]

    first = select_research_queue(
        ideas,
        campaign_id="campaign-b",
        current_symbols={"CURR"},
        recent_research_symbols={"RECENT"},
        top_percent=100,
        min_count=1,
        max_count=3,
    )
    second = select_research_queue(
        ideas,
        campaign_id="campaign-b",
        current_symbols={"CURR"},
        recent_research_symbols={"RECENT"},
        top_percent=100,
        min_count=1,
        max_count=3,
    )

    assert [row["symbol"] for row in first.selected] == ["NEW", "RECENT", "CURR"]
    assert [row["research_selection"]["research_selection_id"] for row in first.selected] == [
        row["research_selection"]["research_selection_id"] for row in second.selected
    ]
    assert first.selected[1]["research_selection"]["portfolio_context"] == "recently_researched"
    assert first.selected[2]["research_selection"]["portfolio_context"] == "current_holding"


def test_select_research_queue_counts_generator_input():
    result = select_research_queue(
        (_idea(symbol, superscore=80) for symbol in ["AAA", "BBB"]),
        campaign_id="generator",
        top_percent=100,
        min_count=1,
        max_count=2,
    )

    assert result.summary["input_count"] == 2
    assert result.summary["selected_count"] == 2


def test_research_selection_cli_writes_queue_artifacts(tmp_path, capsys):
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "selection"
    evidence_path.write_text(
        json.dumps([_idea("AAA", superscore=88), _idea("BBB", superscore=50, warnings=["thin"])]),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--evidence-file",
                str(evidence_path),
                "--output-dir",
                str(output_dir),
                "--campaign-id",
                "campaign-cli",
                "--top-percent",
                "50",
                "--min-count",
                "1",
                "--max-count",
                "1",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    selected = json.loads((output_dir / "research_queue_selected.json").read_text(encoding="utf-8"))
    selected_lines = (output_dir / "research_queue_selected.jsonl").read_text(encoding="utf-8").splitlines()
    summary = json.loads((output_dir / "research_queue_summary.json").read_text(encoding="utf-8"))
    report = (output_dir / "research_queue_report.md").read_text(encoding="utf-8")
    assert code == 0
    assert printed["selected_count"] == 1
    assert selected[0]["symbol"] == "AAA"
    assert len(selected_lines) == 1
    assert summary["selected_output"].endswith("research_queue_selected.json")
    assert "Research Selection Queue" in report
    assert "AAA" in report
