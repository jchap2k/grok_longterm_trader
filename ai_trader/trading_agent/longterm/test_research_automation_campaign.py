import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_automation_campaign_cli import build_parser, run_cli


def _metrics(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "source_type": "python_fundamental_metrics",
        "revenue_growth_cagr": {
            "3_yr_revenue_growth": "22.00%",
            "3_yr_ebitda_growth": "24.00%",
            "3_yr_eps_growth": "18.00%",
            "3_yr_fcf_per_share_growth": "17.00%",
        },
        "valuation_ttm": {
            "price_earnings": "24.0x",
            "ev_ebitda": "17.0x",
            "price_free_cash_flow": "22.0x",
            "price_book_value": "8.0x",
            "price_earnings_growth_5yr": "1.3x",
        },
        "profitability_ttm": {
            "gross_margin": "62.00%",
            "operating_margin": "28.00%",
            "free_cash_flow_margin": "20.00%",
            "return_on_equity": "31.00%",
            "debt_equity": "0.2x",
        },
        "financials_ttm": {"total_cash": "$20.00B", "total_debt": "$10.00B"},
        "warnings": [],
    }


def test_research_automation_campaign_reaches_scan_ready(tmp_path, monkeypatch, capsys):
    import longterm.research_automation_campaign_cli as cli

    campaign_dir = tmp_path / "campaign"

    def fake_loader(url, *, source):
        assert url == "https://example.test/nasdaq.txt"
        return [
            {"symbol": "AAA", "company_name": "AAA Corp", "source": source},
            {"symbol": "BBB", "company_name": "BBB Corp", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)

    code = run_cli(
        build_parser().parse_args(
            [
                "--source-url",
                "https://example.test/nasdaq.txt",
                "--source",
                "nasdaq_listed",
                "--campaign-dir",
                str(campaign_dir),
                "--watchlist-limit",
                "2",
                "--run-until",
                "scan_ready",
                "--max-fundamental-fetches",
                "2",
            ]
        ),
        fetch_metrics=_metrics,
    )

    printed = json.loads(capsys.readouterr().out)
    state = json.loads((campaign_dir / "campaign_state.json").read_text(encoding="utf-8"))
    events = (campaign_dir / "campaign_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert code == 0
    assert printed["stage"] == "scan_ready"
    assert state["stage"] == "scan_ready"
    assert state["scan"]["ready_for_expensive_enrichment"] is True
    assert (campaign_dir / "python_scan_passed.json").exists()
    assert len(events) >= 2


def test_research_automation_campaign_reaches_evidence_ready_with_skip_grok(tmp_path, monkeypatch, capsys):
    import longterm.research_automation_campaign_cli as cli

    campaign_dir = tmp_path / "campaign"

    def fake_loader(url, *, source):
        return [
            {"symbol": "AAA", "company_name": "AAA Corp", "source": source},
            {"symbol": "BBB", "company_name": "BBB Corp", "source": source},
            {"symbol": "CCC", "company_name": "CCC Corp", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)

    code = run_cli(
        build_parser().parse_args(
            [
                "--source-url",
                "https://example.test/nasdaq.txt",
                "--source",
                "nasdaq_listed",
                "--campaign-dir",
                str(campaign_dir),
                "--watchlist-limit",
                "3",
                "--run-until",
                "evidence_ready",
                "--max-fundamental-fetches",
                "3",
                "--evidence-batch-size",
                "2",
                "--max-evidence-batches",
                "2",
                "--skip-grok",
            ]
        ),
        fetch_metrics=_metrics,
    )

    printed = json.loads(capsys.readouterr().out)
    state = json.loads((campaign_dir / "campaign_state.json").read_text(encoding="utf-8"))
    enriched = json.loads((campaign_dir / "evidence_campaign" / "campaign_enriched.json").read_text(encoding="utf-8"))
    assert code == 0
    assert printed["stage"] == "evidence_ready"
    assert state["stage"] == "evidence_ready"
    assert state["evidence"]["enriched_count"] == 3
    assert [idea["symbol"] for idea in enriched] == ["AAA", "BBB", "CCC"]


def test_research_automation_campaign_forwards_evidence_campaign_pause(tmp_path, monkeypatch, capsys):
    import longterm.research_automation_campaign_cli as cli

    campaign_dir = tmp_path / "campaign"

    def fake_loader(url, *, source):
        return [
            {"symbol": "AAA", "company_name": "AAA Corp", "source": source},
            {"symbol": "BBB", "company_name": "BBB Corp", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)

    code = run_cli(
        build_parser().parse_args(
            [
                "--source-url",
                "https://example.test/nasdaq.txt",
                "--source",
                "nasdaq_listed",
                "--campaign-dir",
                str(campaign_dir),
                "--watchlist-limit",
                "2",
                "--run-until",
                "evidence_ready",
                "--max-fundamental-fetches",
                "2",
                "--evidence-batch-size",
                "2",
                "--campaign-batch-pause-seconds",
                "69",
                "--skip-grok",
            ]
        ),
        fetch_metrics=_metrics,
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["stage"] == "evidence_ready"
    assert printed["evidence"]["campaign_batch_pause_seconds"] == 69.0


def test_research_automation_campaign_can_forward_perplexity_research_mode(tmp_path, monkeypatch, capsys):
    import longterm.research_automation_campaign_cli as cli

    campaign_dir = tmp_path / "campaign"
    captured = {}

    def fake_loader(url, *, source):
        return [
            {"symbol": "AAA", "company_name": "AAA Corp", "source": source},
            {"symbol": "BBB", "company_name": "BBB Corp", "source": source},
        ]

    def fake_evidence_campaign(args):
        captured["perplexity_research"] = args.perplexity_research
        captured["perplexity_search_context_size"] = args.perplexity_search_context_size
        captured["perplexity_credits_purchased_to_date"] = args.perplexity_credits_purchased_to_date
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "campaign_enriched.json").write_text(
            json.dumps(
                [
                    {"symbol": "AAA", "evidence_brief": "brief"},
                    {"symbol": "BBB", "evidence_brief": "brief"},
                ]
            ),
            encoding="utf-8",
        )
        (output_dir / "campaign_summary.json").write_text(
            json.dumps(
                {
                    "enriched_count": 2,
                    "research_model_usage": {
                        "provider": "perplexity",
                        "model": "sonar",
                        "estimated_total_cost_usd": 0.02,
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)
    monkeypatch.setattr(cli, "run_evidence_campaign_cli", fake_evidence_campaign)

    code = run_cli(
        build_parser().parse_args(
            [
                "--source-url",
                "https://example.test/nasdaq.txt",
                "--source",
                "nasdaq_listed",
                "--campaign-dir",
                str(campaign_dir),
                "--watchlist-limit",
                "2",
                "--run-until",
                "evidence_ready",
                "--max-fundamental-fetches",
                "2",
                "--evidence-batch-size",
                "2",
                "--perplexity-research",
                "--perplexity-search-context-size",
                "low",
                "--perplexity-credits-purchased-to-date",
                "12",
            ]
        ),
        fetch_metrics=_metrics,
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert captured["perplexity_research"] is True
    assert captured["perplexity_search_context_size"] == "low"
    assert captured["perplexity_credits_purchased_to_date"] == 12.0
    assert printed["stage"] == "evidence_ready"
    assert printed["evidence"]["research_model_usage"]["provider"] == "perplexity"


def test_research_automation_campaign_rejects_conflicting_paid_research_modes(tmp_path):
    parser = build_parser()
    args = parser.parse_args(
        [
            "--source-url",
            "https://example.test/nasdaq.txt",
            "--source",
            "nasdaq_listed",
            "--campaign-dir",
            str(tmp_path / "campaign"),
            "--run-until",
            "evidence_ready",
            "--skip-grok",
            "--perplexity-research",
        ]
    )

    try:
        run_cli(args, fetch_metrics=_metrics)
    except ValueError as exc:
        assert "research provider" in str(exc)
    else:
        raise AssertionError("Expected conflicting research provider modes to fail closed.")


def test_research_automation_campaign_reaches_research_queue_ready(tmp_path, monkeypatch, capsys):
    import longterm.research_automation_campaign_cli as cli

    campaign_dir = tmp_path / "campaign"

    def fake_loader(url, *, source):
        return [
            {"symbol": "AAA", "company_name": "AAA Corp", "source": source},
            {"symbol": "BBB", "company_name": "BBB Corp", "source": source},
            {"symbol": "CCC", "company_name": "CCC Corp", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)

    code = run_cli(
        build_parser().parse_args(
            [
                "--source-url",
                "https://example.test/nasdaq.txt",
                "--source",
                "nasdaq_listed",
                "--campaign-dir",
                str(campaign_dir),
                "--watchlist-limit",
                "3",
                "--run-until",
                "research_queue_ready",
                "--max-fundamental-fetches",
                "3",
                "--evidence-batch-size",
                "3",
                "--selection-top-percent",
                "50",
                "--selection-min-count",
                "1",
                "--selection-max-count",
                "2",
                "--skip-grok",
            ]
        ),
        fetch_metrics=_metrics,
    )

    printed = json.loads(capsys.readouterr().out)
    selected = json.loads(
        (campaign_dir / "research_selection" / "research_queue_selected.json").read_text(encoding="utf-8")
    )
    assert code == 0
    assert printed["stage"] == "research_queue_ready"
    assert printed["research_selection"]["selected_count"] == 2
    assert len(selected) == 2
    assert selected[0]["research_selection"]["research_selection_id"].startswith("rs-")


def test_research_automation_campaign_resume_continues_partial_scan(tmp_path, monkeypatch, capsys):
    import longterm.research_automation_campaign_cli as cli

    campaign_dir = tmp_path / "campaign"

    def fake_loader(url, *, source):
        return [
            {"symbol": "AAA", "company_name": "AAA Corp", "source": source},
            {"symbol": "BBB", "company_name": "BBB Corp", "source": source},
            {"symbol": "CCC", "company_name": "CCC Corp", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)
    parser = build_parser()
    first = parser.parse_args(
        [
            "--source-url",
            "https://example.test/nasdaq.txt",
            "--source",
            "nasdaq_listed",
            "--campaign-dir",
            str(campaign_dir),
            "--watchlist-limit",
            "3",
            "--run-until",
            "scan_ready",
            "--max-fundamental-fetches",
            "1",
        ]
    )
    assert run_cli(first, fetch_metrics=_metrics) == 0
    capsys.readouterr()

    second = parser.parse_args(
        [
            "--source-url",
            "https://example.test/nasdaq.txt",
            "--source",
            "nasdaq_listed",
            "--campaign-dir",
            str(campaign_dir),
            "--resume",
            "--watchlist-limit",
            "3",
            "--run-until",
            "scan_ready",
            "--max-fundamental-fetches",
            "3",
        ]
    )
    assert run_cli(second, fetch_metrics=_metrics) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["stage"] == "scan_ready"
    assert printed["scan"]["fundamentals_coverage_count"] == 3
