"""Cache-friendly recommendation table enrichment."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable


class CachedRecommendationEnricher:
    """Enrich symbols once per day and cache the result on disk."""

    def __init__(
        self,
        *,
        fetch: Callable[[str], dict],
        cache_path: str | Path,
        today: str | None = None,
    ):
        self.fetch = fetch
        self.cache_path = Path(cache_path)
        self.today = today or date.today().isoformat()

    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload

    def _write_cache(self, payload: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def enrich(self, symbol: str) -> dict:
        normalized = symbol.upper()
        cache = self._load_cache()
        cached = cache.get(normalized)
        if cached and cached.get("data_as_of") == self.today:
            return dict(cached)

        enriched = dict(self.fetch(normalized) or {})
        enriched["data_as_of"] = self.today
        cache[normalized] = enriched
        self._write_cache(cache)
        return enriched
