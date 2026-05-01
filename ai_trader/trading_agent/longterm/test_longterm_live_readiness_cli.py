import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.live_readiness_cli import build_parser, run_cli


def test_live_readiness_cli_merges_base_observed_file_and_fragments(tmp_path, capsys):
    base_path = tmp_path / "base.json"
    fragment_path = tmp_path / "broker_fragment.json"
    base_path.write_text(
        json.dumps(
            {
                "dry_run_cycles": 30,
                "benchmark_proven": True,
                "paper_trading_verified": True,
                "protected_symbol_enforced": True,
                "manual_approval": True,
                "kill_switch": True,
                "audit_logs": True,
                "broker_read_reconciliation": True,
                "explicit_live_mode_config": True,
                "secrets_not_committed": True,
            }
        ),
        encoding="utf-8",
    )
    fragment_path.write_text(json.dumps({"broker_capability_match": True}), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        [
            "--observed-file",
            str(base_path),
            "--observed-fragment",
            str(fragment_path),
            "--json",
        ]
    )

    assert run_cli(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] is True
    assert payload["unmet_gate_keys"] == []


def test_live_readiness_cli_fragment_overrides_base_value(tmp_path, capsys):
    base_path = tmp_path / "base.json"
    fragment_path = tmp_path / "broker_fragment.json"
    base_path.write_text(json.dumps({"broker_capability_match": False}), encoding="utf-8")
    fragment_path.write_text(json.dumps({"broker_capability_match": True}), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        [
            "--observed-file",
            str(base_path),
            "--observed-fragment",
            str(fragment_path),
            "--json",
        ]
    )

    run_cli(args)
    payload = json.loads(capsys.readouterr().out)
    gate = next(item for item in payload["gates"] if item["key"] == "broker_capability_match")

    assert gate["observed_value"] is True
