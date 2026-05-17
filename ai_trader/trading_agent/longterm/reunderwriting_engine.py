"""Thesis Re-underwriting Engine for the long-term quality-growth sleeve.

Core philosophy (per design):
- Delta-evidence focused (what changed since original decision or last re-underwrite)
- Re-uses existing ThesisMonitor + ReviewCadencePolicy + deterministic reviewers
- Ties into Tiered Enrichment (lighter tiers preferred for existing holdings via is_existing_holding=True)
- Kronos is fully optional (default OFF for MVP)
- All lineage lives in the single longterm_decision_journal table
- Never filters good ideas to zero; surfaces actionable durability signals
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Mapping, Optional

from longterm.decision_journal import LongTermDecisionJournal, ReunderwritingRecord
from longterm.thesis_monitor import ThesisMonitor
from research.intake import create_research_packet_from_idea
from research.research_packet import ResearchPacket

# Optional Tiered Enrichment integration (available on this branch)
try:
    from longterm.tier_router import route_enrichment_tier
    from longterm.tier_definitions import TIER_1_LIGHT, TIER_2_STANDARD
    TIER_ROUTER_AVAILABLE = True
except ImportError:
    TIER_ROUTER_AVAILABLE = False
    TIER_1_LIGHT = 1
    TIER_2_STANDARD = 2


def run_reunderwriting(
    journal: LongTermDecisionJournal,
    *,
    symbol: str | None = None,
    decision_id: str | None = None,
    dry_run: bool = True,
    force: bool = False,
    manual_durability: str | None = None,
    notes: str = "",
    use_kronos: bool = False,
    fresh_evidence: list[str] | None = None,
    current_price: float | None = None,
) -> dict[str, Any]:
    """Main entry point for re-underwriting a position.

    Returns a structured result dict suitable for CLI and operator surfaces.
    When dry_run=True, nothing is written to the journal.
    """
    result: dict[str, Any] = {
        "success": False,
        "dry_run": dry_run,
        "symbol": (symbol or "").upper() if symbol else None,
        "parent_decision_id": decision_id,
        "action_taken": "none",
        "durability": None,
        "delta_summary": "",
        "warnings": [],
    }

    # Resolve the parent decision
    parent = _resolve_parent_decision(journal, symbol=symbol, decision_id=decision_id)
    if not parent:
        result["warnings"].append("No parent decision found for the given symbol/decision_id")
        return result

    parent_id = parent["decision_id"]
    sym = str(parent["symbol"]).upper()
    result["symbol"] = sym
    result["parent_decision_id"] = parent_id

    # Load original packet
    packet_data = json.loads(parent.get("packet_json") or "{}")
    try:
        original_packet = create_research_packet_from_idea(packet_data)
    except Exception as exc:
        result["warnings"].append(f"Failed to hydrate original packet: {exc}")
        original_packet = None

    # Current durability from denormalized column or last re-underwriting
    current_durability = parent.get("thesis_durability") or "stable"
    last_reunderwritten = parent.get("last_reunderwritten_date")

    # If manual durability provided (operator override)
    if manual_durability:
        proposed_durability = manual_durability
        result["delta_summary"] = f"Manual operator override to '{manual_durability}'. Notes: {notes or '(none)'}"
        result["action_taken"] = "manual_override"
    else:
        # Run ThesisMonitor to compute fresh durability state (cadence + evidence + macro + invalidation)
        monitor = ThesisMonitor()

        last_review_date = _infer_last_review_date(parent, last_reunderwritten)
        evidence = fresh_evidence or []

        thesis_status = monitor.evaluate(
            original_packet or ResearchPacket(symbol=sym, company_name=sym, idea_source="reunderwrite"),
            last_review_date=last_review_date,
            current_evidence=evidence,
        )

        proposed_durability = _map_thesis_state_to_durability(thesis_status.thesis_state)
        result["delta_summary"] = thesis_status.reason
        result["thesis_state_from_monitor"] = thesis_status.thesis_state

        # Respect cadence via the monitor's review_due flag unless --force
        if not force and not thesis_status.review_due and proposed_durability in ("strong", "stable"):
            result["warnings"].append("Review not due per cadence and durability still healthy. Use --force to override.")
            result["action_taken"] = "skipped_not_due"
            result["durability"] = current_durability
            return result

    result["durability"] = proposed_durability
    result["previous_durability"] = current_durability

    # Build the structured re-underwriting record (delta evidence focused)
    record = ReunderwritingRecord(
        decision_id="",
        parent_decision_id=parent_id,
        symbol=sym,
        timestamp=datetime.now().isoformat(),
        thesis_durability=proposed_durability,
        delta_summary=result["delta_summary"],
        new_risks=_extract_new_risks(fresh_evidence or []),
        memo={
            "notes": notes,
            "use_kronos": use_kronos,
            "current_price": current_price,
            "original_key_thesis": parent.get("key_thesis"),
            "reunderwrite_trigger": "manual" if force or manual_durability else "cadence_or_signal",
        },
    )

    if use_kronos:
        # Kronos is optional for MVP — placeholder only (no hard dependency)
        record.kronos_delta = {"status": "skipped", "reason": "Kronos optional and not wired in MVP engine"}
        result["warnings"].append("Kronos requested but remains optional/unwired in this MVP build")

    # Delta enrichment tier recommendation (ties re-underwriting to Tiered Enrichment strategy)
    tier_rec = recommend_delta_enrichment_tier_for_holding(
        symbol=sym,
        current_durability=proposed_durability,
        use_kronos=use_kronos,
    )
    result["recommended_delta_enrichment_tier"] = tier_rec
    record.tier_changes = {"recommended_for_reunderwrite": tier_rec}

    if dry_run:
        result["success"] = True
        result["action_taken"] = "dry_run"
        result["would_record"] = record.to_memo_dict()
        result["message"] = "Dry run complete — no journal write performed. Use without --dry-run to persist."
        return result

    # Real write path
    try:
        child_id = journal.record_reunderwriting(parent_decision_id=parent_id, record=record)
        result["success"] = True
        result["action_taken"] = "recorded"
        result["child_decision_id"] = child_id
        result["message"] = f"Re-underwriting recorded for {sym}. Durability now: {proposed_durability}"
    except Exception as exc:
        result["warnings"].append(f"Failed to record re-underwriting: {exc}")

    return result


def _resolve_parent_decision(
    journal: LongTermDecisionJournal,
    *,
    symbol: str | None,
    decision_id: str | None,
) -> dict[str, Any] | None:
    if decision_id:
        try:
            return journal.get_decision(decision_id)
        except KeyError:
            return None

    if symbol:
        # Find the latest actionable decision for the symbol (use full get_decision for durability columns)
        recent = journal.list_recent_decisions(limit=50)
        for row in recent:
            if str(row.get("symbol", "")).upper() == symbol.upper():
                rec = str(row.get("recommendation") or "").upper()
                if rec in {"BUY", "ADD", "HOLD"}:
                    try:
                        full = journal.get_decision(row["decision_id"])
                        return full
                    except Exception:
                        return row
    return None


def _infer_last_review_date(parent_row: Mapping[str, Any], last_reunderwritten: str | None) -> date:
    from datetime import datetime as dt

    ts = last_reunderwritten or parent_row.get("outcome_updated_at") or parent_row.get("timestamp")
    if ts:
        try:
            return dt.fromisoformat(ts[:10]).date()
        except Exception:
            pass
    return date.today()


def _map_thesis_state_to_durability(thesis_state: str) -> str:
    mapping = {
        "broken": "broken",
        "weakening": "weakening",
        "regime_pressure": "weakening",
        "stale": "stable",
    }
    return mapping.get(thesis_state.lower(), "stable")


def _extract_new_risks(evidence: list[str]) -> list[str]:
    risks: list[str] = []
    lowered = " ".join(evidence).lower()
    risk_keywords = ["margin pressure", "guidance cut", "customer loss", "churn", "debt", "regulatory", "lawsuit"]
    for kw in risk_keywords:
        if kw in lowered:
            risks.append(kw)
    return risks


def recommend_delta_enrichment_tier_for_holding(
    *,
    symbol: str,
    current_durability: str,
    reviewer_average_score: float | None = None,
    has_hard_objection: bool = False,
    kronos_strength: float | None = None,
    use_kronos: bool = False,
) -> dict[str, Any]:
    """Recommend enrichment tier for re-underwriting an existing holding (delta-focused).

    Prefers light tiers (Tier 1) for holdings per the Tiered Enrichment strategy.
    Kronos is only considered when explicitly enabled.
    """
    if not TIER_ROUTER_AVAILABLE:
        return {
            "tier": TIER_1_LIGHT,
            "reasons": ["tier_router_not_available_fallback_to_light"],
            "is_existing_holding": True,
        }

    # Proxy selection score — re-underwrites of holdings are inherently lower priority than new ideas
    proxy_score = 0.65 if current_durability in ("stable", "strong") else 0.45

    routing = route_enrichment_tier(
        research_selection_score=proxy_score,
        reviewer_average_score=reviewer_average_score,
        has_hard_quality_objection=has_hard_objection,
        has_hard_mos_objection=False,
        is_existing_holding=True,  # Critical: tells router to be conservative on cost
        kronos_advisory_strength=(kronos_strength if use_kronos else None),
        high_conviction_override=False,
    )

    return {
        "tier": routing.tier,
        "reasons": routing.reasons,
        "is_existing_holding": True,
        "kronos_used": use_kronos and kronos_strength is not None,
    }


# Convenience helper for future scheduler / engine use
def is_reunderwriting_due(
    journal: LongTermDecisionJournal,
    symbol: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Lightweight check used by Position Review Queue and scheduler."""
    parent = _resolve_parent_decision(journal, symbol=symbol, decision_id=None)
    if not parent:
        return {"due": False, "reason": "no_parent_decision"}

    durability = str(parent.get("thesis_durability") or "stable").lower()
    last_date = parent.get("last_reunderwritten_date")

    due = force or durability in {"weakening", "broken"}
    return {
        "due": due,
        "symbol": symbol.upper(),
        "current_durability": durability,
        "last_reunderwritten_date": last_date,
        "parent_decision_id": parent["decision_id"],
    }
