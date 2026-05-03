import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.extended_universe_scan import (
    build_python_first_pass_markdown,
    run_python_first_pass_scan,
)
from longterm.extended_universe_scan_cli import build_parser, run_cli


def _metrics(
    symbol: str,
    *,
    revenue_growth: str,
    ebitda_growth: str,
    eps_growth: str,
    fcf_growth: str,
    pe: str,
    ev_ebitda: str,
    p_fcf: str,
    peg: str,
    gross_margin: str,
    operating_margin: str,
    fcf_margin: str,
    roe: str,
    debt_equity: str,
) -> dict:
    return {
        "symbol": symbol,
        "source_type": "python_fundamental_metrics",
        "revenue_growth_cagr": {
            "3_yr_revenue_growth": revenue_growth,
            "3_yr_ebitda_growth": ebitda_growth,
            "3_yr_eps_growth": eps_growth,
            "3_yr_fcf_per_share_growth": fcf_growth,
        },
        "valuation_ttm": {
            "price_earnings": pe,
            "ev_ebitda": ev_ebitda,
            "price_free_cash_flow": p_fcf,
            "price_book_value": "8.0x",
            "price_earnings_growth_5yr": peg,
        },
        "profitability_ttm": {
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "free_cash_flow_margin": fcf_margin,
            "return_on_equity": roe,
            "debt_equity": debt_equity,
        },
        "financials_ttm": {"total_cash": "$20.00B", "total_debt": "$10.00B"},
        "warnings": [],
    }


def _strong(symbol: str) -> dict:
    return _metrics(
        symbol,
        revenue_growth="22.00%",
        ebitda_growth="24.00%",
        eps_growth="18.00%",
        fcf_growth="17.00%",
        pe="24.0x",
        ev_ebitda="17.0x",
        p_fcf="22.0x",
        peg="1.3x",
        gross_margin="62.00%",
        operating_margin="28.00%",
        fcf_margin="20.00%",
        roe="31.00%",
        debt_equity="0.2x",
    )


def _okay(symbol: str) -> dict:
    return _metrics(
        symbol,
        revenue_growth="9.00%",
        ebitda_growth="8.00%",
        eps_growth="6.00%",
        fcf_growth="5.00%",
        pe="34.0x",
        ev_ebitda="24.0x",
        p_fcf="32.0x",
        peg="2.4x",
        gross_margin="42.00%",
        operating_margin="13.00%",
        fcf_margin="9.00%",
        roe="14.00%",
        debt_equity="0.7x",
    )


def _weak(symbol: str) -> dict:
    return _metrics(
        symbol,
        revenue_growth="1.00%",
        ebitda_growth="-8.00%",
        eps_growth="-12.00%",
        fcf_growth="-10.00%",
        pe="95.0x",
        ev_ebitda="70.0x",
        p_fcf="85.0x",
        peg="7.0x",
        gross_margin="18.00%",
        operating_margin="3.00%",
        fcf_margin="-2.00%",
        roe="2.00%",
        debt_equity="1.7x",
    )


def test_python_first_pass_scan_advances_top_percent_not_hard_threshold():
    ideas = [
        {"symbol": "WEAK1", "fundamental_metrics": _weak("WEAK1")},
        {"symbol": "TOP1", "fundamental_metrics": _strong("TOP1")},
        {"symbol": "MID1", "fundamental_metrics": _okay("MID1")},
        {"symbol": "TOP2", "fundamental_metrics": _strong("TOP2")},
        {"symbol": "WEAK2", "fundamental_metrics": _weak("WEAK2")},
    ]

    result = run_python_first_pass_scan(ideas, top_percent=40)

    assert result.summary["mode"] == "extended_universe_python_first_pass_scan"
    assert result.summary["top_percent"] == 40.0
    assert result.summary["rank_score_basis"] == "70pct_moneyball_30pct_quant"
    assert result.summary["passed_count"] == 2
    assert [idea["symbol"] for idea in result.passed_ideas] == ["TOP1", "TOP2"]
    assert result.passed_ideas[0]["python_first_pass_scan"]["decision"] == "advance_to_enrichment"
    assert result.deferred_ideas[-1]["python_first_pass_scan"]["decision"] == "defer_after_python_scan"


def test_python_first_pass_scan_exposes_moneyball_quant_and_rank_scores():
    ideas = [
        {"symbol": "TOP1", "fundamental_metrics": _strong("TOP1")},
        {"symbol": "MID1", "fundamental_metrics": _okay("MID1")},
    ]

    result = run_python_first_pass_scan(ideas, top_percent=50)
    scan = result.passed_ideas[0]["python_first_pass_scan"]

    assert scan["score_basis"] == "70pct_moneyball_30pct_quant"
    assert scan["moneyball_score"] == result.passed_ideas[0]["quality_growth_scorecard"]["superscore"]
    assert scan["quant_score"] > 0
    assert scan["rank_score"] == scan["score"]
    assert "Moneyball" in scan["rank_reason"]
    assert "Quant" in scan["rank_reason"]


def test_python_first_pass_scan_keeps_at_least_one_candidate_from_weak_market():
    ideas = [
        {"symbol": "WEAK1", "fundamental_metrics": _weak("WEAK1")},
        {"symbol": "WEAK2", "fundamental_metrics": _weak("WEAK2")},
        {"symbol": "WEAK3", "fundamental_metrics": _weak("WEAK3")},
    ]

    result = run_python_first_pass_scan(ideas, top_percent=10, min_pass_count=1)

    assert result.summary["passed_count"] == 1
    assert result.passed_ideas[0]["python_first_pass_scan"]["rank"] == 1
    assert "relative top 10.0%" in result.passed_ideas[0]["python_first_pass_scan"]["reason"]


def test_python_first_pass_scan_reports_fundamentals_coverage():
    ideas = [
        {"symbol": "TOP1", "fundamental_metrics": _strong("TOP1")},
        {"symbol": "MISSING1"},
        {"symbol": "MID1", "fundamental_metrics": _okay("MID1")},
    ]

    result = run_python_first_pass_scan(ideas, top_percent=50)

    assert result.summary["fundamentals_coverage_count"] == 2
    assert result.summary["fundamentals_missing_count"] == 1
    assert result.summary["fundamentals_coverage_percent"] == 66.67
    assert result.summary["fundamentals_missing_symbols"] == ["MISSING1"]


def test_python_first_pass_scan_marks_enrichment_not_ready_when_coverage_is_low():
    ideas = [
        {"symbol": "TOP1", "fundamental_metrics": _strong("TOP1")},
        {"symbol": "MISSING1"},
        {"symbol": "MISSING2"},
    ]

    result = run_python_first_pass_scan(
        ideas,
        top_percent=50,
        min_coverage_percent_for_enrichment=80,
    )

    assert result.summary["ready_for_expensive_enrichment"] is False
    assert result.summary["scan_recommendation"] == "continue_fundamentals_cache_fill"
    assert "below required 80.0%" in result.summary["readiness_reason"]


def test_python_first_pass_scan_marks_enrichment_ready_when_coverage_is_high():
    ideas = [
        {"symbol": "TOP1", "fundamental_metrics": _strong("TOP1")},
        {"symbol": "MID1", "fundamental_metrics": _okay("MID1")},
        {"symbol": "WEAK1", "fundamental_metrics": _weak("WEAK1")},
    ]

    result = run_python_first_pass_scan(
        ideas,
        top_percent=50,
        min_coverage_percent_for_enrichment=80,
    )

    assert result.summary["ready_for_expensive_enrichment"] is True
    assert result.summary["scan_recommendation"] == "run_evidence_enrichment_on_passed"
    assert "meets required 80.0%" in result.summary["readiness_reason"]


def test_python_first_pass_markdown_summarizes_coverage_and_candidates():
    result = run_python_first_pass_scan(
        [
            {"symbol": "TOP1", "fundamental_metrics": _strong("TOP1")},
            {"symbol": "MISSING1"},
            {"symbol": "MID1", "fundamental_metrics": _okay("MID1")},
        ],
        top_percent=50,
    )
    summary = dict(result.summary)
    summary["fundamentals_fetch_errors"] = [{"symbol": "MISSING1", "error": "provider timeout"}]
    summary["fundamentals_fetch_skipped_symbols"] = ["LATER1"]

    markdown = build_python_first_pass_markdown(
        result.passed_ideas,
        result.deferred_ideas,
        summary,
        title="Extended Universe First Pass",
    )

    assert "# Extended Universe First Pass" in markdown
    assert "Readiness: not ready" in markdown
    assert "Coverage: 2/3 (66.67%)" in markdown
    assert "| TOP1 |" in markdown
    assert "Moneyball" in markdown
    assert "Quant" in markdown
    assert "| MISSING1 |" in markdown
    assert "provider timeout" in markdown
    assert "LATER1" in markdown
    assert "longterm_evidence_enrichment_pipeline.py" in markdown


def test_extended_universe_scan_cli_writes_pass_defer_and_summary(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    snapshots_path = tmp_path / "fundamentals.json"
    passed_output = tmp_path / "passed.json"
    deferred_output = tmp_path / "deferred.json"
    summary_output = tmp_path / "summary.json"
    ideas_path.write_text(
        json.dumps(
            [
                {"symbol": "TOP1", "company_name": "Top One"},
                {"symbol": "WEAK1", "company_name": "Weak One"},
                {"symbol": "MID1", "company_name": "Middle One"},
            ]
        ),
        encoding="utf-8",
    )
    snapshots_path.write_text(
        json.dumps({"TOP1": _strong("TOP1"), "WEAK1": _weak("WEAK1"), "MID1": _okay("MID1")}),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas_path),
                "--snapshot-file",
                str(snapshots_path),
                "--top-percent",
                "34",
                "--passed-output",
                str(passed_output),
                "--deferred-output",
                str(deferred_output),
                "--summary-output",
                str(summary_output),
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    passed = json.loads(passed_output.read_text(encoding="utf-8"))
    deferred = json.loads(deferred_output.read_text(encoding="utf-8"))
    summary = json.loads(summary_output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["passed_count"] == 2
    assert summary["passed_output"] == str(passed_output)
    assert [idea["symbol"] for idea in passed] == ["TOP1", "MID1"]
    assert [idea["symbol"] for idea in deferred] == ["WEAK1"]


def test_extended_universe_scan_cli_reuses_and_updates_provider_cache(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    cache_path = tmp_path / "fundamentals_cache.json"
    passed_output = tmp_path / "passed.json"
    ideas_path.write_text(
        json.dumps(
            [
                {"symbol": "TOP1", "company_name": "Top One"},
                {"symbol": "MID1", "company_name": "Middle One"},
            ]
        ),
        encoding="utf-8",
    )
    cache_path.write_text(json.dumps({"TOP1": _strong("TOP1")}), encoding="utf-8")
    fetched = []

    def fake_fetch(symbol: str) -> dict:
        fetched.append(symbol)
        return _okay(symbol)

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas_path),
                "--provider",
                "yfinance",
                "--fundamentals-cache",
                str(cache_path),
                "--top-percent",
                "100",
                "--passed-output",
                str(passed_output),
            ]
        ),
        fetch_metrics=fake_fetch,
    )

    printed = json.loads(capsys.readouterr().out)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert code == 0
    assert fetched == ["MID1"]
    assert sorted(cache) == ["MID1", "TOP1"]
    assert printed["fundamentals_cache_hits"] == 1
    assert printed["fundamentals_cache_fetches"] == 1


def test_extended_universe_scan_cli_can_fetch_only_next_missing_chunk(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    cache_path = tmp_path / "fundamentals_cache.json"
    passed_output = tmp_path / "passed.json"
    ideas_path.write_text(
        json.dumps(
            [
                {"symbol": "TOP1", "company_name": "Top One"},
                {"symbol": "MID1", "company_name": "Middle One"},
                {"symbol": "WEAK1", "company_name": "Weak One"},
                {"symbol": "WEAK2", "company_name": "Weak Two"},
            ]
        ),
        encoding="utf-8",
    )
    cache_path.write_text(json.dumps({"TOP1": _strong("TOP1")}), encoding="utf-8")
    fetched = []

    def fake_fetch(symbol: str) -> dict:
        fetched.append(symbol)
        return _okay(symbol)

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas_path),
                "--provider",
                "yfinance",
                "--fundamentals-cache",
                str(cache_path),
                "--fetch-limit",
                "2",
                "--top-percent",
                "50",
                "--passed-output",
                str(passed_output),
            ]
        ),
        fetch_metrics=fake_fetch,
    )

    printed = json.loads(capsys.readouterr().out)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert code == 0
    assert fetched == ["MID1", "WEAK1"]
    assert sorted(cache) == ["MID1", "TOP1", "WEAK1"]
    assert printed["fundamentals_cache_fetches"] == 2
    assert printed["fundamentals_fetch_skipped_count"] == 1
    assert printed["fundamentals_fetch_skipped_symbols"] == ["WEAK2"]


def test_extended_universe_scan_cli_writes_markdown_report(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    snapshots_path = tmp_path / "fundamentals.json"
    passed_output = tmp_path / "passed.json"
    markdown_output = tmp_path / "scan.md"
    ideas_path.write_text(
        json.dumps(
            [
                {"symbol": "TOP1", "company_name": "Top One"},
                {"symbol": "WEAK1", "company_name": "Weak One"},
            ]
        ),
        encoding="utf-8",
    )
    snapshots_path.write_text(
        json.dumps({"TOP1": _strong("TOP1"), "WEAK1": _weak("WEAK1")}),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas_path),
                "--snapshot-file",
                str(snapshots_path),
                "--top-percent",
                "50",
                "--passed-output",
                str(passed_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    markdown = markdown_output.read_text(encoding="utf-8")
    assert code == 0
    assert printed["markdown_output"] == str(markdown_output)
    assert "# Extended Universe Python First Pass" in markdown
    assert "| TOP1 |" in markdown
    assert "| WEAK1 |" in markdown


def test_extended_universe_scan_cli_records_fetch_errors_and_continues(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    cache_path = tmp_path / "fundamentals_cache.json"
    passed_output = tmp_path / "passed.json"
    deferred_output = tmp_path / "deferred.json"
    ideas_path.write_text(
        json.dumps(
            [
                {"symbol": "TOP1", "company_name": "Top One"},
                {"symbol": "BAD1", "company_name": "Bad One"},
            ]
        ),
        encoding="utf-8",
    )

    def fake_fetch(symbol: str) -> dict:
        if symbol == "BAD1":
            raise RuntimeError("provider timeout")
        return _strong(symbol)

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas_path),
                "--provider",
                "yfinance",
                "--fundamentals-cache",
                str(cache_path),
                "--top-percent",
                "50",
                "--passed-output",
                str(passed_output),
                "--deferred-output",
                str(deferred_output),
            ]
        ),
        fetch_metrics=fake_fetch,
    )

    printed = json.loads(capsys.readouterr().out)
    deferred = json.loads(deferred_output.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["fundamentals_cache_fetches"] == 1
    assert printed["fundamentals_fetch_error_count"] == 1
    assert printed["fundamentals_fetch_errors"][0]["symbol"] == "BAD1"
    assert sorted(cache) == ["TOP1"]
    assert deferred[0]["symbol"] == "BAD1"
