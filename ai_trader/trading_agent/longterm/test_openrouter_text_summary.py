import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.openrouter_text_summary import (
    OpenRouterTextSummaryClient,
    build_openrouter_text_summary_messages,
)


def test_openrouter_text_summary_prompt_uses_supplied_text_only():
    messages = build_openrouter_text_summary_messages(
        {
            "symbol": "NVDA",
            "title": "Nvidia earnings recap",
            "url": "https://example.com/nvda",
            "published_at": "2026-05-12",
            "text": "Nvidia said data-center demand stayed strong. Gross margin pressure remains a watch item.",
        },
        as_of_date="2026-05-13",
    )
    joined = "\n".join(message["content"] for message in messages)

    assert "summarize only the supplied source text" in joined
    assert "Do not browse" in joined
    assert "Do not make buy, sell, hold, add, reduce, rebalance, or sizing recommendations" in joined
    assert "article_evidence_summaries" in joined
    assert "snippet_grounded" in joined
    assert "Nvidia said data-center demand stayed strong" in joined
    assert "2026-05-13" in joined


def test_openrouter_text_summary_client_returns_normalized_payload(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {
                    "prompt_tokens": 600,
                    "completion_tokens": 180,
                    "total_tokens": 780,
                    "cost": 0.000114,
                },
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "symbol": "nvda",
                                    "source_title": "Nvidia earnings recap",
                                    "source_url": "https://example.com/nvda",
                                    "summary": "Data-center demand remained strong while margins need monitoring.",
                                    "catalyst_tags": ["ai_infrastructure", "earnings"],
                                    "thesis_relevance": "high",
                                    "article_evidence_summaries": [
                                        {
                                            "title": "Nvidia earnings recap",
                                            "url": "https://example.com/nvda",
                                            "summary": "Nvidia reported strong data-center demand.",
                                            "key_facts": ["Data-center demand stayed strong."],
                                            "risk_flags": ["Margin pressure remains a watch item."],
                                            "confidence": 0.78,
                                            "basis": "source_text",
                                        }
                                    ],
                                    "warnings": "none",
                                }
                            )
                        }
                    }
                ],
            }

    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)
    client = OpenRouterTextSummaryClient(api_key="test-openrouter-key")

    result = client.summarize(
        {
            "symbol": "NVDA",
            "title": "Nvidia earnings recap",
            "url": "https://example.com/nvda",
            "text": "Nvidia said data-center demand stayed strong.",
        },
        as_of_date="2026-05-13",
    )

    assert calls[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-openrouter-key"
    assert calls[0]["json"]["model"] == "xiaomi/mimo-v2-flash"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert calls[0]["json"]["temperature"] == 0.0
    assert result["source_type"] == "openrouter_text_summary"
    assert result["provider"] == "openrouter"
    assert result["model"] == "xiaomi/mimo-v2-flash"
    assert result["symbol"] == "NVDA"
    assert result["source_urls"] == ["https://example.com/nvda"]
    assert result["article_evidence_summaries"][0]["basis"] == "snippet_grounded"
    assert result["article_evidence_summaries"][0]["key_facts"] == ["Data-center demand stayed strong."]
    assert result["warnings"] == ["none"]

    usage = client.usage_summary()
    assert usage["provider"] == "openrouter"
    assert usage["request_count"] == 1
    assert usage["prompt_tokens"] == 600
    assert usage["completion_tokens"] == 180
    assert usage["estimated_total_cost_usd"] == 0.000114


def test_openrouter_text_summary_client_falls_back_when_model_json_is_malformed(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "choices": [{"message": {"content": '{"summary": "unterminated"'}}],
            }

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())
    client = OpenRouterTextSummaryClient(api_key="test-key")

    result = client.summarize(
        {
            "symbol": "MSFT",
            "title": "Microsoft AI update",
            "url": "https://example.com/msft",
            "text": "Microsoft said cloud AI demand was durable. Capex remains high.",
        },
        as_of_date="2026-05-13",
    )

    assert result["symbol"] == "MSFT"
    assert result["summary"] == "Microsoft said cloud AI demand was durable. Capex remains high."
    assert result["article_evidence_summaries"][0]["basis"] == "snippet_grounded_fallback"
    assert "openrouter_malformed_json_fallback" in result["warnings"]
    assert "malformed_json_error" in result


def test_openrouter_text_summary_auth_error_does_not_log_key(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())
    client = OpenRouterTextSummaryClient(api_key="secret-value")

    with pytest.raises(RuntimeError) as excinfo:
        client.summarize({"symbol": "AAPL", "text": "Apple sells devices."})

    message = str(excinfo.value)
    assert "OPENROUTER_API_KEY" in message
    assert "secret-value" not in message
