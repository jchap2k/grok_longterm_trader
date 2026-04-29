import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_project_manifest_references_existing_context_files():
    manifest_path = REPO_ROOT / "docs" / "system" / "project_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["project_name"] == "grok_longterm_trader"
    assert manifest["execution_state"] == "research_logging_reporting_dry_run_only"
    assert manifest["protected_symbol"] == "FXAIX"

    for key in ("primary_docs", "core_code", "agent_configs"):
        for relative_path in manifest[key]:
            assert (REPO_ROOT / relative_path).exists(), relative_path


def test_system_safety_doc_states_no_live_trading_and_protected_symbol():
    safety_doc = (REPO_ROOT / "docs" / "system" / "SAFETY.md").read_text(encoding="utf-8")

    assert "not live-trading enabled" in safety_doc
    assert "FXAIX" in safety_doc
    assert "dry-run JSON" in safety_doc
