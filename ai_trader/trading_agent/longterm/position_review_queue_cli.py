"""CLI entrypoint for the no-submit position review queue."""

from __future__ import annotations

from longterm.position_review_queue import run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv)


__all__ = ["main"]
