"""Track multi-batch long-term research campaigns."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


VALID_BATCH_STATUSES = {"pending", "completed", "deferred", "failed", "skipped"}


def build_research_campaign_manifest(batch_dir: str | Path) -> dict[str, Any]:
    """Create a manifest from research-batch JSON files."""
    root = Path(batch_dir)
    batches = []
    for path in sorted(root.glob("research-batch-*.json")):
        ideas = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(ideas, list):
            raise ValueError(f"Batch file must contain a JSON list: {path}")
        batches.append(
            {
                "batch_id": path.stem,
                "batch_path": str(path),
                "idea_count": len(ideas),
                "status": "pending",
                "notes": "",
                "updated_at": "",
            }
        )
    manifest = {
        "campaign_id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "status": "active",
        "batch_dir": str(root),
        "batch_count": len(batches),
        "completed_count": 0,
        "pending_count": len(batches),
        "batches": batches,
    }
    return manifest


def next_research_batch(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the next pending research batch, or an empty dict if done."""
    for batch in manifest.get("batches") or []:
        if str(batch.get("status") or "").lower() == "pending":
            return dict(batch)
    return {}


def mark_research_batch(
    manifest: dict[str, Any],
    batch_id: str,
    status: str,
    *,
    notes: str = "",
) -> dict[str, Any]:
    """Mark one batch status and refresh manifest counters."""
    normalized_status = status.lower()
    if normalized_status not in VALID_BATCH_STATUSES:
        raise ValueError(f"Unsupported batch status: {status}")
    found = False
    updated = dict(manifest)
    batches = []
    for batch in manifest.get("batches") or []:
        item = dict(batch)
        if item.get("batch_id") == batch_id:
            item["status"] = normalized_status
            item["notes"] = notes
            item["updated_at"] = datetime.now().isoformat()
            found = True
        batches.append(item)
    if not found:
        raise KeyError(f"Research batch not found: {batch_id}")
    updated["batches"] = batches
    return refresh_campaign_counts(updated)


def refresh_campaign_counts(manifest: dict[str, Any]) -> dict[str, Any]:
    """Refresh campaign counters from batch statuses."""
    updated = dict(manifest)
    batches = [dict(batch) for batch in manifest.get("batches") or []]
    completed_count = sum(1 for batch in batches if batch.get("status") == "completed")
    pending_count = sum(1 for batch in batches if batch.get("status") == "pending")
    updated["batches"] = batches
    updated["batch_count"] = len(batches)
    updated["completed_count"] = completed_count
    updated["pending_count"] = pending_count
    updated["status"] = "completed" if batches and pending_count == 0 else "active"
    return updated


def build_suggested_cycle_command(
    batch: dict[str, Any],
    *,
    journal_db: str = "",
    portfolio_state: str = "",
) -> str:
    """Build the supervised command for running one research batch."""
    command = f"python scripts/run_longterm_cycle.py --idea-batch {batch['batch_path']}"
    if journal_db:
        command += f" --journal-db {journal_db}"
    if portfolio_state:
        command += f" --portfolio-state {portfolio_state}"
    return command


__all__ = [
    "VALID_BATCH_STATUSES",
    "build_research_campaign_manifest",
    "build_suggested_cycle_command",
    "mark_research_batch",
    "next_research_batch",
    "refresh_campaign_counts",
]
