"""Local preview helpers for generated operator dashboard sites."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def inspect_dashboard_site(site_dir: str | Path) -> dict[str, object]:
    """Validate a generated static dashboard site and return openable paths."""
    root = Path(site_dir).expanduser().resolve()
    index_path = root / "index.html"
    ticker_dir = root / "tickers"
    ticker_pages = sorted(path.name for path in ticker_dir.glob("*.html")) if ticker_dir.exists() else []
    blockers: list[str] = []
    if not root.exists():
        blockers.append("site_dir_missing")
    if not index_path.exists():
        blockers.append("index_html_missing")
    if not ticker_pages:
        blockers.append("ticker_pages_missing")
    return {
        "schema_version": 1,
        "mode": "operator_dashboard_preview",
        "ready": not blockers,
        "blockers": blockers,
        "site_dir": str(root),
        "index_path": str(index_path),
        "index_exists": index_path.exists(),
        "file_url": _file_url(index_path),
        "ticker_page_count": len(ticker_pages),
        "sample_ticker_pages": ticker_pages[:10],
        "notes": [
            "Preview helper only. It does not generate broker orders or mutate artifacts.",
            "If --open is used, the local static index is opened in the default browser.",
        ],
    }


def build_dashboard_preview_markdown(result: dict[str, object]) -> str:
    """Render a short operator-facing preview report."""
    lines = [
        "# Operator Dashboard Preview",
        "",
        f"- Ready: {'yes' if result.get('ready') else 'no'}",
        f"- Site dir: `{result.get('site_dir')}`",
        f"- Index: `{result.get('index_path')}`",
        f"- File URL: `{result.get('file_url')}`",
        f"- Ticker pages: {int(result.get('ticker_page_count') or 0)}",
        "",
        "## Sample Ticker Pages",
        "",
    ]
    pages = [str(item) for item in result.get("sample_ticker_pages") or []]
    lines.extend(f"- {page}" for page in pages) if pages else lines.append("- none")
    lines.extend(["", "## Blockers", ""])
    blockers = [str(item) for item in result.get("blockers") or []]
    lines.extend(f"- {blocker}" for blocker in blockers) if blockers else lines.append("- none")
    return "\n".join(lines) + "\n"


def _file_url(path: Path) -> str:
    resolved = path.expanduser().resolve()
    return "file:///" + quote(str(resolved).replace("\\", "/"), safe="/:")


__all__ = ["build_dashboard_preview_markdown", "inspect_dashboard_site"]
