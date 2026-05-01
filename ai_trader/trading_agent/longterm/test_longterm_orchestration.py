import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_setup import MotleyFoolSetupResult
from longterm.motley_fool_settings import MotleyFoolCaptureSettings
from longterm.orchestration_cli import build_parser, run_cli
from longterm.orchestration import run_longterm_cycle
from longterm.portfolio_state import PortfolioState
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
        manual_ideas=[
            {
                "symbol": "AAPL",
                "company_name": "Apple",
                "idea_source": "manual",
                "thesis_summary": "Durable ecosystem compounder.",
            }
        ],
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


def test_cycle_can_launch_motley_fool_setup_then_capture(tmp_path):
    captured_calls = []
    setup_calls = []

    def fake_capture(source_key, *, profile_dir=None, url=None):
        captured_calls.append((source_key, profile_dir, url))
        return [{"symbol": "NVDA", "company_name": "Nvidia", "idea_source": "motley_fool_new_recommendations"}]

    def fake_setup(settings, **kwargs):
        setup_calls.append((settings.profile_dir, settings.login_url))
        return MotleyFoolSetupResult(
            status="verified",
            settings=MotleyFoolCaptureSettings(
                enabled=True,
                cookie_ready=True,
                profile_dir=settings.profile_dir,
                login_url=settings.login_url,
                sources=["new_recommendations"],
                config_path=settings.config_path,
            ),
            message="verified",
            verification_source="dashboard",
            config_updated=True,
        )

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
        sources=["new_recommendations"],
        config_path=tmp_path / "motley_fool_capture.json",
    )
    runner = FakeRunner()

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[{"symbol": "MSFT", "company_name": "Microsoft"}],
        motley_fool_settings=settings,
        capture_func=fake_capture,
        setup_func=fake_setup,
        launch_login_if_needed=True,
        runner=runner,
        journal_db_path=tmp_path / "journal.db",
    )

    assert result.status == "completed"
    assert result.capture_status == "captured"
    assert result.setup_status == "verified"
    assert result.capture_sources_run == ["new_recommendations"]
    assert result.captured_idea_count == 1
    assert result.total_idea_count == 2
    assert result.decision_ids == ["decision-MSFT", "decision-NVDA"]
    assert runner.symbols == ["MSFT", "NVDA"]
    assert setup_calls == [(settings.profile_dir, settings.login_url)]
    assert captured_calls == [("new_recommendations", settings.profile_dir, None)]


def test_cycle_captures_configured_sources_and_records_all_ideas(tmp_path):
    captured_calls = []

    def fake_capture(source_key, *, profile_dir=None, url=None):
        captured_calls.append((source_key, profile_dir, url))
        return [
                {
                    "symbol": "NVDA" if source_key == "new_recommendations" else "META",
                    "company_name": source_key,
                    "idea_source": f"motley_fool_{source_key}",
                    "business_summary": "Premium source candidate.",
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
        manual_ideas=[
            {
                "symbol": "AAPL",
                "company_name": "Apple",
                "idea_source": "manual",
                "thesis_summary": "Durable ecosystem compounder.",
            }
        ],
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
    assert result.idea_provenance_summary == {
        "manual": 1,
        "motley_fool_new_recommendations": 1,
        "motley_fool_quant_rankings": 1,
    }
    assert result.packet_completeness_warnings == []
    assert result.decision_journal_refs == [
        "decision-AAPL",
        "decision-NVDA",
        "decision-META",
    ]


def test_cycle_can_feed_discovery_candidates_into_research(tmp_path):
    recorded_symbols = []

    class FakeRunner:
        def run_and_record(self, packet, **kwargs):
            recorded_symbols.append(packet.symbol)
            return f"decision-{packet.symbol}"

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[],
        discovery_candidates=[
            {
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "source": "sp500",
                "revenue_growth_1y_pct": 16,
                "earnings_growth_1y_pct": 20,
                "return_on_capital_pct": 28,
                "gross_margin_pct": 68,
                "market_cap": 3_000_000_000_000,
                "category_leader": True,
            },
            {
                "symbol": "PENY",
                "source": "screen_growth",
                "market_cap": 100_000_000,
                "revenue_growth_1y_pct": -40,
            },
        ],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        runner=FakeRunner(),
        journal_db_path=tmp_path / "journal.db",
    )

    assert recorded_symbols == ["MSFT"]
    assert result.discovery_generated is True
    assert result.discovery_summary == {
        "research_queue": 1,
        "watchlist": 0,
        "rejected": 1,
    }
    assert result.discovery_research_symbols == ["MSFT"]


def test_cycle_can_build_report_and_next_actions_outputs(tmp_path):
    class FakeRunner:
        def run_and_record(self, packet, **kwargs):
            return f"decision-{packet.symbol}"

    report_calls = []
    next_actions_calls = []

    def fake_report_builder(journal, *, limit):
        report_calls.append((journal.db_path, limit))
        return "# report\n"

    def fake_next_actions_builder(journal, *, profile, portfolio_state, limit):
        next_actions_calls.append((journal.db_path, profile.benchmark_symbol, portfolio_state.cash, limit))
        return "# next actions\n"

    portfolio_state = PortfolioState(
        cash=2500,
        holdings=[{"symbol": "AAPL", "market_value": 1000, "quantity": 5}],
        protected_symbols=["FXAIX"],
    )

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[{"symbol": "AAPL", "company_name": "Apple"}],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        runner=FakeRunner(),
        journal_db_path=tmp_path / "journal.db",
        portfolio_state=portfolio_state,
        report_builder_func=fake_report_builder,
        next_actions_builder_func=fake_next_actions_builder,
        report_limit=7,
    )

    assert result.recommendation_report_markdown == "# report\n"
    assert result.next_actions_markdown == "# next actions\n"
    assert result.report_generated is True
    assert result.next_actions_generated is True
    assert report_calls == [(tmp_path / "journal.db", 7)]
    assert next_actions_calls == [(tmp_path / "journal.db", "FXAIX", 2500.0, 7)]


def test_cycle_surfaces_packet_completeness_warnings(tmp_path):
    class FakeRunner:
        def run_and_record(self, packet, **kwargs):
            return f"decision-{packet.symbol}"

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[{"symbol": "TSLA"}],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        runner=FakeRunner(),
        journal_db_path=tmp_path / "journal.db",
    )

    assert result.packet_completeness_warnings == [
        "TSLA: missing company_name",
        "TSLA: missing idea_source",
        "TSLA: missing thesis_summary or business_summary",
    ]


def test_cycle_can_build_capital_alert_markdown(tmp_path):
    class FakeRunner:
        def run_and_record(self, packet, **kwargs):
            return f"decision-{packet.symbol}"

    class FakeJournal:
        db_path = tmp_path / "journal.db"

        def summarize_benchmark_performance(self):
            return {
                "evaluated_decisions": 0,
                "average_excess_return_pct": 0.0,
                "decisions_beating_benchmark": 0,
            }

        def list_recommendation_table(self, limit=10):
            return []

    capital_alert_calls = []

    def fake_capital_alert_builder(journal, **kwargs):
        capital_alert_calls.append((journal.db_path, kwargs))
        return type(
            "Alert",
            (),
            {
                "should_alert": True,
                "markdown": "# Capital Needed Alert\n",
                "reason": "Capital shortfall.",
            },
        )()

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        runner=FakeRunner(),
        journal_db_path=tmp_path / "journal.db",
        portfolio_state=PortfolioState(cash=500, holdings=[]),
        active_sleeve_value=35000,
        available_cash=500,
        journal_factory=lambda path: FakeJournal(),
        capital_alert_builder_func=fake_capital_alert_builder,
        report_builder_func=lambda journal, *, limit: "",
        next_actions_builder_func=lambda journal, *, profile, portfolio_state, limit: "",
        rebalance_planner=type(
            "Planner",
            (),
            {
                "propose": lambda self, recommendations, **kwargs: type(
                    "Proposal",
                    (),
                    {"should_rebalance": False},
                )()
            },
        )(),
    )

    assert result.capital_alert_markdown == "# Capital Needed Alert\n"
    assert result.capital_alert_generated is True
    assert capital_alert_calls[0][1]["active_sleeve_value"] == 35000
    assert capital_alert_calls[0][1]["available_cash"] == 500


def test_cycle_can_build_rebalance_markdown(tmp_path):
    class FakeRunner:
        def run_and_record(self, packet, **kwargs):
            return f"decision-{packet.symbol}"

    class FakeJournal:
        db_path = tmp_path / "journal.db"

        def list_recommendation_table(self, limit=10):
            return [
                {"symbol": "NVDA", "rank": 1, "confidence": 92, "suggested_size_pct": 8},
                {"symbol": "AAPL", "rank": 8, "confidence": 65, "suggested_size_pct": 4},
            ]

        def summarize_benchmark_performance(self):
            return {
                "evaluated_decisions": 0,
                "average_excess_return_pct": 0.0,
                "decisions_beating_benchmark": 0,
            }

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        runner=FakeRunner(),
        journal_db_path=tmp_path / "journal.db",
        portfolio_state=PortfolioState(
            cash=500,
            holdings=[{"symbol": "AAPL", "market_value": 5000}],
            protected_symbols=["FXAIX"],
        ),
        journal_factory=lambda path: FakeJournal(),
        report_builder_func=lambda journal, *, limit: "",
        next_actions_builder_func=lambda journal, *, profile, portfolio_state, limit: "",
    )

    assert result.rebalance_generated is True
    assert "# Dry-Run Rebalance Proposal" in result.rebalance_markdown
    assert "AAPL" in result.rebalance_markdown
    assert "NVDA" in result.rebalance_markdown
    assert "| Source current value | $5,000.00 |" in result.rebalance_markdown
    assert "| Source target value | $1,400.00 |" in result.rebalance_markdown
    assert "| Rank gap | 7 |" in result.rebalance_markdown
    assert "| Source review due | n/a |" in result.rebalance_markdown


def test_cycle_can_build_account_action_plan(tmp_path):
    class FakeRunner:
        def run_and_record(self, packet, **kwargs):
            return f"decision-{packet.symbol}"

    class FakeJournal:
        db_path = tmp_path / "journal.db"
        recorded_plans = []

        def list_recommendation_table(self, limit=10):
            return []

        def summarize_benchmark_performance(self):
            return {
                "evaluated_decisions": 0,
                "average_excess_return_pct": 0.0,
                "decisions_beating_benchmark": 0,
            }

        def record_action_plan(self, plan):
            self.recorded_plans.append(plan)
            return plan["plan_id"]

    class FakeAccountActionPlanBuilder:
        def build(self, journal, *, profile, portfolio_state, limit=10):
            return type(
                "Plan",
                (),
                {
                    "to_dict": lambda self: {
                        "schema_version": 1,
                        "plan_id": "plan-123",
                        "mode": "dry_run",
                        "status": "ready",
                        "intents": [{"symbol": "NVDA", "intent_type": "BUY"}],
                    }
                },
            )()

    result = run_longterm_cycle(
        profile=_build_profile(),
        manual_ideas=[],
        motley_fool_settings=MotleyFoolCaptureSettings(enabled=False, cookie_ready=False),
        runner=FakeRunner(),
        journal_db_path=tmp_path / "journal.db",
        portfolio_state=PortfolioState(cash=5000, holdings=[]),
        journal_factory=lambda path: FakeJournal(),
        report_builder_func=lambda journal, *, limit: "",
        next_actions_builder_func=lambda journal, *, profile, portfolio_state, limit: "",
        account_action_plan_builder=FakeAccountActionPlanBuilder(),
    )

    assert result.account_action_plan_generated is True
    assert result.account_action_plan["mode"] == "dry_run"
    assert result.account_action_plan["intents"][0]["symbol"] == "NVDA"
    assert FakeJournal.recorded_plans[0]["mode"] == "dry_run"


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


def test_orchestration_cli_loads_portfolio_state_when_provided(tmp_path, capsys):
    idea_path = tmp_path / "idea.json"
    idea_path.write_text('{"symbol":"aapl","company_name":"Apple"}', encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        '{"account_strategy_mode":"roth_ira","tradable_capital":35000,"protected_symbols":["FXAIX"],"benchmark_symbol":"FXAIX","defensive_parking_symbol":"SPY"}',
        encoding="utf-8",
    )
    portfolio_state_path = tmp_path / "portfolio.json"
    portfolio_state_path.write_text(
        '{"cash":5000,"holdings":[{"symbol":"FXAIX","market_value":34000,"quantity":120.5}]}',
        encoding="utf-8",
    )

    def fake_cycle(**kwargs):
        assert kwargs["portfolio_state"].cash == 5000.0
        assert kwargs["portfolio_state"].protected_symbols == ["FXAIX"]
        return {
            "status": "completed",
            "decision_ids": ["decision-AAPL"],
            "total_idea_count": 1,
            "capture_status": "disabled",
            "next_actions_markdown": "# actions\n",
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--idea-file",
            str(idea_path),
            "--profile-config",
            str(profile_path),
            "--portfolio-state",
            str(portfolio_state_path),
        ]
    )

    exit_code = run_cli(args, cycle_func=fake_cycle)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"next_actions_markdown": "# actions\\n"' in captured.out


def test_orchestration_cli_passes_launch_login_flag(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        '{"account_strategy_mode":"roth_ira","tradable_capital":35000,"protected_symbols":["FXAIX"],"benchmark_symbol":"FXAIX","defensive_parking_symbol":"SPY"}',
        encoding="utf-8",
    )

    def fake_cycle(**kwargs):
        assert kwargs["launch_login_if_needed"] is True
        return {
            "status": "completed",
            "decision_ids": [],
            "total_idea_count": 0,
            "capture_status": "disabled",
            "setup_status": "not_requested",
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--launch-login-if-needed",
        ]
    )

    exit_code = run_cli(args, cycle_func=fake_cycle)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"setup_status": "not_requested"' in captured.out
