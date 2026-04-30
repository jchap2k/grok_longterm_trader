import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_setup import complete_motley_fool_setup
from longterm.motley_fool_setup_cli import build_parser, run_cli
from longterm.motley_fool_settings import MotleyFoolCaptureSettings, load_motley_fool_capture_settings


def test_complete_setup_verifies_and_persists_cookie_ready(tmp_path):
    config_path = tmp_path / "motley_fool_capture.json"
    config_path.write_text(
        json.dumps(
            {
                "motley_fool": {
                    "enabled": True,
                    "cookie_ready": False,
                    "profile_dir": str(tmp_path / "profile"),
                    "login_url": "https://example.test/login",
                    "sources": ["dashboard"],
                }
            }
        ),
        encoding="utf-8",
    )
    settings = load_motley_fool_capture_settings(config_path)
    launched = []
    verified = []
    prompted = []

    result = complete_motley_fool_setup(
        settings=settings,
        launch_browser_func=lambda *, profile_dir, login_url: launched.append((profile_dir, login_url)),
        verify_capture_func=lambda source_key, *, profile_dir=None, url=None: verified.append((source_key, profile_dir, url)) or [{"symbol": "CRWD"}],
        prompt_func=lambda message: prompted.append(message),
        verification_source="dashboard",
    )

    assert result.status == "verified"
    assert result.settings.cookie_ready is True
    assert result.config_updated is True
    assert result.verification_source == "dashboard"
    assert launched == [(settings.profile_dir, settings.login_url)]
    assert verified == [("dashboard", settings.profile_dir, None)]
    assert prompted

    reloaded = load_motley_fool_capture_settings(config_path)
    assert reloaded.cookie_ready is True


def test_setup_cli_uses_config_and_prints_verified_status(tmp_path, capsys):
    config_path = tmp_path / "motley_fool_capture.json"
    config_path.write_text(
        json.dumps(
            {
                "motley_fool": {
                    "enabled": True,
                    "cookie_ready": False,
                    "profile_dir": str(tmp_path / "profile"),
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_setup(*, settings, verification_source):
        assert settings.config_path == config_path
        assert verification_source == "dashboard"
        return {
            "status": "verified",
            "settings": {"cookie_ready": True},
            "verification_source": verification_source,
            "config_updated": True,
        }

    parser = build_parser()
    args = parser.parse_args(["--config", str(config_path)])
    exit_code = run_cli(args, setup_func=fake_setup)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "verified"' in captured.out
