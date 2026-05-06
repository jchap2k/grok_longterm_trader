import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.grok_research_enrichment import normalize_grok_research_result
from longterm.perplexity_research_enrichment import (
    PerplexityResearchClient,
    build_perplexity_research_messages,
)


def _raw_response() -> dict:
    return {
        "symbol": "ADBE",
        "company_name": "Adobe",
        "as_of_date": "2026-05-05",
        "business_summary": "Adobe sells creative, document, and digital experience software.",
        "earnings_summary": {
            "quarter": "latest available",
            "summary": "Adobe continues to monetize creative workflows while AI pressure remains a watch item.",
            "key_takeaways": ["Recurring software revenue remains durable."],
        },
        "thesis_relevant_catalysts": [
            {
                "name": "AI workflow monetization",
                "direction": "positive",
                "time_horizon": "multi_year",
                "evidence": "Creative software customers may pay for AI-enabled productivity.",
                "source_urls": ["https://example.com/adbe-ai"],
                "confidence": 0.72,
            }
        ],
        "article_evidence_summaries": [
            {
                "title": "Adobe highlights AI workflow growth",
                "url": "https://example.com/adbe-ai",
                "source": "Example Finance",
                "date": "2026-05-01",
                "summary": "Adobe emphasized AI tools across Creative Cloud.",
                "thesis_relevance": "Supports the AI monetization thesis.",
                "key_facts": ["Creative Cloud remains central."],
                "risk_flags": ["AI competition is intense."],
                "confidence": 0.76,
                "basis": "source_grounded",
            }
        ],
        "bull_cases": ["AI tools could deepen workflow lock-in."],
        "bear_cases": ["Generative AI competitors could pressure pricing."],
        "thesis_watch_items": ["AI attach rate"],
        "risk_flags": ["competitive_ai_pressure"],
        "financial_snapshot": {"revenue": "$23.77B"},
        "model_estimated_scores": {
            "basis": "model_estimate",
            "quality": 86,
            "growth": 66,
            "valuation": 62,
            "safety": 75,
            "market_attention": 58,
        },
        "source_urls": ["https://example.com/adbe-ai"],
        "confidence": 0.74,
        "warnings": [],
    }


def test_perplexity_prompt_is_source_backed_and_not_fool_impersonation():
    messages = build_perplexity_research_messages(
        {
            "symbol": "ADBE",
            "company_name": "Adobe",
            "relevant_news": [{"title": "Adobe highlights AI", "url": "https://example.com/adbe-ai"}],
        },
        free_facts={"revenue": "$23.77B"},
        as_of_date="2026-05-05",
    )
    joined = "\n".join(message["content"] for message in messages)

    assert "Do not claim to be Motley Fool" in joined
    assert "source_type" in joined
    assert "perplexity_research_enrichment" in joined
    assert "model_estimate" in joined
    assert "Adobe highlights AI" in joined
    assert "At most 3 catalysts" in joined
    assert "At most 2 article summaries" in joined
    assert "Keep every string under 220 characters" in joined


def test_perplexity_client_returns_pipeline_compatible_enrichment(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "total_tokens": 1500,
                },
                "choices": [
                    {
                        "message": {
                            "content": "```json\n"
                            + json.dumps(_raw_response())
                            + "\n```"
                        }
                    }
                ]
            }

    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    client = PerplexityResearchClient(
        api_key="test-key",
        model="sonar",
        credits_purchased_to_date_usd=12.0,
    )
    raw = client.enrich({"symbol": "ADBE", "company_name": "Adobe"}, as_of_date="2026-05-05")
    normalized = normalize_grok_research_result(
        raw,
        idea={"symbol": "ADBE", "company_name": "Adobe"},
        as_of_date="2026-05-05",
    )

    assert calls[0]["json"]["model"] == "sonar"
    assert calls[0]["json"]["max_tokens"] == 3500
    assert "response_format" not in calls[0]["json"]
    assert calls[0]["json"]["web_search_options"]["search_context_size"] == "low"
    assert normalized["source_type"] == "perplexity_research_enrichment"
    assert normalized["symbol"] == "ADBE"
    assert normalized["source_urls"] == ["https://example.com/adbe-ai"]

    usage = client.usage_summary()
    assert usage["request_count"] == 1
    assert usage["request_fees_usd"] == 0.005
    assert usage["estimated_total_cost_usd"] == 0.0065
    assert usage["credits_purchased_to_date_usd"] == 12.0
    assert usage["estimated_remaining_to_tier_1_usd"] == 37.99
    assert usage["console_check_required"] is True


def test_perplexity_client_falls_back_to_response_citations(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                "citations": ["https://example.com/source"],
                "search_results": [
                    {
                        "title": "Adobe source result",
                        "url": "https://example.com/source",
                        "date": "2026-05-01",
                        "snippet": "Adobe cited AI workflow progress.",
                        "source": "web",
                    }
                ],
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "symbol": "ADBE",
                                    "company_name": "Adobe",
                                    "as_of_date": "2026-05-05",
                                    "business_summary": "Adobe sells software.",
                                    "thesis_relevant_catalysts": [],
                                    "article_evidence_summaries": [],
                                    "source_urls": ["https://example.com/..."],
                                    "confidence": 0.4,
                                    "warnings": [],
                                }
                            )
                        }
                    }
                ],
            }

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())
    client = PerplexityResearchClient(api_key="test-key")

    raw = client.enrich({"symbol": "ADBE", "company_name": "Adobe"}, as_of_date="2026-05-05")

    assert raw["source_urls"] == ["https://example.com/source"]
    assert raw["article_evidence_summaries"][0]["basis"] == "perplexity_search_result"
    assert raw["article_evidence_summaries"][0]["summary"] == "Adobe cited AI workflow progress."


def test_perplexity_client_returns_valid_fallback_when_json_is_malformed(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "citations": ["https://example.com/meta-ai"],
                "search_results": [
                    {
                        "title": "Meta AI spending remains a thesis watch item",
                        "url": "https://example.com/meta-ai",
                        "date": "2026-05-04",
                        "snippet": "Meta continues to invest heavily in AI infrastructure.",
                        "source": "web",
                    }
                ],
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"symbol":"META","company_name":"Meta Platforms",'
                                '"business_summary":"Meta operates social and AI platforms",'
                                '"thesis_relevant_catalysts":[{"name":"AI capex"'
                            )
                        }
                    }
                ],
            }

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())
    client = PerplexityResearchClient(api_key="test-key")

    raw = client.enrich({"symbol": "META", "company_name": "Meta Platforms"}, as_of_date="2026-05-05")

    assert raw["symbol"] == "META"
    assert raw["source_type"] == "perplexity_research_enrichment"
    assert raw["source_urls"] == ["https://example.com/meta-ai"]
    assert raw["article_evidence_summaries"][0]["basis"] == "perplexity_search_result"
    assert "perplexity_malformed_json_fallback" in raw["warnings"]
    assert "malformed_json_error" in raw


def test_perplexity_client_removes_display_abbreviated_urls(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "symbol": "TSLA",
                                    "company_name": "Tesla",
                                    "as_of_date": "2026-05-05",
                                    "business_summary": "Tesla sells EVs.",
                                    "thesis_relevant_catalysts": [
                                        {
                                            "name": "Autonomy",
                                            "direction": "mixed",
                                            "source_urls": ["https://example.com/full", "https://example.com/..."],
                                        }
                                    ],
                                    "article_evidence_summaries": [
                                        {
                                            "title": "Shortened link",
                                            "url": "https://example.com/...",
                                            "source_urls": ["https://example.com/..."],
                                        }
                                    ],
                                    "source_urls": ["https://example.com/full", "https://example.com/..."],
                                    "confidence": 0.5,
                                    "warnings": [],
                                }
                            )
                        }
                    }
                ],
            }

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())
    client = PerplexityResearchClient(api_key="test-key")

    raw = client.enrich({"symbol": "TSLA", "company_name": "Tesla"}, as_of_date="2026-05-05")

    assert raw["source_urls"] == ["https://example.com/full"]
    assert raw["thesis_relevant_catalysts"][0]["source_urls"] == ["https://example.com/full"]
    assert raw["article_evidence_summaries"][0]["url"] == ""
    assert raw["article_evidence_summaries"][0]["source_urls"] == []


def test_perplexity_client_auth_failure_does_not_leak_key(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise RuntimeError("401 Client Error")

    def fake_post(url, *, headers, json, timeout):
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)
    client = PerplexityResearchClient(api_key="  secret-test-key  ")

    with pytest.raises(RuntimeError) as excinfo:
        client.enrich({"symbol": "ADBE", "company_name": "Adobe"}, as_of_date="2026-05-05")

    message = str(excinfo.value)
    assert "authentication failed" in message.lower()
    assert "pplx-" in message
    assert "secret-test-key" not in message
    assert client.api_key == "secret-test-key"
