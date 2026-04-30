import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_settings import MotleyFoolCaptureSettings
from longterm.orchestration_cli import build_parser, run_cli
from longterm.orchestration import run_longterm_cycle
from portfolio.portfolio_profile import PortfolioProfile


def _build_profile() -> PortfolioProfile:
    return PortfolioProfile(
        account_strategy_mode="roth_ira",
        tradable_capital=35000,
        protected_symbols=["FXAIX"],
        benchmark_symbol="FXAIX",
        defensive_parking_symbol="SPY",
    )


def test_cycle_skips_motley_fool_capture_when_disabled(tmp_path):
    captured_sources = []

    def fake_capture(*args, **kwargs):
        captured_sources.append((args, kwargs))
        return [{"symbol": "SHOULD_NOT_RUN"}]

    class FakeRunner:
        def __init__(self):
            self.symbols = []

        def run_and_record(self, packet, **kwargs):
            self.symbols.append(packet.symbol)
            return f"decision-{packet.symbol}"

    runner = FakeRunner()
    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[{"symbol": "AAPL", "company_name": "Apple"}],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        capture_func=fake_capture,
        runner=runner,
        journal_db_path=tmp_path / "journal.db",
    )

    assert result.status == "completed"
    assert result.capture_status == "disabled"
    assert result.total_idea_count == 1
    assert result.captured_idea_count == 0
    assert result.decision_ids == ["decision-AAPL"]
    assert runner.symbols == ["AAPL"]
    assert captured_sources == []


def test_cycle_reports_login_required_when_enabled_but_cookie_missing(tmp_path):
    class FakeRunner:
        def __init__(self):
            self.symbols = []

        def run_and_record(self, packet, **kwargs):
            self.symbols.append(packet.symbol)
            return f"decision-{packet.symbol}"

    settings = MotleyFoolCaptureSettings(
        enabled=True,
        cookie_ready=False,
        profile_dir=tmp_path / "profile",
        login_url="https://example.test/login",
    )
    runner = FakeRunner()

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[{"symbol": "MSFT", "company_name": "Microsoft"}],
        motley_fool_settings=settings,
        capture_func=lambda *args, **kwargs: [{"symbol": "SHOULD_NOT_RUN"}],
        runner=runner,
        journal_db_path=tmp_path / "journal.db",
    )

    assert result.status == "login_required"
    assert result.capture_status == "login_required"
    assert result.login_url == "https://example.test/login"
    assert result.profile_dir == settings.profile_dir
    assert result.decision_ids == ["decision-MSFT"]
    assert runner.symbols == ["MSFT"]


def test_cycle_captures_configured_sources_and_records_all_ideas(tmp_path):
    captured_calls = []

    def fake_capture(source_key, *, profile_dir=None, url=None):
        captured_calls.append((source_key, profile_dir, url))
        return [
            {
                "symbol": "NVDA" if source_key == "new_recommendations" else "META",
                "company_name": source_key,
                "idea_source": f"motley_fool_{source_key}",
            }
        ]

    class FakeRunner:
        def __init__(self):
            self.symbols = []

        def run_and_record(self, packet, **kwargs):
            self.symbols.append(packet.symbol)
            return f"decision-{packet.symbol}"

    settings = MotleyFoolCaptureSettings(
        enabled=True,
        cookie_ready=True,
        profile_dir=tmp_path / "profile",
        sources=["new_recommendations", "quant_rankings"],
    )
    runner = FakeRunner()

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[{"symbol": "AAPL", "company_name": "Apple"}],
        motley_fool_settings=settings,
        capture_func=fake_capture,
        runner=runner,
        journal_db_path=tmp_path / "journal.db",
    )

    assert result.status == "completed"
    assert result.capture_status == "captured"
    assert result.capture_sources_run == ["new_recommendations", "quant_rankings"]
    assert result.total_idea_count == 3
    assert result.captured_idea_count == 2
    assert result.decision_ids == [
        "decision-AAPL",
        "decision-NVDA",
        "decision-META",
    ]
    assert runner.symbols == ["AAPL", "NVDA", "META"]
    assert captured_calls == [
        ("new_recommendations", settings.profile_dir, None),
        ("quant_rankings", settings.profile_dir, None),
    ]


def test_orchestration_cli_loads_idea_file_and_prints_summary(tmp_path, capsys):
    idea_path = tmp_path / "idea.json"
    idea_path.write_text('{"symbol":"aapl","company_name":"Apple"}', encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        '{"account_strategy_mode":"roth_ira","tradable_capital":35000,"protected_symbols":["FXAIX"],"benchmark_symbol":"FXAIX","defensive_parking_symbol":"SPY"}',
        encoding="utf-8",
    )

    def fake_cycle(**kwargs):
        assert kwargs["manual_ideas"] == [{"symbol": "aapl", "company_name": "Apple"}]
        assert kwargs["profile"].protected_symbols == ["FXAIX"]
        return {
            "status": "completed",
            "decision_ids": ["decision-AAPL"],
            "total_idea_count": 1,
            "capture_status": "disabled",
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--idea-file",
            str(idea_path),
            "--profile-config",
            str(profile_path),
        ]
    )

    exit_code = run_cli(args, cycle_func=fake_cycle)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "completed"' in captured.out
    assert '"decision_ids": [' in captured.out


def test_orchestration_cli_loads_idea_batch(tmp_path, capsys):
    batch_path = tmp_path / "ideas.json"
    batch_path.write_text('[{"symbol":"msft"},{"symbol":"nvda"}]', encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        '{"account_strategy_mode":"roth_ira","tradable_capital":35000,"protected_symbols":["FXAIX"],"benchmark_symbol":"FXAIX","defensive_parking_symbol":"SPY"}',
        encoding="utf-8",
    )

    def fake_cycle(**kwargs):
        assert kwargs["manual_ideas"] == [{"symbol": "msft"}, {"symbol": "nvda"}]
        return {
            "status": "completed",
            "decision_ids": ["decision-MSFT", "decision-NVDA"],
            "total_idea_count": 2,
            "capture_status": "disabled",
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--idea-batch",
            str(batch_path),
            "--profile-config",
            str(profile_path),
        ]
    )

    exit_code = run_cli(args, cycle_func=fake_cycle)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"total_idea_count": 2' in captured.out
