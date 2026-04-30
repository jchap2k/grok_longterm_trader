"""Dry-run orchestration helpers for one long-term research cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from longterm.motley_fool_capture import capture_motley_fool_ideas
from longterm.motley_fool_settings import (
    MotleyFoolCaptureSettings,
    load_motley_fool_capture_settings,
)
from longterm.next_actions import build_next_actions_markdown
from longterm.portfolio_state import PortfolioState
from longterm.report_builder import build_markdown_report
from longterm.decision_journal import LongTermDecisionJournal
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
    manual_idea_count: int
    captured_idea_count: int
    total_idea_count: int
    decision_ids: list[str] = field(default_factory=list)
    capture_sources_run: list[str] = field(default_factory=list)
    login_url: str = ""
    profile_dir: Path | None = None
    recommendation_report_markdown: str = ""
    next_actions_markdown: str = ""


def run_longterm_cycle(
    *,
    profile: PortfolioProfile,
    manual_ideas: list[Mapping[str, Any]] | None = None,
    motley_fool_settings: MotleyFoolCaptureSettings | None = None,
    capture_func: Callable[..., list[dict[str, Any]]] = capture_motley_fool_ideas,
    runner: LongTermResearchRunner | Any | None = None,
    journal_db_path: str | Path | None = None,
    portfolio_state: PortfolioState | None = None,
    report_builder_func: Callable[..., str] = build_markdown_report,
    next_actions_builder_func: Callable[..., str] = build_next_actions_markdown,
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

    captured_ideas: list[dict[str, Any]] = []
    capture_sources_run: list[str] = []
    capture_status = "disabled"
    status = "completed"

    if settings.can_capture:
        for source_key in settings.sources:
            capture_sources_run.append(source_key)
            captured_ideas.extend(
                capture_func(
                    source_key,
                    profile_dir=settings.profile_dir,
                    url=None,
                )
            )
        capture_status = "captured"
    elif settings.should_open_login:
        capture_status = "login_required"
        status = "login_required"

    all_ideas = [*base_ideas, *captured_ideas]

    if runner is None:
        runner = LongTermResearchRunner(
            config_path=str(agent_config_path),
            agent_preset=agent_preset,
            verbose=verbose,
        )

    decision_ids: list[str] = []
    for idea in all_ideas:
        packet = create_research_packet_from_idea(
            idea,
            profile=profile,
            idea_source=idea.get("idea_source"),
        )
        decision_ids.append(
            runner.run_and_record(
                packet,
                journal_db_path=journal_db_path,
            )
        )

    recommendation_report_markdown = ""
    next_actions_markdown = ""
    if journal_db_path:
        journal = LongTermDecisionJournal(journal_db_path)
        recommendation_report_markdown = report_builder_func(
            journal,
            limit=report_limit,
        )
        if portfolio_state is not None:
            next_actions_markdown = next_actions_builder_func(
                journal,
                profile=profile,
                portfolio_state=portfolio_state,
                limit=report_limit,
            )

    return LongTermCycleResult(
        status=status,
        capture_status=capture_status,
        manual_idea_count=len(base_ideas),
        captured_idea_count=len(captured_ideas),
        total_idea_count=len(all_ideas),
        decision_ids=decision_ids,
        capture_sources_run=capture_sources_run,
        login_url=settings.login_url if settings.should_open_login else "",
        profile_dir=settings.profile_dir if settings.should_open_login else settings.profile_dir,
        recommendation_report_markdown=recommendation_report_markdown,
        next_actions_markdown=next_actions_markdown,
    )
