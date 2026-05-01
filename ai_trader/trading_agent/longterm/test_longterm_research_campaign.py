import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.research_campaign import (
    build_research_campaign_manifest,
    mark_research_batch,
    next_research_batch,
)
from longterm.research_campaign_cli import build_parser, run_cli


def _write_batch(path, symbols):
    path.write_text(
        json.dumps([{"symbol": symbol, "company_name": symbol} for symbol in symbols]),
        encoding="utf-8",
    )


def test_research_campaign_manifest_tracks_batches_in_order(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-002.json", ["NVDA"])
    _write_batch(batch_dir / "research-batch-001.json", ["MSFT", "AAPL"])

    manifest = build_research_campaign_manifest(batch_dir)

    assert manifest["status"] == "active"
    assert manifest["batch_count"] == 2
    assert manifest["batches"][0]["batch_id"] == "research-batch-001"
    assert manifest["batches"][0]["idea_count"] == 2
    assert manifest["batches"][0]["status"] == "pending"
    assert manifest["batches"][1]["batch_id"] == "research-batch-002"


def test_research_campaign_can_mark_batch_and_find_next_pending(tmp_path):
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", ["MSFT"])
    _write_batch(batch_dir / "research-batch-002.json", ["NVDA"])
    manifest = build_research_campaign_manifest(batch_dir)

    updated = mark_research_batch(manifest, "research-batch-001", "completed", notes="journaled")
    next_batch = next_research_batch(updated)

    assert updated["completed_count"] == 1
    assert updated["pending_count"] == 1
    assert updated["batches"][0]["notes"] == "journaled"
    assert next_batch["batch_id"] == "research-batch-002"


def test_research_campaign_cli_init_next_and_mark(tmp_path, capsys):
    batch_dir = tmp_path / "batches"
    manifest_path = tmp_path / "campaign.json"
    batch_dir.mkdir()
    _write_batch(batch_dir / "research-batch-001.json", ["MSFT"])
    _write_batch(batch_dir / "research-batch-002.json", ["NVDA"])
    parser = build_parser()

    init_args = parser.parse_args(
        ["init", "--batch-dir", str(batch_dir), "--manifest-output", str(manifest_path)]
    )
    assert run_cli(init_args) == 0
    init_payload = json.loads(capsys.readouterr().out)
    assert init_payload["batch_count"] == 2

    next_args = parser.parse_args(
        [
            "next",
            "--manifest",
            str(manifest_path),
            "--journal-db",
            "path/to/journal.db",
        ]
    )
    assert run_cli(next_args) == 0
    next_payload = json.loads(capsys.readouterr().out)
    assert next_payload["batch_id"] == "research-batch-001"
    assert "run_longterm_cycle.py" in next_payload["suggested_command"]

    mark_args = parser.parse_args(
        [
            "mark",
            "--manifest",
            str(manifest_path),
            "--batch-id",
            "research-batch-001",
            "--status",
            "completed",
            "--notes",
            "processed",
        ]
    )
    assert run_cli(mark_args) == 0
    mark_payload = json.loads(capsys.readouterr().out)
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert mark_payload["completed_count"] == 1
    assert saved["batches"][0]["status"] == "completed"
