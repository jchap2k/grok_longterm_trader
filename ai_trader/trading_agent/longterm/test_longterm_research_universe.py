import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_universe import build_research_universe_batches
from longterm.research_universe_cli import build_parser, run_cli


def test_build_research_universe_batches_chunks_research_ideas():
    ideas = [
        {"symbol": "MSFT", "company_name": "Microsoft"},
        {"symbol": "NVDA", "company_name": "Nvidia"},
        {"symbol": "AAPL", "company_name": "Apple"},
    ]

    batches = build_research_universe_batches(ideas, batch_size=2)

    assert len(batches) == 2
    assert batches[0]["batch_id"] == "research-batch-001"
    assert [idea["symbol"] for idea in batches[0]["ideas"]] == ["MSFT", "NVDA"]
    assert batches[1]["batch_id"] == "research-batch-002"
    assert batches[1]["ideas"][0]["symbol"] == "AAPL"


def test_research_universe_cli_writes_batch_files(tmp_path, capsys):
    ideas_path = tmp_path / "research_ideas.json"
    output_dir = tmp_path / "batches"
    ideas_path.write_text(
        json.dumps(
            [
                {"symbol": "MSFT", "company_name": "Microsoft"},
                {"symbol": "NVDA", "company_name": "Nvidia"},
                {"symbol": "AAPL", "company_name": "Apple"},
            ]
        ),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--research-ideas",
            str(ideas_path),
            "--batch-size",
            "2",
            "--output-dir",
            str(output_dir),
        ]
    )

    exit_code = run_cli(args)
    payload = json.loads(capsys.readouterr().out)
    first_batch = json.loads((output_dir / "research-batch-001.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["batch_count"] == 2
    assert payload["total_ideas"] == 3
    assert first_batch[0]["symbol"] == "MSFT"
