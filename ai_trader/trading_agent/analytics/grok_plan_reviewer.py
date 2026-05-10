"""
GrokPlanReviewer - Automated second-opinion on implementation plans.

Sends a plan to the Grok web/project reviewer for blind-spot analysis before coding starts.
Uses 2-prompt conversation flow:
  1. Submit plan for Grok to read (leverages project sources for full context)
  2. Request structured JSON feedback (risks, blindspots, improvements)

This is called automatically by Claude Code at the end of planning sessions
so you don't have to manually request Grok's review.

Usage:
    reviewer = GrokPlanReviewer()
    result = reviewer.review(plan_text, feature_name="Dashboard V3")
    # result = {
    #     "blindspots": [...],
    #     "risks": [...],
    #     "improvements": [...],
    #     "alternative_approaches": [...],
    #     "summary": "...",
    #     "confidence": 0.75
    # }
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _load_default_trading_mode() -> str:
    """Load default reviewer trading mode from repo-safe project config."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "grok_project_config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        mode = payload.get("default_trading_mode")
        if mode:
            return str(mode)
    except Exception:
        pass
    return "swing"


# --- Module-level helpers (pure functions, testable without Grok) ---

FOUNDATION_KEYWORDS = {
    "learning": [
        "learning.db", "learning_database", "lessons", "lesson_hash",
        "pruning", "lesson_discovery", "trade_journal",
        "rebuy_block", "is_rebuy_blocked", "loss_analysis", "add_lesson",
        "add_structured_lesson", "backtest",
    ],
    "scheduler": [
        "automated_scheduler", "cron", "schedule.every", "market_hours",
        "3am", "4pm", "subprocess", "scheduler_heartbeat",
        "circuit_breaker", "websocket", "websocket_monitor",
        "market_data", "broker_config", "schwab", "alpaca",
        "paper_trading", "live_trading",
    ],
    "agent": [
        "claude_trading_agent", "conviction", "tool_executor",
        "position_manager", "agent_state", "trading_agent",
        "circuit_breaker", "portfoliocircuitbreaker", "reconcile",
        "_reconcile_positions",
    ],
}


def detect_foundations(text: str) -> list:
    """
    Return which foundations (learning/scheduler/agent) a plan touches.
    Pure function - no Grok, no file I/O.

    Args:
        text: Plan text or file names string.

    Returns:
        List of foundation names touched, e.g. ["learning", "scheduler"].
    """
    text_lower = text.lower()
    touched = []
    for foundation, keywords in FOUNDATION_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            touched.append(foundation)
    return touched


def grok_asked_questions(response: str) -> bool:
    """
    Check whether Grok replied with questions or with 'no questions'.

    Grok is instructed in Prompt 2 to reply with exactly 'no questions'
    if it has sufficient context. However, Grok sometimes responds with a
    verbose acknowledgment that contains the phrase later, e.g.:
    "Understood - I have no questions and am ready to proceed."

    Searches the first 300 chars for common 'no questions' phrases to
    handle verbose responses without false positives.

    Args:
        response: Raw text response from Grok to Prompt 2.

    Returns:
        True  if Grok has questions (no 'no questions' phrase found).
        False if Grok replied 'no questions' (or empty response).
    """
    if not response:
        return False
    first_block = response[:300].lower()
    no_q_phrases = ('no questions', 'i have no questions', 'no additional questions')
    return not any(phrase in first_block for phrase in no_q_phrases)


def parse_json_response(raw: str) -> dict:
    """
    Parse JSON from Grok response. Handles markdown fences and bare objects.
    Falls back to raw_response dict on parse failure.

    Args:
        raw: Raw text from Grok containing JSON.

    Returns:
        Parsed dict, or fallback dict with raw_response key.
    """
    # Try markdown fence ```json {...} ```
    fence_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE
    )
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try bare JSON object
    brace_match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    return {
        "blindspots": [],
        "risks": [],
        "improvements": [],
        "alternative_approaches": [],
        "summary": raw[:500] if raw else "No response received",
        "confidence": 0.0,
        "raw_response": raw,
    }


def extract_files_from_text(text: str) -> list:
    """
    Extract Python file names mentioned in a plan or description.

    Args:
        text: Plan text, may contain file names like automated_scheduler.py

    Returns:
        List of unique .py filenames found in the text.
    """
    pattern = r'\b([\w/]+\.py)\b'
    matches = re.findall(pattern, text)
    seen = set()
    result = []
    for m in matches:
        fname = m.split('/')[-1]
        if fname not in seen:
            seen.add(fname)
            result.append(fname)
    return result


def build_grok_context_warmup_prompt(trading_mode: str = "swing") -> str:
    """Build the first prompt that orients Grok before review/debug details."""
    trading_mode = str(trading_mode or "swing").strip().lower()
    if trading_mode == "longterm":
        return (
            "First action: use your GitHub tools, preferably get_file_contents, "
            "to read docs/system/REPO_CONTEXT.md from the GitHub repository "
            "jchap2k/grok_longterm_trader. Treat the entire file as the "
            "authoritative, current project context for the long-term trader. "
            "Do not rely on stale chat memory or the day/swing trader context. "
            "Do not begin source-file scanning before reading this context file; "
            "after reading it, only inspect additional source files if the review "
            "or debug request requires deeper verification. Reply with a brief "
            "confirmation that includes the repo name, the context file path, "
            "2-3 loaded architectural facts, and any open safety/data blockers "
            "called out by the context file."
        )

    if trading_mode == "swing":
        mode_label = "swing trading"
        review_files = (
            "GROK__SWING_REGIME_FILTER.md, "
            "GROK__SWING_FORCESWING_SIGNAL.md, "
            "GROK__SWING_SWING_EXIT_ENGINE.md, "
            "GROK__SWING_STOCK_SELECTION.md, "
            "GROK__SWING_TRADING_DECISION_PROMPTS.md, "
            "GROK__SWING_BACKTEST_HARNESS.md, "
            "GROK__SWING_SCHEDULER.md, "
            "GROK__SWING_READINESS_ASSESSMENT.md"
        )
        arch_doc = "DAY_TRADER_TO_SWING_TRADER_CHANGES.md"
    else:
        mode_label = "day trading"
        review_files = (
            "GROK_4.2_SCHEDULER_ORCHESTRATION.md, "
            "GROK_4.2_TRADING_DECISION_PROMPTS.md, "
            "GROK_4.2_TRADE_ENTRY_TRACKING.md, "
            "GROK_4.2_TRADE_EXIT_ARCHITECTURE.md, "
            "GROK_4.2_LESSON_LEARNING_SYSTEM.md, "
            "GROK_4.2_RISK_MANAGEMENT.md, "
            "GROK_4.2_POSITION_MANAGEMENT.md, "
            "GROK_4.2_READINESS_ASSESSMENT.md"
        )
        arch_doc = "IMPLEMENTATION_COMPLETE.md"

    return (
        f"Before we begin a plan review, please review your source documents for this "
        f"project. We are doing {mode_label}. "
        f"Please load and review these specific files from the project data sources: "
        f"(1) {arch_doc} for the full architecture context, "
        f"(2) active_rules.txt for the current trading rules and conviction rubric, "
        f"(3) these architecture review docs (if available): {review_files}. "
        f"Reply with a brief confirmation: trading mode, 2-3 key architectural facts "
        f"you have loaded, and any known open CRITICAL-DATA blockers you are tracking "
        f"for this project. This ensures you are oriented correctly before reviewing."
    )


class GrokPlanReviewer:
    """
    Automated plan review using the Grok web/project UI via browser automation.

    Grok's project has full codebase context via uploaded sources, so it
    can identify blindspots that Claude Code might miss (e.g., conflicts
    with existing architecture, overlooked edge cases).
    """

    def __init__(
        self,
        headless: bool = False,
        minimized: bool = True,
        timeout: int = 120,
    ):
        """
        Initialize the plan reviewer.

        Args:
            headless: Run headless (not recommended - bot detection).
            minimized: Run off-screen (invisible but no throttling).
            timeout: Seconds to wait per Grok response.
        """
        self.timeout = timeout

        try:
            from .safe_grok_client import SafeGrokClient

            self.client = SafeGrokClient(
                headless=headless,
                minimized=minimized,
                timeout=timeout,
                max_retries=2,
            )
        except ImportError as e:
            raise ImportError(f"SafeGrokClient not available: {e}") from e

    def _build_rich_context(
        self,
        plan_text: str,
        files_affected: list,
        foundations: list,
    ) -> str:
        """
        Build the rich context block sent in Prompt 1.
        Includes code snippets from affected files and recent changes.
        """
        from pathlib import Path

        sections = []

        # Foundation warning
        if foundations:
            sections.append(
                f"## Foundation Alert\nThis plan touches: {', '.join(foundations).upper()}. "
                f"These are core systems - errors here affect live trading."
            )

        # Code snippets from affected files
        repo_root = Path(__file__).parent.parent.parent.parent
        snippets = []
        search_dirs = [
            repo_root / "ai_trader" / "trading_agent",
            repo_root / "ai_trader" / "trading_agent" / "agent",
            repo_root / "ai_trader" / "trading_agent" / "analytics",
            repo_root / "ai_trader" / "trading_agent" / "brokers",
        ]
        for fname in files_affected[:4]:  # cap at 4 files
            for search_dir in search_dirs:
                candidate = search_dir / fname
                if candidate.exists():
                    try:
                        content = candidate.read_text(encoding='utf-8', errors='replace')
                        lines = content.splitlines()
                        preview = '\n'.join(lines[:60])
                        snippets.append(
                            f"### {fname} (first 60 lines)\n```python\n{preview}\n```"
                        )
                    except Exception:
                        pass
                    break

        if snippets:
            sections.append("## Relevant Code Snippets\n" + '\n\n'.join(snippets))

        # Recent changes context
        recent_changes_path = repo_root / ".claude" / "memory" / "RECENT_CHANGES.md"
        if recent_changes_path.exists():
            try:
                rc_content = recent_changes_path.read_text(encoding='utf-8', errors='replace')
                rc_preview = '\n'.join(rc_content.splitlines()[:60])
                sections.append(f"## Recent Changes (last 2 weeks)\n{rc_preview}")
            except Exception:
                pass

        return '\n\n'.join(sections)

    def review(
        self,
        plan_text: str,
        feature_name: str = "Implementation Plan",
        files_affected: Optional[list] = None,
        extra_context: Optional[str] = None,
        questions_callback: Optional[Any] = None,
        trading_mode: str = "auto",
    ) -> Dict[str, Any]:
        """
        Submit a plan to Grok for blind-spot and risk analysis.

        Uses a bounded 2-3 prompt flow:
          1. Plan + context + questions-or-no-questions instruction (ONE message)
          2a. If Grok has questions: answers + JSON schema request (ONE message)
          2b. If no questions: JSON schema request (ONE message)

        Grok replies to Prompt 1 with either:
          - Questions it needs answered before reviewing, OR
          - Exactly "no questions" to skip straight to JSON

        Args:
            plan_text: The full implementation plan text (markdown OK).
            feature_name: Short name for this feature/change (for logging).
            files_affected: List of .py filenames being modified. If None,
                            auto-detected from plan_text.
            extra_context: Optional extra context to prepend.
            questions_callback: Optional callable(question_text: str) -> str.
                If provided and Grok asks questions, this callback is called with
                Grok's full question text and its return value is sent as the answer.
                If None, falls back to auto-fetching code snippets from mentioned files.
            trading_mode: "longterm", "swing", or "day" - tells Grok which system context applies.
                "longterm" = quality-growth active sleeve, dry-run actions, FXAIX benchmark.
                "swing" = 2-7 day holds, FORCESWING signal, buy stops, SwingExitEngine.
                "day"   = intraday only, gap momentum, same-day forced close.
                Defaults to config/grok_project_config.json when available.

        Returns:
            Dictionary with keys:
                blindspots, risks, improvements, alternative_approaches,
                summary, confidence, proceed_recommendation.
        """
        logger.info(f"[GrokPlanReview] Starting review: {feature_name}")
        print(f"[GrokReview] Starting review: {feature_name}", flush=True)
        if trading_mode == "auto":
            trading_mode = _load_default_trading_mode()

        # Auto-detect files and foundations if not provided
        if files_affected is None:
            files_affected = extract_files_from_text(plan_text)
        foundations = detect_foundations(plan_text + ' ' + ' '.join(files_affected))

        # Build rich context
        rich_context = self._build_rich_context(plan_text, files_affected, foundations)

        context_block = ""
        if extra_context:
            context_block = f"\n\n## Additional Context\n{extra_context}\n"

        # JSON schema used in Prompt 3/final to request the structured analysis
        json_schema = """```json
{
  "findings": [
    {
      "priority": "CRITICAL",
      "category": "DATA|ARCHITECTURE|SAFETY|PERFORMANCE",
      "title": "Short title for this finding",
      "description": "What is missing or wrong",
      "fix_approach": "Concrete fix with file/method names",
      "effort": "quick (<1h) | medium (1-4h) | large (4h+)"
    }
  ],
  "critical_path": [
    "Item A must be done before Item B because ..."
  ],
  "recommended_sequence": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "downstream_impact": [
    "Other system or component affected by these changes"
  ],
  "blindspots": ["Specific thing the plan overlooked"],
  "risks": ["Technical or operational risk"],
  "improvements": ["Concrete change that would strengthen the plan"],
  "alternative_approaches": ["Different implementation path"],
  "summary": "2-3 sentence overall assessment.",
  "confidence": 0.75,
  "proceed_recommendation": "proceed_with_notes"
}
```

findings[].priority must be one of: CRITICAL | HIGH | MEDIUM | LOW
findings[].category must be one of: DATA | ARCHITECTURE | SAFETY | PERFORMANCE | INTEGRATION
proceed_recommendation must be one of:
  proceed_as_written | proceed_with_notes | revise_first | major_rethink_needed

Minimum 2 items in findings and blindspots. Maximum 8 findings, 5 per other array.
Be specific to THIS codebase - reference actual file names, method names, and
component interactions (e.g. decision_journal, trade_id, learning.db, lessons)."""

        # Build trading mode context block for prompt 1
        if trading_mode == "longterm":
            mode_context = (
                "## Trading System Context\n"
                "This is the LONG-TERM TRADER project (grok_longterm_trader), not the day or swing trader.\n"
                "Key characteristics:\n"
                "- Strategy: research-first quality-growth active sleeve\n"
                "- Protected benchmark/core holding: FXAIX, operationally untouchable\n"
                "- Defensive parking symbol: SPY, separate from benchmark logic\n"
                "- Execution state: research, journaling, reporting, alerts, and dry-run action planning only\n"
                "- Decision committee: CGH decision_4 default, decision_6 for high-value/borderline portfolio decisions\n"
                "- Planning safety: no broker orders, protected-symbol blocking, cash checks, benchmark guard versus FXAIX\n"
                "- Current context docs: docs/system/README.md, ARCHITECTURE.md, OPERATIONS.md, SAFETY.md, project_manifest.json"
            )
        elif trading_mode == "swing":
            mode_context = (
                "## Trading System Context\n"
                "This is the SWING TRADER fork (grok_swing_trader), NOT the original day trader.\n"
                "Key differences from the day trader:\n"
                "- Hold duration: 2-7 days (PEAD up to 15 days), not intraday\n"
                "- Entry signal: FORCESWING mechanical scan (Force Index + ADX pullback in trend)\n"
                "- Entry mechanism: buy stop orders placed pre-open, not market orders at open\n"
                "- Exit engine: SwingExitEngine (7 automated conditions), not agent-driven 12:50 PM close\n"
                "- Schedule: 4:30/5:45/6:45/9:00/12:30/1:05 PT - all intraday routines disabled\n"
                "- Lessons: hold_duration='swing' corpus, separate from intraday lessons\n"
                "- No PDT constraint, no power-hour ban, no gap momentum strategies\n"
                "Reference doc: ai_trader/docs/DAY_TRADER_TO_SWING_TRADER_CHANGES.md "
                "(full architecture delta - uploaded to this project as a data source)"
            )
        else:
            mode_context = (
                "## Trading System Context\n"
                "This is the original DAY TRADER system (grok_day_trader).\n"
                "Key characteristics:\n"
                "- Hold duration: intraday only (open before 8 AM PT, force-close 12:50 PM PT)\n"
                "- Entry signal: gap-up momentum, ORB, VWAP reclaim, catalyst plays\n"
                "- Schedule: 6:20/6:30/7:00-8:00 opportunity scans/12:50/1:00 PM PT\n"
                "- PDT protection: 3-trade limit enforced on sub-$25k accounts\n"
                "- Lessons: hold_duration='intraday' corpus"
            )

        # Prompt 1: Submit plan with full context, ask for a brief summary
        prompt1 = f"""# Plan Review Request: {feature_name}

I am about to implement the following plan for our AI trading system.
Please read it carefully along with the code context below.{context_block}

{mode_context}

## The Plan
{plan_text}

---

{rich_context}

---

When you review this plan, please cross-reference it against ALL system components
you have context on -- including but not limited to: decision_journal, trade_id
linkage, learning.db/lessons, active_rules.txt conviction rubric, current sprint
phase, and any CRITICAL-DATA blockers you are tracking. Surface any interaction
where this plan touches or depends on those components even if the plan text does
not mention them explicitly.

Confirm you have read the plan and context by summarizing the plan in 2-3 sentences."""

        # Prompt 0: Context warm-up. For long-term work, Grok now has GitHub
        # access and should read docs/system/REPO_CONTEXT.md before scanning
        # source files. This avoids stale day/swing-trader context.
        prompt0 = build_grok_context_warmup_prompt(trading_mode)

        logger.info("[GrokPlanReview] Prompt 0: Asking Grok to review source documents...")
        print("[GrokReview] Prompt 0: Loading Grok context...", flush=True)
        context_ack = self.client.ask(
            prompt0, max_wait=self.timeout, new_chat=True, close_after=False
        )
        logger.info(f"[GrokPlanReview] Context loaded: {context_ack[:150]}...")
        print(f"[GrokReview] Grok context loaded ({len(context_ack)} chars)", flush=True)

        # Prompt 1: Submit plan -- continues in same chat (context already loaded)
        logger.info("[GrokPlanReview] Prompt 1: Submitting plan with rich context...")
        print("[GrokReview] Prompt 1: Submitting plan...", flush=True)
        summary = self.client.ask(
            prompt1, max_wait=self.timeout, new_chat=False, close_after=False
        )
        logger.info(f"[GrokPlanReview] Grok acknowledged: {summary[:150]}...")
        print(f"[GrokReview] Grok acknowledged plan ({len(summary)} chars)", flush=True)

        # Prompt 2: Single compact string - ask for questions OR "no questions".
        # No blank lines so Playwright sends it as one message (blank lines can
        # trigger premature Enter-submit in the chat textarea).
        prompt2 = (
            "If you need more information before reviewing this plan, ask all your "
            "important questions now in this single message - Claude Code will answer "
            "them all in one consolidated response with code snippets if needed. "
            "If you have enough context to review the plan, reply with exactly: no questions"
        )

        logger.info("[GrokPlanReview] Prompt 2: Asking Grok for questions or 'no questions'...")
        print("[GrokReview] Prompt 2: Questions check...", flush=True)
        response2 = self.client.ask(
            prompt2, max_wait=self.timeout, new_chat=False, close_after=False
        )

        if grok_asked_questions(response2):
            logger.info("[GrokPlanReview] Grok asked questions - preparing answers...")
            print(
                f"[GrokReview] Grok asked questions ({len(response2)} chars):",
                flush=True,
            )
            # Print first 300 chars of questions for visibility
            print(f"  {response2[:300].replace(chr(10), ' ')}...", flush=True)

            if questions_callback is not None:
                # Use caller-provided callback to get targeted answers
                logger.info("[GrokPlanReview] Using questions_callback for answers...")
                print("[GrokReview] Calling questions_callback for answers...", flush=True)
                answers_text = questions_callback(response2)
            else:
                # Fallback: read any files Grok mentioned and send code snippets
                logger.info("[GrokPlanReview] Auto-fetching code snippets for answers...")
                print("[GrokReview] Auto-fetching code snippets...", flush=True)
                grok_files = extract_files_from_text(response2)
                from pathlib import Path
                repo_root = Path(__file__).parent.parent.parent.parent
                search_dirs = [
                    repo_root / "ai_trader" / "trading_agent",
                    repo_root / "ai_trader" / "trading_agent" / "agent",
                    repo_root / "ai_trader" / "trading_agent" / "analytics",
                ]
                answer_parts = ["## Additional Code Context Requested"]
                for fname in grok_files[:3]:  # cap at 3 files
                    for search_dir in search_dirs:
                        candidate = search_dir / fname
                        if candidate.exists():
                            try:
                                content = candidate.read_text(
                                    encoding='utf-8', errors='replace'
                                )
                                lines = content.splitlines()[:80]
                                answer_parts.append(
                                    f"\n### {fname}\n```python\n{chr(10).join(lines)}\n```"
                                )
                            except Exception:
                                pass
                            break
                answers_text = '\n'.join(answer_parts)

            # Prompt 3: answers + JSON schema in ONE message
            prompt3 = (
                "Here are the answers to your questions:\n\n"
                + answers_text
                + "\n\nNow provide your full plan review as JSON:\n\n"
                + json_schema
            )
            logger.info("[GrokPlanReview] Prompt 3: Sending answers + JSON request...")
            print("[GrokReview] Prompt 3: Sending answers + JSON request...", flush=True)
            final_raw = self.client.ask(
                prompt3, max_wait=self.timeout, new_chat=False, close_after=True
            )
        else:
            # Grok has no questions - request final JSON directly
            logger.info("[GrokPlanReview] Grok has no questions - requesting final JSON...")
            print("[GrokReview] No questions - requesting final JSON...", flush=True)
            prompt3 = f"Provide your full plan review as JSON:\n\n{json_schema}"
            final_raw = self.client.ask(
                prompt3, max_wait=self.timeout, new_chat=False, close_after=True
            )

        result = parse_json_response(final_raw)
        logger.info(
            f"[GrokPlanReview] Done - confidence={result.get('confidence', '?')}, "
            f"proceed={result.get('proceed_recommendation', '?')}"
        )
        print(
            f"[GrokReview] Complete - "
            f"confidence={result.get('confidence', '?')}, "
            f"proceed={result.get('proceed_recommendation', '?')}",
            flush=True,
        )
        return result

    def close(self) -> None:
        """Close browser and cleanup."""
        if self.client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def format_review_for_display(result: Dict[str, Any], feature_name: str = "") -> str:
    """
    Format a plan review result for display to the user.

    Returns a markdown-formatted string with the review findings.
    """
    lines = []
    header = f"Grok Browser Plan Review"
    if feature_name:
        header += f": {feature_name}"
    lines.append(f"## {header}")
    lines.append("")

    confidence = result.get("confidence", 0)
    conf_pct = int(confidence * 100)
    if confidence >= 0.80:
        conf_label = "HIGH"
    elif confidence >= 0.65:
        conf_label = "MEDIUM"
    else:
        conf_label = "LOW"
    lines.append(f"**Confidence**: {conf_pct}% ({conf_label})")
    lines.append("")

    rec = result.get("proceed_recommendation", "")
    if rec:
        rec_display = {
            "proceed_as_written": "PROCEED AS WRITTEN",
            "proceed_with_notes": "PROCEED (keep risks in mind)",
            "revise_first": "REVISE PLAN FIRST",
            "major_rethink_needed": "MAJOR RETHINK NEEDED",
        }.get(rec, rec.upper())
        lines.append(f"**Recommendation**: {rec_display}")
        lines.append("")

    summary = result.get("summary", "")
    if summary:
        lines.append(f"**Summary**: {summary}")
        lines.append("")

    # Structured findings (priority-tiered)
    findings = result.get("findings", [])
    if findings:
        lines.append("### Findings")
        priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        sorted_findings = sorted(
            findings,
            key=lambda f: priority_order.get(f.get("priority", "LOW"), 3)
        )
        for f in sorted_findings:
            priority = f.get("priority", "")
            category = f.get("category", "")
            title = f.get("title", "")
            desc = f.get("description", "")
            fix = f.get("fix_approach", "")
            effort = f.get("effort", "")
            tag = f"[{priority}-{category}]" if category else f"[{priority}]"
            lines.append(f"- **{tag} {title}**")
            if desc:
                lines.append(f"  - {desc}")
            if fix:
                lines.append(f"  - Fix: {fix}")
            if effort:
                lines.append(f"  - Effort: {effort}")
        lines.append("")

    # Critical path
    critical_path = result.get("critical_path", [])
    if critical_path:
        lines.append("### Critical Path")
        for item in critical_path:
            lines.append(f"- {item}")
        lines.append("")

    # Recommended sequence
    recommended_sequence = result.get("recommended_sequence", [])
    if recommended_sequence:
        lines.append("### Recommended Sequence")
        for item in recommended_sequence:
            lines.append(f"- {item}")
        lines.append("")

    # Downstream impact
    downstream_impact = result.get("downstream_impact", [])
    if downstream_impact:
        lines.append("### Downstream Impact")
        for item in downstream_impact:
            lines.append(f"- {item}")
        lines.append("")

    blindspots = result.get("blindspots", [])
    if blindspots:
        lines.append("### Blindspots")
        for item in blindspots:
            lines.append(f"- {item}")
        lines.append("")

    risks = result.get("risks", [])
    if risks:
        lines.append("### Risks")
        for item in risks:
            lines.append(f"- {item}")
        lines.append("")

    improvements = result.get("improvements", [])
    if improvements:
        lines.append("### Improvements")
        for item in improvements:
            lines.append(f"- {item}")
        lines.append("")

    alternatives = result.get("alternative_approaches", [])
    if alternatives:
        lines.append("### Alternative Approaches")
        for item in alternatives:
            lines.append(f"- {item}")
        lines.append("")

    if "raw_response" in result:
        lines.append("*Note: JSON parsing failed - raw Grok response shown in summary*")

    return "\n".join(lines)
