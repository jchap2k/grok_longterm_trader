from analytics.grok_plan_reviewer import (
    build_grok_context_warmup_prompt,
    format_review_for_display,
)
from analytics.grok_debugger import format_debug_result


def test_longterm_grok_warmup_prompt_starts_with_repo_context_file():
    prompt = build_grok_context_warmup_prompt("longterm")

    assert "First action" in prompt
    assert "get_file_contents" in prompt
    assert "jchap2k/grok_longterm_trader" in prompt
    assert "docs/system/REPO_CONTEXT.md" in prompt
    assert "Do not begin source-file scanning before reading this context file" in prompt


def test_non_longterm_grok_warmup_prompt_keeps_legacy_source_context():
    prompt = build_grok_context_warmup_prompt("swing")

    assert "docs/system/REPO_CONTEXT.md" not in prompt
    assert "DAY_TRADER_TO_SWING_TRADER_CHANGES.md" in prompt


def test_plan_review_display_uses_model_agnostic_browser_label():
    rendered = format_review_for_display(
        {"confidence": 0.85, "proceed_recommendation": "proceed_with_notes"},
        "Feature",
    )

    assert "Grok Browser Plan Review: Feature" in rendered
    assert "Grok 4.20" not in rendered


def test_debug_display_uses_model_agnostic_browser_label():
    rendered = format_debug_result({"confidence": 0.85}, "Bug")

    assert "Grok Browser Debug Analysis: Bug" in rendered
    assert "Grok 4.20" not in rendered
