"""Read-only localhost dashboard server backed by artifact manifests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from longterm.operator_dashboard import build_operator_dashboard, build_operator_dashboard_site


DEFAULT_PROTECTED_SYMBOLS = {"FXAIX"}


def build_dashboard_manifest(
    *,
    action_plan: str | Path,
    portfolio_state: str | Path = "",
    market_regime: str | Path = "",
    operator_status: str | Path = "",
    evidence_file: str | Path = "",
    price_history_file: str | Path = "",
    decision_journal_path: str | Path = "",
    active_rules_path: str | Path = "",
    lessons_snapshot_path: str | Path = "",
    campaign_id: str = "",
) -> dict[str, Any]:
    """Build a versioned dashboard manifest for live/local serving."""
    rules_path = str(active_rules_path or "")
    return {
        "schema_version": 1,
        "mode": "operator_dashboard_manifest",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "campaign_id": str(campaign_id or ""),
        "action_plan": str(action_plan),
        "portfolio_state": str(portfolio_state or ""),
        "market_regime": str(market_regime or ""),
        "operator_status": str(operator_status or ""),
        "evidence_file": str(evidence_file or ""),
        "price_history_file": str(price_history_file or ""),
        "decision_journal_path": str(decision_journal_path or ""),
        "active_rules_path": rules_path,
        "active_rules_hash": _sha256_file(rules_path) if rules_path else "",
        "lessons_snapshot_path": str(lessons_snapshot_path or ""),
        "order_submission_enabled": False,
        "notes": [
            "Read-only dashboard manifest. It points to saved artifacts and does not authorize orders.",
            "Protected symbols may be displayed as holdings but are filtered from actionable dashboard candidates.",
        ],
    }


def load_dashboard_manifest(path: str | Path) -> dict[str, Any]:
    """Load and lightly validate a dashboard manifest."""
    manifest_path = Path(path).expanduser().resolve()
    payload = _load_json(manifest_path)
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError(f"Unsupported dashboard manifest schema in {manifest_path}.")
    payload["_manifest_path"] = str(manifest_path)
    return payload


def build_dashboard_pages_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Build dashboard pages from the current manifest source files."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    action_plan = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("action_plan")))
    portfolio_state = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("portfolio_state")))
    market_regime = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("market_regime")))
    operator_status = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("operator_status")))
    evidence_items = _load_json_list_optional(_resolve_manifest_path(base_dir, manifest.get("evidence_file")))
    price_history = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("price_history_file")))
    action_plan = _sanitize_action_plan_for_dashboard(action_plan, portfolio_state)
    dashboard = build_operator_dashboard(
        action_plan=action_plan,
        market_regime=market_regime,
        operator_status=operator_status,
    )
    return build_operator_dashboard_site(
        dashboard=dashboard,
        action_plan=action_plan,
        portfolio_state=portfolio_state,
        evidence_items=evidence_items,
        price_history_by_symbol=price_history,
    )


def build_dashboard_summary_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact JSON summary from manifest sources."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    action_plan = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("action_plan")))
    portfolio_state = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("portfolio_state")))
    market_regime = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("market_regime")))
    operator_status = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("operator_status")))
    action_plan = _sanitize_action_plan_for_dashboard(action_plan, portfolio_state)
    dashboard = build_operator_dashboard(
        action_plan=action_plan,
        market_regime=market_regime,
        operator_status=operator_status,
    )
    return {
        **dashboard,
        "manifest": _public_manifest(manifest),
        "order_submission_enabled": False,
    }


def resolve_dashboard_request(manifest_path: str | Path, request_path: str) -> tuple[int, str, bytes]:
    """Resolve a dashboard HTTP path without requiring a running socket."""
    manifest = load_dashboard_manifest(manifest_path)
    parsed_path = unquote(urlparse(request_path).path or "/")
    if parsed_path == "/health":
        return 200, "application/json; charset=utf-8", _json_bytes({"ok": True, "order_submission_enabled": False})
    if parsed_path == "/api/manifest.json":
        return 200, "application/json; charset=utf-8", _json_bytes(_public_manifest(manifest))
    if parsed_path == "/api/summary.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_dashboard_summary_from_manifest(manifest))
    pages = build_dashboard_pages_from_manifest(manifest)
    key = "index.html" if parsed_path in {"/", "/index.html"} else parsed_path.lstrip("/")
    if key not in pages:
        return 404, "text/plain; charset=utf-8", b"not found"
    return 200, "text/html; charset=utf-8", pages[key].encode("utf-8")


def serve_dashboard_manifest(*, manifest_path: str | Path, host: str, port: int) -> None:
    """Serve a live read-only dashboard until interrupted."""
    handler = make_dashboard_handler(manifest_path)
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def make_dashboard_handler(manifest_path: str | Path) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to one manifest path."""

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            status, content_type, body = resolve_dashboard_request(manifest_path, self.path)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib API name
            return

    return DashboardHandler


def _sanitize_action_plan_for_dashboard(
    action_plan: Mapping[str, Any],
    portfolio_state: Mapping[str, Any],
) -> dict[str, Any]:
    protected = _protected_symbols(portfolio_state)
    sanitized = dict(action_plan or {})
    intents = []
    for item in sanitized.get("intents") or []:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").upper().strip()
        intent_type = str(item.get("intent_type") or "").upper()
        if symbol in protected and intent_type in {"BUY", "ADD", "REBALANCE", "SELL"}:
            continue
        intents.append(dict(item))
    sanitized["intents"] = intents
    return sanitized


def _protected_symbols(portfolio_state: Mapping[str, Any]) -> set[str]:
    symbols = {str(item).upper().strip() for item in portfolio_state.get("protected_symbols") or []}
    return {symbol for symbol in symbols if symbol} | DEFAULT_PROTECTED_SYMBOLS


def _resolve_manifest_path(base_dir: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base_dir / path


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _load_json_optional(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return _load_json(path)


def _load_json_list_optional(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("ideas") or payload.get("items") or []
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list in {path}.")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _public_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if not str(key).startswith("_")}


def _sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


__all__ = [
    "build_dashboard_manifest",
    "build_dashboard_pages_from_manifest",
    "build_dashboard_summary_from_manifest",
    "load_dashboard_manifest",
    "make_dashboard_handler",
    "resolve_dashboard_request",
    "serve_dashboard_manifest",
]
