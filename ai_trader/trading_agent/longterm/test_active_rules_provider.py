from pathlib import Path

import pytest

from longterm.active_rules_provider import (
    ActiveRulesProvider,
    DEFAULT_ACTIVE_RULES_PATH,
    DEFAULT_WEEKLY_FULL_SCAN_RULES_PATH,
)


def test_load_keeps_default_decision_rules_behavior():
    text = ActiveRulesProvider().load()

    assert DEFAULT_ACTIVE_RULES_PATH.name == "active_rules.txt"
    assert "<trading_rules>" in text
    assert "Long-term quality-growth active sleeve" in text


def test_load_for_stage_uses_weekly_full_scan_rules():
    text = ActiveRulesProvider().load_for_stage("weekly_full_scan")

    assert DEFAULT_WEEKLY_FULL_SCAN_RULES_PATH.name == "weekly_full_scan_rules.txt"
    assert "<weekly_full_scan_rules>" in text
    assert "scan_stage=weekly_full_scan" in text


def test_load_for_stage_uses_configured_paths(tmp_path: Path):
    decision_rules = tmp_path / "decision_rules.xml"
    weekly_rules = tmp_path / "weekly_rules.xml"
    decision_rules.write_text("<trading_rules>decision only</trading_rules>", encoding="utf-8")
    weekly_rules.write_text("<weekly_full_scan_rules>weekly only</weekly_full_scan_rules>", encoding="utf-8")

    provider = ActiveRulesProvider(
        rules_path=decision_rules,
        weekly_full_scan_rules_path=weekly_rules,
    )

    assert provider.load_for_stage("decision") == "<trading_rules>decision only</trading_rules>"
    assert provider.load_for_stage("weekly_full_scan") == (
        "<weekly_full_scan_rules>weekly only</weekly_full_scan_rules>"
    )


def test_load_for_stage_rejects_unknown_stage():
    provider = ActiveRulesProvider()

    with pytest.raises(ValueError, match="Unknown active-rules stage"):
        provider.load_for_stage("intraday_scalping")
