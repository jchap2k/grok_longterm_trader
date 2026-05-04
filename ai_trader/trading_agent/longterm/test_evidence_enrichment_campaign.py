import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.evidence_enrichment_campaign_cli import build_parser, run_cli


def _idea(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Corp",
        "idea_source": "extended_universe",
        "business_summary": f"{symbol} makes useful products.",
    }


def _fundamentals(symbols: list[str]) -> dict:
    return {
        symbol: {
            "revenue_growth_cagr": {"3_yr_revenue_growth": "20.00%"},
            "valuation_ttm": {"price_earnings": "25.0x"},
            "profitability_ttm": {"gross_margin": "60.00%", "debt_equity": "0.2x"},
            "financials_ttm": {"revenue": "$10.00B"},
        }
        for symbol in symbols
    }


def test_evidence_enrichment_campaign_writes_batches_and_combined_outputs(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    fundamentals_path = tmp_path / "fundamentals.json"
    output_dir = tmp_path / "campaign"
    symbols = ["AAA", "BBB", "CCC"]
    ideas_path.write_text(json.dumps([_idea(symbol) for symbol in symbols]), encoding="utf-8")
    fundamentals_path.write_text(json.dumps(_fundamentals(symbols)), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas_path),
                "--fundamentals-snapshot-file",
                str(fundamentals_path),
                "--skip-grok",
                "--batch-size",
                "2",
                "--output-dir",
                str(output_dir),
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    combined = json.loads((output_dir / "campaign_enriched.json").read_text(encoding="utf-8"))
    combined_lines = [
        json.loads(line)
        for line in (output_dir / "campaign_enriched.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    batch_summaries = sorted((output_dir / "batches").glob("batch_*_summary.json"))
    assert code == 0
    assert printed["input_count"] == 3
    assert printed["completed_batch_count"] == 2
    assert printed["enriched_count"] == 3
    assert [idea["symbol"] for idea in combined] == symbols
    assert [idea["symbol"] for idea in combined_lines] == symbols
    assert len(batch_summaries) == 2
    assert printed["combined_output"].endswith("campaign_enriched.json")
    assert printed["combined_jsonl_output"].endswith("campaign_enriched.jsonl")


def test_evidence_enrichment_campaign_resume_skips_completed_batches(tmp_path, capsys):
    ideas_path = tmp_path / "ideas.json"
    fundamentals_path = tmp_path / "fundamentals.json"
    output_dir = tmp_path / "campaign"
    symbols = ["AAA", "BBB", "CCC"]
    ideas_path.write_text(json.dumps([_idea(symbol) for symbol in symbols]), encoding="utf-8")
    fundamentals_path.write_text(json.dumps(_fundamentals(symbols)), encoding="utf-8")

    first_args = [
        "--idea-batch",
        str(ideas_path),
        "--fundamentals-snapshot-file",
        str(fundamentals_path),
        "--skip-grok",
        "--batch-size",
        "2",
        "--max-batches",
        "1",
        "--output-dir",
        str(output_dir),
    ]
    assert run_cli(build_parser().parse_args(first_args)) == 0
    capsys.readouterr()

    second_args = [
        "--idea-batch",
        str(ideas_path),
        "--fundamentals-snapshot-file",
        str(fundamentals_path),
        "--skip-grok",
        "--batch-size",
        "2",
        "--resume",
        "--output-dir",
        str(output_dir),
    ]
    code = run_cli(build_parser().parse_args(second_args))

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["completed_batch_count"] == 2
    assert printed["skipped_batch_count"] == 1
    assert printed["enriched_count"] == 3
