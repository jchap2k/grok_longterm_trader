import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_queue_reconciliation import reconcile_research_queue
from longterm.research_queue_reconciliation_cli import build_parser, run_cli


def _idea(symbol: str, *, source: str = "wide_universe") -> dict:
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Corp",
        "idea_source": source,
        "source_notes": [f"Source: {source}"],
        "evidence_brief": f"research_evidence_brief_v1 | {symbol}",
    }


def test_reconcile_research_queue_marks_source_convergence_and_recent_research():
    result = reconcile_research_queue(
        [_idea("MSFT"), _idea("EXEL")],
        comparison_sources={"motley_fool": [_idea("MSFT", source="motley_fool"), _idea("AAPL", source="motley_fool")]},
        recent_symbols={"MSFT"},
        primary_source_label="wide_universe",
    )

    msft = result.rows[0]
    exel = result.rows[1]
    assert result.summary["input_count"] == 2
    assert result.summary["converged_symbol_count"] == 1
    assert result.summary["recent_research_symbol_count"] == 1
    assert msft["source_convergence"]["sources"] == ["motley_fool", "wide_universe"]
    assert msft["source_convergence"]["source_count"] == 2
    assert msft["source_convergence"]["recent_research"] is True
    assert msft["source_convergence"]["suggested_research_mode"] == "update_existing_thesis"
    assert "Source convergence: motley_fool + wide_universe." in msft["source_notes"]
    assert "Source convergence:" in msft["evidence_brief"]
    assert exel["source_convergence"]["sources"] == ["wide_universe"]
    assert exel["source_convergence"]["suggested_research_mode"] == "fresh_research"


def test_reconcile_research_queue_dedupes_primary_rows_preserving_order():
    result = reconcile_research_queue(
        [_idea("EXEL"), _idea("EXEL"), _idea("QLYS")],
        primary_source_label="wide_universe",
    )

    assert [row["symbol"] for row in result.rows] == ["EXEL", "QLYS"]
    assert result.rows[0]["source_convergence"]["duplicate_primary_count"] == 2
    assert result.summary["duplicate_primary_symbol_count"] == 1


def test_research_queue_reconciliation_cli_writes_artifacts_and_batches(tmp_path, capsys):
    queue_path = tmp_path / "selected.json"
    fool_path = tmp_path / "fool.json"
    recent_path = tmp_path / "recent.json"
    output_dir = tmp_path / "preflight"
    queue_path.write_text(json.dumps([_idea("MSFT"), _idea("EXEL"), _idea("QLYS")]), encoding="utf-8")
    fool_path.write_text(json.dumps([_idea("MSFT", source="motley_fool")]), encoding="utf-8")
    recent_path.write_text(json.dumps(["MSFT"]), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--research-queue",
                str(queue_path),
                "--comparison-source",
                f"motley_fool={fool_path}",
                "--recent-symbols-file",
                str(recent_path),
                "--output-dir",
                str(output_dir),
                "--batch-size",
                "2",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    reconciled = json.loads((output_dir / "research_queue_reconciled.json").read_text(encoding="utf-8"))
    first_batch = json.loads((output_dir / "committee_batches" / "research-batch-001.json").read_text(encoding="utf-8"))
    manifest = json.loads((output_dir / "research_campaign_manifest.json").read_text(encoding="utf-8"))
    report = (output_dir / "research_queue_reconciliation_report.md").read_text(encoding="utf-8")
    assert code == 0
    assert printed["reconciled_count"] == 3
    assert printed["converged_symbol_count"] == 1
    assert reconciled[0]["symbol"] == "MSFT"
    assert first_batch[0]["symbol"] == "MSFT"
    assert manifest["batch_count"] == 2
    assert "MSFT" in report
