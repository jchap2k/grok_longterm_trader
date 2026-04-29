import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.grok_plan_reviewer import _load_default_trading_mode
from analytics.grok_playwright_client import _load_project_url


def test_grok_project_config_sets_longterm_project_url():
    url = _load_project_url()

    assert url.startswith("https://grok.com/project/e397a91c-e647-4c3b-868f-ff0d0ed6c175")


def test_grok_project_url_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("GROK_PROJECT_URL", "https://grok.com/project/test-override")

    assert _load_project_url() == "https://grok.com/project/test-override"


def test_grok_plan_reviewer_default_mode_is_longterm():
    assert _load_default_trading_mode() == "longterm"


def test_grok_plan_reviewer_signature_defaults_to_auto():
    module = importlib.import_module("analytics.grok_plan_reviewer")

    defaults = module.GrokPlanReviewer.review.__defaults__

    assert "auto" in defaults
