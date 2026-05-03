"""CLI for previewing generated operator dashboard sites."""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path
from typing import Callable

from longterm.operator_dashboard_preview import (
    build_dashboard_preview_markdown,
    inspect_dashboard_site,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and preview a generated operator dashboard site.")
    parser.add_argument("--site-dir", required=True)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def run_cli(args: argparse.Namespace, *, opener: Callable[[str], object] | None = None) -> int:
    result = inspect_dashboard_site(args.site_dir)
    if args.open_browser and result["ready"]:
        (opener or webbrowser.open)(str(result["file_url"]))
    if args.report_output:
        output_path = Path(args.report_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(build_dashboard_preview_markdown(result), end="")
    return 0 if result["ready"] else 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_parser().parse_args(argv))


__all__ = ["build_parser", "main", "run_cli"]
