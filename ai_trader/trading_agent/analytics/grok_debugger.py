"""
GrokDebugger - Root cause analysis and fix review using the Grok web/project UI.

Two modes:
  "diagnose" - Claude Code is stuck, send symptom + code + logs for root cause
  "review_fix" - Claude Code has a fix, send it to Grok before implementing

Uses same bounded Q&A flow as GrokPlanReviewer:
  Prompt 1: Rich context (symptom/fix + code + logs)
  Prompt 2: Grok asks ALL questions at once (or skips to JSON)
  Prompt 3: Claude Code answers all questions
  Prompt 4: Final JSON

Usage:
    debugger = GrokDebugger()
    result = debugger.diagnose(
        symptom="scheduler crashes at 3AM with KeyError",
        error_text="KeyError: 'lesson_hash' at learning_database.py:234",
        suspected_files=["learning_database.py", "automated_scheduler.py"],
        logs="[03:00:12] ERROR scheduler crashed...",
    )
    result = debugger.review_fix(
        bug_description="KeyError on lesson_hash",
        proposed_fix="Add .get() with default instead of direct key access",
        files_to_change=["learning_database.py"],
    )
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from .grok_plan_reviewer import (
    _load_default_trading_mode,
    build_grok_context_warmup_prompt,
    detect_foundations,
    grok_asked_questions,
    parse_json_response,
    extract_files_from_text,
)

logger = logging.getLogger(__name__)

EMPTY_DEBUG_RESULT = {
    "root_cause": "",
    "confidence": 0.0,
    "evidence": [],
    "fix_recommendation": "",
    "files_to_check": [],
    "risks_in_fix": [],
    "alternative_fixes": [],
    "raw_response": "",
}


class GrokDebugger:
    """
    Root cause analysis and fix review using the Grok web/project UI.

    Two modes of operation:
      diagnose()    - send a symptom + code + logs for root cause diagnosis
      review_fix()  - send a proposed fix for Grok to validate before implementing
    """

    REPO_ROOT = Path(__file__).parent.parent.parent.parent
    SEARCH_DIRS = [
        "ai_trader/trading_agent",
        "ai_trader/trading_agent/agent",
        "ai_trader/trading_agent/analytics",
        "ai_trader/trading_agent/brokers",
        "ai_trader/trading_agent/agent/tools",
        "ai_trader/trading_agent/agent/positions",
    ]

    def __init__(
        self,
        headless: bool = False,
        minimized: bool = True,
        timeout: int = 120,
    ):
        """
        Initialize GrokDebugger.

        Args:
            headless: Run headless (not recommended - bot detection).
            minimized: Start with off-screen window (invisible but no throttling).
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

    def diagnose(
        self,
        symptom: str,
        error_text: str = "",
        suspected_files: Optional[List[str]] = None,
        logs: str = "",
        already_tried: str = "",
        trading_mode: str | None = None,
    ) -> Dict[str, Any]:
        """
        Send a bug symptom to Grok for root cause diagnosis.

        Args:
            symptom: Plain English description of the problem.
            error_text: Exact error message / traceback.
            suspected_files: .py files likely involved. Auto-detected if None.
            logs: Relevant log excerpts (keep under 50 lines).
            already_tried: What Claude Code already checked or tried.

        Returns:
            Dict with root_cause, confidence, evidence, fix_recommendation,
            files_to_check, risks_in_fix, alternative_fixes.
        """
        if suspected_files is None:
            suspected_files = extract_files_from_text(error_text + " " + symptom)

        foundations = detect_foundations(symptom + " " + " ".join(suspected_files))
        code_snippets = self._read_files(suspected_files)
        warmup_prompt = build_grok_context_warmup_prompt(trading_mode or _load_default_trading_mode())

        prompt1 = f"""# Debug Request: Root Cause Analysis

## Symptom
{symptom}

## Error / Traceback
{error_text or "(no error text provided)"}

## Suspected Files
{', '.join(suspected_files) if suspected_files else "(auto-detected from error)"}

## Relevant Logs
{logs or "(no logs provided)"}

## What Was Already Tried
{already_tried or "(nothing tried yet)"}

## Foundation Alert
{"Touches: " + ', '.join(foundations).upper() if foundations else "No core foundations identified"}

## Code Context
{code_snippets}

---

Confirm you have read the bug report by summarizing the symptom in 1-2 sentences."""

        logger.info("[GrokDebugger] Prompt 0: Loading repo context...")
        self.client.ask(
            warmup_prompt, max_wait=self.timeout, new_chat=True, close_after=False
        )
        logger.info("[GrokDebugger] Prompt 1: Sending bug report...")
        self.client.ask(
            prompt1, max_wait=self.timeout, new_chat=False, close_after=False
        )

        return self._run_qa_round(mode="diagnose")

    def review_fix(
        self,
        bug_description: str,
        proposed_fix: str,
        files_to_change: Optional[List[str]] = None,
        extra_context: str = "",
        trading_mode: str | None = None,
    ) -> Dict[str, Any]:
        """
        Submit a proposed fix to Grok for review before implementing.

        Args:
            bug_description: What the bug is.
            proposed_fix: What Claude Code plans to do to fix it.
            files_to_change: .py files that will be modified. Auto-detected if None.
            extra_context: Any additional context (e.g. recent changes, constraints).

        Returns:
            Dict with root_cause confirmation, fix_recommendation assessment,
            risks_in_fix, alternative_fixes, confidence.
        """
        if files_to_change is None:
            files_to_change = extract_files_from_text(
                proposed_fix + " " + bug_description
            )

        foundations = detect_foundations(
            bug_description + " " + " ".join(files_to_change)
        )
        code_snippets = self._read_files(files_to_change)
        warmup_prompt = build_grok_context_warmup_prompt(trading_mode or _load_default_trading_mode())

        prompt1 = f"""# Fix Review Request

## Bug
{bug_description}

## Proposed Fix
{proposed_fix}

## Files to Change
{', '.join(files_to_change) if files_to_change else "(not specified)"}

## Foundation Alert
{"Touches: " + ', '.join(foundations).upper() if foundations else "No core foundations identified"}

## Current Code
{code_snippets}

{("## Extra Context\n" + extra_context) if extra_context else ""}

---

Confirm you have read the fix proposal by summarizing it in 1-2 sentences."""

        logger.info("[GrokDebugger] Prompt 0: Loading repo context...")
        self.client.ask(
            warmup_prompt, max_wait=self.timeout, new_chat=True, close_after=False
        )
        logger.info("[GrokDebugger] Prompt 1: Sending fix for review...")
        self.client.ask(
            prompt1, max_wait=self.timeout, new_chat=False, close_after=False
        )

        return self._run_qa_round(mode="review_fix")

    def _run_qa_round(self, mode: str) -> Dict[str, Any]:
        """
        Run Prompts 2-4: Grok asks questions, Claude answers, final JSON.
        Shared by both diagnose() and review_fix().

        Args:
            mode: "diagnose" or "review_fix" - controls schema prompt text.

        Returns:
            Parsed result dict.
        """
        schema = self._get_schema()

        prompt2 = """If you need more information before diagnosing, ask ALL your
important questions now in this single message. Claude Code will answer
all of them in one consolidated response (with code snippets if needed).

If you have enough context, reply with exactly:
no questions

Do not include anything else in your reply if you have no questions."""

        logger.info("[GrokDebugger] Prompt 2: Asking Grok for questions or 'no questions'...")
        response2 = self.client.ask(
            prompt2, max_wait=self.timeout, new_chat=False, close_after=False
        )

        if grok_asked_questions(response2):
            logger.info("[GrokDebugger] Grok asked questions - fetching answers...")
            grok_files = extract_files_from_text(response2)
            code_answers = self._read_files(grok_files)

            prompt3 = f"""Here are answers to your questions:

## Additional Code You Requested
{code_answers if code_answers else "(no additional files found in known locations)"}

Now provide your final JSON analysis."""

            logger.info("[GrokDebugger] Prompt 3: Sending answers...")
            self.client.ask(
                prompt3, max_wait=self.timeout, new_chat=False, close_after=False
            )

            prompt4 = f"Return ONLY your final JSON - no commentary:\n\n{schema}"
            logger.info("[GrokDebugger] Prompt 4: Requesting final JSON...")
            final_raw = self.client.ask(
                prompt4, max_wait=self.timeout, new_chat=False, close_after=True
            )
        else:
            # Grok has no questions - request final JSON now
            logger.info("[GrokDebugger] Grok has no questions - requesting final JSON...")
            prompt3 = f"Provide your full analysis as JSON - no commentary:\n\n{schema}"
            final_raw = self.client.ask(
                prompt3, max_wait=self.timeout, new_chat=False, close_after=True
            )

        result = parse_json_response(final_raw)
        logger.info(
            f"[GrokDebugger] Done - confidence={result.get('confidence', '?')}, "
            f"root_cause={str(result.get('root_cause', '?'))[:80]}"
        )
        return result

    def _read_files(self, filenames: List[str]) -> str:
        """
        Read and return code snippets from requested files (first 60 lines each).

        Args:
            filenames: List of .py filenames to look up.

        Returns:
            Markdown-formatted code snippets string, or "(files not found)" message.
        """
        parts = []
        for fname in filenames[:4]:  # cap at 4 files
            for subdir in self.SEARCH_DIRS:
                candidate = self.REPO_ROOT / subdir / fname
                if candidate.exists():
                    try:
                        content = candidate.read_text(encoding='utf-8', errors='replace')
                        preview = '\n'.join(content.splitlines()[:60])
                        parts.append(f"### {fname}\n```python\n{preview}\n```")
                    except Exception:
                        pass
                    break
        return '\n\n'.join(parts) if parts else "(files not found in known locations)"

    def _get_schema(self) -> str:
        """Return the JSON schema prompt for diagnosis/fix review."""
        return '''```json
{
  "root_cause": "Specific diagnosis of what is failing and why",
  "confidence": 0.80,
  "evidence": [
    "Specific log line or code reference supporting the diagnosis"
  ],
  "fix_recommendation": "Concrete description of what to change and where",
  "files_to_check": [
    "automated_scheduler.py:234"
  ],
  "risks_in_fix": [
    "Side effect or regression the fix could introduce"
  ],
  "alternative_fixes": [
    "Different fix approach worth considering"
  ]
}
```'''

    def close(self) -> None:
        """Close browser and cleanup."""
        if self.client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def format_debug_result(result: Dict[str, Any], title: str = "") -> str:
    """
    Format a debug result for display to the user.

    Returns a markdown-formatted string with the diagnosis/fix review.
    """
    lines = [f"## Grok Browser Debug Analysis{': ' + title if title else ''}"]
    lines.append("")

    confidence = result.get("confidence", 0)
    conf_pct = int(confidence * 100)
    if confidence >= 0.80:
        conf_label = "HIGH"
    elif confidence >= 0.65:
        conf_label = "MEDIUM"
    else:
        conf_label = "LOW"
    lines.append(f"**Confidence in diagnosis**: {conf_pct}% ({conf_label})")
    lines.append("")

    root_cause = result.get("root_cause", "")
    if root_cause:
        lines.append(f"**Root Cause**: {root_cause}")
        lines.append("")

    evidence = result.get("evidence", [])
    if evidence:
        lines.append("### Evidence")
        for item in evidence:
            lines.append(f"- {item}")
        lines.append("")

    fix = result.get("fix_recommendation", "")
    if fix:
        lines.append(f"**Fix**: {fix}")
        lines.append("")

    files_to_check = result.get("files_to_check", [])
    if files_to_check:
        lines.append("### Files to Check")
        for item in files_to_check:
            lines.append(f"- {item}")
        lines.append("")

    risks = result.get("risks_in_fix", [])
    if risks:
        lines.append("### Risks in Fix")
        for item in risks:
            lines.append(f"- {item}")
        lines.append("")

    alternatives = result.get("alternative_fixes", [])
    if alternatives:
        lines.append("### Alternative Fixes")
        for item in alternatives:
            lines.append(f"- {item}")
        lines.append("")

    if "raw_response" in result and result.get("confidence", 0) == 0.0:
        lines.append("*Note: JSON parsing failed - raw response shown in root_cause*")

    return "\n".join(lines)
