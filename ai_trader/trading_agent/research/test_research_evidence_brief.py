from research.research_evidence_brief import build_research_evidence_brief


def test_research_evidence_brief_includes_source_recency_and_repeat_signal():
    brief = build_research_evidence_brief(
        {
            "symbol": "ADBE",
            "source_recommendation_count": 3,
            "source_fresh_recommendation": True,
            "source_priority_reason": "fresh_motley_fool_recommendation",
            "source_priority_boost": 10,
        }
    )

    assert "Source signal:" in brief
    assert "recommendations 3" in brief
    assert "fresh recommendation yes" in brief
    assert "reason fresh_motley_fool_recommendation" in brief
    assert "priority boost 10" in brief
