"""Active long-term rules context for committee prompts."""

from __future__ import annotations

from pathlib import Path


DEFAULT_ACTIVE_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "active_rules.txt"
DEFAULT_WEEKLY_FULL_SCAN_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "rules" / "weekly_full_scan_rules.txt"
)

_DECISION_STAGES = {"decision", "final_decision", "committee"}
_WEEKLY_FULL_SCAN_STAGES = {
    "weekly_full_scan",
    "weekly_scan",
    "discovery",
    "evidence_enrichment",
}


class ActiveRulesProvider:
    """Load the current long-term trading rules for LLM decision context."""

    def __init__(
        self,
        rules_path: str | Path | None = None,
        weekly_full_scan_rules_path: str | Path | None = None,
    ):
        self.rules_path = Path(rules_path) if rules_path else DEFAULT_ACTIVE_RULES_PATH
        self.weekly_full_scan_rules_path = (
            Path(weekly_full_scan_rules_path)
            if weekly_full_scan_rules_path
            else DEFAULT_WEEKLY_FULL_SCAN_RULES_PATH
        )

    def load(self) -> str:
        return self._load_path(self.rules_path)

    def load_for_stage(self, stage: str = "decision") -> str:
        normalized_stage = stage.strip().lower()
        if normalized_stage in _DECISION_STAGES:
            return self.load()
        if normalized_stage in _WEEKLY_FULL_SCAN_STAGES:
            return self._load_path(self.weekly_full_scan_rules_path)
        raise ValueError(f"Unknown active-rules stage: {stage}")

    @staticmethod
    def _load_path(path: Path) -> str:
        if not path.exists():
            return f"Active rules file not found: {path}"
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return f"Active rules file is empty: {path}"
        return text


__all__ = [
    "ActiveRulesProvider",
    "DEFAULT_ACTIVE_RULES_PATH",
    "DEFAULT_WEEKLY_FULL_SCAN_RULES_PATH",
]
