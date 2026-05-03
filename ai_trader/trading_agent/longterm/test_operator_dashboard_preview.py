import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.operator_dashboard_preview import inspect_dashboard_site
from longterm.operator_dashboard_preview_cli import build_parser, run_cli


def _write_site(tmp_path):
    site_dir = tmp_path / "site"
    ticker_dir = site_dir / "tickers"
    ticker_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    (ticker_dir / "MSFT.html").write_text("<html>msft</html>", encoding="utf-8")
    (ticker_dir / "MA.html").write_text("<html>ma</html>", encoding="utf-8")
    return site_dir


def test_inspect_dashboard_site_returns_openable_file_url(tmp_path):
    site_dir = _write_site(tmp_path)

    result = inspect_dashboard_site(site_dir)

    assert result["ready"] is True
    assert result["index_exists"] is True
    assert result["ticker_page_count"] == 2
    assert result["index_path"].endswith("index.html")
    assert result["file_url"].startswith("file:///")
    assert result["sample_ticker_pages"] == ["MA.html", "MSFT.html"]


def test_inspect_dashboard_site_reports_missing_index(tmp_path):
    site_dir = tmp_path / "missing_site"
    site_dir.mkdir()

    result = inspect_dashboard_site(site_dir)

    assert result["ready"] is False
    assert "index_html_missing" in result["blockers"]


def test_dashboard_preview_cli_prints_json_and_can_open(tmp_path, capsys):
    site_dir = _write_site(tmp_path)
    opened = []

    code = run_cli(
        build_parser().parse_args(["--site-dir", str(site_dir), "--open", "--json"]),
        opener=opened.append,
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["ready"] is True
    assert printed["ticker_page_count"] == 2
    assert opened == [printed["file_url"]]
