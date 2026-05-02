import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.news_relevance_enrichment import (
    CachedNewsProvider,
    FakeNewsProvider,
    PolygonNewsProvider,
    enrich_idea_with_relevant_news,
    enrich_ideas_with_relevant_news,
    rank_relevant_news,
)
from longterm.news_relevance_enrichment_cli import build_parser, run_cli


def _articles() -> list[dict]:
    return [
        {
            "title": "Amazon signs multi-year AWS AI infrastructure deal with major enterprise customer",
            "url": "https://example.com/aws-ai-contract",
            "published_utc": "2026-05-01T13:00:00Z",
            "publisher": {"name": "Reuters"},
            "description": "The contract expands Amazon's AI cloud backlog and data-center demand.",
            "tickers": ["AMZN"],
        },
        {
            "title": "Why Amazon stock moved higher today",
            "url": "https://example.com/amzn-stock-move",
            "published_utc": "2026-05-01T14:00:00Z",
            "publisher": {"name": "Generic Blog"},
            "description": "Shares rose after the market opened.",
            "tickers": ["AMZN"],
        },
        {
            "title": "Amazon reports AWS growth and advertising margin expansion in Q1",
            "url": "https://example.com/amzn-earnings",
            "published_utc": "2026-04-29T21:00:00Z",
            "publisher": {"name": "Yahoo Finance"},
            "description": "Management highlighted AWS, advertising, and free cash flow.",
            "tickers": ["AMZN"],
        },
        {
            "title": "Amazon reports AWS growth and advertising margin expansion in Q1",
            "url": "https://example.com/amzn-earnings",
            "published_utc": "2026-04-29T21:00:00Z",
            "publisher": {"name": "Yahoo Finance"},
            "description": "Duplicate syndicated item.",
            "tickers": ["AMZN"],
        },
    ]


def test_rank_relevant_news_filters_price_action_noise_and_dedupes_urls():
    ranked = rank_relevant_news(
        "AMZN",
        _articles(),
        business_context="Amazon AWS cloud advertising AI logistics ecommerce",
        max_items=5,
    )

    assert [item["url"] for item in ranked] == [
        "https://example.com/aws-ai-contract",
        "https://example.com/amzn-earnings",
    ]
    assert ranked[0]["impact_category"] == "Major Contract - High"
    assert ranked[0]["source"] == "Reuters"
    assert ranked[0]["relevance_score"] > ranked[1]["relevance_score"]
    assert all("stock moved" not in item["title"].lower() for item in ranked)


def test_enrich_idea_with_relevant_news_adds_packet_context_and_source_note():
    provider = FakeNewsProvider({"AMZN": _articles()})
    idea = {
        "symbol": "AMZN",
        "company_name": "Amazon",
        "idea_source": "manual",
        "business_summary": "Amazon AWS cloud advertising AI logistics ecommerce",
    }

    enriched = enrich_idea_with_relevant_news(idea, provider=provider, as_of_date="2026-05-02")

    assert len(enriched["relevant_news"]) == 2
    assert "Latest relevant news:" in enriched["source_notes"][-1]
    assert "AWS AI infrastructure deal" in enriched["source_notes"][-1]


def test_enrich_ideas_with_relevant_news_uses_symbol_specific_context():
    provider = FakeNewsProvider({"AMZN": _articles()})

    enriched = enrich_ideas_with_relevant_news(
        [{"symbol": "amzn", "business_summary": "AWS AI cloud"}],
        provider=provider,
        as_of_date="2026-05-02",
    )

    assert enriched[0]["symbol"] == "AMZN"
    assert enriched[0]["relevant_news"][0]["impact_category"] == "Major Contract - High"


def test_cached_news_provider_reuses_daily_symbol_cache(tmp_path):
    calls = []

    def fetch(symbol: str, **kwargs):
        calls.append((symbol, kwargs))
        return _articles()

    provider = CachedNewsProvider(fetch=fetch, cache_path=tmp_path / "news_cache.json", today="2026-05-02")

    first = provider.fetch_news("AMZN", limit=10)
    second = provider.fetch_news("AMZN", limit=10)

    assert first == second
    assert len(calls) == 1
    cache = json.loads((tmp_path / "news_cache.json").read_text(encoding="utf-8"))
    assert cache["AMZN"]["data_as_of"] == "2026-05-02"


def test_polygon_news_provider_builds_expected_request(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "Amazon earnings", "article_url": "https://example.com"}]}

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return Response()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    provider = PolygonNewsProvider(api_key="abc123")

    results = provider.fetch_news("AMZN", published_after="2026-04-01", limit=4)

    assert captured["url"].endswith("/v2/reference/news")
    assert captured["params"]["ticker"] == "AMZN"
    assert captured["params"]["apiKey"] == "abc123"
    assert captured["params"]["published_utc.gte"] == "2026-04-01"
    assert results[0]["title"] == "Amazon earnings"


def test_news_relevance_cli_enriches_from_snapshot_file(tmp_path, capsys):
    ideas = tmp_path / "ideas.json"
    snapshots = tmp_path / "news.json"
    output = tmp_path / "enriched.json"
    ideas.write_text(
        json.dumps([{"symbol": "AMZN", "business_summary": "AWS AI cloud"}]),
        encoding="utf-8",
    )
    snapshots.write_text(json.dumps({"AMZN": _articles()}), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--idea-batch",
                str(ideas),
                "--snapshot-file",
                str(snapshots),
                "--output",
                str(output),
                "--as-of-date",
                "2026-05-02",
            ]
        )
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert payload[0]["relevant_news"][0]["url"] == "https://example.com/aws-ai-contract"
    assert summary["mode"] == "snapshot_file"
    assert summary["enriched_count"] == 1
