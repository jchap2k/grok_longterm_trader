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
from longterm.pipeline_health_cli import build_pipeline_health_report
from longterm.scheduler_config_validation import normalize_scheduler_config_validation


DEFAULT_PROTECTED_SYMBOLS = {"FXAIX"}


def build_dashboard_manifest(
    *,
    action_plan: str | Path,
    portfolio_state: str | Path = "",
    market_regime: str | Path = "",
    operator_status: str | Path = "",
    evidence_file: str | Path = "",
    price_history_file: str | Path = "",
    api_usage: str | Path = "",
    pipeline_summary: str | Path = "",
    pipeline_scheduler_summary: str | Path = "",
    scheduler_config_validation: str | Path = "",
    scheduler_task_plan: str | Path = "",
    scheduler_handoff: str | Path = "",
    scheduler_task_registration: str | Path = "",
    position_review_queue: str | Path = "",
    paper_submit_mode_plan: str | Path = "",
    scheduler_policy: str | Path = "",
    committee_preset_policy: str | Path = "",
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
        "api_usage": str(api_usage or ""),
        "pipeline_summary": str(pipeline_summary or ""),
        "pipeline_scheduler_summary": str(pipeline_scheduler_summary or ""),
        "scheduler_config_validation": str(scheduler_config_validation or ""),
        "scheduler_task_plan": str(scheduler_task_plan or ""),
        "scheduler_handoff": str(scheduler_handoff or ""),
        "scheduler_task_registration": str(scheduler_task_registration or ""),
        "position_review_queue": str(position_review_queue or ""),
        "paper_submit_mode_plan": str(paper_submit_mode_plan or ""),
        "scheduler_policy": str(scheduler_policy or ""),
        "committee_preset_policy": str(committee_preset_policy or ""),
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
    payload = _load_json(_latest_sibling_manifest_path(manifest_path))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError(f"Unsupported dashboard manifest schema in {manifest_path}.")
    payload["_manifest_path"] = str(manifest_path)
    return payload


def load_latest_dashboard_manifest(root: str | Path) -> dict[str, Any]:
    """Load the newest valid dashboard manifest under a generated-artifact root."""
    manifest_path = find_latest_dashboard_manifest(root)
    return load_dashboard_manifest(manifest_path)


def find_latest_dashboard_manifest(root: str | Path) -> Path:
    """Find the newest valid dashboard manifest under root by generated timestamp."""
    root_path = Path(root).expanduser().resolve()
    if root_path.is_file():
        return root_path
    if not root_path.exists():
        raise FileNotFoundError(f"Dashboard manifest root does not exist: {root_path}")
    preferred = root_path / "latest_operator_surface" / "dashboard_manifest.json"
    candidates: list[tuple[str, float, str, Path]] = []
    for candidate in sorted(root_path.rglob("dashboard*_manifest.json")):
        if not candidate.is_file():
            continue
        try:
            payload = _load_json(candidate)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if int(payload.get("schema_version") or 0) != 1:
            continue
        generated_at = str(payload.get("generated_at") or "")
        preferred_rank = "1" if candidate.resolve() == preferred.resolve() else "0"
        candidates.append((generated_at, candidate.stat().st_mtime, preferred_rank, candidate.resolve()))
    if not candidates:
        raise FileNotFoundError(f"No valid dashboard manifests found under: {root_path}")
    candidates.sort(key=lambda item: (item[0], item[1], item[2], str(item[3])))
    return candidates[-1][3]


def build_dashboard_pages_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Build dashboard pages from the current manifest source files."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    action_plan = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("action_plan")))
    portfolio_state = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("portfolio_state")))
    market_regime = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("market_regime")))
    operator_status = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("operator_status")))
    scheduler_policy = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("scheduler_policy")))
    evidence_items = _load_json_list_optional(_resolve_manifest_path(base_dir, manifest.get("evidence_file")))
    price_history = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("price_history_file")))
    scheduler_config_validation = build_scheduler_config_validation_from_manifest(manifest)
    scheduler_task_plan = build_scheduler_task_plan_from_manifest(manifest)
    scheduler_handoff = build_scheduler_handoff_from_manifest(manifest)
    scheduler_task_registration = build_scheduler_task_registration_from_manifest(manifest)
    position_review_queue = build_position_review_queue_from_manifest(manifest)
    paper_submit_mode_plan = build_paper_submit_mode_plan_from_manifest(manifest)
    api_usage = build_api_usage_from_manifest(manifest)
    action_plan = _sanitize_action_plan_for_dashboard(action_plan, portfolio_state)
    dashboard = build_operator_dashboard(
        action_plan=action_plan,
        market_regime=market_regime,
        scheduler_policy=scheduler_policy,
        operator_status=operator_status,
    )
    return build_operator_dashboard_site(
        dashboard=dashboard,
        action_plan=action_plan,
        portfolio_state=portfolio_state,
        evidence_items=evidence_items,
        price_history_by_symbol=price_history,
        api_usage=api_usage,
        scheduler_config_validation=scheduler_config_validation,
        scheduler_task_plan=scheduler_task_plan,
        scheduler_handoff=scheduler_handoff,
        scheduler_task_registration=scheduler_task_registration,
        position_review_queue=position_review_queue,
        paper_submit_mode_plan=paper_submit_mode_plan,
    )


def build_dashboard_summary_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact JSON summary from manifest sources."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    action_plan = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("action_plan")))
    portfolio_state = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("portfolio_state")))
    market_regime = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("market_regime")))
    operator_status = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("operator_status")))
    scheduler_policy = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("scheduler_policy")))
    scheduler_config_validation = build_scheduler_config_validation_from_manifest(manifest)
    scheduler_task_plan = build_scheduler_task_plan_from_manifest(manifest)
    scheduler_handoff = build_scheduler_handoff_from_manifest(manifest)
    scheduler_task_registration = build_scheduler_task_registration_from_manifest(manifest)
    position_review_queue = build_position_review_queue_from_manifest(manifest)
    paper_submit_mode_plan = build_paper_submit_mode_plan_from_manifest(manifest)
    action_plan = _sanitize_action_plan_for_dashboard(action_plan, portfolio_state)
    dashboard = build_operator_dashboard(
        action_plan=action_plan,
        market_regime=market_regime,
        scheduler_policy=scheduler_policy,
        operator_status=operator_status,
    )
    return {
        **dashboard,
        "manifest": _public_manifest(manifest),
        "api_usage": build_api_usage_from_manifest(manifest),
        "scheduler_config_validation": scheduler_config_validation,
        "scheduler_task_plan": scheduler_task_plan,
        "scheduler_handoff": scheduler_handoff,
        "scheduler_task_registration": scheduler_task_registration,
        "position_review_queue": position_review_queue,
        "paper_submit_mode_plan": paper_submit_mode_plan,
        "order_submission_enabled": False,
    }


def build_api_usage_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build a read-only API usage summary from explicit usage or pipeline artifacts."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    explicit_usage = _resolve_manifest_path(base_dir, manifest.get("api_usage"))
    pipeline_summary = _resolve_manifest_path(base_dir, manifest.get("pipeline_summary"))
    pipeline_scheduler_summary = _resolve_manifest_path(base_dir, manifest.get("pipeline_scheduler_summary"))
    source_path = explicit_usage or pipeline_summary
    if not source_path:
        return _empty_api_usage("api_usage_artifact_missing")
    payload = _load_json_optional(source_path)
    if not payload:
        return _empty_api_usage("api_usage_artifact_unreadable", source_path=source_path)
    return _normalize_api_usage_payload(payload, source_path=source_path)


def build_portfolio_summary_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build current portfolio value/gain data from the manifest's portfolio state."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    portfolio_state = _load_json_optional(_resolve_manifest_path(base_dir, manifest.get("portfolio_state")))
    protected = {str(symbol).upper().strip() for symbol in portfolio_state.get("protected_symbols") or []}
    holdings = []
    totals = {
        "original_purchase_total_cost": 0.0,
        "current_total_value": 0.0,
        "gain_amount": 0.0,
        "gain_percent": 0.0,
        "cash": _number(portfolio_state.get("cash")),
    }
    for holding in portfolio_state.get("holdings") or []:
        if not isinstance(holding, Mapping):
            continue
        symbol = str(holding.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        quantity = _number(_first_present(holding.get("quantity"), holding.get("shares")))
        current_price = _number(holding.get("current_price"))
        current_value = _number(
            _first_present(
                holding.get("market_value"),
                holding.get("current_total_value"),
                holding.get("current_value"),
            )
        )
        if current_value <= 0 and current_price > 0 and quantity > 0:
            current_value = current_price * quantity
        cost = _holding_total_cost(holding, quantity=quantity)
        gain_amount = current_value - cost if cost > 0 else 0.0
        gain_percent = (gain_amount / cost) * 100.0 if cost > 0 else 0.0
        status = str(holding.get("status") or "").strip()
        if not status:
            status = "Protected / core" if symbol in protected else "Active holding"
        holdings.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "current_price": current_price,
                "original_purchase_total_cost": cost,
                "current_total_value": current_value,
                "gain_amount": gain_amount,
                "gain_percent": gain_percent,
                "status": status,
                "protected": symbol in protected,
            }
        )
        totals["original_purchase_total_cost"] += cost
        totals["current_total_value"] += current_value
    totals["gain_amount"] = totals["current_total_value"] - totals["original_purchase_total_cost"]
    if totals["original_purchase_total_cost"] > 0:
        totals["gain_percent"] = (totals["gain_amount"] / totals["original_purchase_total_cost"]) * 100.0
    holdings.sort(key=lambda item: str(item["symbol"]))
    return {
        "schema_version": 1,
        "mode": "portfolio_value_summary",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "holding_count": len(holdings),
        "holdings": holdings,
        "totals": totals,
        "order_submission_enabled": False,
    }


def build_pipeline_health_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build a read-only pipeline artifact health report from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    pipeline_summary = _resolve_manifest_path(base_dir, manifest.get("pipeline_summary"))
    pipeline_scheduler_summary = _resolve_manifest_path(base_dir, manifest.get("pipeline_scheduler_summary"))
    if not pipeline_summary:
        return {
            "schema_version": 1,
            "mode": "pipeline_artifact_health",
            "status": "unavailable",
            "pipeline_summary": "",
            "order_submission_enabled": False,
            "missing_required_artifacts": [],
            "health": {
                "status": "attention_required",
                "present_count": 0,
                "missing_count": 1,
                "malformed_count": 0,
                "empty_path_count": 0,
                "present": [],
                "missing": ["pipeline_summary"],
                "malformed": [],
                "empty_path": [],
            },
            "rollup": {},
            "next_safe_action": "write_or_select_pipeline_summary_artifact",
        }
    return build_pipeline_health_report(
        pipeline_summary=pipeline_summary,
        pipeline_scheduler_summary=pipeline_scheduler_summary,
    )


def build_scheduler_policy_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the current advisory scheduler policy artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    scheduler_policy = _resolve_manifest_path(base_dir, manifest.get("scheduler_policy"))
    if not scheduler_policy:
        return {
            "schema_version": 1,
            "mode": "pipeline_scheduler_policy",
            "status": "unavailable",
            "recommended_mode": "unavailable",
            "urgency": "unknown",
            "reasons": ["scheduler_policy_artifact_missing"],
            "warnings": [],
            "blockers": [],
            "affected_symbols": [],
            "next_safe_action": "generate_scheduler_policy_artifact",
            "order_submission_enabled": False,
        }
    payload = _load_json_optional(scheduler_policy)
    if not payload:
        return {
            "schema_version": 1,
            "mode": "pipeline_scheduler_policy",
            "status": "unavailable",
            "recommended_mode": "unavailable",
            "urgency": "unknown",
            "reasons": ["scheduler_policy_artifact_unreadable"],
            "warnings": [],
            "blockers": [],
            "affected_symbols": [],
            "next_safe_action": "regenerate_scheduler_policy_artifact",
            "order_submission_enabled": False,
        }
    payload = dict(payload)
    payload["order_submission_enabled"] = False
    return payload


def build_scheduler_config_validation_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the safe scheduler config-validation artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    validation_path = _resolve_manifest_path(base_dir, manifest.get("scheduler_config_validation"))
    if not validation_path:
        return _empty_scheduler_config_validation("scheduler_config_validation_artifact_missing")
    payload = _load_json_optional(validation_path)
    if not payload:
        return _empty_scheduler_config_validation(
            "scheduler_config_validation_artifact_unreadable",
            source_path=validation_path,
        )
    return normalize_scheduler_config_validation(payload, source_path=validation_path)


def build_scheduler_task_plan_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the read-only Windows Task Scheduler plan artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    task_plan_path = _resolve_manifest_path(base_dir, manifest.get("scheduler_task_plan"))
    if not task_plan_path:
        return _empty_scheduler_task_plan("scheduler_task_plan_artifact_missing")
    payload = _load_json_optional(task_plan_path)
    if not payload:
        return _empty_scheduler_task_plan("scheduler_task_plan_artifact_unreadable", source_path=task_plan_path)
    normalized = dict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("mode", "windows_task_scheduler_plan")
    normalized.setdefault("status", "unknown")
    normalized.setdefault("task_name", "")
    normalized.setdefault("next_safe_action", "generate_windows_task_scheduler_plan")
    normalized["source_path"] = str(task_plan_path)
    normalized["order_submission_enabled"] = False
    return normalized


def build_scheduler_handoff_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the scheduler handoff check artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    handoff_path = _resolve_manifest_path(base_dir, manifest.get("scheduler_handoff"))
    if not handoff_path:
        return _empty_scheduler_handoff("scheduler_handoff_artifact_missing")
    payload = _load_json_optional(handoff_path)
    if not payload:
        return _empty_scheduler_handoff("scheduler_handoff_artifact_unreadable", source_path=handoff_path)
    normalized = dict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("mode", "scheduler_handoff_check")
    normalized.setdefault("status", "unknown")
    normalized.setdefault("checks", {})
    normalized.setdefault("blockers", [])
    normalized.setdefault("warnings", [])
    normalized.setdefault("next_safe_action", "generate_scheduler_handoff_check")
    normalized["source_path"] = str(handoff_path)
    normalized["order_submission_enabled"] = False
    return normalized


def build_scheduler_task_registration_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the guarded Windows Task Scheduler registration-review artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    registration_path = _resolve_manifest_path(base_dir, manifest.get("scheduler_task_registration"))
    if not registration_path:
        return _empty_scheduler_task_registration("scheduler_task_registration_artifact_missing")
    payload = _load_json_optional(registration_path)
    if not payload:
        return _empty_scheduler_task_registration(
            "scheduler_task_registration_artifact_unreadable",
            source_path=registration_path,
        )
    normalized = dict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("mode", "windows_task_scheduler_registration_review")
    normalized.setdefault("status", "unknown")
    normalized.setdefault("task_name", "")
    normalized.setdefault("registration_requested", False)
    normalized.setdefault("registration_executed", False)
    normalized.setdefault("registration_command", "")
    normalized.setdefault("warnings", [])
    normalized.setdefault("blockers", [])
    normalized.setdefault(
        "next_safe_action",
        "run_scheduler_task_registration_review",
    )
    normalized["source_path"] = str(registration_path)
    normalized["order_submission_enabled"] = False
    return normalized


def build_position_review_queue_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the no-submit position review queue artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    queue_path = _resolve_manifest_path(base_dir, manifest.get("position_review_queue"))
    if not queue_path:
        return _empty_position_review_queue("position_review_queue_artifact_missing")
    payload = _load_json_optional(queue_path)
    if not payload:
        return _empty_position_review_queue("position_review_queue_artifact_unreadable", source_path=queue_path)
    normalized = dict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("mode", "position_review_queue")
    normalized.setdefault("status", "unknown")
    normalized.setdefault("review_count", 0)
    normalized.setdefault("counts_by_review_type", {})
    normalized.setdefault("review_queue", [])
    normalized.setdefault("excluded_protected_symbols", [])
    normalized.setdefault("next_safe_action", "generate_position_review_queue")
    normalized["source_path"] = str(queue_path)
    normalized["order_submission_enabled"] = False
    normalized["broker_calls_enabled"] = False
    normalized["llm_calls_enabled"] = False
    return normalized


def build_paper_submit_mode_plan_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load the disabled paper submit-mode readiness artifact from the manifest."""
    base_dir = Path(str(manifest.get("_manifest_path") or ".")).parent
    plan_path = _resolve_manifest_path(base_dir, manifest.get("paper_submit_mode_plan"))
    if not plan_path:
        return _empty_paper_submit_mode_plan("paper_submit_mode_plan_artifact_missing")
    payload = _load_json_optional(plan_path)
    if not payload:
        return _empty_paper_submit_mode_plan("paper_submit_mode_plan_artifact_unreadable", source_path=plan_path)
    normalized = dict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("mode", "paper_submit_mode_plan")
    normalized.setdefault("status", "unknown")
    normalized.setdefault("checks", {})
    normalized.setdefault("blockers", [])
    normalized.setdefault("warnings", [])
    normalized.setdefault("next_safe_action", "generate_paper_submit_mode_plan")
    normalized["source_path"] = str(plan_path)
    normalized["order_submission_enabled"] = False
    normalized["submit_profile_enabled"] = False
    normalized["broker_calls_enabled"] = False
    normalized["runnable_submit_command_emitted"] = False
    return normalized


def resolve_dashboard_request(
    manifest_path: str | Path,
    request_path: str,
    *,
    auto_manifest_root: str | Path = "",
) -> tuple[int, str, bytes]:
    """Resolve a dashboard HTTP path without requiring a running socket."""
    manifest = _load_active_manifest(manifest_path, auto_manifest_root=auto_manifest_root)
    parsed_path = unquote(urlparse(request_path).path or "/")
    if parsed_path == "/health":
        return 200, "application/json; charset=utf-8", _json_bytes({"ok": True, "order_submission_enabled": False})
    if parsed_path == "/api/manifest.json":
        return 200, "application/json; charset=utf-8", _json_bytes(_public_manifest(manifest))
    if parsed_path == "/api/summary.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_dashboard_summary_from_manifest(manifest))
    if parsed_path == "/api/portfolio.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_portfolio_summary_from_manifest(manifest))
    if parsed_path == "/api/api-usage.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_api_usage_from_manifest(manifest))
    if parsed_path == "/api/pipeline-health.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_pipeline_health_from_manifest(manifest))
    if parsed_path == "/api/scheduler-policy.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_scheduler_policy_from_manifest(manifest))
    if parsed_path == "/api/scheduler-config-validation.json":
        return 200, "application/json; charset=utf-8", _json_bytes(
            build_scheduler_config_validation_from_manifest(manifest)
        )
    if parsed_path == "/api/scheduler-task-plan.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_scheduler_task_plan_from_manifest(manifest))
    if parsed_path == "/api/scheduler-handoff.json":
        return 200, "application/json; charset=utf-8", _json_bytes(build_scheduler_handoff_from_manifest(manifest))
    if parsed_path == "/api/scheduler-task-registration.json":
        return 200, "application/json; charset=utf-8", _json_bytes(
            build_scheduler_task_registration_from_manifest(manifest)
        )
    if parsed_path == "/api/position-review-queue.json":
        return 200, "application/json; charset=utf-8", _json_bytes(
            build_position_review_queue_from_manifest(manifest)
        )
    if parsed_path == "/api/paper-submit-mode-plan.json":
        return 200, "application/json; charset=utf-8", _json_bytes(
            build_paper_submit_mode_plan_from_manifest(manifest)
        )
    pages = build_dashboard_pages_from_manifest(manifest)
    key = "index.html" if parsed_path in {"/", "/index.html"} else parsed_path.lstrip("/")
    if key not in pages:
        return 404, "text/plain; charset=utf-8", b"not found"
    return 200, "text/html; charset=utf-8", pages[key].encode("utf-8")


def serve_dashboard_manifest(
    *,
    manifest_path: str | Path,
    host: str,
    port: int,
    auto_manifest_root: str | Path = "",
) -> None:
    """Serve a live read-only dashboard until interrupted."""
    handler = make_dashboard_handler(manifest_path, auto_manifest_root=auto_manifest_root)
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def make_dashboard_handler(
    manifest_path: str | Path,
    *,
    auto_manifest_root: str | Path = "",
) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to one manifest path."""

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            status, content_type, body = resolve_dashboard_request(
                manifest_path,
                self.path,
                auto_manifest_root=auto_manifest_root,
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib API name
            return

    return DashboardHandler


def _load_active_manifest(manifest_path: str | Path, *, auto_manifest_root: str | Path = "") -> dict[str, Any]:
    if auto_manifest_root:
        return load_latest_dashboard_manifest(auto_manifest_root)
    return load_dashboard_manifest(manifest_path)


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


def _empty_api_usage(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "api_usage_summary",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "unavailable",
        "source_path": str(source_path or ""),
        "providers": [],
        "totals": {
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_total_cost_usd": 0.0,
        },
        "tier_tracking": {},
        "warnings": [reason],
        "next_safe_action": "run_or_select_enrichment_artifact_with_usage_summary",
        "order_submission_enabled": False,
    }


def _empty_scheduler_config_validation(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return normalize_scheduler_config_validation(
        {},
        source_path=source_path or "",
        unavailable_reason=reason,
    )


def _empty_scheduler_task_plan(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "windows_task_scheduler_plan",
        "status": "unavailable",
        "source_path": str(source_path or ""),
        "task_name": "",
        "profile_file": "",
        "profile_run_mode": "",
        "schedule": {},
        "warnings": [reason],
        "next_safe_action": "generate_windows_task_scheduler_plan",
        "order_submission_enabled": False,
    }


def _empty_scheduler_handoff(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "scheduler_handoff_check",
        "status": "unavailable",
        "source_path": str(source_path or ""),
        "checks": {},
        "blockers": [],
        "warnings": [reason],
        "next_safe_action": "generate_scheduler_handoff_check",
        "order_submission_enabled": False,
    }


def _empty_scheduler_task_registration(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "windows_task_scheduler_registration_review",
        "status": "unavailable",
        "source_path": str(source_path or ""),
        "task_name": "",
        "registration_requested": False,
        "registration_executed": False,
        "registration_command": "",
        "blockers": [],
        "warnings": [reason],
        "next_safe_action": "run_scheduler_task_registration_review",
        "order_submission_enabled": False,
    }


def _empty_position_review_queue(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "position_review_queue",
        "status": "unavailable",
        "source_path": str(source_path or ""),
        "review_count": 0,
        "counts_by_review_type": {},
        "review_queue": [],
        "excluded_protected_symbols": [],
        "warnings": [reason],
        "next_safe_action": "generate_position_review_queue",
        "order_submission_enabled": False,
        "broker_calls_enabled": False,
        "llm_calls_enabled": False,
    }


def _empty_paper_submit_mode_plan(reason: str, *, source_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "paper_submit_mode_plan",
        "status": "unavailable",
        "source_path": str(source_path or ""),
        "checks": {},
        "blockers": [],
        "warnings": [reason],
        "next_safe_action": "generate_paper_submit_mode_plan",
        "order_submission_enabled": False,
        "submit_profile_enabled": False,
        "broker_calls_enabled": False,
        "runnable_submit_command_emitted": False,
    }


def _normalize_api_usage_payload(payload: Mapping[str, Any], *, source_path: Path) -> dict[str, Any]:
    if payload.get("mode") == "api_usage_summary" and isinstance(payload.get("providers"), list):
        normalized = dict(payload)
        normalized["order_submission_enabled"] = False
        normalized.setdefault("source_path", str(source_path))
        return normalized
    providers = _usage_providers_from_payload(payload)
    if not providers:
        return _empty_api_usage("research_model_usage_missing", source_path=source_path)
    totals = {
        "request_count": sum(int(_number(item.get("request_count"))) for item in providers),
        "prompt_tokens": sum(int(_number(item.get("prompt_tokens"))) for item in providers),
        "completion_tokens": sum(int(_number(item.get("completion_tokens"))) for item in providers),
        "total_tokens": sum(int(_number(item.get("total_tokens"))) for item in providers),
        "estimated_total_cost_usd": round(
            sum(float(_number(item.get("estimated_total_cost_usd"))) for item in providers),
            6,
        ),
    }
    tier_tracking = _tier_tracking_from_providers(providers)
    return {
        "schema_version": 1,
        "mode": "api_usage_summary",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "available",
        "source_path": str(source_path),
        "providers": providers,
        "totals": totals,
        "tier_tracking": tier_tracking,
        "warnings": [],
        "next_safe_action": "review_usage_before_large_paid_enrichment_runs",
        "order_submission_enabled": False,
    }


def _usage_providers_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_values: list[Any] = []
    if isinstance(payload.get("research_model_usage"), Mapping):
        raw_values.append(payload.get("research_model_usage"))
    if isinstance(payload.get("api_usage"), Mapping):
        raw_values.append(payload.get("api_usage"))
    if isinstance(payload.get("providers"), list):
        raw_values.extend(payload.get("providers") or [])
    providers = []
    for raw in raw_values:
        if not isinstance(raw, Mapping):
            continue
        provider = str(raw.get("provider") or raw.get("client") or raw.get("source") or "unknown").strip()
        model = str(raw.get("model") or raw.get("model_name") or "").strip()
        if not provider and not model:
            continue
        providers.append(
            {
                "provider": provider or "unknown",
                "model": model,
                "search_context_size": str(raw.get("search_context_size") or raw.get("context_size") or "").strip(),
                "request_count": int(_number(raw.get("request_count"))),
                "prompt_tokens": int(_number(raw.get("prompt_tokens"))),
                "completion_tokens": int(_number(raw.get("completion_tokens"))),
                "total_tokens": int(_number(raw.get("total_tokens"))),
                "request_fees_usd": round(float(_number(raw.get("request_fees_usd"))), 6),
                "input_token_cost_usd": round(float(_number(raw.get("input_token_cost_usd"))), 6),
                "output_token_cost_usd": round(float(_number(raw.get("output_token_cost_usd"))), 6),
                "estimated_total_cost_usd": round(float(_number(raw.get("estimated_total_cost_usd"))), 6),
                "credits_purchased_to_date_usd": _optional_number(raw.get("credits_purchased_to_date_usd")),
                "tier_1_credit_target_usd": _optional_number(raw.get("tier_1_credit_target_usd")),
                "estimated_progress_to_tier_1_usd": _optional_number(raw.get("estimated_progress_to_tier_1_usd")),
                "estimated_remaining_to_tier_1_usd": _optional_number(raw.get("estimated_remaining_to_tier_1_usd")),
                "console_check_required": bool(raw.get("console_check_required")),
                "tier_note": str(raw.get("tier_note") or raw.get("note") or "").strip(),
            }
        )
    return providers


def _tier_tracking_from_providers(providers: list[Mapping[str, Any]]) -> dict[str, Any]:
    tracked = [item for item in providers if _optional_number(item.get("tier_1_credit_target_usd")) is not None]
    if not tracked:
        return {}
    target = max(float(_optional_number(item.get("tier_1_credit_target_usd")) or 0.0) for item in tracked)
    progress = max(float(_optional_number(item.get("estimated_progress_to_tier_1_usd")) or 0.0) for item in tracked)
    remaining_values = [
        float(_optional_number(item.get("estimated_remaining_to_tier_1_usd")) or 0.0)
        for item in tracked
    ]
    remaining = min(remaining_values) if remaining_values else max(0.0, target - progress)
    return {
        "tier_1_credit_target_usd": round(target, 2),
        "estimated_progress_to_tier_1_usd": round(progress, 2),
        "estimated_remaining_to_tier_1_usd": round(max(0.0, remaining), 2),
        "progress_percent": round((progress / target) * 100.0, 2) if target > 0 else 0.0,
        "console_check_required": any(bool(item.get("console_check_required")) for item in tracked),
    }


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _public_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if not str(key).startswith("_")}


def _sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _holding_total_cost(holding: Mapping[str, Any], *, quantity: float) -> float:
    explicit = _number(
        _first_present(
            holding.get("original_purchase_total_cost"),
            holding.get("purchase_total_cost"),
            holding.get("total_cost"),
            holding.get("cost_basis"),
        )
    )
    if explicit > 0:
        return explicit
    avg_price = _number(_first_present(holding.get("avg_entry_price"), holding.get("average_entry_price")))
    return avg_price * quantity if avg_price > 0 and quantity > 0 else 0.0


def _latest_sibling_manifest_path(manifest_path: Path) -> Path:
    """Let the canonical manifest serve the newest generated dashboard manifest."""
    if manifest_path.name != "dashboard_manifest.json" or not manifest_path.exists():
        return manifest_path
    candidates: list[tuple[str, float, Path]] = []
    for candidate in manifest_path.parent.glob("dashboard*_manifest.json"):
        if not candidate.is_file():
            continue
        try:
            payload = _load_json(candidate)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        generated_at = str(payload.get("generated_at") or "")
        candidates.append((generated_at, candidate.stat().st_mtime, candidate))
    if not candidates:
        return manifest_path
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return candidates[-1][2]


__all__ = [
    "build_api_usage_from_manifest",
    "build_dashboard_manifest",
    "build_dashboard_pages_from_manifest",
    "build_dashboard_summary_from_manifest",
    "build_paper_submit_mode_plan_from_manifest",
    "build_pipeline_health_from_manifest",
    "build_position_review_queue_from_manifest",
    "build_portfolio_summary_from_manifest",
    "build_scheduler_config_validation_from_manifest",
    "build_scheduler_handoff_from_manifest",
    "build_scheduler_policy_from_manifest",
    "build_scheduler_task_registration_from_manifest",
    "build_scheduler_task_plan_from_manifest",
    "find_latest_dashboard_manifest",
    "load_latest_dashboard_manifest",
    "load_dashboard_manifest",
    "make_dashboard_handler",
    "resolve_dashboard_request",
    "serve_dashboard_manifest",
]
