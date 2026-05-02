"""Playwright capture helpers for Motley Fool premium tables."""

from __future__ import annotations

import os
from pathlib import Path

from longterm.motley_fool_intake import (
    default_motley_fool_sources,
    motley_table_payloads_to_ideas,
)


DEFAULT_PROFILE_DIR = Path(os.path.expanduser("~")) / ".grok3api_chrome_profile"


def capture_motley_fool_ideas(
    source_key: str,
    *,
    profile_dir: str | Path | None = None,
    url: str | None = None,
) -> list[dict]:
    """Capture Motley Fool table rows through the logged-in Playwright profile."""
    table_payloads = capture_motley_fool_table_payloads(
        source_key,
        profile_dir=profile_dir,
        url=url,
    )
    return motley_table_payloads_to_ideas(source_key, table_payloads)


def capture_motley_fool_table_payloads(
    source_key: str,
    *,
    profile_dir: str | Path | None = None,
    url: str | None = None,
) -> list[dict]:
    """Open a premium page and extract semantic table payloads."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("playwright is required for Motley Fool capture") from exc

    sources = default_motley_fool_sources()
    target_url = url or sources[source_key].url
    user_data_dir = str(profile_dir or DEFAULT_PROFILE_DIR)

    with sync_playwright() as playwright:
        context = _launch_persistent_context(playwright, user_data_dir)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function(
                """
                () => Array.from(document.querySelectorAll('table')).some((table) => {
                    const text = table.innerText.replace(/\\u200c/g, '').trim();
                    return text.includes('expand current row') || /\\b[A-Z]{1,5}\\b/.test(text);
                })
                """,
                timeout=60000,
            )
            return extract_table_payloads_from_page(page)
        finally:
            context.close()


def extract_table_payloads_from_page(page) -> list[dict]:
    """Extract table-like data from the active Playwright page."""
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('table')).map((table) => {
            const cleanText = (value) => (value || '').replace(/\\u200c/g, '').trim();
            const visibleRowCells = (row) => {
                const cellTexts = Array.from(row.querySelectorAll('td,th')).map((cell) => cleanText(cell.innerText));
                if (cellTexts.some(Boolean)) {
                    return cellTexts;
                }
                return cleanText(row.innerText).split(/\\t+/).map(cleanText).filter(Boolean);
            };
            const visibleRowLinks = (row) => Array.from(row.querySelectorAll('td,th')).map((cell) => {
                const anchor = cell.querySelector('a[href]');
                return anchor ? anchor.href : '';
            });
            const heading = table.closest('section, div')?.querySelector('h1,h2,h3')?.innerText || '';
            const headers = Array.from(table.querySelectorAll('thead th')).map((cell) => cleanText(cell.innerText));
            const extractedRows = Array.from(table.querySelectorAll('tbody tr')).map((row) => ({
                cells: visibleRowCells(row),
                links: visibleRowLinks(row),
            })).filter((row) => row.cells.some(Boolean));
            return {
                title: heading,
                headers,
                rows: extractedRows.map((row) => row.cells),
                row_links: extractedRows.map((row) => row.links),
            };
        }).filter((table) => table.rows.length > 0);
        """
    )


def _launch_persistent_context(playwright, user_data_dir: str):
    """Launch with Chrome first, then fall back to bundled Chromium."""
    launch_options = {
        "user_data_dir": user_data_dir,
        "headless": False,
        "viewport": {"width": 1600, "height": 1000},
    }
    try:
        return playwright.chromium.launch_persistent_context(
            channel="chrome",
            **launch_options,
        )
    except Exception:
        return playwright.chromium.launch_persistent_context(**launch_options)
