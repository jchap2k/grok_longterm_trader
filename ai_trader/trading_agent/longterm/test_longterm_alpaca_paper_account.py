import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brokers.base_broker import AccountInfo, Position
from longterm.alpaca_paper_account import (
    AlpacaPaperAccountReader,
    paper_account_snapshot_to_portfolio_state,
)
from longterm.alpaca_paper_account_cli import build_parser, run_cli
from portfolio.portfolio_profile import PortfolioProfile


class FakeReadOnlyBroker:
    def __init__(self):
        self.connected = False
        self.disconnected = False

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.disconnected = True

    def get_account_info(self):
        return AccountInfo(
            cash=1250.5,
            portfolio_value=12000.0,
            buying_power=2500.0,
            positions=[
                Position(
                    symbol="AAPL",
                    quantity=10,
                    avg_entry_price=150.0,
                    current_price=185.0,
                    unrealized_pnl=350.0,
                    unrealized_pnl_percent=23.3,
                ),
                Position(
                    symbol="FXAIX",
                    quantity=20,
                    avg_entry_price=170.0,
                    current_price=180.0,
                    unrealized_pnl=200.0,
                    unrealized_pnl_percent=5.8,
                ),
            ],
        )


class ChattyFakeReadOnlyBroker(FakeReadOnlyBroker):
    def connect(self):
        print("connected noise")
        return super().connect()

    def disconnect(self):
        print("disconnected noise")
        super().disconnect()


def test_alpaca_paper_reader_converts_account_to_portfolio_state():
    profile = PortfolioProfile(protected_symbols=["FXAIX"])
    broker = FakeReadOnlyBroker()

    snapshot = AlpacaPaperAccountReader(broker=broker).read_snapshot(profile=profile)
    state = paper_account_snapshot_to_portfolio_state(snapshot)

    assert broker.connected is True
    assert broker.disconnected is True
    assert snapshot.mode == "paper"
    assert snapshot.cash == 1250.5
    assert snapshot.portfolio_value == 12000.0
    assert state.cash == 1250.5
    assert state.holding_value("AAPL") == 1850.0
    assert state.holding_value("FXAIX") == 3600.0
    assert state.active_market_value == 1850.0
    assert state.protected_market_value == 3600.0


def test_alpaca_paper_reader_rejects_non_paper_mode():
    broker = FakeReadOnlyBroker()

    try:
        AlpacaPaperAccountReader(broker=broker, paper_trading=False).read_snapshot()
    except ValueError as exc:
        assert "paper" in str(exc).lower()
    else:
        raise AssertionError("Expected non-paper mode to be rejected.")


def test_alpaca_paper_cli_can_emit_portfolio_state_json_with_injected_reader(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    output_path = tmp_path / "portfolio_state.json"
    profile_path.write_text(
        json.dumps({"protected_symbols": ["FXAIX"], "tradable_capital": 34000}),
        encoding="utf-8",
    )

    def fake_reader_factory():
        return AlpacaPaperAccountReader(broker=FakeReadOnlyBroker())

    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--portfolio-state-output",
            str(output_path),
        ]
    )

    exit_code = run_cli(args, reader_factory=fake_reader_factory)
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["mode"] == "paper"
    assert saved["cash"] == 1250.5
    assert saved["protected_symbols"] == ["FXAIX"]
    assert saved["holdings"][0]["symbol"] == "AAPL"


def test_alpaca_paper_cli_keeps_stdout_json_when_broker_prints(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")

    def fake_reader_factory():
        return AlpacaPaperAccountReader(broker=ChattyFakeReadOnlyBroker())

    parser = build_parser()
    args = parser.parse_args(["--profile-config", str(profile_path)])

    run_cli(args, reader_factory=fake_reader_factory)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["mode"] == "paper"
    assert "connected noise" in captured.err
