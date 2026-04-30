import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics import grok_playwright_client


class FakeChromium:
    def __init__(self, fail_channel: str | None = None):
        self.fail_channel = fail_channel
        self.calls = []

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("channel") == self.fail_channel:
            raise RuntimeError("channel unavailable")
        return {"context": "ok", "kwargs": kwargs}


def test_launch_persistent_context_prefers_real_chrome_channel():
    chromium = FakeChromium()

    context = grok_playwright_client._launch_persistent_context(
        chromium,
        user_data_dir="profile",
        launch_options={"headless": False},
    )

    assert context["context"] == "ok"
    assert chromium.calls[0]["channel"] == "chrome"
    assert chromium.calls[0]["user_data_dir"] == "profile"


def test_launch_persistent_context_falls_back_to_bundled_chromium():
    chromium = FakeChromium(fail_channel="chrome")

    context = grok_playwright_client._launch_persistent_context(
        chromium,
        user_data_dir="profile",
        launch_options={"headless": False},
    )

    assert context["context"] == "ok"
    assert chromium.calls[0]["channel"] == "chrome"
    assert "channel" not in chromium.calls[1]
