import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_project_manifest_references_existing_context_files():
    manifest_path = REPO_ROOT / "docs" / "system" / "project_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["project_name"] == "grok_longterm_trader"
    assert manifest["execution_state"] == "research_logging_reporting_supervised_paper_buy_only"
    assert manifest["protected_symbol"] == "FXAIX"

    for key in ("primary_docs", "core_code", "agent_configs"):
        for relative_path in manifest[key]:
            assert (REPO_ROOT / relative_path).exists(), relative_path


def test_system_safety_doc_states_no_live_trading_and_protected_symbol():
    safety_doc = (REPO_ROOT / "docs" / "system" / "SAFETY.md").read_text(encoding="utf-8")

    assert "not live-trading enabled" in safety_doc
    assert "FXAIX" in safety_doc
    assert "dry-run JSON" in safety_doc


def test_active_rules_are_xml_structured_and_include_quality_durability():
    rules_path = REPO_ROOT / "ai_trader" / "rules" / "active_rules.txt"
    root = ET.fromstring(rules_path.read_text(encoding="utf-8"))

    assert root.tag == "trading_rules"
    assert root.findtext("metadata/paradigm") == "Long-term quality-growth active sleeve"
    assert root.find("quality_durability_rules") is not None
    assert "pricing power" in rules_path.read_text(encoding="utf-8")


def test_weekly_full_scan_rules_are_stage_specific_and_xml_structured():
    rules_path = REPO_ROOT / "ai_trader" / "rules" / "weekly_full_scan_rules.txt"
    text = rules_path.read_text(encoding="utf-8")
    root = ET.fromstring(text)

    assert root.tag == "weekly_full_scan_rules"
    assert root.findtext("metadata/derived_from") == "ai_trader/rules/active_rules.txt"
    assert root.find("stage_objective") is not None
    assert root.find("inclusion_bias") is not None
    assert root.find("scan_framing") is not None
    assert root.find("conviction_rubric_light") is not None
    assert root.find("routing_outputs") is not None
    assert root.find("provenance_requirements") is not None
    assert "Motley Fool" in text
    assert "latest and previous recommendation dates" in text
    assert "S&amp;P 500 / SPY-like constituents" in text
    assert "FRED macro data is context" in text
    assert "Kronos is advisory only" in text
    assert "ResearchPacket" in text
    assert "QualityDurabilityReviewer" in text
    assert "scan_stage=weekly_full_scan" in text
    assert "Final BUY, ADD, HOLD, PASS, REDUCE, SELL" in text
