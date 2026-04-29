import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.book_principles import BookPrinciplesProvider


def test_book_principles_provider_returns_relevant_lines_from_notes(tmp_path):
    notes_dir = tmp_path / "docs"
    notes_dir.mkdir()
    (notes_dir / "one_up_on_wall_street_notes.md").write_text(
        "\n".join(
            [
                "# One Up",
                "A stock is a business first, not a ticker symbol.",
                "The research process should classify companies before judging them.",
                "Debt and balance-sheet quality matter.",
            ]
        ),
        encoding="utf-8",
    )
    (notes_dir / "the_little_book_that_still_beats_the_market_notes.md").write_text(
        "Separate business quality from valuation discipline.",
        encoding="utf-8",
    )
    (notes_dir / "quality_investing_notes.md").write_text(
        "Quality investing favors durable returns on capital, recurring revenue, and pricing power.",
        encoding="utf-8",
    )

    provider = BookPrinciplesProvider(notes_dir=notes_dir)
    text = provider.recall("classification valuation balance sheet pricing power", max_lines=5)

    assert "classify companies" in text
    assert "balance-sheet quality" in text
    assert "valuation discipline" in text
    assert "pricing power" in text


def test_book_principles_provider_has_safe_fallback_for_missing_notes(tmp_path):
    provider = BookPrinciplesProvider(notes_dir=tmp_path / "missing")

    text = provider.recall("anything")

    assert "No book principles found" in text
