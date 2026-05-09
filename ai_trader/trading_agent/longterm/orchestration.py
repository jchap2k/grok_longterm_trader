"""Dry-run orchestration helpers for one long-term research cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from pathlib import Path
from typing import Any, Callable, Mapping

from longterm.account_action_plan import AccountActionPlanBuilder
from longterm.buy_promotion import build_buy_promotion_markdown, build_buy_promotion_reviews
from longterm.capital_alert import build_capital_needed_alert
from longterm.decision_journal import LongTermDecisionJournal
from longterm.benchmark_guard import BenchmarkGuard
from longterm.discovery import DiscoveryEngine
from longterm.idle_cash_policy import MarketRegimeSnapshot
from longterm.market_regime_snapshot import market_regime_to_dict
from longterm.motley_fool_capture import capture_motley_fool_ideas
from longterm.motley_fool_settings import (
    MotleyFoolCaptureSettings,
    load_motley_fool_capture_settings,
)
from longterm.motley_fool_setup import complete_motley_fool_setup
from longterm.next_actions import build_next_actions_markdown
from longterm.portfolio_state import PortfolioState
from longterm.rebalance_planner import RebalancePlanner
from longterm.report_builder import build_markdown_report
from longterm.research_runner import LongTermResearchRunner
from portfolio.portfolio_profile import PortfolioProfile
from research.intake import create_research_packet_from_idea


DEFAULT_AGENT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent"
    / "configs"
    / "longterm_trading_agent_specs.json"
)


@dataclass(frozen=True)
class LongTermCycleResult:
    status: str
    capture_status: str
    setup_status: str
    manual_idea_count: int
    captured_idea_count: int
    total_idea_count: int
    skipped_idea_count: int = 0
    skipped_ideas: list[dict[str, str]] = field(default_factory=list)
    deferred_research_queue: list[dict[str, Any]] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    capture_sources_run: list[str] = field(default_factory=list)
    discovery_summary: dict[str, int] = field(default_factory=dict)
    discovery_research_symbols: list[str] = field(default_factory=list)
    login_url: str = ""
    profile_dir: Path | None = None
    recommendation_report_markdown: str = ""
    buy_promotion_markdown: str = ""
    next_actions_markdown: str = ""
    capital_alert_markdown: str = ""
    rebalance_markdown: str = ""
    account_action_plan: dict[str, Any] = field(default_factory=dict)
    capital_alert_generated: bool = False
    rebalance_generated: bool = False
    account_action_plan_generated: bool = False
    buy_promotion_generated: bool = False
    discovery_generated: bool = False
    report_generated: bool = False
    next_actions_generated: bool = False
    idea_provenance_summary: dict[str, int] = field(default_factory=dict)
    packet_completeness_warnings: list[str] = field(default_factory=list)
    decision_journal_refs: list[str] = field(default_factory=list)


def run_longterm_cycle(
    *,
    profile: PortfolioProfile,
    manual_ideas: list[Mapping[str, Any]] | None = None,
    discovery_candidates: list[Mapping[str, Any]] | None = None,
    discovery_engine: DiscoveryEngine | None = None,
    motley_fool_settings: MotleyFoolCaptureSettings | None = None,
    capture_func: Callable[..., list[dict[str, Any]]] = capture_motley_fool_ideas,
    setup_func: Callable[..., Any] = complete_motley_fool_setup,
    launch_login_if_needed: bool = False,
    runner: LongTermResearchRunner | Any | None = None,
    journal_db_path: str | Path | None = None,
    portfolio_state: PortfolioState | None = None,
    market_regime: MarketRegimeSnapshot | None = None,
    report_builder_func: Callable[..., str] = build_markdown_report,
    next_actions_builder_func: Callable[..., str] = build_next_actions_markdown,
    capital_alert_builder_func: Callable[..., Any] = build_capital_needed_alert,
    rebalance_planner: RebalancePlanner | None = None,
    benchmark_guard: BenchmarkGuard | None = None,
    account_action_plan_builder: AccountActionPlanBuilder | None = None,
    journal_factory: Callable[[str | Path | None], LongTermDecisionJournal] = LongTermDecisionJournal,
    active_sleeve_value: float | None = None,
    available_cash: float | None = None,
    report_limit: int = 10,
    agent_config_path: str | Path = DEFAULT_AGENT_CONFIG_PATH,
    agent_preset: str = "decision_4",
    verbose: bool = False,
) -> LongTermCycleResult:
    """Run one long-term research cycle in dry-run-safe mode.

    This orchestration path intentionally performs research, idea intake, and
    decision logging only. It does not place broker orders.
    """
    settings = motley_fool_settings or load_motley_fool_capture_settings()
    base_ideas = [dict(idea) for idea in (manual_ideas or [])]
    for idea in base_ideas:
        idea.setdefault("_provenance_bucket", "manual")

    discovery_ideas: list[dict[str, Any]] = []
    discovery_summary: dict[str, int] = {}
    discovery_research_symbols: list[str] = []
    discovery_generated = False
    if discovery_candidates:
        discovery_result = (discovery_engine or DiscoveryEngine()).build_queue(
            [dict(candidate) for candidate in discovery_candidates],
            research_limit=report_limit,
        )
        discovery_ideas = DiscoveryEngine.to_research_ideas(discovery_result.research_queue)
        for idea in discovery_ideas:
            idea.setdefault("_provenance_bucket", "discovery_research_queue")
        discovery_summary = {
            "research_queue": len(discovery_result.research_queue),
            "watchlist": len(discovery_result.watchlist),
            "rejected": len(discovery_result.rejected),
        }
        discovery_research_symbols = [candidate.symbol for candidate in discovery_result.research_queue]
        discovery_generated = True

    captured_ideas: list[dict[str, Any]] = []
    capture_sources_run: list[str] = []
    capture_status = "disabled"
    setup_status = "not_requested"
    status = "completed"

    if settings.should_open_login and launch_login_if_needed:
        setup_result = setup_func(settings=settings)
        setup_status = setup_result.status
        settings = setup_result.settings

    if settings.can_capture:
        for source_key in settings.sources:
            capture_sources_run.append(source_key)
            captured_ideas.extend(
                _with_provenance_bucket(
                    capture_func(
                        source_key,
                        profile_dir=settings.profile_dir,
                        url=None,
                    ),
                    source_key,
                )
            )
        capture_status = "captured"
    elif settings.should_open_login:
        capture_status = "login_required"
        status = "login_required"
        setup_status = "login_required" if setup_status == "not_requested" else setup_status

    all_ideas = [*base_ideas, *discovery_ideas, *captured_ideas]

    if runner is None:
        runner = LongTermResearchRunner(
            config_path=str(agent_config_path),
            agent_preset=agent_preset,
            verbose=verbose,
        )

    journal = journal_factory(journal_db_path) if journal_db_path else None
    decision_ids: list[str] = []
    packet_completeness_warnings: list[str] = []
    skipped_idea_count = 0
    skipped_ideas: list[dict[str, str]] = []
    deferred_research_queue: list[dict[str, Any]] = []
    for idea in all_ideas:
        enriched_idea = (
            journal.enrich_idea_with_symbol_feedback(idea)
            if journal is not None and hasattr(journal, "enrich_idea_with_symbol_feedback")
            else idea
        )
        packet_idea = {
            key: value for key, value in enriched_idea.items() if not str(key).startswith("_")
        }
        if market_regime is not None:
            packet_idea.setdefault("macro_regime_context", market_regime_to_dict(market_regime))
        packet = create_research_packet_from_idea(
            packet_idea,
            profile=profile,
            idea_source=packet_idea.get("idea_source"),
        )
        assessment = _packet_completeness_assessment(packet)
        packet_completeness_warnings.extend(assessment["warnings"])
        if assessment["block_research"]:
            skipped_idea_count += 1
            packet_completeness_warnings.append(f"{packet.symbol or 'UNKNOWN'}: skipped incomplete research packet")
            skipped_ideas.append(
                {
                    "symbol": packet.symbol or "UNKNOWN",
                    "reason": "incomplete_research_packet",
                }
            )
            deferred_research_queue.append(
                _deferred_research_item(
                    packet,
                    idea=idea,
                    warnings=assessment["warnings"],
                )
            )
            continue
        decision_ids.append(
            runner.run_and_record(
                packet,
                journal_db_path=journal_db_path,
                portfolio_state=portfolio_state,
                macro_regime=_format_macro_regime_context(packet.macro_regime_context),
            )
        )

    recommendation_report_markdown = ""
    buy_promotion_markdown = ""
    next_actions_markdown = ""
    capital_alert_markdown = ""
    rebalance_markdown = ""
    account_action_plan: dict[str, Any] = {}
    capital_alert_generated = False
    rebalance_generated = False
    account_action_plan_generated = False
    buy_promotion_generated = False
    report_generated = False
    next_actions_generated = False
    if journal_db_path:
        journal = journal or journal_factory(journal_db_path)
        if hasattr(journal, "record_deferred_research_item"):
            for item in deferred_research_queue:
                journal.record_deferred_research_item(item)
        recommendation_report_markdown = report_builder_func(
            journal,
            limit=report_limit,
        )
        report_generated = bool(recommendation_report_markdown)
        if report_generated and hasattr(journal, "record_recommendation_rank_snapshot"):
            journal.record_recommendation_rank_snapshot(
                journal.list_recommendation_table(limit=report_limit)
            )
        if active_sleeve_value is not None and available_cash is not None:
            alert = capital_alert_builder_func(
                journal,
                active_sleeve_value=active_sleeve_value,
                available_cash=available_cash,
                portfolio_state=portfolio_state,
                limit=report_limit,
            )
            capital_alert_markdown = getattr(alert, "markdown", "") or ""
            capital_alert_generated = bool(getattr(alert, "should_alert", False))
        if portfolio_state is not None:
            promotion_reviews = build_buy_promotion_reviews(
                journal,
                profile=profile,
                portfolio_state=portfolio_state,
                limit=report_limit,
            )
            if promotion_reviews:
                buy_promotion_markdown = build_buy_promotion_markdown(promotion_reviews)
                buy_promotion_generated = True
            guard = benchmark_guard or BenchmarkGuard()
            guard_result = guard.evaluate(journal.summarize_benchmark_performance())
            proposal = (rebalance_planner or RebalancePlanner()).propose(
                journal.list_recommendation_table(limit=report_limit),
                profile=profile,
                portfolio_state=portfolio_state,
                benchmark_guard_result=guard_result,
            )
            rebalance_markdown = _rebalance_markdown(proposal)
            rebalance_generated = proposal.should_rebalance
            plan_builder = account_action_plan_builder or AccountActionPlanBuilder(
                market_regime=market_regime,
            )
            plan = plan_builder.build(
                journal,
                profile=profile,
                portfolio_state=portfolio_state,
                limit=report_limit,
            )
            account_action_plan = plan.to_dict()
            if hasattr(journal, "record_action_plan"):
                journal.record_action_plan(account_action_plan)
            account_action_plan_generated = bool(account_action_plan)
            next_actions_markdown = next_actions_builder_func(
                journal,
                profile=profile,
                portfolio_state=portfolio_state,
                limit=report_limit,
                **_supported_next_actions_kwargs(
                    next_actions_builder_func,
                    deferred_research_queue=deferred_research_queue,
                    account_action_plan=account_action_plan,
                ),
            )
            next_actions_generated = bool(next_actions_markdown)

    return LongTermCycleResult(
        status=status,
        capture_status=capture_status,
        setup_status=setup_status,
        manual_idea_count=len(base_ideas),
        captured_idea_count=len(captured_ideas),
        total_idea_count=len(all_ideas),
        skipped_idea_count=skipped_idea_count,
        skipped_ideas=skipped_ideas,
        deferred_research_queue=deferred_research_queue,
        decision_ids=decision_ids,
        capture_sources_run=capture_sources_run,
        discovery_summary=discovery_summary,
        discovery_research_symbols=discovery_research_symbols,
        login_url=settings.login_url if settings.should_open_login else "",
        profile_dir=settings.profile_dir if settings.should_open_login else settings.profile_dir,
        recommendation_report_markdown=recommendation_report_markdown,
        buy_promotion_markdown=buy_promotion_markdown,
        next_actions_markdown=next_actions_markdown,
        capital_alert_markdown=capital_alert_markdown,
        rebalance_markdown=rebalance_markdown,
        account_action_plan=account_action_plan,
        capital_alert_generated=capital_alert_generated,
        rebalance_generated=rebalance_generated,
        account_action_plan_generated=account_action_plan_generated,
        buy_promotion_generated=buy_promotion_generated,
        discovery_generated=discovery_generated,
        report_generated=report_generated,
        next_actions_generated=next_actions_generated,
        idea_provenance_summary=_idea_provenance_summary(all_ideas),
        packet_completeness_warnings=packet_completeness_warnings,
        decision_journal_refs=list(decision_ids),
    )


def _with_provenance_bucket(
    ideas: list[dict[str, Any]],
    source_key: str,
) -> list[dict[str, Any]]:
    bucket = f"motley_fool_{source_key}"
    enriched = []
    for idea in ideas:
        payload = dict(idea)
        payload.setdefault("_provenance_bucket", bucket)
        enriched.append(payload)
    return enriched


def _idea_provenance_summary(ideas: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for idea in ideas:
        bucket = str(
            idea.get("_provenance_bucket")
            or idea.get("idea_source")
            or "manual"
        )
        summary[bucket] = summary.get(bucket, 0) + 1
    return summary


def _packet_completeness_assessment(packet) -> dict[str, Any]:
    warnings = packet.completeness_warnings()
    return {
        "warnings": warnings,
        "block_research": bool(warnings),
    }


def _deferred_research_item(packet, *, idea: Mapping[str, Any], warnings: list[str]) -> dict[str, Any]:
    return {
        "symbol": packet.symbol or "UNKNOWN",
        "reason": "incomplete_research_packet",
        "missing_fields": _missing_fields_from_warnings(warnings),
        "provenance_bucket": str(
            idea.get("_provenance_bucket")
            or packet.idea_source
            or "manual"
        ),
        "suggested_next_step": "enrich_candidate_before_research",
        "suggested_enrichment_command": (
            "python scripts/run_longterm_discovery.py --candidates path\\to\\candidates.json "
            "--enrichment-file path\\to\\fundamentals.json --enrichment-source fundamentals_cache"
        ),
    }


def _missing_fields_from_warnings(warnings: list[str]) -> list[str]:
    missing_fields = []
    for warning in warnings:
        if "missing company_name" in warning:
            missing_fields.append("company_name")
        elif "missing idea_source" in warning:
            missing_fields.append("idea_source")
        elif "missing research context" in warning:
            missing_fields.append("research_context")
    return missing_fields


def _format_macro_regime_context(context: Mapping[str, Any] | None) -> str:
    if not context:
        return ""

    pieces = [
        f"risk_regime={context.get('risk_regime') or 'unknown'}",
        f"macro_regime_label={context.get('macro_regime_label') or context.get('risk_regime') or 'unknown'}",
        f"provider_status={context.get('provider_status') or 'unknown'}",
        f"provider_mode={context.get('provider_mode') or 'unknown'}",
    ]
    for key in (
        "vix_level",
        "ten_year_yield_trend",
        "inflation_pressure",
        "yield_curve_spread",
        "credit_spread",
    ):
        value = context.get(key)
        if value is not None and value != "":
            pieces.append(f"{key}={value}")

    warning = str(context.get("provider_warning") or "").strip()
    if warning:
        pieces.append(f"provider_warning={warning}")

    macro_signals = context.get("macro_signals") or {}
    policy_boundary = ""
    if isinstance(macro_signals, Mapping):
        policy_boundary = str(macro_signals.get("policy_boundary") or "").strip()
    if policy_boundary:
        pieces.append(f"policy_boundary={policy_boundary}")

    return "Macro regime snapshot: " + "; ".join(pieces)


def _supported_next_actions_kwargs(builder: Callable[..., str], **kwargs: Any) -> dict[str, Any]:
    signature = inspect.signature(builder)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in parameters}


def _rebalance_markdown(proposal) -> str:
    if not proposal.should_rebalance:
        return ""
    rows = [
        ("Source current value", f"${proposal.source_current_value:,.2f}"),
        ("Source target value", f"${proposal.source_target_value:,.2f}"),
        ("Source rank", str(proposal.source_rank)),
        ("Target rank", str(proposal.target_rank)),
        ("Rank gap", str(proposal.rank_gap)),
        ("Target suggested size", f"{proposal.target_suggested_size_pct:.1f}%"),
        ("Source decision ID", proposal.source_decision_id or "n/a"),
        ("Target decision ID", proposal.target_decision_id or "n/a"),
        ("Source review due", _format_optional_bool(proposal.source_review_due)),
        ("Target review due", _format_optional_bool(proposal.target_review_due)),
        ("Source thesis state", proposal.source_thesis_state or "n/a"),
        ("Target thesis state", proposal.target_thesis_state or "n/a"),
        ("Source review adjustment", str(proposal.source_review_adjustment)),
        ("Source rebalance score", str(proposal.source_rebalance_score)),
        ("Rebalance score gap", str(proposal.rebalance_score_gap)),
        ("Benchmark gate", proposal.benchmark_guard_reason or "n/a"),
    ]
    details = "\n".join(f"| {label} | {value} |" for label, value in rows)
    return (
        "# Dry-Run Rebalance Proposal\n\n"
        f"Fund from: {proposal.fund_from_symbol}\n\n"
        f"Target: {proposal.target_symbol}\n\n"
        f"Proposed sell value: ${proposal.proposed_sell_value:,.2f}\n\n"
        f"Reason: {proposal.reason}\n\n"
        "## Details\n\n"
        "| Field | Value |\n"
        "|---|---:|\n"
        f"{details}\n"
    )


def _format_optional_bool(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"
