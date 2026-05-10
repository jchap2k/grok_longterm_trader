"""State tracking for Motley Fool new-recommendation deltas."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable


DATE_FORMATS = (
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%m/%d/%Y",
    "%m/%d/%y",
)


def build_motley_fool_new_recs_delta(
    *,
    ideas: Iterable[dict],
    previous_state: dict | None = None,
    now: str,
    bootstrap_if_empty: bool = False,
) -> dict:
    """Return new Motley Fool recommendations plus the next persisted state."""
    previous_state = dict(previous_state or {})
    prior_seen = set(_as_string_list(previous_state.get("seen_recommendation_keys")))
    prior_latest = str(previous_state.get("latest_recommendation_date") or "")
    empty_state = not prior_seen and not prior_latest

    normalized = [_normalize_idea(idea) for idea in ideas if isinstance(idea, dict)]
    normalized = [item for item in normalized if item["symbol"]]
    latest_date = _latest_recommendation_date(
        [item["recommendation_date"] for item in normalized],
        fallback=prior_latest,
    )

    seen_next = set(prior_seen)
    new_items: list[dict] = []
    for item in normalized:
        seen_next.add(item["recommendation_key"])
        if item["recommendation_key"] in prior_seen:
            continue
        if bootstrap_if_empty and empty_state:
            continue
        new_items.append(item)

    new_symbols = sorted({item["symbol"] for item in new_items})
    next_state = {
        "schema_version": 1,
        "mode": "motley_fool_new_recommendations_state",
        "updated_at": now,
        "previous_latest_recommendation_date": prior_latest,
        "latest_recommendation_date": latest_date,
        "seen_recommendation_keys": sorted(seen_next),
        "seen_count": len(seen_next),
        "last_new_count": len(new_items),
        "last_new_symbols": new_symbols,
    }
    return {
        "schema_version": 1,
        "mode": "motley_fool_new_recs_delta",
        "updated_at": now,
        "previous_latest_recommendation_date": prior_latest,
        "latest_recommendation_date": latest_date,
        "new_count": len(new_items),
        "new_symbols": new_symbols,
        "new_recommendations": [item["idea"] for item in new_items],
        "seen_count": len(seen_next),
        "state": next_state,
    }


def load_state(path: str | Path) -> dict:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def save_state(path: str | Path, state: dict) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _normalize_idea(idea: dict) -> dict:
    symbol = str(idea.get("symbol") or idea.get("ticker") or "").strip().upper()
    recommendation_date = _extract_recommendation_date(idea)
    action = _extract_action(idea)
    source_url = str(
        idea.get("motley_fool_company_url")
        or idea.get("source_url")
        or idea.get("url")
        or ""
    ).strip()
    company_name = str(idea.get("company_name") or idea.get("name") or "").strip()
    recommendation_key = _recommendation_key(
        symbol=symbol,
        recommendation_date=recommendation_date,
        action=action,
        source_url=source_url,
        company_name=company_name,
    )
    enriched_idea = dict(idea)
    if recommendation_date and not enriched_idea.get("recommendation_date"):
        enriched_idea["recommendation_date"] = recommendation_date
    if action and not enriched_idea.get("recommendation_action"):
        enriched_idea["recommendation_action"] = action
    enriched_idea["motley_fool_recommendation_key"] = recommendation_key
    return {
        "symbol": symbol,
        "recommendation_date": recommendation_date,
        "recommendation_key": recommendation_key,
        "idea": enriched_idea,
    }


def _extract_recommendation_date(idea: dict) -> str:
    for key in ("recommendation_date", "rec_date", "date"):
        value = str(idea.get(key) or "").strip()
        if value:
            return value
    notes = _notes_text(idea)
    match = re.search(r"Recommendation date:\s*([^.]+)", notes, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_action(idea: dict) -> str:
    for key in ("recommendation_action", "action", "rating"):
        value = str(idea.get(key) or "").strip()
        if value:
            return value
    notes = _notes_text(idea)
    match = re.search(r"New recommendation action:\s*([^.]+)", notes, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _notes_text(idea: dict) -> str:
    notes = idea.get("source_notes") or idea.get("notes") or ""
    if isinstance(notes, list):
        return "\n".join(str(item) for item in notes)
    return str(notes)


def _recommendation_key(
    *,
    symbol: str,
    recommendation_date: str,
    action: str,
    source_url: str,
    company_name: str,
) -> str:
    raw = "|".join(
        [
            "motley_fool_new_recommendations",
            symbol,
            recommendation_date.lower(),
            action.lower(),
            source_url.lower() or company_name.lower(),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"motley_fool_new_recommendations|{symbol}|{recommendation_date}|{digest}"


def _latest_recommendation_date(values: Iterable[str], *, fallback: str = "") -> str:
    best_text = fallback
    best_key = _date_sort_key(fallback)
    for value in values:
        sort_key = _date_sort_key(value)
        if sort_key and (not best_key or sort_key > best_key):
            best_key = sort_key
            best_text = value
    return best_text


def _date_sort_key(value: str) -> str:
    text = str(value or "").strip().rstrip(".")
    if not text:
        return ""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


__all__ = [
    "build_motley_fool_new_recs_delta",
    "load_state",
    "save_state",
]
