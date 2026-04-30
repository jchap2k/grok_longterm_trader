"""Interactive setup helpers for Motley Fool premium capture."""

from __future__ import annotations

import json
import shutil
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from longterm.motley_fool_capture import capture_motley_fool_ideas
from longterm.motley_fool_settings import MotleyFoolCaptureSettings


@dataclass(frozen=True)
class MotleyFoolSetupResult:
    status: str
    settings: MotleyFoolCaptureSettings
    message: str
    verification_source: str = ""
    config_updated: bool = False


def complete_motley_fool_setup(
    *,
    settings: MotleyFoolCaptureSettings,
    launch_browser_func: Callable[..., Any] | None = None,
    verify_capture_func: Callable[..., list[dict[str, Any]]] = capture_motley_fool_ideas,
    prompt_func: Callable[[str], Any] = input,
    verification_source: str = "dashboard",
) -> MotleyFoolSetupResult:
    """Open the configured profile, verify access, and persist cookie readiness."""
    if not settings.enabled:
        return MotleyFoolSetupResult(
            status="disabled",
            settings=settings,
            message="Motley Fool capture is disabled.",
        )

    if settings.cookie_ready:
        return MotleyFoolSetupResult(
            status="already_ready",
            settings=settings,
            message="Motley Fool cookies are already marked ready.",
            verification_source=verification_source,
        )

    launcher = launch_browser_func or open_motley_fool_login_browser
    launcher(profile_dir=settings.profile_dir, login_url=settings.login_url)
    prompt_func("Complete Motley Fool login in the opened browser, then press Enter to verify.")

    ideas = verify_capture_func(
        verification_source,
        profile_dir=settings.profile_dir,
        url=None,
    )
    if not ideas:
        return MotleyFoolSetupResult(
            status="verification_failed",
            settings=settings,
            message="Login verification did not capture any Motley Fool ideas.",
            verification_source=verification_source,
        )

    updated_settings = _replace_settings(settings, cookie_ready=True)
    config_updated = persist_motley_fool_cookie_ready(updated_settings)
    return MotleyFoolSetupResult(
        status="verified",
        settings=updated_settings,
        message="Motley Fool login verified and cookie_ready persisted.",
        verification_source=verification_source,
        config_updated=config_updated,
    )


def open_motley_fool_login_browser(*, profile_dir: str | Path, login_url: str) -> None:
    """Open the login URL in Chrome using the configured persistent profile."""
    user_data_dir = str(Path(profile_dir).expanduser())
    chrome_path = _find_chrome_executable()
    if chrome_path:
        subprocess.Popen(
            [
                chrome_path,
                f"--user-data-dir={user_data_dir}",
                login_url,
            ],
            close_fds=True,
        )
        return
    webbrowser.open(login_url)


def persist_motley_fool_cookie_ready(settings: MotleyFoolCaptureSettings) -> bool:
    """Persist cookie_ready=true into the local Motley Fool config file."""
    config_path = settings.config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        payload = {}

    section = payload.get("motley_fool")
    if not isinstance(section, dict):
        section = dict(payload) if payload else {}
        payload = {"motley_fool": section}

    section["enabled"] = settings.enabled
    section["cookie_ready"] = True
    section["profile_dir"] = str(settings.profile_dir)
    section["open_login_when_cookie_missing"] = settings.open_login_when_cookie_missing
    section["login_url"] = settings.login_url
    section["sources"] = list(settings.sources)

    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True


def _replace_settings(
    settings: MotleyFoolCaptureSettings,
    *,
    cookie_ready: bool,
) -> MotleyFoolCaptureSettings:
    return MotleyFoolCaptureSettings(
        enabled=settings.enabled,
        cookie_ready=cookie_ready,
        profile_dir=settings.profile_dir,
        open_login_when_cookie_missing=settings.open_login_when_cookie_missing,
        login_url=settings.login_url,
        sources=list(settings.sources),
        config_path=settings.config_path,
    )


def _find_chrome_executable() -> str:
    for candidate in (
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return ""
