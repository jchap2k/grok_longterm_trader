import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.motley_fool_new_recs_state import (
    build_motley_fool_new_recs_delta,
)
from longterm.motley_fool_new_recs_state_cli import build_parser, run_cli


def _idea(symbol: str, recommendation_date: str, action: str = "Buy") -> dict:
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Corp",
        "motley_fool_company_url": f"https://www.fool.com/quote/{symbol.lower()}",
        "source_notes": [
            f"New recommendation action: {action}.",
            f"Recommendation date: {recommendation_date}.",
        ],
    }


def test_new_recs_delta_bootstraps_existing_recommendations_without_requeueing() -> None:
    result = build_motley_fool_new_recs_delta(
        ideas=[_idea("MSFT", "May 1, 2026"), _idea("NVDA", "May 4, 2026")],
        previous_state={},
        now="2026-05-09T16:00:00Z",
        bootstrap_if_empty=True,
    )

    assert result["previous_latest_recommendation_date"] == ""
    assert result["latest_recommendation_date"] == "May 4, 2026"
    assert result["new_count"] == 0
    assert result["new_symbols"] == []
    assert result["state"]["previous_latest_recommendation_date"] == ""
    assert result["state"]["latest_recommendation_date"] == "May 4, 2026"
    assert result["state"]["last_new_count"] == 0
    assert result["state"]["seen_count"] == 2


def test_new_recs_delta_tracks_previous_latest_and_new_symbols() -> None:
    initial = build_motley_fool_new_recs_delta(
        ideas=[_idea("MSFT", "May 1, 2026"), _idea("NVDA", "May 4, 2026")],
        previous_state={},
        now="2026-05-09T16:00:00Z",
        bootstrap_if_empty=True,
    )

    result = build_motley_fool_new_recs_delta(
        ideas=[
            _idea("MSFT", "May 1, 2026"),
            _idea("NVDA", "May 4, 2026"),
            _idea("ADBE", "May 8, 2026", action="Best Buy Now"),
        ],
        previous_state=initial["state"],
        now="2026-05-10T16:00:00Z",
    )

    assert result["previous_latest_recommendation_date"] == "May 4, 2026"
    assert result["latest_recommendation_date"] == "May 8, 2026"
    assert result["new_count"] == 1
    assert result["new_symbols"] == ["ADBE"]
    assert result["new_recommendations"][0]["symbol"] == "ADBE"
    assert result["state"]["previous_latest_recommendation_date"] == "May 4, 2026"
    assert result["state"]["latest_recommendation_date"] == "May 8, 2026"
    assert result["state"]["last_new_count"] == 1


def test_new_recs_state_cli_persists_state_and_delta(tmp_path) -> None:
    ideas_path = tmp_path / "ideas.json"
    state_path = tmp_path / "state.json"
    output_path = tmp_path / "delta.json"
    new_ideas_path = tmp_path / "new_ideas.json"
    ideas_path.write_text(
        """[
  {"symbol": "MSFT", "source_notes": ["New recommendation action: Buy.", "Recommendation date: May 1, 2026."]},
  {"symbol": "ADBE", "source_notes": ["New recommendation action: Buy.", "Recommendation date: May 8, 2026."]}
]""",
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--ideas-file",
                str(ideas_path),
                "--state-file",
                str(state_path),
                "--output",
                str(output_path),
                "--new-ideas-output",
                str(new_ideas_path),
                "--bootstrap-if-empty",
                "--now",
                "2026-05-09T16:00:00Z",
            ]
        )
    )

    assert code == 0
    assert state_path.exists()
    assert output_path.exists()
    assert new_ideas_path.exists()
    assert '"latest_recommendation_date": "May 8, 2026"' in output_path.read_text(encoding="utf-8")
    assert new_ideas_path.read_text(encoding="utf-8").strip() == "[]"
