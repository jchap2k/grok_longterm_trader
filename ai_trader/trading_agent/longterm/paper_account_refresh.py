"""Read-only paper account artifact refresh for scheduler-facing dashboards."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from longterm.alpaca_paper_account import AlpacaPaperAccountReader, paper_account_snapshot_to_portfolio_state
from longterm.decision_journal import LongTermDecisionJournal
from longterm.operator_dashboard import build_operator_dashboard, build_operator_dashboard_site
from longterm.operator_dashboard_server import build_dashboard_manifest
from longterm.operator_status_bundle import build_operator_status_bundle
from longterm.paper_outcomes import summarize_paper_outcomes
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.portfolio_state import PortfolioState
from portfolio.portfolio_profile import PortfolioProfile


DEFAULT_PROTECTED_SYMBOL = "FXAIX"


def refresh_paper_account_artifacts(
    *,
    profile_config: str | Path,
    journal_db: str | Path,
    action_plan_path: str | Path,
    paper_ledger_db: str | Path,
    output_dir: str | Path,
    market_regime_path: str | Path = "",
    evidence_file: str | Path = "",
    price_history_file: str | Path = "",
    pipeline_summary_path: str | Path = "",
    pipeline_scheduler_summary_path: str | Path = "",
    scheduler_config_validation_path: str | Path = "",
    scheduler_task_plan_path: str | Path = "",
    scheduler_handoff_path: str | Path = "",
    position_review_queue_path: str | Path = "",
    paper_submit_mode_plan_path: str | Path = "",
    scheduler_policy_path: str | Path = "",
    committee_preset_policy_path: str | Path = "",
    status_refresh_file: str | Path = "",
    dashboard_manifest_output: str | Path = "",
    dashboard_site_output_dir: str | Path = "",
    reader_factory: Callable[[], AlpacaPaperAccountReader] | None = None,
) -> dict[str, Any]:
    """Refresh account/status/dashboard artifacts without submitting orders."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    profile = PortfolioProfile.from_file(profile_config)
    protected_symbols = _protected_symbols(profile)
    reader = reader_factory() if reader_factory else _default_reader_factory()
    with redirect_stdout(sys.stderr):
        snapshot = reader.read_snapshot(profile=profile)
    portfolio_state = _with_protected_symbols(
        paper_account_snapshot_to_portfolio_state(snapshot),
        protected_symbols=protected_symbols,
    )

    account_snapshot_path = output / "account_snapshot.json"
    portfolio_state_path = output / "portfolio_state.json"
    operator_status_path = output / "operator_status_bundle.json"
    paper_outcome_summary_path = output / "paper_outcome_summary.json"
    refresh_summary_path = output / "refresh_summary.json"
    manifest_path = Path(dashboard_manifest_output) if dashboard_manifest_output else output / "dashboard_manifest.json"
    site_dir = Path(dashboard_site_output_dir) if dashboard_site_output_dir else None

    _write_json(account_snapshot_path, snapshot.to_dict())
    portfolio_payload = _portfolio_state_payload(portfolio_state)
    _write_json(portfolio_state_path, portfolio_payload)

    action_plan = _load_json_optional(action_plan_path)
    market_regime = _load_json_optional(market_regime_path)
    status_refresh = _load_json_optional(status_refresh_file)
    scheduler_policy = _load_json_optional(scheduler_policy_path)
    scheduler_config_validation = _load_json_optional(scheduler_config_validation_path)
    scheduler_task_plan = _load_json_optional(scheduler_task_plan_path)
    scheduler_handoff = _load_json_optional(scheduler_handoff_path)
    position_review_queue = _load_json_optional(position_review_queue_path)
    paper_submit_mode_plan = _load_json_optional(paper_submit_mode_plan_path)
    committee_preset_policy = _load_json_optional(committee_preset_policy_path)
    price_history = _load_json_optional(price_history_file)
    evidence_items = _load_json_list_optional(evidence_file)
    journal = LongTermDecisionJournal(journal_db)
    ledger = PaperTradeLedger(paper_ledger_db)
    outcome_price_map = _merge_price_history_latest(
        _price_map_from_portfolio(portfolio_payload),
        price_history,
    )
    paper_outcome_summary = summarize_paper_outcomes(
        ledger,
        price_map=outcome_price_map,
        journal=journal,
    )
    _write_json(paper_outcome_summary_path, paper_outcome_summary)
    status_bundle = build_operator_status_bundle(
        journal,
        portfolio_state=portfolio_state,
        paper_ledger=ledger,
        action_plan=action_plan,
        price_map=outcome_price_map,
        status_refresh=status_refresh,
        scheduler_policy=scheduler_policy,
        committee_preset_policy=committee_preset_policy,
    )
    _write_json(operator_status_path, status_bundle)

    dashboard = build_operator_dashboard(
        action_plan=action_plan,
        market_regime=market_regime,
        scheduler_policy=scheduler_policy,
        operator_status=status_bundle,
    )
    manifest = build_dashboard_manifest(
        action_plan=action_plan_path,
        portfolio_state=portfolio_state_path,
        market_regime=market_regime_path,
        operator_status=operator_status_path,
        evidence_file=evidence_file,
        price_history_file=price_history_file,
        pipeline_summary=pipeline_summary_path,
        pipeline_scheduler_summary=pipeline_scheduler_summary_path,
        scheduler_config_validation=scheduler_config_validation_path,
        scheduler_task_plan=scheduler_task_plan_path,
        scheduler_handoff=scheduler_handoff_path,
        position_review_queue=position_review_queue_path,
        paper_submit_mode_plan=paper_submit_mode_plan_path,
        scheduler_policy=scheduler_policy_path,
        committee_preset_policy=committee_preset_policy_path,
        decision_journal_path=journal_db,
        active_rules_path=_active_rules_path(),
        campaign_id=f"paper_account_refresh_{_timestamp_slug()}",
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(manifest_path, manifest)

    site_page_count = 0
    if site_dir is not None:
        pages = build_operator_dashboard_site(
            dashboard=dashboard,
            action_plan=action_plan,
            portfolio_state=portfolio_payload,
            evidence_items=evidence_items,
            price_history_by_symbol=price_history,
            scheduler_config_validation=scheduler_config_validation,
            scheduler_task_plan=scheduler_task_plan,
            scheduler_handoff=scheduler_handoff,
            position_review_queue=position_review_queue,
            paper_submit_mode_plan=paper_submit_mode_plan,
        )
        _write_site(site_dir, pages)
        site_page_count = len(pages)

    summary = {
        "schema_version": 1,
        "mode": "paper_account_artifact_refresh",
        "read_only": True,
        "paper_mode": True,
        "live_mode": False,
        "order_submission_enabled": False,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "action_plan_refresh_scope": "existing_action_plan_only",
        "protected_symbols_applied": protected_symbols,
        "account_snapshot_path": str(account_snapshot_path),
        "portfolio_state_path": str(portfolio_state_path),
        "operator_status_path": str(operator_status_path),
        "paper_outcome_summary_path": str(paper_outcome_summary_path),
        "paper_outcome_evaluated_fills": int(paper_outcome_summary.get("evaluated_fills") or 0),
        "paper_outcome_pending_count": int(paper_outcome_summary.get("pending_count") or 0),
        "paper_outcome_average_excess_return_pct": float(paper_outcome_summary.get("average_excess_return_pct") or 0.0),
        "dashboard_manifest_path": str(manifest_path),
        "dashboard_site_output_dir": str(site_dir) if site_dir else "",
        "dashboard_site_page_count": site_page_count,
        "refresh_summary_path": str(refresh_summary_path),
        "components": {
            "profile_config": _component(profile_config),
            "journal_db": _component(journal_db),
            "action_plan": _component(action_plan_path),
            "paper_ledger_db": _component(paper_ledger_db),
            "market_regime": _component(market_regime_path),
            "evidence_file": _component(evidence_file),
            "price_history_file": _component(price_history_file),
            "pipeline_summary": _component(pipeline_summary_path),
            "pipeline_scheduler_summary": _component(pipeline_scheduler_summary_path),
            "scheduler_config_validation": _component(scheduler_config_validation_path),
            "scheduler_task_plan": _component(scheduler_task_plan_path),
            "scheduler_handoff": _component(scheduler_handoff_path),
            "position_review_queue": _component(position_review_queue_path),
            "paper_submit_mode_plan": _component(paper_submit_mode_plan_path),
            "scheduler_policy": _component(scheduler_policy_path),
            "committee_preset_policy": _component(committee_preset_policy_path),
            "status_refresh_file": _component(status_refresh_file),
            "paper_outcome_summary": _component(paper_outcome_summary_path),
        },
        "notes": [
            "Read-only refresh. No paper or live broker orders were submitted.",
            "The supplied action plan is reused as-is; this command does not regenerate recommendations or next actions.",
            f"{DEFAULT_PROTECTED_SYMBOL} and profile protected symbols remain protected in refreshed portfolio artifacts.",
        ],
    }
    _write_json(refresh_summary_path, summary)
    return summary


def _default_reader_factory() -> AlpacaPaperAccountReader:
    from brokers.alpaca_broker import AlpacaBroker

    return AlpacaPaperAccountReader(broker=AlpacaBroker(paper_trading=True), paper_trading=True)


def _with_protected_symbols(state: PortfolioState, *, protected_symbols: list[str]) -> PortfolioState:
    state.protected_symbols = protected_symbols
    return state


def _protected_symbols(profile: PortfolioProfile) -> list[str]:
    symbols = [str(symbol).upper().strip() for symbol in profile.protected_symbols or []]
    symbols.append(DEFAULT_PROTECTED_SYMBOL)
    return sorted({symbol for symbol in symbols if symbol})


def _portfolio_state_payload(state: PortfolioState) -> dict[str, Any]:
    protected = {symbol.upper() for symbol in state.protected_symbols}
    holdings = []
    for holding in state.holdings:
        item = asdict(holding)
        if item["symbol"] in protected:
            item["status"] = "Protected / core"
        holdings.append(item)
    return {
        "cash": state.cash,
        "protected_symbols": state.protected_symbols,
        "holdings": holdings,
    }


def _price_map_from_portfolio(payload: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for holding in payload.get("holdings") or []:
        if not isinstance(holding, Mapping):
            continue
        symbol = str(holding.get("symbol") or "").upper().strip()
        price = float(holding.get("current_price") or 0.0)
        if symbol and price > 0:
            result[symbol] = {"current_price": price}
    return result


def _merge_price_history_latest(
    price_map: dict[str, dict[str, float]],
    price_history: Mapping[str, Any],
) -> dict[str, dict[str, float]]:
    merged = {symbol: dict(value) for symbol, value in price_map.items()}
    for symbol, rows in (price_history or {}).items():
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol or not isinstance(rows, list):
            continue
        latest = _latest_price_history_row(rows)
        price = float(latest.get("close") or latest.get("current_price") or latest.get("price") or 0.0)
        if price > 0:
            merged.setdefault(clean_symbol, {})["current_price"] = price
    return merged


def _latest_price_history_row(rows: list[Any]) -> Mapping[str, Any]:
    records = [dict(item) for item in rows if isinstance(item, Mapping)]
    if not records:
        return {}
    return sorted(records, key=lambda item: str(item.get("date") or item.get("timestamp") or ""))[-1]


def _write_site(site_dir: Path, pages: Mapping[str, str]) -> None:
    for relative_path, html in pages.items():
        page_path = site_dir / relative_path
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(html, encoding="utf-8")


def _write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_json_optional(path: str | Path) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_json_list_optional(path: str | Path) -> list[dict[str, Any]]:
    if not path:
        return []
    target = Path(path)
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("ideas") or payload.get("items") or []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _component(path: str | Path) -> dict[str, Any]:
    if not path:
        return {"path": "", "exists": False, "modified_at": ""}
    target = Path(path)
    if not target.exists():
        return {"path": str(target), "exists": False, "modified_at": ""}
    return {
        "path": str(target),
        "exists": True,
        "modified_at": datetime.fromtimestamp(target.stat().st_mtime, UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _active_rules_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt")


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


__all__ = ["refresh_paper_account_artifacts"]
