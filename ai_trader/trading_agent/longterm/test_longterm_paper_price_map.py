import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.paper_price_map import build_price_map_from_action_plan, build_price_map_markdown
from longterm.paper_price_map_cli import build_parser, run_cli


@dataclass
class FakeQuote:
    price: float


class FakeQuoteProvider:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def get_quote(self, symbol):
        self.calls.append(symbol)
        value = self.prices[symbol]
        if isinstance(value, Exception):
            raise value
        return FakeQuote(value)


class ChattyQuoteProvider(FakeQuoteProvider):
    def get_quote(self, symbol):
        print(f"price noise for {symbol}")
        return super().get_quote(symbol)

    def close(self):
        print("provider close noise")


def _chatty_provider_factory():
    print("provider factory noise")
    return ChattyQuoteProvider({"NVDA": 910.0})


def test_price_map_fetches_buy_and_rebalance_symbols_without_protected_symbols():
    plan = {
        "intents": [
            {"symbol": "NVDA", "intent_type": "BUY", "allowed": True},
            {"symbol": "MSFT", "source_symbol": "AAPL", "intent_type": "REBALANCE", "allowed": True},
            {"symbol": "FXAIX", "intent_type": "BUY", "allowed": True},
            {"symbol": "TSLA", "intent_type": "REVIEW", "allowed": True},
        ]
    }
    provider = FakeQuoteProvider({"NVDA": 910.12345, "MSFT": 415.0, "AAPL": 200.0})

    result = build_price_map_from_action_plan(
        plan,
        quote_provider=provider,
        protected_symbols={"FXAIX"},
    ).to_dict()

    assert provider.calls == ["NVDA", "MSFT", "AAPL"]
    assert result["price_map"] == {"AAPL": 200.0, "MSFT": 415.0, "NVDA": 910.1235}
    assert result["missing_symbols"] == []
    assert "NVDA" in build_price_map_markdown(result)


def test_price_map_records_missing_symbols_when_quote_fails_or_is_zero():
    plan = {
        "intents": [
            {"symbol": "NVDA", "intent_type": "BUY"},
            {"symbol": "MSFT", "intent_type": "BUY"},
        ]
    }
    provider = FakeQuoteProvider({"NVDA": RuntimeError("quote failed"), "MSFT": 0.0})

    result = build_price_map_from_action_plan(plan, quote_provider=provider).to_dict()

    assert result["price_map"] == {}
    assert result["missing_symbols"] == ["NVDA", "MSFT"]
    assert result["errors"]["NVDA"] == "quote failed"
    assert result["errors"]["MSFT"] == "quote_price_not_positive"


def test_price_map_cli_outputs_json_and_price_map_file(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    output_path = tmp_path / "prices.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(
        json.dumps({"intents": [{"symbol": "NVDA", "intent_type": "BUY"}]}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--action-plan",
            str(plan_path),
            "--price-map-output",
            str(output_path),
            "--json",
        ]
    )

    assert run_cli(args, quote_provider_factory=lambda: FakeQuoteProvider({"NVDA": 910.0})) == 0
    payload = json.loads(capsys.readouterr().out)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["price_map"] == {"NVDA": 910.0}
    assert written == {"NVDA": 910.0}


def test_price_map_cli_keeps_json_stdout_clean_when_provider_is_chatty(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    profile_path.write_text(json.dumps({"protected_symbols": ["FXAIX"]}), encoding="utf-8")
    plan_path.write_text(json.dumps({"intents": [{"symbol": "NVDA", "intent_type": "BUY"}]}), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--profile-config",
            str(profile_path),
            "--action-plan",
            str(plan_path),
            "--json",
        ]
    )

    assert run_cli(args, quote_provider_factory=_chatty_provider_factory) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["price_map"] == {"NVDA": 910.0}
    assert "price noise" not in captured.out
    assert "provider factory noise" not in captured.out
    assert "provider close noise" not in captured.out
    assert "price noise" in captured.err
    assert "provider factory noise" in captured.err
    assert "provider close noise" in captured.err
