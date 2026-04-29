"""Parsing helpers for long-term Grok decision responses."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _coerce_numeric_fields(decision: dict[str, Any]) -> dict[str, Any]:
    for key in ("confidence", "suggested_size_pct"):
        value = decision.get(key)
        if isinstance(value, str):
            try:
                numeric = float(value)
            except ValueError:
                continue
            decision[key] = int(numeric) if key == "confidence" else numeric
    return decision


def parse_decision_response(raw_response: str) -> dict[str, Any]:
    """Parse strict or fenced JSON from a Grok decision response."""
    text = (raw_response or "").strip()
    match = _FENCED_JSON_RE.search(text)
    if match:
        text = match.group(1).strip()
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Decision response must parse to a JSON object.")
    return _coerce_numeric_fields(parsed)
