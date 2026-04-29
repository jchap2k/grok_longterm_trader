"""Book-principle retrieval for long-term trading research prompts."""

from __future__ import annotations

from pathlib import Path


DEFAULT_NOTE_FILES = [
    "one_up_on_wall_street_notes.md",
    "the_little_book_that_still_beats_the_market_notes.md",
    "quality_investing_notes.md",
    "longterm_trader_research_direction.md",
    "longterm_reframing_of_existing_swing_books.md",
]


class BookPrinciplesProvider:
    """Small deterministic provider for curated long-term book notes."""

    def __init__(self, notes_dir: str | Path | None = None):
        if notes_dir is None:
            notes_dir = Path(__file__).resolve().parents[4] / "knowledge_agent" / "docs"
        self.notes_dir = Path(notes_dir)

    def recall(self, query: str, max_lines: int = 12) -> str:
        """Return relevant lines from curated book notes."""
        if not self.notes_dir.exists():
            return "No book principles found."

        query_tokens = {
            token.lower()
            for token in query.replace("-", " ").replace("_", " ").split()
            if len(token) > 2
        }
        query_stems = {token[:5] for token in query_tokens if len(token) >= 5}
        matches: list[str] = []
        fallback: list[str] = []

        for filename in DEFAULT_NOTE_FILES:
            path = self.notes_dir / filename
            if not path.exists():
                continue
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith("- ["):
                    continue
                if len(fallback) < max_lines:
                    fallback.append(line)
                lowered = line.lower()
                line_tokens = {
                    token.strip(".,:;()[]").lower()
                    for token in line.replace("-", " ").replace("_", " ").split()
                }
                line_stems = {token[:5] for token in line_tokens if len(token) >= 5}
                if any(token in lowered for token in query_tokens) or query_stems & line_stems:
                    matches.append(line)
                if len(matches) >= max_lines:
                    break
            if len(matches) >= max_lines:
                break

        selected = matches[:max_lines] or fallback[:max_lines]
        if not selected:
            return "No book principles found."
        return "Research principles from book notes:\n" + "\n".join(
            f"- {line}" for line in selected
        )
