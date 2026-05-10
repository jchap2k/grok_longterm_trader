import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_capture_cli import build_parser, run_cli


def test_capture_cli_writes_output_file(tmp_path, capsys):
    output = tmp_path / "new_recommendations.json"

    def fake_capture(source_key, *, profile_dir=None, url=None):
        assert source_key == "new_recommendations"
        assert profile_dir == "profile"
        assert url is None
        return [{"symbol": "NVDA", "company_name": "NVIDIA"}]

    code = run_cli(
        build_parser().parse_args(
            [
                "--source",
                "new_recommendations",
                "--profile-dir",
                "profile",
                "--output",
                str(output),
            ]
        ),
        capture_func=fake_capture,
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed == saved == [{"company_name": "NVIDIA", "symbol": "NVDA"}]
