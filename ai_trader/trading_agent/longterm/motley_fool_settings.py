"""Settings for optional Motley Fool premium idea capture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_MOTLEY_FOOL_CONFIG_PATH = CONFIG_DIR / "motley_fool_capture.json"
DEFAULT_PROFILE_DIR = Path.home() / ".grok3api_chrome_profile"
DEFAULT_LOGIN_URL = "https://www.fool.com/premium?watchSymbols=NASDAQ%3ACRWD"
DEFAULT_SOURCES = ["new_recommendations", "analyst_rankings", "quant_rankings"]


@dataclass(frozen=True)
class MotleyFoolCaptureSettings:
    enabled: bool = False
    cookie_ready: bool = False
    profile_dir: Path = DEFAULT_PROFILE_DIR
    open_login_when_cookie_missing: bool = True
    login_url: str = DEFAULT_LOGIN_URL
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    config_path: Path = DEFAULT_MOTLEY_FOOL_CONFIG_PATH

    @property
    def can_capture(self) -> bool:
        """Return whether scheduled capture can run without an interactive login."""
        return self.enabled and self.cookie_ready

    @property
    def should_open_login(self) -> bool:
        """Return whether scheduler setup should open the profile for login."""
        return self.enabled and not self.cookie_ready and self.open_login_when_cookie_missing


def load_motley_fool_capture_settings(
    path: str | Path | None = None,
) -> MotleyFoolCaptureSettings:
    """Load optional Motley Fool capture settings.

    Missing config is intentionally disabled so users without Motley Fool do not
    break normal scheduler runs.
    """
    config_path = Path(path or DEFAULT_MOTLEY_FOOL_CONFIG_PATH)
    if not config_path.exists():
        return MotleyFoolCaptureSettings(config_path=config_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    section = data.get("motley_fool", data)
    return MotleyFoolCaptureSettings(
        enabled=bool(section.get("enabled", False)),
        cookie_ready=bool(section.get("cookie_ready", False)),
        profile_dir=Path(section.get("profile_dir") or DEFAULT_PROFILE_DIR).expanduser(),
        open_login_when_cookie_missing=bool(section.get("open_login_when_cookie_missing", True)),
        login_url=str(section.get("login_url") or DEFAULT_LOGIN_URL),
        sources=_normalize_sources(section.get("sources")),
        config_path=config_path,
    )


def _normalize_sources(value: Any) -> list[str]:
    if not value:
        return list(DEFAULT_SOURCES)
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]
