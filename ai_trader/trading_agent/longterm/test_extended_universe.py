import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.extended_universe import prepare_extended_universe
from longterm.extended_universe_cli import build_parser, run_cli


def test_prepare_extended_universe_exports_watchlist_ideas_and_batches():
    candidates = [
        {"symbol": "AAPL", "company_name": "Apple", "source": "nasdaq_listed"},
        {"symbol": "MSFT", "company_name": "Microsoft", "source": "nasdaq_listed"},
        {"symbol": "NVDA", "company_name": "Nvidia", "source": "nasdaq_listed"},
    ]

    result = prepare_extended_universe(
        candidates,
        source="nasdaq_listed",
        watchlist_limit=2,
        batch_size=1,
    )

    assert result.summary["mode"] == "extended_universe_prepare"
    assert result.summary["source"] == "nasdaq_listed"
    assert result.summary["watchlist_count"] == 3
    assert result.summary["watchlist_ideas_count"] == 2
    assert result.summary["batch_count"] == 2
    assert [idea["symbol"] for idea in result.watchlist_ideas] == ["AAPL", "MSFT"]
    assert result.batches[0]["batch_id"] == "research-batch-001"
    assert result.batches[0]["ideas"][0]["symbol"] == "AAPL"
    assert "longterm_evidence_enrichment_pipeline.py" in result.summary["next_enrichment_command"]


def test_extended_universe_cli_writes_ideas_batches_and_summary(tmp_path, monkeypatch, capsys):
    import longterm.extended_universe_cli as cli

    ideas_output = tmp_path / "ideas.json"
    summary_output = tmp_path / "summary.json"
    batches_dir = tmp_path / "batches"

    def fake_loader(url, *, source):
        assert url == "https://example.test/nasdaqlisted.txt"
        assert source == "nasdaq_listed"
        return [
            {"symbol": "AAPL", "company_name": "Apple", "source": source},
            {"symbol": "MSFT", "company_name": "Microsoft", "source": source},
            {"symbol": "NVDA", "company_name": "Nvidia", "source": source},
        ]

    monkeypatch.setattr(cli, "load_candidate_source_url", fake_loader)
    args = build_parser().parse_args(
        [
            "--source-url",
            "https://example.test/nasdaqlisted.txt",
            "--source",
            "nasdaq_listed",
            "--watchlist-limit",
            "2",
            "--batch-size",
            "1",
            "--ideas-output",
            str(ideas_output),
            "--batches-output-dir",
            str(batches_dir),
            "--summary-output",
            str(summary_output),
        ]
    )

    exit_code = run_cli(args)

    printed = json.loads(capsys.readouterr().out)
    ideas = json.loads(ideas_output.read_text(encoding="utf-8"))
    saved_summary = json.loads(summary_output.read_text(encoding="utf-8"))
    batch_files = sorted(path.name for path in batches_dir.glob("*.json"))
    assert exit_code == 0
    assert printed["watchlist_ideas_count"] == 2
    assert saved_summary["batch_count"] == 2
    assert [idea["symbol"] for idea in ideas] == ["AAPL", "MSFT"]
    assert batch_files == ["research-batch-001.json", "research-batch-002.json"]
